from __future__ import annotations

import json
from pathlib import Path

from .helpers import append_iteration_note, default_export_nc, loop_y_segments_at_x, subtract_segments


def _path_centroid_xy(path):
    pts = path.get("points_xyz") or []
    if not pts:
        return (0.0, 0.0)
    xs = [float(pt[0]) for pt in pts if len(pt) >= 2]
    ys = [float(pt[1]) for pt in pts if len(pt) >= 2]
    if not xs or not ys:
        return (0.0, 0.0)
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _collect_geometry(layer_path_ids, paths):
    outer_ids = []
    inner_items = []
    infill_ids = []
    outer_loop = None
    hole_loops = []
    for pid in layer_path_ids:
        path = paths.get(pid)
        if not isinstance(path, dict):
            continue
        role = str(path.get("role_hint") or "").upper()
        pts = path.get("points_xyz") or []
        if role == "OUTER":
            outer_ids.append(pid)
            if outer_loop is None:
                outer_loop = pts
        elif role == "INNER":
            inner_items.append((pid, _path_centroid_xy(path), pts))
        elif role == "INFILL":
            infill_ids.append(pid)
    inner_items.sort(key=lambda item: (item[1][1], item[1][0], item[0]))
    inner_ids = [pid for pid, _centroid, _pts in inner_items]
    hole_loops = [pts for _pid, _centroid, pts in inner_items]
    return outer_ids, inner_ids, infill_ids, outer_loop, hole_loops


def _material_windows_for_x(outer_loop, hole_loops, x_value):
    base = loop_y_segments_at_x(outer_loop, x_value)
    cuts = []
    for hole in hole_loops:
        cuts.extend(loop_y_segments_at_x(hole, x_value))
    return subtract_segments(base, cuts, min_len=0.6)


def _estimate_x_samples(infill_ids, paths, outer_loop):
    _ = (infill_ids, paths)
    outer_xs = [float(p[0]) for p in (outer_loop or []) if len(p) >= 2]
    if not outer_xs:
        return []

    xmin, xmax = min(outer_xs), max(outer_xs)
    if xmax <= xmin:
        return [xmin]

    count = 8
    nominal_step = (xmax - xmin) / max(1, count - 1)
    inset = min(nominal_step * 0.5, max(0.0, (xmax - xmin) * 0.02))
    start_x = xmin + inset
    end_x = xmax - inset
    if end_x <= start_x:
        start_x = xmin
        end_x = xmax
    if count == 1:
        return [0.5 * (start_x + end_x)]
    step = (end_x - start_x) / max(1, count - 1)
    return [start_x + i * step for i in range(count)]


def _rewrite_layer_contour_then_fill(layer, paths):
    outer_ids, inner_ids, infill_ids, outer_loop, hole_loops = _collect_geometry(layer.get("path_ids") or [], paths)
    if not outer_loop:
        layer["path_ids"] = outer_ids + inner_ids
        return len(outer_ids), len(inner_ids), 0, 0

    x_samples = _estimate_x_samples(infill_ids, paths, outer_loop)

    next_id_num = 0
    for key in paths.keys():
        if isinstance(key, str) and key.startswith("P"):
            try:
                next_id_num = max(next_id_num, int(key.split("__", 1)[0][1:]) + 1)
            except Exception:
                pass

    region_id = None
    for pid in infill_ids:
        tags = paths.get(pid, {}).get("planner_tags") or {}
        region_id = region_id or tags.get("region_id")

    generated_ids = []
    hole_crossings = 0

    for col_idx, x_value in enumerate(sorted(x_samples)):
        windows = _material_windows_for_x(outer_loop, hole_loops, x_value)
        if not windows:
            continue
        forward = (col_idx % 2 == 0)
        ordered_windows = windows if forward else list(reversed(windows))

        for seg_idx, (y0, y1) in enumerate(ordered_windows):
            start_y, end_y = (y0, y1) if forward else (y1, y0)
            dep_id = f"P{next_id_num}"
            next_id_num += 1
            paths[dep_id] = {
                "id": dep_id,
                "role_hint": "INFILL",
                "frame": "wcs_frame",
                "closed": False,
                "points_xyz": [[float(x_value), float(start_y), 0.0], [float(x_value), float(end_y), 0.0]],
                "planner_tags": {
                    "region_id": region_id,
                    "region_mode": "contour_plus_hatch",
                    "continuity_reversible": False,
                    "experimental_orientation": "y_direction_fill",
                },
            }
            generated_ids.append(dep_id)

            if seg_idx < len(ordered_windows) - 1:
                next_seg = ordered_windows[seg_idx + 1]
                next_start_y = next_seg[0] if forward else next_seg[1]
                link_id = f"P{next_id_num}__LINK__"
                next_id_num += 1
                paths[link_id] = {
                    "id": link_id,
                    "role_hint": "LINK",
                    "frame": "wcs_frame",
                    "closed": False,
                    "points_xyz": [[float(x_value), float(end_y), 0.0], [float(x_value), float(next_start_y), 0.0]],
                    "planner_tags": {
                        "region_id": region_id,
                        "region_mode": "hole_crossing_travel",
                        "skip_deposition": True,
                        "continuity_reversible": False,
                        "experimental_orientation": "y_direction_hole_crossing",
                    },
                }
                generated_ids.append(link_id)
                hole_crossings += 1

    layer["path_ids"] = outer_ids + inner_ids + generated_ids
    return len(outer_ids), len(inner_ids), len(generated_ids), hole_crossings


class RuntimeActivePlanner:
    key = "runtime_active"
    label = "Active runtime experimental NC"
    output_filename = "waam_baseline_experimental.nc"

    def export_nc(self, context):
        plan_path = Path(context.plan_path)
        data = json.loads(plan_path.read_text(encoding="utf-8"))

        llm_context = data.setdefault("llm_context", {})
        structured = llm_context.get("waam_structured")
        total_outer = 0
        total_inner = 0
        total_fill = 0
        total_crossings = 0

        if isinstance(structured, dict):
            paths = structured.get("paths") or {}
            for layer in structured.get("layers") or []:
                if not isinstance(layer, dict):
                    continue
                count_outer, count_inner, count_fill, crossings = _rewrite_layer_contour_then_fill(layer, paths)
                total_outer += count_outer
                total_inner += count_inner
                total_fill += count_fill
                total_crossings += crossings
            metadata = structured.setdefault("experimental_overrides", {})
            metadata["strategy"] = "outer contour, then all inner contours, then Y-direction fill from low X to high X"
            metadata["fill_direction"] = "Y"
            metadata["x_progression"] = "low_to_high"
            metadata["hole_crossing_mode"] = "same-line torch-off travel"
            metadata["force_contours_before_fill"] = True
            metadata["process_override"] = {
                "path": {
                    "topology_sensitive": {
                        "enabled": False,
                        "continuity": {
                            "emit_chains": False
                        }
                    }
                }
            }

        temp_plan = plan_path.with_name(plan_path.stem + "_experimental_contour_then_y_fill.json")
        temp_plan.write_text(json.dumps(data, indent=2), encoding="utf-8")

        append_iteration_note(
            context,
            heading="Contour then Y-direction fill",
            lines=[
                "- scope: experimental runtime planner only",
                "- order: outer contour, inner contours, then fill",
                "- fill direction: Y direction",
                "- X progression: low X to high X",
                "- hole behavior: LINK paths for same-line torch-off travel",
                f"- outer contours kept: {total_outer}",
                f"- inner contours kept: {total_inner}",
                f"- fill/link paths generated: {total_fill}",
                f"- hole crossings inserted: {total_crossings}",
                f"- temporary plan: {temp_plan}",
            ],
        )

        original_plan = context.plan_path
        context.plan_path = temp_plan
        try:
            return default_export_nc(context)
        finally:
            context.plan_path = original_plan
