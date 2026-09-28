"""Line clipping against polygons for WAAM toolpaths."""

from __future__ import annotations

from typing import List, Tuple, Iterable

Point = Tuple[float, float]
Segment = Tuple[Point, Point]


def point_in_polygon(x: float, y: float, polygon: List[Point]) -> bool:
    """Ray-casting algorithm to determine if a point is inside a polygon."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    p1x, p1y = polygon[0]
    for i in range(1, n + 1):
        p2x, p2y = polygon[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside


def _intersect_segments(p1: Point, p2: Point, p3: Point, p4: Point) -> float | None:
    """
    Find intersection between segment p1-p2 and p3-p4.
    Returns the interpolation parameter t (0.0 to 1.0) along p1-p2, or None if no intersection.
    """
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4

    den = (y4 - y3) * (x2 - x1) - (x4 - x3) * (y2 - y1)
    if abs(den) < 1e-9:
        return None

    ua = ((x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3)) / den
    ub = ((x2 - x1) * (y1 - y3) - (y2 - y1) * (x1 - x3)) / den

    if 0.0 - 1e-6 <= ua <= 1.0 + 1e-6 and 0.0 - 1e-6 <= ub <= 1.0 + 1e-6:
        # Clamp to valid range to avoid floating point drift extending segments
        return max(0.0, min(1.0, ua))
    return None


def _get_intersections_with_poly(seg: Segment, poly: List[Point]) -> List[float]:
    t_vals = []
    n = len(poly)
    for i in range(n):
        p1 = poly[i]
        p2 = poly[(i + 1) % n]
        t = _intersect_segments(seg[0], seg[1], p1, p2)
        if t is not None:
            t_vals.append(t)
    return t_vals


def clip_segment_to_polygon(
    seg: Segment,
    outer_poly: List[Point] | None,
    hole_polys: List[List[Point]]
) -> List[Segment]:
    """
    Clip a single segment against an outer bounding polygon and any hole polygons.
    Returns the segments that lie INSIDE the outer poly and OUTSIDE all holes.
    """
    p1, p2 = seg
    
    # Fast path: point-like segment
    dist_sq = (p2[0] - p1[0])**2 + (p2[1] - p1[1])**2
    if dist_sq < 1e-9:
        return [seg]

    # Collect all intersection 't' parameters
    t_set = {0.0, 1.0}
    if outer_poly:
        for t in _get_intersections_with_poly(seg, outer_poly):
            t_set.add(t)
    for hole in hole_polys:
        for t in _get_intersections_with_poly(seg, hole):
            t_set.add(t)

    t_list = sorted(list(t_set))
    valid_segments = []

    for i in range(len(t_list) - 1):
        t_start = t_list[i]
        t_end = t_list[i + 1]
        
        # Skip zero-length sub-segments
        if t_end - t_start < 1e-6:
            continue
            
        t_mid = (t_start + t_end) / 2.0
        mid_x = p1[0] + t_mid * (p2[0] - p1[0])
        mid_y = p1[1] + t_mid * (p2[1] - p1[1])
        
        # Check if midpoint is valid
        is_valid = True
        
        # Must be inside outer polygon
        if outer_poly and not point_in_polygon(mid_x, mid_y, outer_poly):
            is_valid = False
            
        # Must be outside all holes
        if is_valid and hole_polys:
            for hole in hole_polys:
                if point_in_polygon(mid_x, mid_y, hole):
                    is_valid = False
                    break
                    
        if is_valid:
            sx = p1[0] + t_start * (p2[0] - p1[0])
            sy = p1[1] + t_start * (p2[1] - p1[1])
            ex = p1[0] + t_end * (p2[0] - p1[0])
            ey = p1[1] + t_end * (p2[1] - p1[1])
            valid_segments.append(((sx, sy), (ex, ey)))

    return valid_segments


def clip_paths_to_contours(
    paths: List[Segment],
    outer_contour: List[Point] | None,
    inner_contours: List[List[Point]]
) -> List[Segment]:
    """
    Given a list of path segments (e.g. raster hatch lines), clip them against the contour boundary.
    Only portions inside the outer contour and outside inner contours (holes) are retained.
    """
    if not paths:
        return []
        
    if not outer_contour and not inner_contours:
        return paths
        
    clipped_paths = []
    for seg in paths:
        clipped = clip_segment_to_polygon(seg, outer_contour, inner_contours)
        clipped_paths.extend(clipped)
        
    return clipped_paths
