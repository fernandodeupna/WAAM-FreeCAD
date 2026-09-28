"""Core services shared across WAAM."""

from __future__ import annotations

from .config_loader import load_standard_config
from .paths import get_output_dir, get_workspace_root

__all__ = [
    "create_job",
    "disable_job_stock",
    "ensure_setup_sheet_placement",
    "get_output_dir",
    "get_workspace_root",
    "load_standard_config",
]


def __getattr__(name):
    """Lazily import FreeCAD-dependent helpers so headless callers still work."""
    if name in {"create_job", "disable_job_stock", "ensure_setup_sheet_placement"}:
        from .path_compat import (  # type: ignore
            create_job,
            disable_job_stock,
            ensure_setup_sheet_placement,
        )

        return {
            "create_job": create_job,
            "disable_job_stock": disable_job_stock,
            "ensure_setup_sheet_placement": ensure_setup_sheet_placement,
        }[name]
    raise AttributeError(name)
