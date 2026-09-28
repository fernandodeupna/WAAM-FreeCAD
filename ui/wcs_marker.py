from __future__ import annotations

from typing import Optional, Tuple

import FreeCAD as App  # type: ignore
import Part  # type: ignore

_GROUP_NAME = "WAAM_WCS"
_AXES_DATA: Tuple[Tuple[str, App.Vector, Tuple[float, float, float]], ...] = (
    ("WAAM_WCS_X", App.Vector(1, 0, 0), (1.0, 0.2, 0.2)),
    ("WAAM_WCS_Y", App.Vector(0, 1, 0), (0.2, 1.0, 0.2)),
    ("WAAM_WCS_Z", App.Vector(0, 0, 1), (0.2, 0.4, 1.0)),
)


def _find_group(doc: App.Document):
    if doc is None:
        return None
    group = doc.getObject(_GROUP_NAME)
    if group is not None:
        return group
    target = _GROUP_NAME.lower()
    fallback = None
    for obj in getattr(doc, "Objects", []):
        name = getattr(obj, "Name", "")
        label = getattr(obj, "Label", "")
        if isinstance(name, str) and name.lower() == target:
            fallback = obj
            if hasattr(obj, "WcsLabel") or hasattr(obj, "Group"):
                return obj
        if isinstance(label, str) and label.lower() == target:
            fallback = obj
            if hasattr(obj, "WcsLabel") or hasattr(obj, "Group"):
                return obj
    return fallback


def _ensure_group(doc: App.Document):
    group = doc.getObject(_GROUP_NAME)
    if group is None:
        group = doc.addObject("App::DocumentObjectGroup", _GROUP_NAME)
    if not hasattr(group, "Placement"):
        try:
            group.addProperty(
                "App::PropertyPlacement",
                "Placement",
                "Base",
                "Stored WAAM tooling WCS placement.",
            )
        except Exception:
            pass
    if not hasattr(group, "WcsLabel"):
        try:
            group.addProperty(
                "App::PropertyString",
                "WcsLabel",
                "Base",
                "Stored WAAM WCS label (e.g., G54).",
            )
            group.WcsLabel = "G54"
        except Exception:
            pass
    if not hasattr(group, "HasDepositionStart"):
        try:
            group.addProperty(
                "App::PropertyBool",
                "HasDepositionStart",
                "Base",
                "Whether a deposition start point is stored.",
            )
            group.HasDepositionStart = False
        except Exception:
            pass
    if not hasattr(group, "DepositionStart"):
        try:
            group.addProperty(
                "App::PropertyVector",
                "DepositionStart",
                "Base",
                "Stored deposition start point in global coordinates.",
            )
        except Exception:
            pass
    return group


def _ensure_axis_feature(doc: App.Document, group, name: str):
    obj = doc.getObject(name)
    if obj is None:
        obj = doc.addObject("Part::Feature", name)
    if obj not in getattr(group, "Group", []):
        group.addObject(obj)

    view = getattr(obj, "ViewObject", None)
    if view is not None:
        try:
            view.DisplayMode = "Wireframe"
        except Exception:
            pass
        try:
            view.LineWidth = 3
        except Exception:
            pass
    return obj


def store_wcs(
    doc: App.Document,
    placement: App.Placement,
    label: Optional[str] = None,
    *,
    start_point: Optional[App.Vector] = None,
    clear_start: bool = False,
) -> None:
    group = _ensure_group(doc)
    if hasattr(group, "Placement"):
        group.Placement = App.Placement(placement)
    if label and hasattr(group, "WcsLabel"):
        group.WcsLabel = str(label)
    if start_point is not None and hasattr(group, "DepositionStart"):
        group.DepositionStart = App.Vector(start_point)
        if hasattr(group, "HasDepositionStart"):
            group.HasDepositionStart = True
    elif clear_start and hasattr(group, "HasDepositionStart"):
        group.HasDepositionStart = False


def get_stored_wcs(doc: App.Document) -> Optional[App.Placement]:
    group = _find_group(doc)
    if group and hasattr(group, "Placement"):
        placement = getattr(group, "Placement", None)
        if placement is not None:
            return App.Placement(placement)
    return None


def get_wcs_label(doc: App.Document) -> Optional[str]:
    group = _find_group(doc)
    if group and hasattr(group, "WcsLabel"):
        label = getattr(group, "WcsLabel", None)
        if isinstance(label, str) and label.strip():
            return label.strip()
    return None


def get_deposition_start(doc: App.Document) -> Optional[App.Vector]:
    group = _find_group(doc)
    if group and hasattr(group, "HasDepositionStart") and hasattr(group, "DepositionStart"):
        try:
            if getattr(group, "HasDepositionStart", False):
                return App.Vector(getattr(group, "DepositionStart"))
        except Exception:
            return None
    return None


def ensure_tool_wcs_marker(
    doc: App.Document,
    placement: App.Placement,
    *,
    length: float = 20.0,
    label: Optional[str] = None,
    start_point: Optional[App.Vector] = None,
    clear_start: bool = False,
) -> None:
    """Plot the WAAM WCS axes as simple line features."""
    if doc is None:
        return

    group = _ensure_group(doc)
    store_wcs(doc, placement, label=label, start_point=start_point, clear_start=clear_start)

    base = placement.Base
    rotation = placement.Rotation

    for name, axis_dir, color in _AXES_DATA:
        axis_feature = _ensure_axis_feature(doc, group, name)
        world_dir = rotation.multVec(axis_dir)
        mag = world_dir.Length
        if mag == 0:
            shape = Part.makePoint(base)
        else:
            unit = App.Vector(world_dir)
            unit.normalize()
            end_point = base + unit * length
            shape = Part.makeLine(base, end_point)
        axis_feature.Shape = shape

        view = getattr(axis_feature, "ViewObject", None)
        if view is not None:
            try:
                view.LineColor = color
            except Exception:
                pass
            try:
                view.PointColor = color
            except Exception:
                pass

    if App.ActiveDocument is doc:
        doc.recompute()
