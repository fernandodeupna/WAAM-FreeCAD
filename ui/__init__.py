"""UI helpers for the WAAM workbench.

Keep this package initializer lightweight.
"""

from __future__ import annotations

import importlib

from .wcs_marker import ensure_tool_wcs_marker, get_stored_wcs, get_wcs_label, store_wcs

__all__ = [
    "commands_light",
    "ensure_tool_wcs_marker",
    "get_stored_wcs",
    "get_wcs_label",
    "show_wcs_dialog",
    "store_wcs",
]


def __getattr__(name: str):
    if name == "commands_light":
        return importlib.import_module(f"{__name__}.commands_light")
    if name == "show_wcs_dialog":
        return importlib.import_module(f"{__name__}.ui_wcs").show_wcs_dialog
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)
