from __future__ import annotations

import json
import math
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


def _polyline_length_xy(points):
    total = 0.0
    for a, b in zip(points, points[1:]):
        if len(a) < 2 or len(b) < 2:
            continue
        total += math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
    return total


def _closed_loop(points):
    pts = [list(p) for p in (points or []) if len(p) >= 2]
    if len(pts) < 3:
        return pts
    if pts[0][:2] != pts[-1][:2]:
        pts.append(list(pts[0]))
    return pts


def _signed_area_xy(points):
    loop = _closed_loop(points)
    if len(loop) < 4:
        return 0.0
    area = 0.0
    for a, b in zip(loop, loop[1:]):
        area += float(a[0]) * float(b[1]) - float(b[0]) * float(a[1])
    return 0.5 * area


def _resample_closed_loop(points, target_step=1.0):
    loop = _closed_loop(points)
    if len(loop) < 4:
        return loop
    total_len = _polyline_length_xy(loop)
    if total_len <= 0.0:
        return loop
    sample_count = max(len(loop) - 1, int(math.ceil(total_len / max(0.25, target_step))))
    if sample_count < 3:
        return loop

    cumulative = [0.0]
    for a, b in zip(loop, loop[1:]):
        cumulative.append(cumulative[-1] + math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1])))

    sampled = []
    segment_idx = 0
    for i in range(sample_count):
        s = total_len * i / sample_count
        while segment_idx + 1 < len(cumulative) and cumulative[segment_idx + 1] < s:
            segment_idx += 1
        a = loop[segment_idx]
        b = loop[min(segment_idx + 1, len(loop) - 1)]
        seg_len = cumulative[min(segment_idx + 1, len(cumulative) - 1)] - cumulative[segment_idx]
        if seg_len <= 1e-9:
            sampled.append([float(a[0]), float(a[1]), float(a[2] if len(a) >= 3 else 0.0)])
            continue
        t = (s - cumulative[segment_idx]) / seg_len
        ax, ay = float(a[0]), float(a[1])
        bx, by = float(b[0]), float(b[1])
        az = float(a[2]) if len(a) >= 3 else 0.0
        bz = float(b[2]) if len(b) >= 3 else az
        sampled.append([
            ax + t * (bx - ax),
            ay + t * (by - ay),
            az + t * (bz - az),
        ])
    if sampled and sampled[0][:2] != sampled[-1][:2]:
        sampled.append(list(sampled[0]))
    return sampled


def _distance_point_to_segment_xy(px, py, ax, ay, bx, by):
    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    qx = ax + t * dx
    qy = ay + t * dy
    return math.hypot(px - qx, py - qy)


def _remove_near_duplicate_points(points, tol=0.05):
    cleaned = []
    for pt in points or []:
        if len(pt) < 2:
            continue
        px = float(pt[0])
        py = float(pt[1])
        pz = float(pt[2]) if len(pt) >= 3 else 0.0
        if cleaned:
            prev = cleaned[-1]
            if math.hypot(px - prev[0], py - prev[1]) <= tol:
                continue
        cleaned.append([px, py, pz])
    if len(cleaned) >= 2 and math.hypot(cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1]) <= tol:
        cleaned[-1] = list(cleaned[0])
    return cleaned


def _smooth_closed_loop(points, passes=2, weight=0.22):
    loop = _closed_loop(_remove_near_duplicate_points(points))
    if len(loop) < 6:
        return loop
    base = [list(p) for p in loop[:-1]]
    n = len(base)
    smoothed = [list(p) for p in base]
    for _ in range(max(1, passes)):
        updated = []
        for i in range(n):
            prev_pt = smoothed[(i - 1) % n]
            cur_pt = smoothed[i]
            next_pt = smoothed[(i + 1) % n]
            cx, cy = float(cur_pt[0]), float(cur_pt[1])
            cz = float(cur_pt[2]) if len(cur_pt) >= 3 else 0.0
            px, py = float(prev_pt[0]), float(prev_pt[1])
            nx, ny = float(next_pt[0]), float(next_pt[1])
            updated.append([
                cx * (1.0 - 2.0 * weight) + weight * (px + nx),
                cy * (1.0 - 2.0 * weight) + weight * (py + ny),
                cz,
            ])
        smoothed = updated
    smoothed.append(list(smoothed[0]))
    return smoothed


def _classify_inner_loops(inner_items, outer_loop):
    if not inner_items:
        return [], []
    outer_area_ref = abs(_signed_area_xy(outer_loop)) if outer_loop else 0.0
    holes = []
    islands = []
    for pid, centroid, pts in inner_items:
        area = abs(_signed_area_xy(pts))
        item = (pid, centroid, pts, area)
        if outer_area_ref and area < outer_area_ref * 0.12:
            holes.append(item)
        else:
            islands.append(item)
    holes.sort(key=lambda item: (-item[3], item[1][0], item[0]))
    if len(holes) > 2:
        islands.extend(holes[2:])
        holes = holes[:2]
    return holes, islands


def _collect_geometry(layer_path_ids, paths):
    outer_ids = []
    inner_items = []
    infill_ids = []
    outer_loop = None
    for pid in layer_path_ids:
        path = paths.get(pid)
        if not isinstance(path, dict):
            continue
        role = str(path.get("role_hint") or "").upper()
        pts = path.get("points_xyz") or []
        if role == "OUTER":
            outer_ids.append(pid)
            if outer_loop is None:
                refined = _resample_closed_loop(pts, target_step=0.45)
                outer_loop = _smooth_closed_loop(refined, passes=2, weight=0.18)
                path["points_xyz"] = outer_loop
        elif role == "INNER":
            refined = _resample_closed_loop(pts, target_step=0.45)
            resampled = _smooth_closed_loop(refined, passes=1, weight=0.12)
            path["points_xyz"] = resampled
            inner_items.append((pid, _path_centroid_xy(path), resampled))
        elif role == "INFILL":
            infill_ids.append(pid)
    hole_items, island_items = _classify_inner_loops(inner_items, outer_loop)
    hole_items.sort(key=lambda item: (item[1][0], item[0]))
    island_items.sort(key=lambda item: (item[1][1], item[1][0], item[0]))
    kept_inner_ids = [pid for pid, _centroid, _pts, _area in hole_items]
    hole_loops = [pts for _pid, _centroid, pts, _area in hole_items]
    island_loops = [pts for _pid, _centroid, pts, _area in island_items]
    return outer_ids, kept_inner_ids, infill_ids, outer_loop, hole_loops, island_loops


def _material_windows_for_x(outer_loop, hole_loops, island_loops, x_value, clearance=1.2):
    base = loop_y_segments_at_x(outer_loop, x_value)
    cuts = []
    for hole in hole_loops:
        for y0, y1 in loop_y_segments_at_x(hole, x_value):
            cuts.append((float(y0) - clearance, float(y1) + clearance))
    material = subtract_segments(base, cuts, min_len=max(0.8, clearance * 0.75))
    if island_loops:
        for island in island_loops:
            material.extend(loop_y_segments_at_x(island, x_value))
    return sorted((float(a), float(b)) for a, b in material if float(b) - float(a) >= max(0.8, clearance * 0.75))


def _estimate_x_samples(infill_ids, paths, outer_loop):
    _ = (infill_ids, paths)
    outer_xs = [float(p[0]) for p in (outer_loop or []) if len(p) >= 2]
    if not outer_xs:
        return []
    xmin, xmax = min(outer_xs), max(outer_xs)
    if xmax <= xmin:
        return [xmin]
    count = 12
    nominal_step = (xmax - xmin) / max(1, count - 1)
    inset = min(nominal_step * 0.65, max(0.0, (xmax - xmin) * 0.02))
    start_x = xmin + inset
    end_x = xmax - inset
    if end_x <= start_x:
        start_x = xmin
        end_x = xmax
    step = (end_x - start_x) / max(1, count - 1)
    return [start_x + i * step for i in range(count)]


def _build_zig_zag_paths(paths, *, next_id_num, region_id, windows_by_x):
    generated_ids = []
    hole_crossings = 0
    previous_end = None

    for col_idx, (x_value, windows) in enumerate(windows_by_x):
        if not windows:
            continue
        forward = (col_idx % 2 == 0)
        ordered_windows = windows if forward else list(reversed(windows))

        for seg_idx, (y0, y1) in enumerate(ordered_windows):
            start_y, end_y = (y0, y1) if forward else (y1, y0)
            start_pt = [float(x_value), float(start_y), 0.0]
            end_pt = [float(x_value), float(end_y), 0.0]

            if previous_end is not None:
                link_id = f"P{next_id_num}__LINK__"
                next_id_num += 1
                paths[link_id] = {
                    "id": link_id,
                    "role_hint": "LINK",
                    "frame": "wcs_frame",
                    "closed": False,
                    "points_xyz": [list(previous_end), list(start_pt)],
                    "planner_tags": {
                        "region_id": region_id,
                        "region_mode": "zig_zag_link_travel",
                        "skip_deposition": True,
                        "continuity_reversible": False,
                        "experimental_orientation": "zig_zag_link",
                    },
                }
                generated_ids.append(link_id)
                hole_crossings += 1

            dep_id = f"P{next_id_num}"
            next_id_num += 1
            paths[dep_id] = {
                "id": dep_id,
                "role_hint": "INFILL",
                "frame": "wcs_frame",
                "closed": False,
                "points_xyz": [start_pt, end_pt],
                "planner_tags": {
                    "region_id": region_id,
                    "region_mode": "zig_zag_fill",
                    "continuity_reversible": False,
                    "experimental_orientation": "layer_zig_zag",
                },
            }
            generated_ids.append(dep_id)
            previous_end = end_pt

    return generated_ids, hole_crossings


def _rewrite_layer_contour_then_fill(layer, paths):
    outer_ids, inner_ids, infill_ids, outer_loop, hole_loops, island_loops = _collect_geometry(layer.get("path_ids") or [], paths)
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

    hole_clearance = 1.6
    windows_by_x = []
    for x_value in sorted(x_samples):
        windows = _material_windows_for_x(outer_loop, hole_loops, island_loops, x_value, clearance=hole_clearance)
        if windows:
            windows_by_x.append((float(x_value), windows))

    generated_ids, hole_crossings = _build_zig_zag_paths(
        paths,
        next_id_num=next_id_num,
        region_id=region_id,
        windows_by_x=windows_by_x,
    )

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
            metadata["strategy"] = "outer contour, then all inner contours, then layer-by-layer zig-zag fill"
            metadata["fill_direction"] = "Y zig-zag by X columns"
            metadata["x_progression"] = "alternating zig-zag"
            metadata["hole_crossing_mode"] = "torch-off link travel between zig-zag segments"
            metadata["force_contours_before_fill"] = True
            metadata["contour_target_step_mm"] = 0.45
            metadata["contour_smoothing_passes"] = 2
            metadata["hole_clearance_mm"] = 1.6
            metadata["infill_column_count"] = 12
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
            heading="Contour then layer-by-layer zig-zag fill",
            lines=[
                "- scope: experimental runtime planner only",
                "- order: outer contour, inner contours, then zig-zag fill",
                "- fill direction: alternating Y-direction zig-zag by X columns",
                "- X progression: alternating path continuity",
                "- hole behavior: LINK paths for torch-off travel with extra clearance around hole spans",
                "- contour refinement: closed-loop resampling at 0.45 mm target step plus closed-loop smoothing",
                "- infill density: 12 X columns for more conservative interior deposition",
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
