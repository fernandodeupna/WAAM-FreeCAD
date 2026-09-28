"""Matplotlib Qt backend helpers for PySide2/PySide6 FreeCAD environments."""

from __future__ import annotations

import importlib
import os
import sys
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Dict, Iterator, Tuple

from .qt_compat import IS_PYSIDE2, IS_PYSIDE6, QtWidgets

_QT_BACKEND_MODULES = (
    "matplotlib.backends.qt_compat",
    "matplotlib.backends.backend_qt",
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_qt5agg",
)
_QT_BINDINGS = ("PyQt6", "PySide6", "PyQt5", "PySide2")


def _preferred_qt_api() -> str:
    if IS_PYSIDE6:
        return "PySide6"
    if IS_PYSIDE2:
        return "PySide2"
    raise ImportError("FreeCAD Qt compatibility layer did not expose PySide2 or PySide6.")


@contextmanager
def _force_freecad_qt_binding(qt_api: str) -> Iterator[None]:
    saved_qt_api = os.environ.get("QT_API")
    os.environ["QT_API"] = qt_api.lower()

    hidden_modules: Dict[str, Any] = {}
    for binding in _QT_BINDINGS:
        if binding == qt_api:
            continue
        prefix = f"{binding}."
        for module_name in list(sys.modules):
            if module_name == binding or module_name.startswith(prefix):
                hidden_modules[module_name] = sys.modules.pop(module_name)

    for module_name in _QT_BACKEND_MODULES:
        sys.modules.pop(module_name, None)

    try:
        yield
    finally:
        sys.modules.update(hidden_modules)
        if saved_qt_api is None:
            os.environ.pop("QT_API", None)
        else:
            os.environ["QT_API"] = saved_qt_api


@lru_cache(maxsize=1)
def get_matplotlib_qt() -> Tuple[Any, Any, Any]:
    qt_api = _preferred_qt_api()
    errors = []

    # Keep Matplotlib on FreeCAD's Qt binding even when user site-packages
    # include PyQt, otherwise the returned canvas is not a QWidget for PySide.
    with _force_freecad_qt_binding(qt_api):
        import matplotlib
        from matplotlib.figure import Figure

        for module_name in ("matplotlib.backends.backend_qtagg", "matplotlib.backends.backend_qt5agg"):
            try:
                backend_module = importlib.import_module(module_name)
                figure_canvas = getattr(backend_module, "FigureCanvasQTAgg")
                if not issubclass(figure_canvas, QtWidgets.QWidget):
                    raise TypeError(
                        f"{module_name} returned a canvas that is not compatible with FreeCAD's {qt_api} widgets."
                    )
                return matplotlib, Figure, figure_canvas
            except Exception as exc:
                errors.append(f"{module_name}: {exc}")

    detail = "; ".join(errors) or "no Qt-compatible Matplotlib backend was available"
    raise ImportError(
        "Unable to load a Qt-compatible Matplotlib backend. "
        f"Tried backend_qtagg and backend_qt5agg. Details: {detail}"
    )
