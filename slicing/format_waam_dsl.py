from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Dict, Tuple, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # only for type hints to avoid circular import at runtime
    from .step_to_waam_dsl import Contour, LayerSlice


def _quantize_point(p, ndigits: int = 3) -> Tuple[float, float]:
    """Round a point for stable hashing (geometry is already approximate)."""
    x, y = p
    return (round(float(x), ndigits), round(float(y), ndigits))


def _best_rotation(seq: List[Tuple[float, float]]) -> Tuple[Tuple[float, float], ...]:
    """Return lexicographically minimal rotation of a point list."""
    if not seq:
        return tuple()
    rotations = [tuple(seq[i:] + seq[:i]) for i in range(len(seq))]
    return min(rotations)


def _canonical_points(points: List[Tuple[float, float]], ndigits: int = 3) -> Tuple[Tuple[float, float], ...]:
    """Orientation/rotation-invariant canonical representation for dedup."""
    if not points:
        return tuple()
    quantized = [_quantize_point(p, ndigits) for p in points]
    if len(quantized) > 1 and quantized[0] == quantized[-1]:
        quantized = quantized[:-1]
    if not quantized:
        return tuple()
    forward = _best_rotation(list(quantized))
    backward = _best_rotation(list(reversed(quantized)))
    return forward if forward <= backward else backward


def _contour_key(contour: Contour, ndigits: int = 3) -> Tuple[Tuple[float, float], ...]:
    """Geometry-only key so identical shapes (outer/inner) share PATH IDs."""
    return _canonical_points(contour.points, ndigits=ndigits)


def _closed_from_key(key: Tuple[Tuple[float, float], ...]) -> List[Tuple[float, float]]:
    """Rebuild a closed loop from a canonical key."""
    pts = list(key)
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def _fmt_num(value: float, precision: int) -> str:
    """Format numbers without trailing zeros to save tokens."""
    txt = f"{float(value):.{precision}f}"
    txt = txt.rstrip("0").rstrip(".")
    return txt or "0"


def _fmt_point(x: float, y: float, precision: int) -> str:
    return f"({_fmt_num(x, precision)},{_fmt_num(y, precision)})"


def format_llm_waam_dsl(
    part_name: str,
    layers: List["LayerSlice"],
    units: str = "mm",
    layer_height: Optional[float] = None,
    bead_width: Optional[float] = None,
    *,
    compact: bool = True,
    precision: int = 3,
) -> str:
    """
    WAAM DSL optimized for LLMs & token usage:

      - A PATHLIB section defines unique 2D paths (contours).
      - Each layer references those paths with USE_PATH.

    Geometry is only written once per unique contour, and the compact mode
    removes padding/indentation to shrink tokens further.
    """

    precision = max(0, min(6, int(precision)))

    # ------------------------------------------------------------------
    # 1) Build path library (deduplicate contours across all layers)
    # ------------------------------------------------------------------
    path_lib: "OrderedDict[Tuple[Tuple[float, float], ...], Dict[str, object]]" = OrderedDict()
    contour_to_path_id: Dict[Tuple[Tuple[float, float], ...], str] = {}
    next_id = 0

    for layer in layers:
        for contour in layer.contours:
            key = _contour_key(contour)
            if key not in contour_to_path_id:
                path_id = f"P{next_id}"
                next_id += 1
                contour_to_path_id[key] = path_id
                path_lib[key] = {
                    "id": path_id,
                    "role_hint": "OUTER" if contour.is_outer else "INNER",
                    "points": _closed_from_key(key),
                }

    # ------------------------------------------------------------------
    # 2) Emit header
    # ------------------------------------------------------------------
    lines: List[str] = []
    lines.append(f"WAAMPART {part_name}")
    lines.append(f"UNITS {units}")
    if layer_height is not None:
        lines.append(f"LAYER_HEIGHT {_fmt_num(layer_height, precision)}")
    if bead_width is not None:
        lines.append(f"BEAD_WIDTH {_fmt_num(bead_width, precision)}")
    if not compact:
        lines.append("")

    # ------------------------------------------------------------------
    # 3) Emit PATHLIB with full geometry once per unique contour
    # ------------------------------------------------------------------
    lines.append("PATHLIB_BEGIN")
    for data in path_lib.values():
        path_id = data["id"]
        role_hint = data["role_hint"]
        points = data["points"]
        if compact:
            pt_line = "".join(_fmt_point(x, y, precision) for (x, y) in points)
            lines.append(f"PATH ID={path_id} ROLE_HINT={role_hint}")
            if pt_line:
                lines.append(pt_line)
            lines.append("END_PATH")
        else:
            lines.append(f"  PATH ID={path_id} ROLE_HINT={role_hint}")
            lines.append("    POLYLINE")
            for (x, y) in points:
                lines.append(f"      {_fmt_point(x, y, precision)}")
            lines.append("  END_PATH")
            lines.append("")  # blank between paths
    lines.append("PATHLIB_END")
    if not compact:
        lines.append("")

    # ------------------------------------------------------------------
    # 4) Emit layers as lightweight references
    # ------------------------------------------------------------------
    for layer in layers:
        header = f"LAYER {layer.index}"
        if layer.z is not None:
            header += f" Z={_fmt_num(layer.z, precision)}"
        lines.append(header)
        for contour in layer.contours:
            key = _contour_key(contour)
            path_id = contour_to_path_id[key]
            role = "OUTER_CONTOUR" if contour.is_outer else "INNER_CONTOUR"
            lines.append(f"USE_PATH ID={path_id} AS={role}")
        lines.append("END_LAYER")
        if not compact:
            lines.append("")

    lines.append("END_PART")
    return "\n".join(lines)


def write_llm_waam_dsl(
    output_path: str | Path,
    *,
    part_name: str,
    layers: List["LayerSlice"],
    units: str = "mm",
    layer_height: Optional[float] = None,
    bead_width: Optional[float] = None,
    compact: bool = True,
    precision: int = 3,
) -> Path:
    text = format_llm_waam_dsl(
        part_name=part_name,
        layers=layers,
        units=units,
        layer_height=layer_height,
        bead_width=bead_width,
        compact=compact,
        precision=precision,
    )
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    return output


# Backward-compatible alias inside this module only
format_waam_dsl = format_llm_waam_dsl


__all__ = ["format_llm_waam_dsl", "write_llm_waam_dsl", "format_waam_dsl"]
