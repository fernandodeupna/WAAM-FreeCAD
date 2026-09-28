from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from Waam_tech.waamgen import generate_nc as waamgen_generate
from Waam_tech.waamgen import pipeline as waam_pipeline

ACTIVE_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ACTIVE_ROOT / "manifest.json"
NOTES_PATH = ACTIVE_ROOT / "notes.md"

Segment1D = Tuple[float, float]


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_manifest():
    if not MANIFEST_PATH.is_file():
        return {}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_manifest(payload) -> None:
    MANIFEST_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def experimental_output_path(context) -> Path:
    target = context.output_dir / "nc_files" / "waam_baseline_experimental.nc"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def append_iteration_note(context, *, heading: str, lines) -> None:
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not NOTES_PATH.exists():
        NOTES_PATH.write_text("# Active Experiment Notes\n\n", encoding="utf-8")
    with NOTES_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"## {heading} ({_timestamp()})\n")
        handle.write("- EXECUTION_MARKER: experimental_runtime_active_helpers_v2\n")
        for line in lines:
            handle.write(f"{line}\n")
        handle.write("\n")


def _rewrite_link_paths_to_travel(nc_text: str) -> str:
    lines = nc_text.splitlines()
    out: List[str] = []
    in_link = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('(Path '):
            in_link = 'LINK' in stripped or '__LINK__' in stripped
        if in_link and stripped.startswith('M62 '):
            out.append('M63 P0')
            continue
        out.append(line)
    return '\n'.join(out) + ('\n' if nc_text.endswith('\n') else '')


def _strict_order_export_nc(context) -> Path:
    target = experimental_output_path(context)
    plan_data = json.loads(Path(context.plan_path).read_text(encoding='utf-8'))
    structured = ((plan_data.get('llm_context') or {}).get('waam_structured') or {})
    structured_layers = structured.get('layers') or []
    structured_paths = structured.get('paths') or {}

    job = waam_pipeline.load_light_job(context.plan_path)
    ir = waam_pipeline.build_ir(job)
    meta = ir.get('meta') or {}
    process = ir.get('process') or {}
    post_obj = ir.get('post') or {}
    post_meta = ir.get('post_meta') or {}

    path_points = {}
    for pid, pts in (ir.get('paths') or {}).items():
        path_points[str(pid)] = pts
    for pid, pdata in structured_paths.items():
        if str(pid) in path_points:
            continue
        pts = pdata.get('points_xyz') or pdata.get('points') or []
        coords = []
        for entry in pts:
            try:
                x = float(entry[0])
                y = float(entry[1])
                z = float(entry[2]) if len(entry) >= 3 else 0.0
                coords.append((x, y, z))
            except Exception:
                continue
        if coords:
            path_points[str(pid)] = coords

    layers = []
    for item in structured_layers:
        if not isinstance(item, dict):
            continue
        layers.append({
            'index': int(item.get('i') or item.get('index') or 0),
            'z': float(item.get('z') or item.get('z_mm') or 0.0),
            'path_ids': [str(pid) for pid in (item.get('path_ids') or [])],
        })

    post_cfg = post_obj if hasattr(post_obj, 'torch_on') else waam_pipeline.validate_post_config(post_obj)
    torch = waam_pipeline._torch_sequence(post_cfg, post_meta, float(process.get('wire_feed_mm_min', 3200.0)))
    travel_speed = float(process.get('travel_speed_mm_min', 300.0))
    dwell_layer = float(process.get('dwell_per_layer_s', 1.5))
    clearance, rapid_z = waam_pipeline._resolve_clearance(job)
    wcs_name = (meta.get('wcs') or {}).get('name', 'G54') if isinstance(meta.get('wcs'), dict) else 'G54'
    program_name = meta.get('program') or meta.get('program_name') or 'WAAM_BASELINE_EXPERIMENTAL.NC'

    lines = [
        '%',
        f'(PROGRAM: {program_name})',
        '(EXECUTION_MARKER: experimental_runtime_active_helpers_v2)',
        '(OPTIMIZATIONS: EXPERIMENTAL STRICT PATH ORDER)',
        f"(WIRE FEED SPEED: {float(process.get('wire_feed_mm_min', 3200.0)) / 1000.0:.1f} m/min | TRAVEL SPEED: {travel_speed:.0f} mm/min)",
        '',
        'G21 (Units in mm)',
        'G90 (Absolute Positioning)',
        'G17 (XY Plane)',
        wcs_name,
        'G94 (Feed per minute)',
        'G40 (Cancel cutter comp)',
        'G49 (Cancel tool length)',
        f'G0 Z{clearance:.3f}',
        '',
        '( --- START SEQUENCE --- )',
        '',
    ]

    for layer in layers:
        z_val = float(layer.get('z', 0.0))
        layer_idx = layer.get('index')
        lines.append(f'(--- LAYER {layer_idx} Z={z_val:.3f} ---)')
        for path_id in layer.get('path_ids') or []:
            pts = path_points.get(path_id) or []
            if len(pts) < 2:
                continue
            lines.append(f'(Path {path_id})')
            is_link = '__LINK__' in str(path_id) or str(path_id).endswith('LINK')
            if is_link:
                start_xy = waam_pipeline._point_xy(pts[0])
                start_z = float(pts[0][2]) if waam_pipeline._point_has_z(pts[0]) else z_val
                lines.append(f'G0 X{start_xy[0]:.3f} Y{start_xy[1]:.3f} Z{clearance:.3f}')
                lines.append(str(torch['gas_on']))
                lines.append(f"G4 P{float(torch['gas_preflow']):.1f}")
                lines.append(str(torch['wire_feed']))
                lines.append(f'G1 Z{start_z:.3f} F{max(50.0, travel_speed * 0.35):.1f}')
                lines.append(str(torch['torch_off']))
                lines.append(f"G4 P{float(torch['arc_stop_delay']):.1f}")
                first_move_feed = waam_pipeline._travel_command(torch, travel_speed)[1]
                waam_pipeline._emit_path_body(lines, pts, z_val, first_move_feed=first_move_feed)
                end_xy = waam_pipeline._point_xy(pts[-1])
                lines.append(str(torch['gas_off']))
                lines.append(f"G4 P{float(torch['gas_postflow']):.1f}")
                lines.append(f'G0 X{end_xy[0]:.3f} Y{end_xy[1]:.3f} Z{rapid_z:.3f}')
                lines.append('')
                continue

            first_move_feed = waam_pipeline._emit_deposition_start(
                lines,
                pts[0],
                z_val=z_val,
                clearance=clearance,
                travel_speed=travel_speed,
                torch=torch,
            )
            waam_pipeline._emit_path_body(lines, pts, z_val, first_move_feed=first_move_feed)
            waam_pipeline._emit_deposition_end(
                lines,
                pts[-1],
                rapid_z=rapid_z,
                dwell_layer=dwell_layer,
                torch=torch,
            )
            lines.append('')

    lines.extend([str(torch['torch_off']), str(torch['gas_off']), f'G0 Z{clearance:.3f}', 'G0 X0.0 Y0.0', 'M30', '%'])
    text = '\n'.join(lines) + '\n'
    text = _rewrite_link_paths_to_travel(text)
    target.write_text(text, encoding='utf-8')
    return target


def default_export_nc(context, *, max_iters: int = 1, geom_tol: float = 0.8) -> Path:
    _ = (max_iters, geom_tol)
    return _strict_order_export_nc(context)


def loop_y_segments_at_x(loop: Sequence[Sequence[float]], x_value: float) -> List[Segment1D]:
    pts = [(float(p[0]), float(p[1])) for p in loop if len(p) >= 2]
    if len(pts) < 3:
        return []
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    hits: List[float] = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if abs(x2 - x1) < 1e-9:
            continue
        if (x1 <= x_value < x2) or (x2 <= x_value < x1):
            t = (x_value - x1) / (x2 - x1)
            hits.append(y1 + t * (y2 - y1))
    hits.sort()
    return [(hits[i], hits[i + 1]) for i in range(0, len(hits) - 1, 2)]


def subtract_segments(base_segments: Iterable[Segment1D], cut_segments: Iterable[Segment1D], *, min_len: float = 0.25) -> List[Segment1D]:
    pieces = [(float(a), float(b)) for a, b in base_segments if float(b) - float(a) > min_len]
    for cut_a, cut_b in cut_segments:
        next_pieces: List[Segment1D] = []
        for seg_a, seg_b in pieces:
            lo = max(seg_a, float(cut_a))
            hi = min(seg_b, float(cut_b))
            if hi <= lo:
                next_pieces.append((seg_a, seg_b))
                continue
            if seg_a < lo and lo - seg_a > min_len:
                next_pieces.append((seg_a, lo))
            if hi < seg_b and seg_b - hi > min_len:
                next_pieces.append((hi, seg_b))
        pieces = next_pieces
    return pieces
