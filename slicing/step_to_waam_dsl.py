"""STEP (.stp/.step) -> WAAM-layer DSL converter using FreeCAD's Part module (no external OCC wheels)."""

from __future__ import annotations

DSL_VERSION = "step-waam/v1"

import argparse
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Any

from ..core.paths import get_output_dir
from .format_waam_dsl import write_llm_waam_dsl

try:  # Use built-in FreeCAD geometry kernel
    import FreeCAD as App  # type: ignore
    import Part  # type: ignore

    _FC_AVAILABLE = True
    _BACKEND_LABEL = "FreeCAD Part"
except Exception:  # pragma: no cover - FreeCAD may be absent in headless runs
    App = None  # type: ignore
    Part = None  # type: ignore
    _FC_AVAILABLE = False
    _BACKEND_LABEL = "missing"


def _require_fc() -> None:
    if not _FC_AVAILABLE:
        raise ImportError(
            "FreeCAD Part module is required for STEP->WAAM DSL conversion. "
            "Run inside FreeCAD's Python or install FreeCAD modules."
        )


# =============================================================================
# Data structures
# =============================================================================

Point2D = Tuple[float, float]


@dataclass
class Contour:
    points: List[Point2D] = field(default_factory=list)
    is_outer: bool = False

    @property
    def area(self) -> float:
        return polygon_area(self.points)


@dataclass
class LayerSlice:
    index: int
    z: float
    contours: List[Contour] = field(default_factory=list)


# =============================================================================
# STEP loading & bounding box (FreeCAD Part)
# =============================================================================

def load_step_shape(step_path: str):
    """Load a STEP file into a FreeCAD Part shape."""
    _require_fc()
    shape = Part.Shape()
    shape.read(step_path)
    return shape


def compute_bounding_box(
    shape,
) -> Tuple[float, float, float, float, float, float]:
    """Return (xmin, ymin, zmin, xmax, ymax, zmax)."""
    _require_fc()
    bbox = shape.BoundBox
    return bbox.XMin, bbox.YMin, bbox.ZMin, bbox.XMax, bbox.YMax, bbox.ZMax


def _copy_shape(shape):
    copier = getattr(shape, "copy", None)
    if callable(copier):
        try:
            return copier()
        except Exception:
            pass
    return shape


def transform_shape_to_local_frame(shape, placement):
    """
    Return a copy of ``shape`` transformed by the inverse of ``placement`` so
    slicing can be done in the placement's local XY plane.
    """
    _require_fc()
    if placement is None:
        return shape

    try:
        inverse = placement.inverse()
    except Exception as exc:
        raise RuntimeError(f"Invalid WCS placement: {exc}") from exc

    matrix = None
    to_matrix = getattr(inverse, "toMatrix", None)
    if callable(to_matrix):
        try:
            matrix = to_matrix()
        except Exception:
            matrix = None
    if matrix is None:
        matrix = getattr(inverse, "Matrix", None)
    if matrix is None:
        raise RuntimeError("WCS placement could not be converted to a transform matrix.")

    local_shape = _copy_shape(shape)

    transform_shape = getattr(local_shape, "transformShape", None)
    if callable(transform_shape):
        for args in ((matrix,), (matrix, False), (matrix, False, True)):
            try:
                result = transform_shape(*args)
                return result if result is not None else local_shape
            except TypeError:
                continue
            except Exception:
                break

    transform_geometry = getattr(local_shape, "transformGeometry", None)
    if callable(transform_geometry):
        try:
            result = transform_geometry(matrix)
            return result if result is not None else local_shape
        except Exception:
            pass

    try:
        local_shape.Placement = inverse.multiply(getattr(local_shape, "Placement", App.Placement()))
        return local_shape
    except Exception as exc:
        raise RuntimeError(f"Failed to transform shape into the requested WCS: {exc}") from exc


# =============================================================================
# Slicing: intersect with horizontal planes, sample edges
# =============================================================================

def slice_shape_at_z(
    shape,
    z: float,
) -> List[Any]:
    """
    Intersect 'shape' with plane Z = z and return a list of Part.Wires.
    """
    _require_fc()
    def _collect_wires(item) -> List[Any]:
        out: List[Any] = []
        direct_wires = getattr(item, "Wires", None) or getattr(getattr(item, "Shape", None), "Wires", [])
        for wire in direct_wires or []:
            try:
                if wire.isValid() and wire.Length > 1e-6:
                    out.append(wire)
            except Exception:
                continue

        edges = getattr(item, "Edges", None) or getattr(getattr(item, "Shape", None), "Edges", [])
        if edges:
            try:
                for group in Part.sortEdges(edges):
                    try:
                        wire = Part.Wire(group)
                    except Exception:
                        continue
                    try:
                        if wire.isValid() and wire.Length > 1e-6:
                            out.append(wire)
                    except Exception:
                        continue
            except Exception:
                try:
                    wire = Part.Wire(edges)
                    if wire.isValid() and wire.Length > 1e-6:
                        out.append(wire)
                except Exception:
                    pass
        return out

    def _make_section_face():
        xmin, ymin, _zmin, xmax, ymax, _zmax = compute_bounding_box(shape)
        span_x = max(1.0, float(xmax) - float(xmin))
        span_y = max(1.0, float(ymax) - float(ymin))
        pad = max(2.0, 0.1 * max(span_x, span_y))
        p1 = App.Vector(xmin - pad, ymin - pad, z)
        p2 = App.Vector(xmax + pad, ymin - pad, z)
        p3 = App.Vector(xmax + pad, ymax + pad, z)
        p4 = App.Vector(xmin - pad, ymax + pad, z)
        wire = Part.makePolygon([p1, p2, p3, p4, p1])
        return Part.Face(wire)

    # Prefer a true geometric section against a finite plane face. This is
    # more reliable for complex solids than reading wires back out of
    # ``shape.slice(...)`` results.
    wires: List[Any] = []
    try:
        section_face = _make_section_face()
        section_shape = shape.section(section_face)
        wires.extend(_collect_wires(section_shape))
    except Exception:
        wires = []

    if not wires:
        try:
            slices = shape.slice(App.Vector(0, 0, 1), z)
        except Exception:
            return []
        for sl in slices:
            wires.extend(_collect_wires(sl))

    unique = []
    seen = set()
    for wire in wires:
        try:
            bb = wire.BoundBox
            key = (
                round(float(bb.XMin), 4),
                round(float(bb.YMin), 4),
                round(float(bb.XMax), 4),
                round(float(bb.YMax), 4),
                round(float(wire.Length), 4),
            )
        except Exception:
            key = id(wire)
        if key in seen:
            continue
        seen.add(key)
        unique.append(wire)

    return [w for w in unique if w.isValid()]


# =============================================================================
# Path stitching: turn edge polylines into closed contours
# =============================================================================

def stitch_paths_to_contours(
    paths: List[List[Point2D]],
    close_tol: float = 0.5,
    min_points: int = 3,
) -> List[Contour]:
    """
    Stitch individual edge polylines into closed contour loops.

    Heuristic:
      - Start with one path.
      - Greedily attach other paths whose endpoints are close (within 'close_tol').
      - Reverse paths when needed so endpoints match.
      - When first/last point are close, close the loop.

    Returns a list of Contour objects.
    """
    unused = [list(p) for p in paths]  # copy
    contours: List[Contour] = []
    close_tol2 = close_tol * close_tol

    while unused:
        # Start a new contour from one path
        current = unused.pop()
        changed = True

        while changed and unused:
            changed = False
            start = current[0]
            end = current[-1]

            # Stop extending if the contour is already closed
            # This prevents duplicate closed loops from being merged together
            if len(current) >= min_points and _dist2(start, end) <= close_tol2:
                break

            best_idx = None
            best_case = None  # "end->start", "end->end", "start->start", "start->end"
            best_dist2 = None

            for idx, p in enumerate(unused):
                p_start = p[0]
                p_end = p[-1]

                candidates = [
                    ("end->start", end, p_start),
                    ("end->end", end, p_end),
                    ("start->start", start, p_start),
                    ("start->end", start, p_end),
                ]

                for case, a, b in candidates:
                    d2 = _dist2(a, b)
                    if d2 <= close_tol2 and (best_dist2 is None or d2 < best_dist2):
                        best_idx = idx
                        best_case = case
                        best_dist2 = d2

            if best_idx is not None and best_case is not None:
                other = unused.pop(best_idx)

                if best_case == "end->start":
                    # current ... , other ...
                    current.extend(other)
                elif best_case == "end->end":
                    # current ..., reversed(other) ...
                    current.extend(reversed(other))
                elif best_case == "start->start":
                    # reversed(other) ..., current ...
                    current = list(reversed(other)) + current
                elif best_case == "start->end":
                    # other ..., current ...
                    current = other + current
                else:
                    # should not happen
                    unused.append(other)

                changed = True

        # Close the loop if endpoints are near
        if len(current) >= min_points and _dist2(current[0], current[-1]) <= close_tol2:
            # Ensure exact closure by duplicating start as end
            if current[-1] != current[0]:
                current.append(current[0])

        if len(current) >= min_points:
            contours.append(Contour(points=current))

    return contours


# =============================================================================
# Contour classification (outer vs inner)
# =============================================================================

def _wire_to_contour(wire, samples_per_edge: int, is_outer: bool) -> Contour:
    num_edges = max(1, len(wire.Edges))
    num = max(4, samples_per_edge * num_edges)
    pts_fc = wire.discretize(Number=num)
    pts = [(float(p.x), float(p.y)) for p in pts_fc]
    cleaned = _clean_loop(pts)
    
    if len(cleaned) < 3:
        return None
        
    c = Contour(points=cleaned, is_outer=is_outer)
    a = c.area
    if is_outer and a < 0:
        c.points = list(reversed(c.points))
    elif not is_outer and a > 0:
        c.points = list(reversed(c.points))
    return c

def classify_outer_inner(
    wires: List[Any],
    area_threshold: float = 1e-6,
    samples_per_edge: int = 24,
) -> List[Contour]:
    """
    Classify Part.Wires as outer (is_outer=True) or inner (is_outer=False)
    using robust Python ray-casting to avoid FreeCAD C++ backend freezes
    often caused by Part::FaceMakerBullseye with imprecise wires.
    """
    if not wires:
        return []

    valid_wires = [w for w in wires if w.isValid() and w.Length > 1e-6]
    if not valid_wires:
        return []

    raw_contours = []
    for w in valid_wires:
        c = _wire_to_contour(w, samples_per_edge, is_outer=True)
        if c and abs(c.area) >= area_threshold:
            raw_contours.append(c)

    if not raw_contours:
        return []

    # Sort contours by absolute area, largest first. This ensures outer boundary is checked before inner.
    raw_contours.sort(key=lambda c: abs(c.area), reverse=True)
    depths = [0] * len(raw_contours)
    
    for i, inner_c in enumerate(raw_contours):
        pt = _centroid_point(inner_c.points)
        for j in range(i):
            outer_c = raw_contours[j]
            # Fast bbox check before detailed ray casting
            if _bbox_contains(_bbox_of_points(outer_c.points), pt, pad=1e-3):
                if _point_in_polygon(pt, outer_c.points):
                    depths[i] += 1

    final_contours = []
    for c, depth in zip(raw_contours, depths):
        is_outer = (depth % 2 == 0)
        c.is_outer = is_outer
        a = c.area
        if is_outer and a < 0:
            c.points = list(reversed(c.points))
        elif not is_outer and a > 0:
            c.points = list(reversed(c.points))
        final_contours.append(c)

    return final_contours


def polygon_area(points: List[Point2D]) -> float:
    """Signed polygon area (positive/negative = orientation)."""
    n = len(points)
    if n < 3:
        return 0.0

    area = 0.0
    for i in range(n - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        area += x1 * y2 - x2 * y1

    # close polygon
    x1, y1 = points[-1]
    x2, y2 = points[0]
    area += x1 * y2 - x2 * y1
    return 0.5 * area


# =============================================================================
# Layer generation
# =============================================================================

def generate_layer_slices(
    shape,
    layer_height: float,
    samples_per_edge: int = 24,
    z_offset: float = 0.75,
    min_contour_area: float = 1.0,
    max_layers: Optional[int] = None,
    stitch_close_tol: float = 0.5,
) -> List[LayerSlice]:
    """
    Slice the shape into layers and build LayerSlice objects.

    Parameters
    ----------
    layer_height : float
        Distance between slicing planes in Z.
    samples_per_edge : int
        Number of samples per intersection edge.
    z_offset : float
        Additional offset above the bottom of the bounding box.
    min_contour_area : float
        Discard contours with |area| < min_contour_area.
    max_layers : Optional[int]
        Limit the number of layers (useful for debugging).
    stitch_close_tol : float
        Endpoint tolerance for stitching wires into closed loops.

    Returns
    -------
    List[LayerSlice]
    """
    _require_fc()
    if layer_height <= 0:
        raise ValueError("layer_height must be > 0")
    samples_per_edge = max(2, int(samples_per_edge))
    _, _, zmin, _, _, zmax = compute_bounding_box(shape)

    z_start = zmin + z_offset
    z_end = zmax + 1e-6  # small epsilon

    layers: List[LayerSlice] = []
    layer_index = 0
    z = z_start

    while z <= z_end:
        if max_layers is not None and layer_index >= max_layers:
            break

        wires = slice_shape_at_z(shape, z)
        if not wires:
            z += layer_height
            layer_index += 1
            continue

        classified = classify_outer_inner(
            wires, 
            area_threshold=min_contour_area, 
            samples_per_edge=samples_per_edge
        )
        if not classified:
            z += layer_height
            layer_index += 1
            continue

        layers.append(LayerSlice(index=layer_index, z=z, contours=classified))

        z += layer_height
        layer_index += 1

    return layers


# =============================================================================
# WAAM DSL formatting
# =============================================================================

def format_waam_dsl(
    part_name: str,
    layers: List[LayerSlice],
    units: str = "mm",
    layer_height: Optional[float] = None,
    bead_width: Optional[float] = None,
) -> str:
    """
    Create a WAAM-layer DSL string from a list of LayerSlice objects.
    """
    lines: List[str] = []
    lines.append(f"WAAMPART {part_name}")
    lines.append(f"UNITS {units}")
    if layer_height is not None:
        lines.append(f"LAYER_HEIGHT {layer_height:.4f}")
    if bead_width is not None:
        lines.append(f"BEAD_WIDTH {bead_width:.4f}")
    lines.append("")  # blank line

    for layer in layers:
        lines.append(f"LAYER {layer.index} Z={layer.z:.4f}")
        for contour in layer.contours:
            contour_type = "OUTER_CONTOUR" if contour.is_outer else "INNER_CONTOUR"
            lines.append(f"  {contour_type}")
            pts_str = " -> ".join(
                f"({round(p[0], 3)},{round(p[1], 3)})" for p in contour.points
            )
            lines.append(f"    {pts_str}")
        lines.append("END_LAYER")
        lines.append("")

    lines.append("END_PART")
    return "\n".join(lines)


# =============================================================================
# Public API
# =============================================================================

def build_waam_dsl(
    step_path: Optional[str] = None,
    *,
    layers: Optional[List[LayerSlice]] = None,
    wcs_placement: Optional[Any] = None,
    layer_height: float = 1.5,
    bead_width: Optional[float] = 6.0,
    samples_per_edge: int = 24,
    z_offset: float = 0.75,
    min_contour_area: float = 1.0,
    max_layers: Optional[int] = None,
    stitch_close_tol: float = 0.5,
    units: str = "mm",
) -> str:
    """
    Produce a WAAM DSL string either from a STEP file or precomputed layers.

    If 'layers' is provided it is used directly; otherwise 'step_path' must be
    supplied so the STEP geometry can be sliced.
    """
    if layers is None:
        if not step_path:
            raise ValueError("Provide either 'step_path' or a list of 'layers'.")
        _require_fc()
        shape = load_step_shape(step_path)
        if wcs_placement is not None:
            shape = transform_shape_to_local_frame(shape, wcs_placement)
        layers = generate_layer_slices(
            shape,
            layer_height=layer_height,
            samples_per_edge=samples_per_edge,
            z_offset=z_offset,
            min_contour_area=min_contour_area,
            max_layers=max_layers,
            stitch_close_tol=stitch_close_tol,
        )
        part_name = derive_part_name_from_path(step_path)
    else:
        part_name = derive_part_name_from_path(step_path or "waam_part")

    return format_waam_dsl(
        part_name=part_name,
        layers=layers,
        units=units,
        layer_height=layer_height,
        bead_width=bead_width,
    )


def write_waam_dsl(
    output_path: str | Path,
    *,
    step_path: Optional[str] = None,
    layers: Optional[List[LayerSlice]] = None,
    wcs_placement: Optional[Any] = None,
    layer_height: float = 1.5,
    bead_width: Optional[float] = 6.0,
    samples_per_edge: int = 24,
    z_offset: float = 0.75,
    min_contour_area: float = 1.0,
    max_layers: Optional[int] = None,
    stitch_close_tol: float = 0.5,
    units: str = "mm",
) -> Path:
    """Generate WAAM DSL text and write it to a file."""
    dsl_text = build_waam_dsl(
        step_path=step_path,
        layers=layers,
        wcs_placement=wcs_placement,
        layer_height=layer_height,
        bead_width=bead_width,
        samples_per_edge=samples_per_edge,
        z_offset=z_offset,
        min_contour_area=min_contour_area,
        max_layers=max_layers,
        stitch_close_tol=stitch_close_tol,
        units=units,
    )
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(dsl_text, encoding="utf-8")
    return output


def convert_step_to_waam(
    step_path: str,
    out_path: Optional[str | Path] = None,
    *,
    wcs_placement: Optional[Any] = None,
    layer_height: float = 1.5,
    bead_width: Optional[float] = 6.0,
    samples_per_edge: int = 24,
    z_offset: float = 0.75,
    min_contour_area: float = 1.0,
    max_layers: Optional[int] = None,
    stitch_close_tol: float = 0.5,
    emit_llm: bool = True,
    llm_output_path: Optional[str | Path] = None,
    units: str = "mm",
) -> None:
    """
    High-level function: STEP -> WAAM layer outputs.

    Parameters
    ----------
    step_path : str
        Input STEP file path (.stp/.step).
    out_path : Optional[str | Path]
        Optional output WAAM DSL file path (.waam or .txt). When omitted,
        the full human-readable DSL is not written.
    layer_height : float
        Slicing layer height.
    bead_width : Optional[float]
        Bead width for WAAM (metadata only, doesn't affect geometry).
    samples_per_edge : int
        Samples per intersection edge.
    z_offset : float
        Offset from bottom bounding box to start slicing.
    min_contour_area : float
        Discard micro contours below this area.
    max_layers : Optional[int]
        Optional limit on number of layers to generate.
    stitch_close_tol : float
        Endpoint tolerance for stitching wires into closed loops.
    emit_llm : bool
        If True, also writes compressed LLM-friendly WAAM (.llm_waam).
    llm_output_path : Optional[str | Path]
        Override output path for the compressed WAAM file (defaults next to .waam).
    units : str
        Units descriptor for the DSL header (e.g. "mm").
    """
    _require_fc()
    print(f"[INFO] Loading STEP file: {step_path} (backend: {_BACKEND_LABEL})")
    part_name = derive_part_name_from_path(step_path)

    shape = load_step_shape(step_path)
    if wcs_placement is not None:
        shape = transform_shape_to_local_frame(shape, wcs_placement)
    layers = generate_layer_slices(
        shape,
        layer_height=layer_height,
        samples_per_edge=samples_per_edge,
        z_offset=z_offset,
        min_contour_area=min_contour_area,
        max_layers=max_layers,
        stitch_close_tol=stitch_close_tol,
    )

    if out_path is not None:
        dsl_text = format_waam_dsl(
            part_name=part_name,
            layers=layers,
            units=units,
            layer_height=layer_height,
            bead_width=bead_width,
        )
        target = Path(out_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as f:
            f.write(dsl_text)
        print(f"[INFO] WAAM DSL written to: {target}")

    if emit_llm:
        if llm_output_path:
            llm_target = Path(llm_output_path).expanduser()
        elif out_path is not None:
            llm_target = Path(out_path).expanduser().with_suffix(".llm_waam")
        else:
            llm_target = default_llm_waam_output_path(step_path)
        write_llm_waam_dsl(
            llm_target,
            part_name=part_name,
            layers=layers,
            units=units,
            layer_height=layer_height,
            bead_width=bead_width,
        )
        print(f"[INFO] LLM WAAM written to: {llm_target}")
    elif out_path is None:
        raise ValueError("No output was requested. Provide out_path and/or enable emit_llm.")


def derive_part_name_from_path(path: str) -> str:
    """Make a safe part name from a file path."""
    base = os.path.basename(path)
    name, _ = os.path.splitext(base)
    return name.replace(" ", "_")


def default_dsl_output_path(step_path: str, *, output_root: Optional[Path | str] = None) -> Path:
    """
    Standard WAAM DSL output location for a given STEP source.

    Files are placed under <output_root>/dsl_waam/part.waam
    where <output_root> defaults to the workbench output directory.
    """
    root = Path(output_root).expanduser() if output_root else get_output_dir()
    return root / "dsl_waam" / "part.waam"


def default_llm_waam_output_path(step_path: str, *, output_root: Optional[Path | str] = None) -> Path:
    """
    Standard compact LLM WAAM output location for a given STEP source.

    Files are placed under <output_root>/dsl_waam/part.llm_waam
    where <output_root> defaults to the workbench output directory.
    """
    root = Path(output_root).expanduser() if output_root else get_output_dir()
    return root / "dsl_waam" / "part.llm_waam"


# =============================================================================
# CLI
# =============================================================================

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a STEP (.stp/.step) file to a WAAM-layer DSL file (runs inside FreeCAD).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "input",
        help="Input STEP file (.stp / .step)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output WAAM DSL file (.waam / .txt). "
             "If not given, saves under <output>/dsl_waam/<input_basename>.waam",
    )
    parser.add_argument(
        "--layer-height",
        type=float,
        default=1.5,
        help="Layer height in units (e.g. mm).",
    )
    parser.add_argument(
        "--bead-width",
        type=float,
        default=6.0,
        help="Bead width for WAAM header (metadata only).",
    )
    parser.add_argument(
        "--samples-per-edge",
        type=int,
        default=24,
        help="Number of sample points per intersection edge.",
    )
    parser.add_argument(
        "--z-offset",
        type=float,
        default=0.75,
        help="Additional offset above bottom of bounding box for slicing start.",
    )
    parser.add_argument(
        "--min-contour-area",
        type=float,
        default=1.0,
        help="Minimum |area| of contour to keep (filters tiny artifacts).",
    )
    parser.add_argument(
        "--close-tol",
        type=float,
        default=0.5,
        help="Endpoint tolerance when stitching intersection paths into contours.",
    )
    parser.add_argument(
        "--llm-output",
        type=str,
        default=None,
        help="Optional path for compressed LLM-friendly WAAM (.llm_waam). Defaults to alongside the .waam output.",
    )
    parser.add_argument(
        "--no-llm",
        dest="emit_llm",
        action="store_false",
        help="Skip writing the compressed LLM-friendly WAAM (.llm_waam).",
    )
    parser.set_defaults(emit_llm=True)
    parser.add_argument(
        "--max-layers",
        type=int,
        default=None,
        help="Optional maximum number of layers (for debugging).",
    )
    parser.add_argument(
        "--units",
        type=str,
        default="mm",
        help="Units label used in WAAM DSL header.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    step_path = args.input
    if not os.path.isfile(step_path):
        print(f"[ERROR] Input file not found: {step_path}", file=sys.stderr)
        return 1

    if args.output:
        out_path = args.output
    else:
        out_path = default_dsl_output_path(step_path)

    try:
        convert_step_to_waam(
            step_path=step_path,
            out_path=out_path,
            layer_height=args.layer_height,
            bead_width=args.bead_width,
            samples_per_edge=args.samples_per_edge,
            z_offset=args.z_offset,
            min_contour_area=args.min_contour_area,
            max_layers=args.max_layers,
            stitch_close_tol=args.close_tol,
            emit_llm=args.emit_llm,
            llm_output_path=args.llm_output,
            units=args.units,
        )
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    return 0


# =============================================================================
# Helpers
# =============================================================================

def _dist2(p1: Point2D, p2: Point2D) -> float:
    dx = p1[0] - p2[0]
    dy = p1[1] - p2[1]
    return dx * dx + dy * dy


def _clean_loop(points: List[Point2D], tol: float = 1e-6) -> List[Point2D]:
    """Remove near-duplicate consecutive points and ensure closure."""
    if not points:
        return []
    tol2 = tol * tol
    cleaned: List[Point2D] = []
    for pt in points:
        if not cleaned or _dist2(cleaned[-1], pt) > tol2:
            cleaned.append(pt)
    if cleaned and _dist2(cleaned[0], cleaned[-1]) > tol2:
        cleaned.append(cleaned[0])
    return cleaned


def _bbox_of_points(points: List[Point2D]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_contains(bbox: Tuple[float, float, float, float], pt: Point2D, pad: float = 0.0) -> bool:
    xmin, ymin, xmax, ymax = bbox
    x, y = pt
    return (xmin - pad) <= x <= (xmax + pad) and (ymin - pad) <= y <= (ymax + pad)


def _centroid_point(points: List[Point2D]) -> Point2D:
    """Return a simple centroid-like point (ignores closing duplicate)."""
    if not points:
        return (0.0, 0.0)
    pts = points[:-1] if len(points) > 1 and points[0] == points[-1] else points
    count = max(1, len(pts))
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    return (sx / count, sy / count)


def _point_in_polygon(point: Point2D, polygon: List[Point2D]) -> bool:
    """
    Ray-casting point-in-polygon (even-odd rule).
    Assumes polygon is closed or will treat last->first as closing edge.
    """
    x, y = point
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        # Check if edge crosses the horizontal ray
        intersects = (y1 > y) != (y2 > y)
        if intersects:
            # Avoid division by zero on horizontal edges
            x_at_y = x1 + (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-16)
            if x_at_y > x:
                inside = not inside
    return inside


__all__ = [
    "DSL_VERSION",
    "Contour",
    "LayerSlice",
    "load_step_shape",
    "compute_bounding_box",
    "transform_shape_to_local_frame",
    "slice_shape_at_z",
    "stitch_paths_to_contours",
    "classify_outer_inner",
    "generate_layer_slices",
    "format_waam_dsl",
    "build_waam_dsl",
    "write_waam_dsl",
    "convert_step_to_waam",
    "derive_part_name_from_path",
    "default_dsl_output_path",
    "default_llm_waam_output_path",
    "main",
]


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    raise SystemExit(main())
