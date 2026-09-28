import importlib
from pathlib import Path as SysPath

import FreeCADGui as Gui

_ICON_PATH = str((SysPath(__file__).resolve().parent / "icons" / "waam.svg"))
_SHUTDOWN_HOOK_INSTALLED = False


def _close_waam_windows():
    try:
        from .ui.qt_compat import QtWidgets
    except Exception:
        return

    app = QtWidgets.QApplication.instance()
    if app is None:
        return

    for widget in list(app.topLevelWidgets()):
        try:
            title = str(widget.windowTitle() or "")
        except Exception:
            continue
        if not title.startswith("WAAM"):
            continue
        try:
            widget.close()
        except Exception:
            pass
        try:
            widget.deleteLater()
        except Exception:
            pass


def _install_shutdown_hook():
    global _SHUTDOWN_HOOK_INSTALLED
    if _SHUTDOWN_HOOK_INSTALLED:
        return

    try:
        from .ui.qt_compat import QtWidgets
    except Exception:
        return

    app = QtWidgets.QApplication.instance()
    if app is None:
        return

    app.aboutToQuit.connect(_close_waam_windows)
    _SHUTDOWN_HOOK_INSTALLED = True


def _append_workbench_ui(workbench, module_name: str, *, toolbar_name: str, menu_name: str) -> None:
    cmds = importlib.import_module(f"{__package__}.{module_name}")
    workbench.appendToolbar(toolbar_name, cmds.COMMANDS)
    workbench.appendMenu(menu_name, cmds.MENU)


class WaamWorkbench(Gui.Workbench):
    MenuText = "WAAM"
    ToolTip = "WAAM workflow: WCS, STEP export, planner, mesh prep, NC, and torch viewer"
    Icon = _ICON_PATH if SysPath(_ICON_PATH).is_file() else ""

    def Initialize(self):
        _install_shutdown_hook()
        _append_workbench_ui(self, "ui.commands_light", toolbar_name="WAAM", menu_name="WAAM")

    def GetClassName(self):
        return "Gui::PythonWorkbench"


WaamLiteWorkbench = WaamWorkbench


__all__ = ["WaamWorkbench", "WaamLiteWorkbench"]
