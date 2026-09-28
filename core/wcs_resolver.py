"""Centralised WCS resolution for WAAM.

This module encapsulates how we pick a work coordinate system (WCS)
placement for geometry logic:

1. Prefer the stored WAAM WCS marker (`WAAM_WCS` group).
2. Fall back to the first Path Job's placement / setup sheet.
3. As a last resort, use the identity placement (world coordinates).

It also performs light validation and emits diagnostics so callers
can understand which source was used and whether we had to fall back.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import List, Optional

import FreeCAD as App  # type: ignore

from ..ui.wcs_marker import get_stored_wcs, get_wcs_label
from .path_compat import ensure_setup_sheet_placement  # type: ignore


@dataclass(frozen=True)
class WcsResolution:
    """Result of resolving a WCS placement for a document."""

    placement: App.Placement
    source: str  # "stored_marker", "path_job", or "identity_fallback"
    label: Optional[str]
    job_name: Optional[str]
    warnings: tuple[str, ...]


def _iter_path_jobs(doc: App.Document):
    """Yield Path Job objects while ignoring individual operations.

    This mirrors the heuristics used in feature extraction and voxel matrix
    generation but keeps them in a single place.
    """

    for obj in getattr(doc, "Objects", []):
        proxy = getattr(obj, "Proxy", None)
        if proxy is None:
            continue
        proxy_type = getattr(proxy, "Type", "")
        if isinstance(proxy_type, str) and proxy_type.lower() == "pathjob":
            yield obj
            continue
        proxy_name = getattr(proxy.__class__, "__name__", "")
        if isinstance(proxy_name, str) and "pathjob" in proxy_name.lower():
            yield obj
            continue
        module_name = getattr(proxy.__class__, "__module__", "")
        if isinstance(module_name, str) and "PathScripts.PathJob" in module_name:
            yield obj


def _job_placement(job: App.DocumentObject) -> App.Placement:
    """Return the placement for a Path Job or its setup sheet.

    FC 1.0 prefers job.Placement; older FC uses SetupSheet.Placement.
    """

    if hasattr(job, "Placement"):
        return job.Placement
    ss = getattr(job, "SetupSheet", None)
    if ss is not None and hasattr(ss, "Placement"):
        return ss.Placement
    # Identity if nothing else – callers will validate.
    return App.Placement()


def _is_identity(placement: App.Placement, tol: float = 1e-9) -> bool:
    """Heuristic check for an identity placement."""

    base = getattr(placement, "Base", App.Vector(0, 0, 0))
    try:
        if App.Vector(base).Length > tol:
            return False
    except Exception:
        return False

    rot = getattr(placement, "Rotation", App.Rotation())
    try:
        angle = float(getattr(rot, "Angle", 0.0))
    except Exception:
        angle = 0.0
    return abs(angle) <= tol


def _validate_placement(placement: App.Placement, source: str) -> List[str]:
    """Return a list of human-readable warnings for a placement."""

    warnings: List[str] = []
    base = getattr(placement, "Base", App.Vector(0, 0, 0))
    rot = getattr(placement, "Rotation", App.Rotation())

    for coord_name, coord in (("x", base.x), ("y", base.y), ("z", base.z)):
        if not math.isfinite(float(coord)):
            warnings.append(f"WCS base.{coord_name} is not finite; using 0.0 instead.")

    # Detect identity / near-identity placements so callers can warn the user.
    if _is_identity(placement):
        if source == "identity_fallback":
            warnings.append(
                "No stored WAAM WCS or Path Job placement found; using identity placement "
                "(world coordinates)."
            )
        else:
            warnings.append(
                "Resolved WCS placement is identity; verify that WAAM WCS was configured as expected."
            )

    # Sanity check rotation – we expect a proper rotation with non-zero basis.
    try:
        x = rot.multVec(App.Vector(1, 0, 0))
        y = rot.multVec(App.Vector(0, 1, 0))
        z = rot.multVec(App.Vector(0, 0, 1))
        if x.Length == 0 or y.Length == 0 or z.Length == 0:
            warnings.append("WCS rotation has a zero-length axis; results may be invalid.")
    except Exception:
        warnings.append("Failed to probe WCS rotation; results may be invalid.")

    return warnings


def resolve_wcs(doc: App.Document) -> WcsResolution:
    """Resolve the WAAM WCS placement for a FreeCAD document.

    Preference order:
        1) Stored WAAM WCS marker (`WAAM_WCS` group)
        2) First Path Job placement / setup sheet
        3) Identity placement (world coordinates)
    """

    if doc is None:
        raise RuntimeError("No document provided for WCS resolution.")

    placement: App.Placement
    source: str
    job = None

    stored = get_stored_wcs(doc)
    wcs_label = get_wcs_label(doc)

    if stored is not None:
        placement = App.Placement(stored)
        source = "stored_marker"
    else:
        job = next(_iter_path_jobs(doc), None)
        if job is not None:
            try:
                ensure_setup_sheet_placement(job)
            except Exception:
                # Best-effort; we'll still try to read the job placement.
                pass
            placement = _job_placement(job)
            source = "path_job"
        else:
            placement = App.Placement()
            source = "identity_fallback"

    warnings = _validate_placement(placement, source)
    for msg in warnings:
        try:
            App.Console.PrintWarning(f"WAAM WCS: {msg}\n")
        except Exception:
            # Headless / console-less environments.
            pass

    return WcsResolution(
        placement=placement,
        source=source,
        label=wcs_label,
        job_name=getattr(job, "Name", None) if job is not None else None,
        warnings=tuple(warnings),
    )


__all__ = ["WcsResolution", "resolve_wcs", "_iter_path_jobs", "_job_placement"]

