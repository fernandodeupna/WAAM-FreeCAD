from __future__ import annotations

import json
from pathlib import Path

from .helpers import append_iteration_note, default_export_nc, loop_y_segments_at_x, subtract_segments


X_COLUMN_COUNT = 6
MIN_SEGMENT_LEN = 0.8


def _path_centroid_xy(path):
    pts = path.get("points_xyz") or []
    if not pts:
        return (0.0, 0.0)
    xs = [float(pt[0]) for pt in pts if len(pt) >= 2]
    ys = [float(pt[1]) for pt in pts if len(pt) >= 2]
    if not xs or not ys:
        return (0.0, 0.0)
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _signed_area_xy(points):
    pts = [(float(p[0]), float(p[1])) for p in (points or []) if len(p) >= 2]
    if len(pts) < 3:
        return 0.0
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    area = 0.0
    for a, b in zip(pts, pts[1:]):
        area += a[0] * b[1] - b[0] * a[1]
    return 0.5 * area


def _collect_geometry(layer_path_ids, paths):
    outer_loop = None
    inner_items = []
    infill_ids = []
    for pid in layer_path_ids:
        path = paths.get(pid)
        if not isinstance(path, dict):
            continue
        role = str(path.get("role_hint") or "").upper()
        pts = path.get("points_xyz") or []
        if role == "OUTER" and outer_loop is None:
            outer_loop = pts
        elif role == "INNER":
            inner_items.append((pid, _path_centroid_xy(path), pts, abs(_signed_area_xy(pts))))
        elif role == "INFILL":
            infill_ids.append(pid)
    inner_items.sort(key=lambda item: (-item[3], item[1][0], item[0]))
    hole_loops = [pts for _pid, _centroid, pts, _area in inner_items[:2]]
    return outer_loop, hole_loops, infill_ids


def _estimate_x_samples(outer_loop):
    outer_xs = [float(p[0]) for p in (outer_loop or []) if len(p) >= 2]
    if not outer_xs:
        return []
    xmin, xmax = min(outer_xs), max(outer_xs)
    if xmax <= xmin:
        return [xmin]
    step = (xmax - xmin) / max(1, X_COLUMN_COUNT - 1)
    inset = min(step * 0.5, max(0.0, (xmax - xmin) * 0.02))
    start_x = xmin + inset
    end_x = xmax - inset
    if end_x <= start_x:
        start_x = xmin
        end_x = xmax
    if X_COLUMN_COUNT == 1:
        return [0.5 * (start_x + end_x)]
    real_step = (end_x - start_x) / max(1, X_COLUMN_COUNT - 1)
    return [start_x + i * real_step for i in range(X_COLUMN_COUNT)]


def _material_windows_for_x(outer_loop, hole_loops, x_value):
    base = loop_y_segments_at_x(outer_loop, x_value)
    cuts = []
    for hole in hole_loops:
        cuts.extend(loop_y_segments_at_x(hole, x_value))
    return subtract_segments(base, cuts, min_len=MIN_SEGMENT_LEN)


def _rewrite_layer_as_staircase_meander(layer, paths):
    outer_loop, hole_loops, infill_ids = _collect_geometry(layer.get("path_ids") or [], paths)
    if not outer_loop:
        layer["path_ids"] = []
        return 0, 0

    x_samples = _estimate_x_samples(outer_loop)
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

    sampled_windows = []
    for x_value in sorted(x_samples):
        windows = _material_windows_for_x(outer_loop, hole_loops, x_value)
        if not windows:
            continue
        sampled_windows.append((float(x_value), windows))

    if not sampled_windows:
        layer["path_ids"] = []
        return 0, 0

    generated_ids = []
    link_count = 0
    current_end = None

    for col_idx, (x_value, windows) in enumerate(sampled_windows):
        forward = (col_idx % 2 == 0)
        y_candidates = [y for seg in windows for y in seg]
        if not y_candidates:
            continue
        start_y = min(y_candidates) if forward else max(y_candidates)
        end_y = max(y_candidates) if forward else min(y_candidates)
        dep_points = [[x_value, start_y, 0.0]]

        ordered = windows if forward else list(reversed(windows))
        cursor_y = start_y
        for idx, (y0, y1) in enumerate(ordered):
            seg_start = y0 if forward else y1
            seg_end = y1 if forward else y0
            if abs(cursor_y - seg_start) > 1e-6:
                dep_points.append([x_value, seg_start, 0.0])
            dep_points.append([x_value, seg_end, 0.0])
            cursor_y = seg_end
            if idx < len(ordered) - 1:
                next_y0, next_y1 = ordered[idx + 1]
                next_seg_start = next_y0 if forward else next_y1
                dep_points.append([x_value, next_seg_start, 0.0])
                cursor_y = next_seg_start

        if current_end is not None:
            prev_x, prev_y, prev_z = [float(v) for v in current_end]
            next_x, next_y, next_z = dep_points[0]
            lift_id = f"P{next_id_num}__LINK_LIFT__"
            next_id_num += 1
            paths[lift_id] = {
                "id": lift_id,
                "role_hint": "LINK",
                "frame": "wcs_frame",
                "closed": False,
                "points_xyz": [[prev_x, prev_y, prev_z], [prev_x, prev_y, prev_z + 20.0]],
                "planner_tags": {
                    "region_id": region_id,
                    "region_mode": "staircase_link_lift",
                    "skip_deposition": True,
                    "continuity_reversible": False,
                    "experimental_orientation": "staircase_meander_lift",
                },
            }
            generated_ids.append(lift_id)

            shift_id = f"P{next_id_num}__LINK_SHIFT__"
            next_id_num += 1
            paths[shift_id] = {
                "id": shift_id,
                "role_hint": "LINK",
                "frame": "wcs_frame",
                "closed": False,
                "points_xyz": [[prev_x, prev_y, prev_z + 20.0], [next_x, next_y, next_z + 20.0]],
                "planner_tags": {
                    "region_id": region_id,
                    "region_mode": "staircase_link_shift",
                    "skip_deposition": True,
                    "continuity_reversible": False,
                    "experimental_orientation": "staircase_meander_shift",
                },
            }
            generated_ids.append(shift_id)

            drop_id = f"P{next_id_num}__LINK_DROP__"
            next_id_num += 1
            paths[drop_id] = {
                "id": drop_id,
                "role_hint": "LINK",
                "frame": "wcs_frame",
                "closed": False,
                "points_xyz": [[next_x, next_y, next_z + 20.0], [next_x, next_y, next_z]],
                "planner_tags": {
                    "region_id": region_id,
                    "region_mode": "staircase_link_drop",
                    "skip_deposition": True,
                    "continuity_reversible": False,
                    "experimental_orientation": "staircase_meander_drop",
                },
            }
            generated_ids.append(drop_id)
            link_count += 3

        dep_id = f"P{next_id_num}"
        next_id_num += 1
        paths[dep_id] = {
            "id": dep_id,
            "role_hint": "INFILL",
            "frame": "wcs_frame",
            "closed": False,
            "points_xyz": dep_points,
            "planner_tags": {
                "region_id": region_id,
                "region_mode": "staircase_meander_fill",
                "continuity_reversible": False,
                "experimental_orientation": "staircase_meander_column",
            },
        }
        generated_ids.append(dep_id)
        current_end = dep_points[-1]

    layer["path_ids"] = generated_ids
    return len(generated_ids), link_count


class RuntimeActivePlanner:
    key = "runtime_active"
    label = "Active runtime experimental NC"
    output_filename = "waam_baseline_experimental.nc"

    def export_nc(self, context):
        plan_path = Path(context.plan_path)
        data = json.loads(plan_path.read_text(encoding="utf-8"))

        llm_context = data.setdefault("llm_context", {})
        structured = llm_context.get("waam_structured")
        total_generated = 0
        total_links = 0

        if isinstance(structured, dict):
            paths = structured.get("paths") or {}
            for layer in structured.get("layers") or []:
                if not isinstance(layer, dict):
                    continue
                generated, links = _rewrite_layer_as_staircase_meander(layer, paths)
                total_generated += generated
                total_links += links
            metadata = structured.setdefault("experimental_overrides", {})
            metadata["strategy"] = "staircase meander fill only"
            metadata["fill_direction"] = "alternating Y-direction staircase/meander by X columns"
            metadata["force_contours_before_fill"] = False
            metadata["emit_outer_contour"] = False
            metadata["emit_inner_contours"] = False
            metadata["fill_continuity"] = "continuous staircase meander, no contour-first path"
            metadata["hole_handling"] = "subtract two largest inner loops from fill"
            metadata["x_column_count"] = X_COLUMN_COUNT

        temp_plan = plan_path.with_name(plan_path.stem + "_experimental_continuous_zig_zag.json")
        temp_plan.write_text(json.dumps(data, indent=2), encoding="utf-8")

        append_iteration_note(
            context,
            heading="Staircase meander fill only",
            lines=[
                "- scope: experimental runtime planner only",
                "- order: no contour, no inner contour, fill only",
                "- fill style: staircase/meander path like the reference sketch",
                "- hole handling: subtract the two largest inner loops from fill windows",
                f"- x columns: {X_COLUMN_COUNT}",
                f"- generated paths: {total_generated}",
                f"- torch-off links: {total_links}",
                f"- temporary plan: {temp_plan}",
            ],
        )

        original_plan = context.plan_path
        context.plan_path = temp_plan
        try:
            return default_export_nc(context)
        finally:
            context.plan_path = original_plan
