from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Point2D = Tuple[float, float]
Segment2D = Tuple[Point2D, Point2D]

_POINT_TOL = 1e-6


def support_radius_from_bead(
    bead_width: float,
    *,
    margin: float = 0.0,
    min_overlap_ratio: Optional[float] = None,
    max_overhang_angle_deg: Optional[float] = None,
    layer_height: Optional[float] = None,
) -> float:
    try:
        bw = float(bead_width)
    except Exception:
        bw = 0.0
    radius = max(0.0, 0.5 * bw + float(margin))

    if min_overlap_ratio is not None:
        try:
            ratio = float(min_overlap_ratio)
        except Exception:
            ratio = None
        if ratio is not None:
            ratio = max(0.0, min(1.0, ratio))
            overlap_radius = bw * max(0.0, 1.0 - ratio)
            radius = min(radius, overlap_radius)

    if max_overhang_angle_deg is not None and layer_height is not None:
        try:
            angle_deg = float(max_overhang_angle_deg)
            lh = float(layer_height)
        except Exception:
            angle_deg = None
            lh = 0.0
        if angle_deg is not None and lh:
            allowed = abs(lh) * math.tan(math.radians(abs(angle_deg)))
            if math.isfinite(allowed):
                radius = min(radius, max(0.0, allowed))

    if radius <= 0.0 and (min_overlap_ratio is not None or max_overhang_angle_deg is not None):
        radius = 1e-6
    return max(0.0, radius)


def segments_from_polyline(points: Sequence[Point2D]) -> List[Segment2D]:
    segments: List[Segment2D] = []
    if not points or len(points) < 2:
        return segments
    for start, end in zip(points, points[1:]):
        if _points_close(start, end):
            continue
        segments.append((start, end))
    return segments


def segments_from_polylines(polylines: Iterable[Sequence[Point2D]]) -> List[Segment2D]:
    segments: List[Segment2D] = []
    for points in polylines:
        segments.extend(segments_from_polyline(points))
    return segments


@dataclass
class SupportIndex:
    grid: Dict[Tuple[int, int], List[Segment2D]]
    cell_size: float
    radius_sq: float

    def is_supported(self, point: Point2D) -> bool:
        if not self.grid:
            return False
        ix = int(math.floor(point[0] / self.cell_size))
        iy = int(math.floor(point[1] / self.cell_size))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for seg in self.grid.get((ix + dx, iy + dy), []):
                    if _point_segment_distance_sq(point, seg[0], seg[1]) <= self.radius_sq:
                        return True
        return False


def build_support_index(
    segments: Sequence[Segment2D],
    radius: float,
) -> Optional[SupportIndex]:
    if not segments or radius <= 0.0:
        return None
    # Fix: ensure cell_size is reasonably large so we don't iterate millions of times per segment
    cell_size = max(radius, 2.0)
    grid: Dict[Tuple[int, int], List[Segment2D]] = {}
    for start, end in segments:
        minx = min(start[0], end[0]) - radius
        maxx = max(start[0], end[0]) + radius
        miny = min(start[1], end[1]) - radius
        maxy = max(start[1], end[1]) + radius
        ix_min = int(math.floor(minx / cell_size))
        ix_max = int(math.floor(maxx / cell_size))
        iy_min = int(math.floor(miny / cell_size))
        iy_max = int(math.floor(maxy / cell_size))
        for ix in range(ix_min, ix_max + 1):
            for iy in range(iy_min, iy_max + 1):
                grid.setdefault((ix, iy), []).append((start, end))
    return SupportIndex(grid=grid, cell_size=cell_size, radius_sq=radius * radius)


def trim_segments_to_support(
    segments: Sequence[Segment2D],
    support_index: Optional[SupportIndex],
    radius: float,
    *,
    sample_step: Optional[float] = None,
    min_segment_length: Optional[float] = None,
) -> List[Segment2D]:
    if support_index is None or radius <= 0.0:
        return list(segments)
    trimmed: List[Segment2D] = []
    for start, end in segments:
        trimmed.extend(
            trim_segment_to_support(
                start,
                end,
                support_index,
                radius,
                sample_step=sample_step,
                min_segment_length=min_segment_length,
            )
        )
    return trimmed


def trim_polyline_to_support(
    points: Sequence[Point2D],
    support_index: Optional[SupportIndex],
    radius: float,
    *,
    sample_step: Optional[float] = None,
    min_segment_length: Optional[float] = None,
) -> List[List[Point2D]]:
    if not points or len(points) < 2:
        return []
    if support_index is None or radius <= 0.0:
        return [list(points)]
    polylines: List[List[Point2D]] = []
    current: List[Point2D] = []
    for start, end in zip(points, points[1:]):
        segments = trim_segment_to_support(
            start,
            end,
            support_index,
            radius,
            sample_step=sample_step,
            min_segment_length=min_segment_length,
        )
        if not segments:
            if len(current) >= 2:
                polylines.append(current)
            current = []
            continue
        for seg_start, seg_end in segments:
            if current:
                if _points_close(current[-1], seg_start):
                    current.append(seg_end)
                else:
                    if len(current) >= 2:
                        polylines.append(current)
                    current = [seg_start, seg_end]
            else:
                current = [seg_start, seg_end]
    if len(current) >= 2:
        polylines.append(current)
    return polylines


def trim_segment_to_support(
    start: Point2D,
    end: Point2D,
    support_index: SupportIndex,
    radius: float,
    *,
    sample_step: Optional[float] = None,
    min_segment_length: Optional[float] = None,
    refine_steps: int = 6,
) -> List[Segment2D]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return []
    if sample_step is None:
        sample_step = max(0.1, radius * 0.5)
    if min_segment_length is None:
        min_segment_length = max(0.05, radius * 0.05)
    steps = max(1, int(math.ceil(length / max(sample_step, 1e-6))))

    def point_at(t: float) -> Point2D:
        return (start[0] + dx * t, start[1] + dy * t)

    t_prev = 0.0
    supported_prev = support_index.is_supported(point_at(t_prev))
    current_start = 0.0 if supported_prev else None
    intervals: List[Tuple[float, float]] = []

    for i in range(1, steps + 1):
        t = i / steps
        supported = support_index.is_supported(point_at(t))
        if supported != supported_prev:
            lo, hi = _refine_transition(
                t_prev, t, supported_prev, support_index, point_at, refine_steps
            )
            if supported_prev:
                if current_start is not None and lo > current_start:
                    intervals.append((current_start, lo))
                current_start = None
            else:
                current_start = hi
        t_prev = t
        supported_prev = supported

    if supported_prev and current_start is not None and 1.0 > current_start:
        intervals.append((current_start, 1.0))

    trimmed: List[Segment2D] = []
    for t0, t1 in intervals:
        if t1 <= t0:
            continue
        seg_start = point_at(t0)
        seg_end = point_at(t1)
        if _segment_length(seg_start, seg_end) >= min_segment_length:
            trimmed.append((seg_start, seg_end))
    return trimmed


def _refine_transition(
    t0: float,
    t1: float,
    supported0: bool,
    support_index: SupportIndex,
    point_at,
    steps: int,
) -> Tuple[float, float]:
    lo, hi = t0, t1
    for _ in range(steps):
        mid = 0.5 * (lo + hi)
        if support_index.is_supported(point_at(mid)) == supported0:
            lo = mid
        else:
            hi = mid
    return lo, hi


def _segment_length(start: Point2D, end: Point2D) -> float:
    return math.hypot(end[0] - start[0], end[1] - start[1])


def _points_close(a: Point2D, b: Point2D, tol: float = _POINT_TOL) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _point_segment_distance_sq(point: Point2D, start: Point2D, end: Point2D) -> float:
    px, py = point
    ax, ay = start
    bx, by = end
    vx = bx - ax
    vy = by - ay
    wx = px - ax
    wy = py - ay
    c1 = vx * wx + vy * wy
    if c1 <= 0.0:
        return wx * wx + wy * wy
    c2 = vx * vx + vy * vy
    if c2 <= c1:
        dx = px - bx
        dy = py - by
        return dx * dx + dy * dy
    t = c1 / c2 if c2 else 0.0
    projx = ax + t * vx
    projy = ay + t * vy
    dx = px - projx
    dy = py - projy
    return dx * dx + dy * dy


__all__ = [
    "Point2D",
    "Segment2D",
    "SupportIndex",
    "build_support_index",
    "segments_from_polyline",
    "segments_from_polylines",
    "support_radius_from_bead",
    "trim_polyline_to_support",
    "trim_segment_to_support",
    "trim_segments_to_support",
]
