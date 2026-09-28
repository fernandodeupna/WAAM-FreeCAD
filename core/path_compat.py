"""Minimal compatibility helpers for the FreeCAD Path API."""

from __future__ import annotations

from typing import Any, Iterable

import FreeCAD  # type: ignore

try:  # pragma: no cover - depends on FreeCAD build
    from PathScripts import PathJob as _PathJob  # type: ignore
    _LEGACY_PATH_API = True
except ImportError:  # pragma: no cover
    from Path.Main import Job as _PathJob  # type: ignore
    _LEGACY_PATH_API = False


def ensure_setup_sheet_placement(job: Any) -> None:
    """Make sure the job exposes a Placement property on its SetupSheet."""
    setup = getattr(job, "SetupSheet", None)
    if not setup:
        return
    if not hasattr(setup, "Placement"):
        setup.addProperty(
            "App::PropertyPlacement",
            "Placement",
            "Base",
            "Work coordinate system placement (WAAM).",
        )
        setup.Placement = FreeCAD.Placement()
    elif getattr(setup, "Placement", None) is None:
        setup.Placement = FreeCAD.Placement()


def disable_job_stock(job: Any) -> None:
    """Disable subtractive stock; WAAM works directly from the part geometry."""
    stock = getattr(job, "Stock", None)
    try:
        if hasattr(job, "StockType"):
            job.StockType = "None"
        if stock is None:
            return
        if hasattr(stock, "StockType"):
            stock.StockType = "None"
        if hasattr(stock, "Base"):
            stock.Base = None
        for attr in ("ExtXneg", "ExtXpos", "ExtYneg", "ExtYpos", "ExtZneg", "ExtZpos"):
            if hasattr(stock, attr):
                setattr(stock, attr, 0.0)
    except Exception as exc:  # pragma: no cover - defensive
        FreeCAD.Console.PrintWarning(f"WAAM: unable to disable stock - {exc}\n")


def remove_job_stock_from_tree(job: Any) -> None:
    """Detach any existing stock object from the document tree."""
    stock = getattr(job, "Stock", None)
    if not stock:
        return
    try:
        if hasattr(job, "Stock"):
            job.Stock = None
    except Exception:
        pass
    doc = getattr(stock, "Document", None) or getattr(job, "Document", None)
    if doc and hasattr(stock, "Name"):
        try:
            doc.removeObject(stock.Name)
            doc.recompute()
        except Exception:
            pass


def _as_iterable(obj: Any) -> Iterable[Any]:
    if isinstance(obj, (list, tuple)):
        return obj
    return [obj]


def create_job(base_object: Any, label: str = "AI_WAAM_Job") -> Any:
    """Create a Path Job across legacy and modern FreeCAD releases."""
    if _LEGACY_PATH_API:
        job = _PathJob.Create(base_object)
    else:
        job = _PathJob.Create(label, tuple(_as_iterable(base_object)))
    ensure_setup_sheet_placement(job)
    disable_job_stock(job)
    return job


__all__ = [
    "create_job",
    "disable_job_stock",
    "ensure_setup_sheet_placement",
    "remove_job_stock_from_tree",
]
