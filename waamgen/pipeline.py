"""Iterative WAAM NC generator with validation, plotting, and a small CLI.

The generator prefers toolpaths/slices over mesh payloads and can run in three
stages:
1) Build a light IR from a structured WAAM plan JSON (paths + layers only).
2) Convert IR -> LinuxCNC G-code with torch/gas sequencing from the post.
3) Validate (syntax -> safety -> geometry) and retry up to max_iters.

CLI:
    waamgen generate --input job.json --out job.nc --max-iters 5 [--plot]
    waamgen validate --gcode job.nc --ref job.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple, Set

from ..core.config_loader import (
    ConfigError,
    DepositionParams,
    PostCommands,
    validate_deposition_params,
    validate_post_config,
)
from ..core.paths import _output_root_for
from ..nc import parser as nc_parser
from ..nc.support_trim import (
    build_support_index,
    segments_from_polyline,
    support_radius_from_bead,
    trim_polyline_to_support,
)

try:  # Optional Jinja2 prompt rendering.
    import jinja2
except Exception:  # pragma: no cover - optional dependency
    jinja2 = None


Point2D = Tuple[float, float]
Point3D = Tuple[float, float, float]


@dataclass
class LayerSpec:
    index: int
    z: float
    path_ids: List[str]
    meta: Optional[Mapping[str, object]] = None


@dataclass
class LightJob:
    source_path: Path
    metadata: Mapping[str, object]
    process: Mapping[str, object]
    post: PostCommands
    post_raw: Mapping[str, object]
    deposition: DepositionParams
    frames: Mapping[str, object]
    paths: Dict[str, List[Point2D]]
    path_meta: Dict[str, Mapping[str, object]]
    layers: List[LayerSpec]


@dataclass
class ValidationReport:
    syntax_ok: bool
    safety_ok: bool
    geometry_ok: bool
    messages: List[str]

    @property
    def ok(self) -> bool:
        return self.syntax_ok and self.safety_ok and self.geometry_ok


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _as_optional_float(value) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _support_constraints_from_process(process: Mapping[str, object]) -> Tuple[Optional[float], Optional[float]]:
    dep = process.get("deposition") if isinstance(process.get("deposition"), Mapping) else process
    if not isinstance(dep, Mapping):
        return None, None
    overlap_ratio = None
    overlap_pct = _as_optional_float(dep.get("support_min_overlap_pct"))
    if overlap_pct is not None:
        overlap_ratio = max(0.0, min(1.0, overlap_pct / 100.0))
    else:
        overlap_ratio = _as_optional_float(dep.get("support_min_overlap_ratio"))
        if overlap_ratio is not None:
            overlap_ratio = max(0.0, min(1.0, overlap_ratio))
    overhang_deg = _as_optional_float(
        dep.get("support_max_overhang_angle_deg") or dep.get("support_overhang_angle_deg")
    )
    return overlap_ratio, overhang_deg


def _support_trim_settings_from_process(
    process: Mapping[str, object],
) -> Tuple[bool, float]:
    """
    Extract coarse support trimming controls from process parameters.

    Layout (all optional):

        process.deposition.support_trim.enabled: bool
        process.deposition.support_trim.radius_scale: float

    When absent, defaults preserve previous behaviour (enabled with
    radius_scale=1.0).
    """

    dep = process.get("deposition") if isinstance(process.get("deposition"), Mapping) else process
    trim_cfg = {}
    if isinstance(dep, Mapping) and isinstance(dep.get("support_trim"), Mapping):
        trim_cfg = dep.get("support_trim") or {}

    enabled_raw = trim_cfg.get("enabled")
    enabled = bool(enabled_raw) if enabled_raw is not None else True

    radius_scale = _as_optional_float(trim_cfg.get("radius_scale"))
    if radius_scale is None or not math.isfinite(radius_scale):
        radius_scale = 1.0
    radius_scale = max(0.0, radius_scale)

    return enabled, radius_scale


def _frame_key(value: object, default: str = "mesh_frame") -> str:
    return str(value or default).strip().lower()


def _mesh_to_cad_offset(frames: Mapping[str, object]) -> Optional[Tuple[float, float, float]]:
    """Return mesh->CAD translation from frames when available."""
    raw = frames.get("T_cad_to_mesh")
    if not isinstance(raw, Sequence) or len(raw) < 3:
        return None
    try:
        tx = float(raw[0][3])
        ty = float(raw[1][3])
        tz = float(raw[2][3])
    except Exception:
        return None
    # T_cad_to_mesh moves CAD points into mesh frame; invert for mesh->CAD.
    return (-tx, -ty, -tz)


def _matrix_from_frames(frames: Mapping[str, object], key: str) -> Optional[List[List[float]]]:
    raw = frames.get(key)
    if not isinstance(raw, Sequence) or len(raw) < 4:
        return None
    rows: List[List[float]] = []
    for row in raw[:4]:
        if not isinstance(row, Sequence) or len(row) < 4:
            return None
        try:
            rows.append([float(row[0]), float(row[1]), float(row[2]), float(row[3])])
        except Exception:
            return None
    return rows


def _apply_xy_transform(coords: List[Point2D], matrix: List[List[float]]) -> List[Point2D]:
    m = matrix
    transformed: List[Point2D] = []
    for x, y in coords:
        nx = m[0][0] * x + m[0][1] * y + m[0][3]
        ny = m[1][0] * x + m[1][1] * y + m[1][3]
        transformed.append((nx, ny))
    return transformed


def _apply_layer_z(z_val: float, matrix: List[List[float]]) -> float:
    return matrix[2][2] * z_val + matrix[2][3]


def _point_xy(point: Sequence[float]) -> Point2D:
    return (float(point[0]), float(point[1]))


def _point_has_z(point: Sequence[float]) -> bool:
    return len(point) >= 3


def _point_xyz(point: Sequence[float], *, default_z: float = 0.0) -> Point3D:
    x, y = float(point[0]), float(point[1])
    z = float(point[2]) if len(point) >= 3 else float(default_z)
    return (x, y, z)


def _matrix_mixes_axes(matrix: List[List[float]], tol: float = 1e-8) -> bool:
    try:
        return (
            abs(matrix[0][2]) > tol
            or abs(matrix[1][2]) > tol
            or abs(matrix[2][0]) > tol
            or abs(matrix[2][1]) > tol
        )
    except Exception:
        return False


def _apply_xyz_transform(point: Sequence[float], matrix: List[List[float]], *, z_fallback: float = 0.0) -> Point3D:
    x, y, z = _point_xyz(point, default_z=z_fallback)
    m = matrix
    nx = m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3]
    ny = m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3]
    nz = m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3]
    return (nx, ny, nz)


def _load_json(path: Path) -> MutableMapping[str, object]:
    if not path.is_file():
        raise FileNotFoundError(f"Input JSON not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, MutableMapping):
        raise ValueError("Input JSON must contain an object at the top level.")
    return data


def _start_point_from_metadata(metadata: Mapping[str, object]) -> Optional[Tuple[float, float, float]]:
    dep = metadata.get("deposition_start")
    point = None
    if isinstance(dep, Mapping):
        point = dep.get("point_wcs") or dep.get("point") or dep.get("start_point")
    else:
        point = dep
    if isinstance(point, Sequence) and len(point) >= 2:
        try:
            x = float(point[0])
            y = float(point[1])
            z = float(point[2]) if len(point) > 2 else 0.0
            return (x, y, z)
        except Exception:
            return None
    return None


def _is_closed_path(points: Sequence[Point2D], tol: float = 1e-6) -> bool:
    if len(points) < 3:
        return False
    p0 = _point_xy(points[0])
    p1 = _point_xy(points[-1])
    return abs(p0[0] - p1[0]) <= tol and abs(p0[1] - p1[1]) <= tol


def _closest_point_index(points: Sequence[Point2D], target: Point2D) -> Optional[int]:
    if not points:
        return None
    core = points[:-1] if _is_closed_path(points) else points
    if not core:
        return None
    tx, ty = target
    best_idx = 0
    best_dist = None
    for idx, pt in enumerate(core):
        x, y = _point_xy(pt)
        dx = x - tx
        dy = y - ty
        dist = dx * dx + dy * dy
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_idx = idx
    return best_idx


def _min_distance_sq(points: Sequence[Point2D], target: Point2D) -> float:
    idx = _closest_point_index(points, target)
    if idx is None:
        return float("inf")
    x, y = _point_xy(points[idx])
    dx = x - target[0]
    dy = y - target[1]
    return dx * dx + dy * dy


def _rotate_path_points(points: Sequence[Point2D], start_idx: int) -> List[Point2D]:
    if not points or start_idx <= 0:
        return list(points)
    closed = _is_closed_path(points)
    core = list(points[:-1]) if closed else list(points)
    if not core:
        return list(points)
    start_idx = min(start_idx, len(core) - 1)
    rotated = core[start_idx:] + core[:start_idx]
    if closed:
        rotated.append(rotated[0])
    return rotated


def _points_close(a: Point2D, b: Point2D, tol: float = 1e-6) -> bool:
    ax, ay = _point_xy(a)
    bx, by = _point_xy(b)
    return abs(ax - bx) <= tol and abs(ay - by) <= tol


def _polyline_equivalent(a: Sequence[Point2D], b: Sequence[Point2D], tol: float = 1e-6) -> bool:
    if len(a) != len(b):
        return False
    return all(_points_close(pa, pb, tol=tol) for pa, pb in zip(a, b))


def _unique_path_id(base: str, used: set[str]) -> str:
    if base not in used:
        return base
    counter = 2
    while True:
        candidate = f"{base}_{counter}"
        if candidate not in used:
            return candidate
        counter += 1


def _clone_path_meta(
    meta: Optional[Mapping[str, object]],
    *,
    new_id: Optional[str] = None,
    closed: Optional[bool] = None,
    trim_source: Optional[str] = None,
) -> Dict[str, object]:
    out = dict(meta or {})
    if new_id:
        out["id"] = new_id
    if closed is not None:
        out["closed"] = bool(closed)
    tags = dict(out.get("planner_tags") or {})
    if trim_source:
        tags["trim_source"] = str(trim_source)
    if closed is False:
        tags.setdefault("continuity_reversible", True)
    elif closed is True:
        tags.setdefault("continuity_reversible", False)
    if tags:
        out["planner_tags"] = tags
    return out


def _path_meta_from_raw(path_id: str, pdata: Mapping[str, object]) -> Dict[str, object]:
    meta = {key: value for key, value in pdata.items() if key not in {"points_xyz", "points"}}
    meta["id"] = path_id
    tags = dict(meta.get("planner_tags") or {})
    tags.setdefault("continuity_reversible", not bool(meta.get("closed")))
    if tags:
        meta["planner_tags"] = tags
    return meta


def _layer_meta_from_raw(item: Mapping[str, object]) -> Optional[Mapping[str, object]]:
    meta = {key: value for key, value in item.items() if key not in {"i", "index", "z", "z_mm", "path_ids"}}
    return meta or None


def _trim_paths_to_support(
    paths: Dict[str, List[Point2D]],
    path_meta: Dict[str, Mapping[str, object]],
    layers: List[LayerSpec],
    support_radius: float,
) -> Tuple[Dict[str, List[Point2D]], Dict[str, Mapping[str, object]], List[LayerSpec]]:
    if support_radius <= 0.0 or not paths or not layers:
        return paths, path_meta, layers
    sample_step = max(0.1, support_radius * 0.5)
    min_segment_length = max(0.05, support_radius * 0.05)
    new_paths: Dict[str, List[Point2D]] = {}
    new_path_meta: Dict[str, Mapping[str, object]] = {}
    new_layers: List[LayerSpec] = []
    used_ids: set[str] = set()
    support_index = None
    base_open = True

    for layer in layers:
        layer_ids: List[str] = []
        layer_segments: List[Tuple[Point2D, Point2D]] = []

        if base_open:
            for pid in layer.path_ids:
                pts = paths.get(pid)
                if not pts or len(pts) < 2:
                    continue
                new_paths[pid] = list(pts)
                new_path_meta[pid] = _clone_path_meta(path_meta.get(pid), new_id=pid)
                used_ids.add(pid)
                layer_ids.append(pid)
                layer_segments.extend(segments_from_polyline(pts))
            if layer_segments:
                base_open = False
        else:
            if support_index is None:
                new_layers.append(LayerSpec(index=layer.index, z=layer.z, path_ids=[], meta=layer.meta))
                continue
            for pid in layer.path_ids:
                pts = paths.get(pid)
                if not pts or len(pts) < 2:
                    continue
                trimmed = trim_polyline_to_support(
                    pts,
                    support_index,
                    support_radius,
                    sample_step=sample_step,
                    min_segment_length=min_segment_length,
                )
                if not trimmed:
                    continue
                if len(trimmed) == 1 and _polyline_equivalent(pts, trimmed[0]):
                    new_paths[pid] = list(pts)
                    new_path_meta[pid] = _clone_path_meta(path_meta.get(pid), new_id=pid)
                    used_ids.add(pid)
                    layer_ids.append(pid)
                    layer_segments.extend(segments_from_polyline(pts))
                else:
                    for idx, poly in enumerate(trimmed, start=1):
                        base = f"{pid}__trim{idx}"
                        new_id = _unique_path_id(base, used_ids)
                        new_paths[new_id] = poly
                        new_path_meta[new_id] = _clone_path_meta(
                            path_meta.get(pid),
                            new_id=new_id,
                            closed=False,
                            trim_source=pid,
                        )
                        used_ids.add(new_id)
                        layer_ids.append(new_id)
                        layer_segments.extend(segments_from_polyline(poly))

        new_layers.append(LayerSpec(index=layer.index, z=layer.z, path_ids=layer_ids, meta=layer.meta))
        support_index = (
            build_support_index(layer_segments, support_radius) if layer_segments else None
        )

    return new_paths, new_path_meta, new_layers


def _extract_structured(
    job: Mapping[str, object],
) -> Tuple[Dict[str, List[Point2D] | List[Point3D]], Dict[str, Mapping[str, object]], List[LayerSpec], Mapping[str, object], bool]:
    """Prefer `llm_context.waam_structured`; fallback to `llm_context.toolpath`."""
    ctx = job.get("llm_context") or {}
    structured = ctx.get("waam_structured") or ctx.get("toolpath") or {}
    paths_raw = structured.get("paths") or {}
    layers_raw = structured.get("layers") or []
    frames = (
        structured.get("frames")
        or (ctx.get("toolpath") or {}).get("frames")
        or (ctx.get("summary") or {}).get("frames")
        or ctx.get("frames")
        or {}
    )
    mesh_to_wcs = _matrix_from_frames(frames, "T_mesh_to_wcs")
    mesh_to_cad = _mesh_to_cad_offset(frames) if mesh_to_wcs is None else None
    full_3d = bool(mesh_to_wcs and _matrix_mixes_axes(mesh_to_wcs))

    raw_paths: Dict[str, List[Point3D]] = {}
    raw_path_meta: Dict[str, Mapping[str, object]] = {}
    for pid, pdata in paths_raw.items():
        pts = pdata.get("points_xyz") or pdata.get("points") or []
        coords: List[Point3D] = []
        for entry in pts:
            try:
                x, y = float(entry[0]), float(entry[1])
                z = float(entry[2]) if len(entry) >= 3 else 0.0
                coords.append((x, y, z))
            except Exception:
                continue
        if coords:
            path_id = str(pid)
            raw_paths[path_id] = coords
            raw_path_meta[path_id] = _path_meta_from_raw(path_id, pdata if isinstance(pdata, Mapping) else {})

    layers_pre: List[LayerSpec] = []
    for item in layers_raw:
        if not isinstance(item, Mapping):
            continue
        idx = int(item.get("i") or item.get("index") or 0)
        z_val = _as_float(item.get("z"), default=_as_float(item.get("z_mm"), 0.0))
        frame_key = _frame_key(item.get("frame"))
        if frame_key == "mesh_frame" and not full_3d:
            if mesh_to_wcs:
                z_val = _apply_layer_z(z_val, mesh_to_wcs)
            elif mesh_to_cad:
                z_val += mesh_to_cad[2]
        path_ids = [str(pid) for pid in (item.get("path_ids") or [])]
        if not path_ids:
            continue
        layers_pre.append(LayerSpec(index=idx, z=z_val, path_ids=path_ids, meta=_layer_meta_from_raw(item)))

    layers: List[LayerSpec] = [
        LayerSpec(index=i, z=layer.z, path_ids=layer.path_ids, meta=layer.meta) for i, layer in enumerate(layers_pre)
    ]

    if full_3d:
        # Apply full 3D transform using layer Z so rotated WCS axes are respected.
        new_paths: Dict[str, List[Point3D]] = {}
        new_path_meta: Dict[str, Mapping[str, object]] = {}
        new_layers: List[LayerSpec] = []
        used_ids: set[str] = set()
        for layer in layers:
            layer_ids: List[str] = []
            z_samples: List[float] = []
            for pid in layer.path_ids:
                pts = raw_paths.get(pid)
                if not pts:
                    continue
                new_id = _unique_path_id(f"{pid}__L{layer.index}", used_ids)
                used_ids.add(new_id)
                transformed: List[Point3D] = []
                frame_key = _frame_key((paths_raw.get(pid) or {}).get("frame"))
                for x, y, z0 in pts:
                    z_mesh = float(z0) + float(layer.z)
                    if frame_key == "mesh_frame":
                        nx, ny, nz = _apply_xyz_transform((x, y, z_mesh), mesh_to_wcs, z_fallback=0.0)
                    else:
                        nx, ny, nz = x, y, z_mesh
                    transformed.append((nx, ny, nz))
                    z_samples.append(nz)
                new_paths[new_id] = transformed
                new_path_meta[new_id] = _clone_path_meta(raw_path_meta.get(pid), new_id=new_id)
                layer_ids.append(new_id)
            if not layer_ids:
                continue
            avg_z = sum(z_samples) / len(z_samples) if z_samples else layer.z
            new_layers.append(LayerSpec(index=layer.index, z=avg_z, path_ids=layer_ids, meta=layer.meta))
        return new_paths, new_path_meta, new_layers, frames, True

    # 2D transform path (rotation about Z + translation).
    paths: Dict[str, List[Point2D]] = {}
    path_meta: Dict[str, Mapping[str, object]] = {}
    for pid, pts in raw_paths.items():
        coords: List[Point2D] = [(x, y) for x, y, _ in pts]
        frame_key = _frame_key((paths_raw.get(pid) or {}).get("frame"))
        if frame_key == "mesh_frame":
            if mesh_to_wcs:
                coords = _apply_xy_transform(coords, mesh_to_wcs)
            elif mesh_to_cad:
                coords = [(x + mesh_to_cad[0], y + mesh_to_cad[1]) for x, y in coords]
        paths[str(pid)] = coords
        path_meta[str(pid)] = _clone_path_meta(raw_path_meta.get(pid), new_id=str(pid))

    # Normalize Z only when no explicit CAD/mesh alignment is present.
    if layers and mesh_to_cad is None and mesh_to_wcs is None:
        try:
            min_z = min(layer.z for layer in layers)
            if abs(min_z) > 1e-6:
                for layer in layers:
                    layer.z -= min_z
        except Exception:
            pass

    return paths, path_meta, layers, frames, False


def load_light_job(path: Path | str) -> LightJob:
    """Reduce a structured WAAM plan JSON to the minimum needed for NC generation."""
    job_path = Path(path).expanduser().resolve()
    data = _load_json(job_path)
    paths, path_meta, layers, frames, uses_3d_paths = _extract_structured(data)
    if not paths or not layers:
        raise ValueError("No structured paths/layers found in llm_context.waam_structured.")

    metadata = data.get("metadata") or {}
    units_val = metadata.get("units")
    if not isinstance(units_val, str) or not units_val.strip():
        raise ValueError("Job metadata.units is required and must be a string (e.g., 'mm').")
    wcs_val = metadata.get("wcs")
    if isinstance(wcs_val, str):
        wcs_name = wcs_val.strip()
    elif isinstance(wcs_val, Mapping):
        wcs_name = str(wcs_val.get("name") or "").strip()
    else:
        wcs_name = ""
    if not wcs_name:
        raise ValueError("Job metadata.wcs is required (e.g., 'G54').")
    _ = wcs_name  # validated for presence; retained in metadata for downstream use
    process = (data.get("process_parameters") or data.get("process") or {})
    post_raw = data.get("post") or metadata.get("post") or {}
    try:
        post = validate_post_config(post_raw)
    except ConfigError as exc:
        raise ValueError(f"Invalid post config in job: {exc}") from exc

    dep_raw = (process.get("deposition") or process)
    try:
        deposition = validate_deposition_params(dep_raw)
    except ConfigError as exc:
        raise ValueError(f"Invalid deposition parameters in job: {exc}") from exc

    layers_sorted = sorted(enumerate(layers), key=lambda item: (float(item[1].z), int(item[1].index), item[0]))
    layers = [LayerSpec(index=i, z=layer.z, path_ids=list(layer.path_ids), meta=layer.meta) for i, (_order, layer) in enumerate(layers_sorted)]

    start_point = _start_point_from_metadata(metadata)
    if start_point and layers:
        first_layer = layers[0]
        target_xy = (start_point[0], start_point[1])
        if first_layer.path_ids:
            first_layer.path_ids.sort(
                key=lambda pid: _min_distance_sq(paths.get(pid) or [], target_xy)
            )
            first_path = first_layer.path_ids[0]
            pts = paths.get(first_path) or []
            idx = _closest_point_index(pts, target_xy)
            if idx is not None and idx != 0:
                paths[first_path] = _rotate_path_points(pts, idx)

    support_overlap_ratio, support_overhang_deg = _support_constraints_from_process(process)
    trim_enabled, radius_scale = _support_trim_settings_from_process(process)
    support_radius = support_radius_from_bead(
        deposition.bead_width_mm,
        min_overlap_ratio=support_overlap_ratio,
        max_overhang_angle_deg=support_overhang_deg,
        layer_height=deposition.layer_height_mm,
    )
    if not trim_enabled:
        support_radius = 0.0
    else:
        support_radius = max(0.0, support_radius * radius_scale)
    if not uses_3d_paths and support_radius > 0.0:
        paths, path_meta, layers = _trim_paths_to_support(paths, path_meta, layers, support_radius)

    return LightJob(
        source_path=job_path,
        metadata=metadata,
        process=process,
        post=post,
        post_raw=post_raw,
        deposition=deposition,
        frames=frames,
        paths=paths,
        path_meta=path_meta,
        layers=layers,
    )


def _torch_sequence(post: PostCommands, post_raw: Mapping[str, object], wire_feed: float) -> Dict[str, str]:
    # Timing fields remain optional but must be numeric when present.
    return {
        "torch_on": post.torch_on,
        "torch_off": post.torch_off,
        "gas_on": post.gas_on,
        "gas_off": post.gas_off,
        "wire_feed": post.wire_feed.format(wire_feed=wire_feed),
        "travel_speed": post.travel_speed,
        "arc_start_delay": float(_as_float(post_raw.get("arc_start_delay_s"), 0.4)),
        "arc_stop_delay": float(_as_float(post_raw.get("arc_stop_delay_s"), 0.2)),
        "gas_preflow": float(_as_float(post_raw.get("gas_preflow_s"), 1.0)),
        "gas_postflow": float(_as_float(post_raw.get("gas_postflow_s"), 2.0)),
    }


def _format_xy(x: float, y: float, feed: Optional[str] = None, *, z: Optional[float] = None) -> str:
    line = f"G1 X{x:.3f} Y{y:.3f}"
    if z is not None:
        line = f"{line} Z{z:.3f}"
    if feed:
        line = f"{line} {feed}"
    return line


def _chain_gap_mm(a_end: Point2D, b_start: Point2D) -> float:
    dx = float(b_start[0]) - float(a_end[0])
    dy = float(b_start[1]) - float(a_end[1])
    return math.hypot(dx, dy)


def _path_role_hint(path_meta: Mapping[str, object]) -> str:
    return str(path_meta.get("role_hint") or "").strip().upper()


def _planner_tags(path_meta: Mapping[str, object]) -> Mapping[str, object]:
    tags = path_meta.get("planner_tags")
    return tags if isinstance(tags, Mapping) else {}


def _path_region_mode(path_meta: Mapping[str, object]) -> str:
    return str(_planner_tags(path_meta).get("region_mode") or "").strip().lower()


def _path_hotspot_kinds(path_meta: Mapping[str, object]) -> list[str]:
    tags = _planner_tags(path_meta)
    raw = tags.get("hotspot_kinds") or []
    if not isinstance(raw, Sequence):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip().lower()
        if text:
            out.append(text)
    return out


def _path_region_id(path_meta: Mapping[str, object]) -> str:
    return str(_planner_tags(path_meta).get("region_id") or "").strip()


def _path_skip_deposition(path_meta: Mapping[str, object]) -> bool:
    if _path_role_hint(path_meta) == "LINK":
        return True
    raw = _planner_tags(path_meta).get("skip_deposition")
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)


def _path_start_heading_deg(points: Sequence[Sequence[float]]) -> Optional[float]:
    if len(points) < 2:
        return None
    a = _point_xy(points[0])
    b = _point_xy(points[1])
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    if abs(dx) <= 1e-9 and abs(dy) <= 1e-9:
        return None
    return math.degrees(math.atan2(dy, dx))


def _path_end_heading_deg(points: Sequence[Sequence[float]]) -> Optional[float]:
    if len(points) < 2:
        return None
    a = _point_xy(points[-2])
    b = _point_xy(points[-1])
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    if abs(dx) <= 1e-9 and abs(dy) <= 1e-9:
        return None
    return math.degrees(math.atan2(dy, dx))


def _heading_delta_deg(a_deg: Optional[float], b_deg: Optional[float]) -> float:
    if a_deg is None or b_deg is None:
        return 0.0
    delta = abs(float(a_deg) - float(b_deg)) % 360.0
    return min(delta, 360.0 - delta)


def _continuity_cfg_from_process_config(process_cfg: Mapping[str, object]) -> Mapping[str, object]:
    path_cfg = process_cfg.get("path") if isinstance(process_cfg.get("path"), Mapping) else {}
    topo_cfg = path_cfg.get("topology_sensitive") if isinstance(path_cfg.get("topology_sensitive"), Mapping) else {}
    return topo_cfg.get("continuity") if isinstance(topo_cfg.get("continuity"), Mapping) else {}


def _can_chain_paths(
    prev_path_id: str,
    next_path_id: str,
    *,
    paths: Mapping[str, Sequence[Sequence[float]]],
    path_meta: Mapping[str, Mapping[str, object]],
    continuity_cfg: Mapping[str, object],
) -> tuple[bool, str]:
    if not bool(continuity_cfg.get("emit_chains", False)):
        return False, "emit_chains_disabled"

    prev_points = paths.get(prev_path_id) or []
    next_points = paths.get(next_path_id) or []
    if len(prev_points) < 2 or len(next_points) < 2:
        return False, "degenerate"

    prev_meta = path_meta.get(prev_path_id) or {}
    next_meta = path_meta.get(next_path_id) or {}
    if _path_skip_deposition(prev_meta) or _path_skip_deposition(next_meta):
        return False, "skip_deposition"
    if bool(prev_meta.get("closed")) or bool(next_meta.get("closed")):
        return False, "closed_path"
    if _is_closed_path(prev_points) or _is_closed_path(next_points):
        return False, "closed_path"

    allowed_roles = {
        str(item).strip().upper()
        for item in (continuity_cfg.get("allowed_roles") or ["INFILL"])
        if str(item).strip()
    }
    prev_role = _path_role_hint(prev_meta)
    next_role = _path_role_hint(next_meta)
    if prev_role != next_role or prev_role not in allowed_roles:
        return False, "role"

    allowed_modes = {
        str(item).strip().lower()
        for item in (continuity_cfg.get("allowed_region_modes") or ["bulk_fill", "band_fill"])
        if str(item).strip()
    }
    prev_mode = _path_region_mode(prev_meta)
    next_mode = _path_region_mode(next_meta)
    if prev_mode != next_mode or prev_mode not in allowed_modes:
        return False, "region_mode"

    prev_region = _path_region_id(prev_meta)
    next_region = _path_region_id(next_meta)
    if not prev_region or prev_region != next_region:
        return False, "region_id"

    blocked_hotspots = {
        str(item).strip().lower()
        for item in (continuity_cfg.get("disallow_hotspot_kinds") or ["corner", "junction"])
        if str(item).strip()
    }
    if blocked_hotspots.intersection(_path_hotspot_kinds(prev_meta)) or blocked_hotspots.intersection(_path_hotspot_kinds(next_meta)):
        return False, "hotspot"

    gap = _chain_gap_mm(_point_xy(prev_points[-1]), _point_xy(next_points[0]))
    max_gap = float(continuity_cfg.get("max_chain_gap_mm") or 0.0)
    if gap > max_gap + 1e-6:
        return False, "gap"

    max_heading_delta = float(continuity_cfg.get("max_chain_heading_delta_deg") or 180.0)
    heading_delta = _heading_delta_deg(
        _path_end_heading_deg(prev_points),
        _path_start_heading_deg(next_points),
    )
    if heading_delta > max_heading_delta + 1e-6:
        return False, "heading"

    return True, "ok"


def _build_layer_path_chains(
    layer: Mapping[str, object],
    *,
    paths: Mapping[str, Sequence[Sequence[float]]],
    path_meta: Mapping[str, Mapping[str, object]],
    continuity_cfg: Mapping[str, object],
) -> list[list[str]]:
    path_ids = [str(pid) for pid in (layer.get("path_ids") or []) if str(pid) in paths]
    if not path_ids:
        return []
    if not bool(continuity_cfg.get("emit_chains", False)):
        return [[pid] for pid in path_ids]

    max_paths_per_chain = max(1, int(continuity_cfg.get("max_paths_per_chain") or 12))
    chains: list[list[str]] = []
    current_chain = [path_ids[0]]
    for path_id in path_ids[1:]:
        can_chain = len(current_chain) < max_paths_per_chain
        if can_chain:
            can_chain, _reason = _can_chain_paths(
                current_chain[-1],
                path_id,
                paths=paths,
                path_meta=path_meta,
                continuity_cfg=continuity_cfg,
            )
        if can_chain:
            current_chain.append(path_id)
        else:
            chains.append(current_chain)
            current_chain = [path_id]
    chains.append(current_chain)
    return chains


def _travel_command(torch: Mapping[str, object], travel_speed: float) -> tuple[str, Optional[str]]:
    travel_cmd = str(torch["travel_speed"]).format(travel_speed=travel_speed)
    first_move_feed = travel_cmd if travel_cmd.startswith("F") else None
    return travel_cmd, first_move_feed


def _emit_deposition_start(
    lines: List[str],
    start: Sequence[float],
    *,
    z_val: float,
    clearance: float,
    travel_speed: float,
    torch: Mapping[str, object],
) -> Optional[str]:
    start_xy = _point_xy(start)
    start_z = float(start[2]) if _point_has_z(start) else z_val
    lines.append(f"G0 X{start_xy[0]:.3f} Y{start_xy[1]:.3f} Z{clearance:.3f}")
    lines.append(str(torch["gas_on"]))
    lines.append(f"G4 P{float(torch['gas_preflow']):.1f}")
    lines.append(str(torch["wire_feed"]))
    lines.append(f"G1 Z{start_z:.3f} F{max(50.0, travel_speed * 0.35):.1f}")
    lines.append(str(torch["torch_on"]))
    lines.append(f"G4 P{float(torch['arc_start_delay']):.1f}")
    travel_cmd, first_move_feed = _travel_command(torch, travel_speed)
    if not first_move_feed:
        lines.append(travel_cmd)
    return first_move_feed


def _emit_path_body(
    lines: List[str],
    pts: Sequence[Sequence[float]],
    z_val: float,
    *,
    first_move_feed: Optional[str] = None,
) -> None:
    if len(pts) < 2:
        return
    for idx, pt in enumerate(pts[1:]):
        x, y = _point_xy(pt)
        z = float(pt[2]) if _point_has_z(pt) else z_val
        if idx == 0 and first_move_feed:
            lines.append(_format_xy(x, y, first_move_feed, z=z if _point_has_z(pt) else None))
        else:
            lines.append(_format_xy(x, y, z=z if _point_has_z(pt) else None))


def _emit_chain_link_move(
    lines: List[str],
    target: Sequence[float],
    *,
    z_val: float,
) -> None:
    x, y = _point_xy(target)
    z = float(target[2]) if _point_has_z(target) else z_val
    lines.append(_format_xy(x, y, z=z if _point_has_z(target) else None))


def _emit_deposition_end(
    lines: List[str],
    end: Sequence[float],
    *,
    rapid_z: float,
    dwell_layer: float,
    torch: Mapping[str, object],
) -> None:
    lines.append(f"G4 P{dwell_layer:.1f}")
    lines.append(str(torch["torch_off"]))
    lines.append(f"G4 P{float(torch['arc_stop_delay']):.1f}")
    lines.append(str(torch["gas_off"]))
    lines.append(f"G4 P{float(torch['gas_postflow']):.1f}")
    end_xy = _point_xy(end)
    lines.append(f"G0 X{end_xy[0]:.3f} Y{end_xy[1]:.3f} Z{rapid_z:.3f}")


def _emit_travel_only_path(
    lines: List[str],
    pts: Sequence[Sequence[float]],
    *,
    z_val: float,
    clearance: float,
    rapid_z: float,
    travel_speed: float,
    torch: Mapping[str, object],
) -> None:
    if len(pts) < 2:
        return
    start_xy = _point_xy(pts[0])
    start_z = float(pts[0][2]) if _point_has_z(pts[0]) else z_val
    lines.append(f"G0 X{start_xy[0]:.3f} Y{start_xy[1]:.3f} Z{clearance:.3f}")
    lines.append(f"G1 Z{start_z:.3f} F{max(50.0, travel_speed * 0.35):.1f}")
    travel_cmd, first_move_feed = _travel_command(torch, travel_speed)
    if not first_move_feed:
        lines.append(travel_cmd)
    _emit_path_body(lines, pts, z_val, first_move_feed=first_move_feed)
    end_xy = _point_xy(pts[-1])
    lines.append(f"G0 X{end_xy[0]:.3f} Y{end_xy[1]:.3f} Z{rapid_z:.3f}")


def _emit_path_chain(
    lines: List[str],
    chain_path_ids: Sequence[str],
    *,
    paths: Mapping[str, Sequence[Sequence[float]]],
    path_meta: Mapping[str, Mapping[str, object]],
    z_val: float,
    torch: Mapping[str, object],
    clearance: float,
    rapid_z: float,
    travel_speed: float,
    dwell_layer: float,
    continuity_cfg: Mapping[str, object],
) -> None:
    if not chain_path_ids:
        return

    first_path_id = str(chain_path_ids[0])
    first_pts = paths.get(first_path_id) or []
    if len(first_pts) < 2:
        return
    first_meta = path_meta.get(first_path_id) or {}

    coincident_tol = float(continuity_cfg.get("coincident_endpoint_tol_mm") or 0.05)
    if len(chain_path_ids) > 1:
        lines.append(f"(Chain {' -> '.join(str(pid) for pid in chain_path_ids)})")
    lines.append(f"(Path {first_path_id})")
    if _path_skip_deposition(first_meta):
        _emit_travel_only_path(
            lines,
            first_pts,
            z_val=z_val,
            clearance=clearance,
            rapid_z=rapid_z,
            travel_speed=travel_speed,
            torch=torch,
        )
        lines.append("")
        return
    first_move_feed = _emit_deposition_start(
        lines,
        first_pts[0],
        z_val=z_val,
        clearance=clearance,
        travel_speed=travel_speed,
        torch=torch,
    )
    _emit_path_body(lines, first_pts, z_val, first_move_feed=first_move_feed)
    previous_pts = first_pts

    for path_id in chain_path_ids[1:]:
        pts = paths.get(path_id) or []
        if len(pts) < 2:
            continue
        lines.append(f"(Path {path_id})")
        gap = _chain_gap_mm(_point_xy(previous_pts[-1]), _point_xy(pts[0]))
        if gap > coincident_tol + 1e-6:
            _emit_chain_link_move(lines, pts[0], z_val=z_val)
        _emit_path_body(lines, pts, z_val)
        previous_pts = pts

    _emit_deposition_end(
        lines,
        previous_pts[-1],
        rapid_z=rapid_z,
        dwell_layer=dwell_layer,
        torch=torch,
    )
    lines.append("")


def build_ir(job: LightJob) -> Dict[str, object]:
    """Construct an intermediate representation from structured paths and process."""
    dep = job.deposition
    bead_width = dep.bead_width_mm
    layer_height = dep.layer_height_mm
    travel_speed = dep.travel_speed_mm_min
    wire_feed = dep.wire_feed_mm_min
    dwell_per_layer = dep.dwell_per_layer_s
    post_dict = asdict(job.post) if hasattr(job.post, "__dataclass_fields__") else job.post

    return {
        "meta": {
            "units": (job.metadata.get("units") or "mm"),
            "wcs": (job.metadata.get("wcs") or {}),
            "bead_width_mm": bead_width,
            "layer_height_mm": layer_height,
        },
        "metadata": job.metadata,
        "process": {
            "travel_speed_mm_min": travel_speed,
            "wire_feed_mm_min": wire_feed,
            "dwell_per_layer_s": dwell_per_layer,
        },
        "process_config": job.process,
        "post": post_dict,
        "post_meta": job.post_raw,
        "paths": job.paths,
        "path_meta": job.path_meta,
        "layers": [{"index": layer.index, "z": layer.z, "path_ids": layer.path_ids, "meta": layer.meta} for layer in job.layers],
    }


def ir_to_gcode(ir: Mapping[str, object]) -> str:
    """Convert IR to LinuxCNC G-code."""
    meta = ir.get("meta") or {}
    process = ir.get("process") or {}
    process_cfg = ir.get("process_config") or {}
    post_obj = ir.get("post") or {}
    post_meta = ir.get("post_meta") or {}
    post_cfg = post_obj if isinstance(post_obj, PostCommands) else validate_post_config(post_obj)
    paths = ir.get("paths") or {}
    path_meta = ir.get("path_meta") or {}
    layers = ir.get("layers") or []
    continuity_cfg = _continuity_cfg_from_process_config(process_cfg if isinstance(process_cfg, Mapping) else {})

    bead_width = _as_float(meta.get("bead_width_mm") or meta.get("bead_width"), 6.0)
    travel_speed = _as_float(process.get("travel_speed_mm_min"), 300.0)
    wire_feed = _as_float(process.get("wire_feed_mm_min"), 3200.0)
    dwell_layer = _as_float(process.get("dwell_per_layer_s"), 1.5)
    meta_cfg = ir.get("metadata") or {}
    clearance_raw = meta_cfg.get("clearance")
    if clearance_raw is None:
        clearance_raw = meta.get("clearance") if "clearance" in meta else 15.0
    clearance = float(_as_float(clearance_raw, 15.0))
    clearance_offset_raw = meta_cfg.get("clearance_offset")
    if clearance_offset_raw is None:
        clearance_offset_raw = 20.0
    clearance_offset = float(_as_float(clearance_offset_raw, 20.0))
    max_layer_z = 0.0
    for layer in layers:
        try:
            z_val = float(layer.get("z"))
        except Exception:
            continue
        if z_val > max_layer_z:
            max_layer_z = z_val
    # If any path carries explicit Z values, include them in clearance planning.
    try:
        for pts in paths.values():
            for pt in pts or []:
                if _point_has_z(pt):
                    max_layer_z = max(max_layer_z, float(pt[2]))
    except Exception:
        pass
    clearance = max(clearance, max_layer_z, max_layer_z + clearance_offset)

    torch = _torch_sequence(post_cfg, post_meta, wire_feed)
    rapid_raw = meta_cfg.get("rapidZ")
    if rapid_raw is None:
        rapid_raw = clearance
    rapid_z = float(_as_float(rapid_raw, clearance))
    if rapid_z < clearance:
        rapid_z = clearance
    wcs_name = (meta.get("wcs") or {}).get("name", "G54")
    program_name = meta.get("program") or meta.get("program_name")
    if not isinstance(program_name, str) or not program_name.strip():
        program_name = "WAAM_BASELINE.NC"
    optimizations = meta.get("optimizations")
    if not isinstance(optimizations, str) or not optimizations.strip():
        optimizations = "CONTINUOUS PATHS, ALTERNATING DIRECTION, STAGGERED STARTS"
    preamble = [
        "%",
        f"(PROGRAM: {program_name})",
        f"(OPTIMIZATIONS: {optimizations})",
        f"(WIRE FEED SPEED: {wire_feed / 1000.0:.1f} m/min | TRAVEL SPEED: {travel_speed:.0f} mm/min)",
        "",
        "G21 (Units in mm)",
        "G90 (Absolute Positioning)",
        "G17 (XY Plane)",
        wcs_name,
        "G94 (Feed per minute)",
        "G40 (Cancel cutter comp)",
        "G49 (Cancel tool length)",
        f"G0 Z{clearance:.3f}",
        "",
        "( --- START SEQUENCE --- )",
        "",
    ]
    lines: List[str] = list(preamble)

    for layer in layers:
        try:
            z_val = float(layer.get("z"))
        except Exception:
            z_val = 0.0
        layer_idx = layer.get("index")
        lines.append(f"(--- LAYER {layer_idx} Z={z_val:.3f} ---)")
        for chain_path_ids in _build_layer_path_chains(
            layer,
            paths=paths if isinstance(paths, Mapping) else {},
            path_meta=path_meta if isinstance(path_meta, Mapping) else {},
            continuity_cfg=continuity_cfg,
        ):
            _emit_path_chain(
                lines,
                chain_path_ids,
                paths=paths if isinstance(paths, Mapping) else {},
                path_meta=path_meta if isinstance(path_meta, Mapping) else {},
                z_val=z_val,
                torch=torch,
                clearance=clearance,
                rapid_z=rapid_z,
                travel_speed=travel_speed,
                dwell_layer=dwell_layer,
                continuity_cfg=continuity_cfg,
            )

    lines.extend([torch["torch_off"], torch["gas_off"], f"G0 Z{clearance:.3f}", "G0 X0.0 Y0.0", "M30", "%"])
    return "\n".join(lines)


def _render_prompt(job: LightJob, template_path: Optional[Path] = None) -> str:
    """Build a rigid prompt from a Jinja template or fallback string."""
    tpl_path = template_path or (Path(__file__).resolve().parents[1] / "prompts" / "waam_gcode.jinja")
    wcs = job.metadata.get("wcs") if isinstance(job.metadata, Mapping) else None
    wcs_name = (wcs.get("name") if isinstance(wcs, Mapping) else None) or "G54"
    units = job.metadata.get("units") if isinstance(job.metadata, Mapping) else "mm"
    post_dict = asdict(job.post) if hasattr(job.post, "__dataclass_fields__") else job.post
    context = {
        "metadata": job.metadata,
        "process": job.process,
        "post": post_dict,
        "frames": job.frames,
        "paths": job.paths,
        "layers": [layer.__dict__ for layer in job.layers],
        "wcs_name": wcs_name,
        "units": units,
    }
    if tpl_path.is_file() and jinja2:
        template = jinja2.Environment(loader=jinja2.FileSystemLoader(str(tpl_path.parent))).get_template(tpl_path.name)
        return template.render(**context)

    # Fallback prompt (no Jinja2 installed)
    lines = [
        "SYSTEM: You output only LinuxCNC G-code. Never explain.",
        "RULES:",
        "- Output MUST be valid LinuxCNC G-code.",
        "- Never rapid (G0) with arc/torch on (M62/M63).",
        "- Use M62/M63/M64/M65/M68 exactly as provided.",
        "- Follow reference layer Z values exactly; do not invent coordinates.",
        "- Only use provided PATHS coordinates.",
        "CONTEXT:",
        json.dumps(context, indent=2),
        "OUTPUT: G-code only.",
    ]
    return "\n".join(lines)


_LAYER_MARKER_RE = re.compile(r"^\(--- LAYER (\d+) Z=([-+0-9.]+) ---\)")


def _extract_layer_marker(line: str) -> Optional[int]:
    match = _LAYER_MARKER_RE.match(line.strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _resolve_clearance(job: LightJob) -> Tuple[float, float]:
    meta_cfg = job.metadata or {}
    clearance_raw = meta_cfg.get("clearance")
    if clearance_raw is None:
        clearance_raw = 15.0
    clearance_offset_raw = meta_cfg.get("clearance_offset")
    if clearance_offset_raw is None:
        clearance_offset_raw = 20.0
    clearance_offset = float(_as_float(clearance_offset_raw, 20.0))
    max_layer_z = max((layer.z for layer in job.layers), default=0.0)
    clearance = max(float(_as_float(clearance_raw, 15.0)), max_layer_z, max_layer_z + clearance_offset)
    rapid_raw = meta_cfg.get("rapidZ")
    if rapid_raw is None:
        rapid_raw = clearance
    rapid_z = float(_as_float(rapid_raw, clearance))
    if rapid_z < clearance:
        rapid_z = clearance
    return clearance, rapid_z


def _emit_patch_path(
    lines: List[str],
    pts: Sequence[Point2D],
    z_val: float,
    torch: Mapping[str, object],
    *,
    clearance: float,
    rapid_z: float,
    travel_speed: float,
    dwell_layer: float,
    label: Optional[str] = None,
) -> None:
    if len(pts) < 2:
        return
    start = _point_xy(pts[0])
    if label:
        lines.append(f"(Patch {label})")
    lines.append(f"G0 X{start[0]:.3f} Y{start[1]:.3f} Z{clearance:.3f}")
    lines.append(str(torch["gas_on"]))
    lines.append(f"G4 P{float(torch['gas_preflow']):.1f}")
    lines.append(str(torch["wire_feed"]))
    lines.append(f"G1 Z{z_val:.3f} F{max(50.0, travel_speed * 0.35):.1f}")
    lines.append(str(torch["torch_on"]))
    lines.append(f"G4 P{float(torch['arc_start_delay']):.1f}")
    travel_cmd = str(torch["travel_speed"]).format(travel_speed=travel_speed)
    first_move_feed = travel_cmd if travel_cmd.startswith("F") else None
    if not first_move_feed:
        lines.append(travel_cmd)
    for idx, pt in enumerate(pts[1:]):
        x, y = _point_xy(pt)
        if idx == 0 and first_move_feed:
            lines.append(_format_xy(x, y, first_move_feed))
        else:
            lines.append(_format_xy(x, y))
    lines.append(f"G4 P{dwell_layer:.1f}")
    lines.append(str(torch["torch_off"]))
    lines.append(f"G4 P{float(torch['arc_stop_delay']):.1f}")
    lines.append(str(torch["gas_off"]))
    lines.append(f"G4 P{float(torch['gas_postflow']):.1f}")
    end = _point_xy(pts[-1])
    lines.append(f"G0 X{end[0]:.3f} Y{end[1]:.3f} Z{rapid_z:.3f}")
    lines.append("")


def _layers_requiring_patch(
    job: LightJob,
    actual_by_layer: Mapping[int, Sequence[Point2D]],
    *,
    geom_tol: float,
) -> Set[int]:
    layers_to_patch: Set[int] = set()
    for layer in job.layers:
        ref_pts = [job.paths[pid] for pid in layer.path_ids if pid in job.paths]
        flat_ref = [pt for seq in ref_pts for pt in seq]
        if not flat_ref:
            continue
        actual_pts = list(actual_by_layer.get(layer.index, []))
        if not actual_pts or _has_degenerate(actual_pts):
            layers_to_patch.add(layer.index)
            continue
        hd = _hausdorff(flat_ref, actual_pts)
        if hd > geom_tol:
            layers_to_patch.add(layer.index)
            continue
        ref_area, ref_spans = _bbox_area_and_span(flat_ref)
        act_area, act_spans = _bbox_area_and_span(actual_pts)
        if ref_area > 0.0:
            if act_area < 0.2 * ref_area or act_area > 1.8 * ref_area:
                layers_to_patch.add(layer.index)
                continue
            dx = abs(act_spans[0] - ref_spans[0])
            dy = abs(act_spans[1] - ref_spans[1])
            if dx > geom_tol or dy > geom_tol:
                layers_to_patch.add(layer.index)
    return layers_to_patch


def apply_geometry_patch(
    gcode_text: str,
    job: LightJob,
    *,
    geom_tol: float = 0.8,
    max_layers: Optional[int] = None,
) -> Tuple[str, List[int]]:
    """Append deterministic patch passes for layers that fail geometry checks."""
    points, deposition_segments = nc_parser.parse_nc_text(
        gcode_text,
        torch_on_codes=[job.post.torch_on],
        torch_off_codes=[job.post.torch_off],
        allow_config=False,
    )
    _, depo_moves = nc_parser.split_moves(points, deposition_segments)
    actual_by_layer = _layer_points_from_gcode(depo_moves, job.layers)
    layers_to_patch = _layers_requiring_patch(job, actual_by_layer, geom_tol=geom_tol)
    if not layers_to_patch:
        return gcode_text, []
    if max_layers is not None:
        layers_to_patch = set(sorted(layers_to_patch)[:max_layers])

    dep = job.deposition
    travel_speed = dep.travel_speed_mm_min
    wire_feed = dep.wire_feed_mm_min
    dwell_layer = dep.dwell_per_layer_s
    torch = _torch_sequence(job.post, job.post_raw, wire_feed)
    clearance, rapid_z = _resolve_clearance(job)

    patch_blocks: Dict[int, List[str]] = {}
    for layer in job.layers:
        if layer.index not in layers_to_patch:
            continue
        block: List[str] = []
        block.append(f"(--- PATCH LAYER {layer.index} Z={layer.z:.3f} ---)")
        for path_id in layer.path_ids:
            pts = job.paths.get(path_id) or []
            _emit_patch_path(
                block,
                pts,
                layer.z,
                torch,
                clearance=clearance,
                rapid_z=rapid_z,
                travel_speed=travel_speed,
                dwell_layer=dwell_layer,
                label=path_id,
            )
        patch_blocks[layer.index] = block

    lines = gcode_text.splitlines()
    new_lines: List[str] = []
    current_layer: Optional[int] = None
    inserted: Set[int] = set()

    def maybe_insert_patch(layer_idx: Optional[int]) -> None:
        if layer_idx is None:
            return
        if layer_idx in patch_blocks and layer_idx not in inserted:
            new_lines.extend(patch_blocks[layer_idx])
            inserted.add(layer_idx)

    for line in lines:
        layer_idx = _extract_layer_marker(line)
        if layer_idx is not None:
            maybe_insert_patch(current_layer)
            current_layer = layer_idx
        if line.strip() in {"M30", "%"}:
            maybe_insert_patch(current_layer)
        new_lines.append(line)

    maybe_insert_patch(current_layer)
    return "\n".join(new_lines), sorted(layers_to_patch)


def _open_coverage_document(doc_path: Optional[Path | str]):
    try:
        import FreeCAD as App  # type: ignore
    except Exception as exc:  # pragma: no cover - FreeCAD dependency
        raise RuntimeError(f"FreeCAD is required for coverage gating: {exc}") from exc

    if doc_path:
        resolved = Path(doc_path).expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Coverage document not found: {resolved}")
        doc = App.openDocument(str(resolved))
        App.setActiveDocument(doc.Name)
        return doc, True

    doc = App.ActiveDocument
    if doc is None:
        raise RuntimeError("No active FreeCAD document for coverage gating.")
    return doc, False


def _pick_solid_object(doc) -> Optional[object]:
    for obj in getattr(doc, "Objects", []) or []:
        shape = getattr(obj, "Shape", None)
        if shape is None:
            continue
        try:
            solids = getattr(shape, "Solids", None)
            if solids and len(solids) > 0:
                return obj
        except Exception:
            continue
    return getattr(doc, "ActiveObject", None)


def _run_coverage_gate(
    nc_path: Path,
    job: LightJob,
    *,
    doc_path: Optional[Path | str],
    voxel_size_mm: float,
    bead_width_mm: Optional[float],
    layer_height_mm: Optional[float],
    sample_step_mm: float,
    max_voxels: int,
    threshold_pct: float,
) -> Tuple[bool, float]:
    _ = (nc_path, job, doc_path, voxel_size_mm, bead_width_mm, layer_height_mm, sample_step_mm, max_voxels, threshold_pct)
    raise RuntimeError("Coverage gating is not included in this WAAM build.")


def _hausdorff(a: Sequence[Point2D], b: Sequence[Point2D]) -> float:
    if not a or not b:
        return float("inf")

    def directed(src: Sequence[Point2D], dst: Sequence[Point2D]) -> float:
        worst = 0.0
        for pt in src:
            px, py = _point_xy(pt)
            best = min(math.hypot(px - qx, py - qy) for qx, qy in (_point_xy(d) for d in dst))
            worst = max(worst, best)
        return worst

    return max(directed(a, b), directed(b, a))


def _layer_points_from_gcode(depo_moves: List[Tuple[Point3D, Point3D]], ref_layers: List[LayerSpec]) -> Dict[int, List[Point2D]]:
    """Bucket deposition XY points by nearest reference layer."""
    if not ref_layers:
        return {}
    z_targets = [layer.z for layer in ref_layers]
    by_layer: Dict[int, List[Point2D]] = {layer.index: [] for layer in ref_layers}

    for start, end in depo_moves:
        _, _, z_val = end
        # Find nearest reference layer by Z
        nearest_idx = min(range(len(z_targets)), key=lambda i: abs(z_targets[i] - z_val))
        ref_layer = ref_layers[nearest_idx]
        by_layer.setdefault(ref_layer.index, []).extend([(start[0], start[1]), (end[0], end[1])])
    return by_layer


def _bbox(points: Sequence[Point2D]) -> Tuple[float, float, float, float]:
    xs = [_point_xy(p)[0] for p in points]
    ys = [_point_xy(p)[1] for p in points]
    return min(xs), max(xs), min(ys), max(ys)


def _bbox_area_and_span(points: Sequence[Point2D]) -> Tuple[float, Tuple[float, float]]:
    if not points:
        return 0.0, (0.0, 0.0)
    xmin, xmax, ymin, ymax = _bbox(points)
    span_x = max(0.0, xmax - xmin)
    span_y = max(0.0, ymax - ymin)
    return span_x * span_y, (span_x, span_y)


def _has_degenerate(points: Sequence[Point2D], *, tol: float = 1e-6) -> bool:
    if len(points) < 2:
        return True
    first = _point_xy(points[0])
    for p in points[1:]:
        px, py = _point_xy(p)
        if abs(first[0] - px) > tol or abs(first[1] - py) > tol:
            return False
    return True


def validate_gcode(gcode_text: str, job: LightJob, geom_tol: float = 0.8) -> ValidationReport:
    """Run syntax, safety, and geometry checks on a G-code program."""
    cleaned_lines = list(nc_parser.iter_clean_lines(gcode_text))
    if isinstance(job.post, PostCommands):
        torch_on_codes = [job.post.torch_on]
        torch_off_codes = [job.post.torch_off]
    else:
        torch_on_codes = [job.post.get("torch_on")] if isinstance(job.post.get("torch_on"), str) else None
        torch_off_codes = [job.post.get("torch_off")] if isinstance(job.post.get("torch_off"), str) else None
    resolved_on, resolved_off = nc_parser.resolve_torch_codes(
        torch_on_codes, torch_off_codes, allow_config=False
    )
    # Track feed values and WCS usage for consistency checks.
    feed_values: List[float] = []
    wcs_target = None
    meta_wcs = job.metadata.get("wcs")
    if isinstance(meta_wcs, str):
        wcs_target = meta_wcs.strip().upper()
    elif isinstance(meta_wcs, Mapping):
        wcs_target = str(meta_wcs.get("name") or "").strip().upper()
    messages: List[str] = []

    allowed_prefixes = (
        "G0",
        "G1",
        "G4",
        "G17",
        "G18",
        "G19",
        "G20",
        "G21",
        "G40",
        "G41",
        "G42",
        "G43",
        "G49",
        "G54",
        "G55",
        "G56",
        "G57",
        "G58",
        "G59",
        "G90",
        "G91",
        "G94",
        "M30",
        "M64",
        "M65",
        "M68",
        *tuple(sorted(resolved_on | resolved_off)),
    )
    syntax_ok = True
    wcs_seen = set()
    feed_pattern = re.compile(r"F([-+0-9.]+)")
    wcs_pattern = re.compile(r"G5[4-9]")
    for line in cleaned_lines:
        if not line.startswith(allowed_prefixes):
            syntax_ok = False
            messages.append(f"Unsupported or unsafe command: {line}")
        feed_match = feed_pattern.search(line) if line.startswith("G1") else None
        if feed_match:
            try:
                feed_values.append(float(feed_match.group(1)))
            except ValueError:
                pass
        if wcs_target:
            for wcs_code in wcs_pattern.findall(line):
                wcs_seen.add(wcs_code)
    points, deposition_segments = nc_parser.parse_nc_text(
        gcode_text,
        torch_on_codes=resolved_on,
        torch_off_codes=resolved_off,
        allow_config=False,
    )
    travel_moves, depo_moves = nc_parser.split_moves(points, deposition_segments)

    safety_ok = True
    torch_on = False
    for line in cleaned_lines:
        if any(line.startswith(code) for code in resolved_on):
            torch_on = True
        elif any(line.startswith(code) for code in resolved_off):
            torch_on = False
        if torch_on and line.startswith("G0"):
            safety_ok = False
            messages.append("Rapid move (G0) while torch is on.")
    if not depo_moves:
        safety_ok = False
        messages.append("No deposition moves detected (torch may never turn on).")
    if wcs_target:
        if not wcs_seen:
            safety_ok = False
            messages.append(f"WCS code {wcs_target} not found in program.")
        elif wcs_target not in wcs_seen:
            safety_ok = False
            messages.append(f"WCS mismatch: expected {wcs_target}, saw {sorted(wcs_seen)}.")
    if feed_values:
        if any(feed <= 0.0 for feed in feed_values):
            safety_ok = False
            messages.append("Non-positive feedrate detected.")
        elif max(feed_values) / max(min(feed_values), 1e-6) > 10.0:
            messages.append("Feedrates vary by more than 10x; verify travel/deposition feeds.")
    else:
        safety_ok = False
        messages.append("No feedrate (F) commands found in motion blocks.")

    geometry_ok = True
    if depo_moves and job.layers:
        actual_by_layer = _layer_points_from_gcode(depo_moves, job.layers)
        for layer in job.layers:
            ref_pts = [job.paths[pid] for pid in layer.path_ids if pid in job.paths]
            flat_ref = [pt for seq in ref_pts for pt in seq]
            actual_pts = actual_by_layer.get(layer.index, [])
            if not actual_pts:
                geometry_ok = False
                messages.append(f"Layer {layer.index} missing in G-code.")
                continue
            if _has_degenerate(actual_pts):
                geometry_ok = False
                messages.append(f"Layer {layer.index} contains only duplicate/degenerate points.")
                continue
            hd = _hausdorff(flat_ref, actual_pts)
            if hd > geom_tol:
                geometry_ok = False
                messages.append(f"Layer {layer.index} geometry off by {hd:.2f}mm (tolerance {geom_tol}mm).")
            ref_area, ref_spans = _bbox_area_and_span(flat_ref)
            act_area, act_spans = _bbox_area_and_span(actual_pts)
            if ref_area > 0.0:
                if act_area < 0.2 * ref_area or act_area > 1.8 * ref_area:
                    geometry_ok = False
                    messages.append(
                        f"Layer {layer.index} area mismatch (expected ~{ref_area:.2f}, got {act_area:.2f})."
                    )
                dx = abs(act_spans[0] - ref_spans[0])
                dy = abs(act_spans[1] - ref_spans[1])
                if dx > geom_tol or dy > geom_tol:
                    geometry_ok = False
                    messages.append(
                        f"Layer {layer.index} extents drift (dx={dx:.2f}mm, dy={dy:.2f}mm; tol={geom_tol}mm)."
                    )
    else:
        geometry_ok = False
        messages.append("No deposition geometry to compare.")

    return ValidationReport(syntax_ok=syntax_ok, safety_ok=safety_ok, geometry_ok=geometry_ok, messages=messages)


def _write_attempt(attempt_dir: Path, iteration: int, gcode_text: str, feedback: ValidationReport) -> Path:
    attempt_dir.mkdir(parents=True, exist_ok=True)
    nc_path = attempt_dir / f"attempt_{iteration}.nc"
    fb_path = attempt_dir / f"feedback_{iteration}.json"
    nc_path.write_text(gcode_text, encoding="utf-8")
    fb_path.write_text(
        json.dumps(
            {
                "iteration": iteration,
                "syntax_ok": feedback.syntax_ok,
                "safety_ok": feedback.safety_ok,
                "geometry_ok": feedback.geometry_ok,
                "messages": feedback.messages,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return nc_path


def _default_attempt_dir(job_path: Path) -> Path:
    root = _output_root_for(job_path)
    return root / "waamgen_attempts"

def generate_nc(
    input_path: Path | str,
    *,
    output_path: Path | str,
    max_iters: int = 1,
    geom_tol: float = 0.8,
    attempt_dir: Optional[Path | str] = None,
    plot: bool = False,
    prompt_template: Optional[Path | str] = None,
    apply_patch: bool = False,
    patch_max_layers: Optional[int] = None,
    coverage_doc: Optional[Path | str] = None,
    coverage_threshold: Optional[float] = None,
    coverage_voxel_size: float = 2.0,
    coverage_sample_step: float = 1.0,
    coverage_bead_width: Optional[float] = None,
    coverage_layer_height: Optional[float] = None,
    coverage_max_voxels: int = 2_000_000,
) -> Path:
    """Run the iterative generate → validate loop until PASS or max_iters reached."""
    job = load_light_job(input_path)
    attempt_dir_path = Path(attempt_dir) if attempt_dir else _default_attempt_dir(job.source_path)

    prompt_text = _render_prompt(job, Path(prompt_template) if prompt_template else None)
    _ = prompt_text  # Placeholder: prompt is emitted to LLM in future online mode.

    final_nc = Path(output_path).expanduser().resolve()
    final_nc.parent.mkdir(parents=True, exist_ok=True)

    last_nc_text = ""
    last_coverage_ok: Optional[bool] = None
    last_coverage_pct: Optional[float] = None
    for iteration in range(1, max_iters + 1):
        ir = build_ir(job)
        nc_text = ir_to_gcode(ir)
        if apply_patch:
            nc_text, _ = apply_geometry_patch(
                nc_text,
                job,
                geom_tol=geom_tol,
                max_layers=patch_max_layers,
            )
        feedback = validate_gcode(nc_text, job, geom_tol=geom_tol)
        attempt_path = _write_attempt(attempt_dir_path, iteration, nc_text, feedback)
        coverage_ok = True
        if coverage_threshold is not None:
            coverage_ok, coverage_pct = _run_coverage_gate(
                attempt_path,
                job,
                doc_path=coverage_doc,
                voxel_size_mm=coverage_voxel_size,
                bead_width_mm=coverage_bead_width,
                layer_height_mm=coverage_layer_height,
                sample_step_mm=coverage_sample_step,
                max_voxels=coverage_max_voxels,
                threshold_pct=float(coverage_threshold),
            )
            last_coverage_ok = coverage_ok
            last_coverage_pct = coverage_pct
            feedback.messages.append(
                f"Coverage {coverage_pct:.2f}% (threshold {float(coverage_threshold):.2f}%)."
            )
            if not coverage_ok:
                feedback.messages.append("Coverage below threshold.")
        if plot:
            # Plot support was part of the older analysis stack. This WAAM build keeps
            # the flag for CLI compatibility but does not ship the plotting
            # helper anymore.
            pass
        last_nc_text = nc_text
        if feedback.ok and coverage_ok:
            final_nc.write_text(nc_text, encoding="utf-8")
            return final_nc
        # Provide a hint for the (future) LLM regenerations
        feedback.messages.append("Regenerate focusing only on correcting the above errors.")

    # Last attempt becomes the deliverable even if not perfect; caller can inspect feedback.
    final_nc.write_text(last_nc_text, encoding="utf-8")
    if coverage_threshold is not None and last_coverage_ok is False:
        raise RuntimeError(
            f"Coverage gate failed: {last_coverage_pct:.2f}% < {float(coverage_threshold):.2f}%."
        )
    return final_nc


def validate_cli(gcode_path: Path | str, ref_path: Path | str, *, geom_tol: float = 0.8) -> ValidationReport:
    job = load_light_job(ref_path)
    gcode_text = Path(gcode_path).read_text(encoding="utf-8")
    return validate_gcode(gcode_text, job, geom_tol=geom_tol)


def generate_cli(args: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAAM LLM-style NC generator and validator.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    gen = sub.add_parser("generate", help="Generate NC using the iterative loop.")
    gen.add_argument("--input", required=True, help="Input structured WAAM job JSON (for example waam_slice_plan.json).")
    gen.add_argument("--out", required=True, help="Target NC output path.")
    gen.add_argument("--max-iters", type=int, default=1, help="Max regeneration iterations.")
    gen.add_argument("--geom-tol", type=float, default=0.8, help="Hausdorff tolerance per layer (mm).")
    gen.add_argument("--plot", action="store_true", help="Save 2D/3D preview plots for each attempt.")
    gen.add_argument("--prompt", help="Override prompt template path (jinja).")
    gen.add_argument("--patch", action="store_true", help="Apply a deterministic geometry patch pass.")
    gen.add_argument(
        "--patch-max-layers",
        type=int,
        default=None,
        help="Limit the number of layers patched (lowest indices first).",
    )
    gen.add_argument(
        "--coverage-doc",
        help="Optional FreeCAD document path used for torch coverage gating.",
    )
    gen.add_argument(
        "--coverage-threshold",
        type=float,
        default=None,
        help="Fail generation if coverage percentage falls below this value.",
    )
    gen.add_argument(
        "--coverage-voxel-size",
        type=float,
        default=2.0,
        help="Voxel size (mm) for coverage gating.",
    )
    gen.add_argument(
        "--coverage-sample-step",
        type=float,
        default=1.0,
        help="Sample step (mm) along the toolpath for coverage gating.",
    )
    gen.add_argument(
        "--coverage-bead-width",
        type=float,
        default=None,
        help="Override bead width (mm) used for coverage gating.",
    )
    gen.add_argument(
        "--coverage-layer-height",
        type=float,
        default=None,
        help="Override layer height (mm) used for coverage gating.",
    )
    gen.add_argument(
        "--coverage-max-voxels",
        type=int,
        default=2_000_000,
        help="Max voxel count for coverage gating.",
    )

    val = sub.add_parser("validate", help="Validate NC against a structured WAAM job JSON.")
    val.add_argument("--gcode", required=True, help="NC program to validate.")
    val.add_argument("--ref", required=True, help="Reference structured WAAM job JSON.")
    val.add_argument("--geom-tol", type=float, default=0.8, help="Hausdorff tolerance per layer (mm).")

    parsed = parser.parse_args(args)
    if parsed.cmd == "generate":
        try:
            output = generate_nc(
                parsed.input,
                output_path=parsed.out,
                max_iters=parsed.max_iters,
                geom_tol=parsed.geom_tol,
                plot=parsed.plot,
                prompt_template=parsed.prompt,
                apply_patch=parsed.patch,
                patch_max_layers=parsed.patch_max_layers,
                coverage_doc=parsed.coverage_doc,
                coverage_threshold=parsed.coverage_threshold,
                coverage_voxel_size=parsed.coverage_voxel_size,
                coverage_sample_step=parsed.coverage_sample_step,
                coverage_bead_width=parsed.coverage_bead_width,
                coverage_layer_height=parsed.coverage_layer_height,
                coverage_max_voxels=parsed.coverage_max_voxels,
            )
        except Exception as exc:
            print(f"[ERROR] Generate failed: {exc}")
            return 1
        print(f"[INFO] NC written to: {output}")
        return 0

    if parsed.cmd == "validate":
        try:
            report = validate_cli(parsed.gcode, parsed.ref, geom_tol=parsed.geom_tol)
        except Exception as exc:
            print(f"[ERROR] Validate failed: {exc}")
            return 1
        status = "PASS" if report.ok else "FAIL"
        print(f"[{status}] syntax={report.syntax_ok} safety={report.safety_ok} geometry={report.geometry_ok}")
        for msg in report.messages:
            print(f" - {msg}")
        return 0 if report.ok else 2

    return 1


def main() -> None:
    raise SystemExit(generate_cli())


if __name__ == "__main__":
    main()
