"""Compatibility layer for PySide2 (FreeCAD 0.21) and PySide6 (FreeCAD 1.0+).

This module attempts to import PySide6 first, and falls back to PySide2.
It exposes the standard Qt modules (QtCore, QtGui, QtWidgets) for use by other UI components,
ensuring consistent behavior across different FreeCAD versions.
"""

from __future__ import annotations

import sys

# Attempt to load PySide6 (preferred for newer FreeCAD / Python 3.10+)
try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt
    IS_PYSIDE6 = True
    IS_PYSIDE2 = False
except ImportError:
    # Fallback to PySide2 (common in FreeCAD 0.20/0.21)
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
        from PySide2.QtCore import Qt
        IS_PYSIDE6 = False
        IS_PYSIDE2 = True
    except ImportError as e:
        # If neither is found, we can't run the UI
        raise ImportError("Neither PySide6 nor PySide2 could be imported.") from e

# Export for consumers
# These are redundant but explicit for type checkers / linters
__all__ = [
    "QtCore",
    "QtGui",
    "QtWidgets",
    "Qt",
    "IS_PYSIDE6",
    "IS_PYSIDE2",
]
