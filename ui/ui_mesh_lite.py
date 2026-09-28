"""Minimal mesh preparation dialog for the WAAM workbench."""

from __future__ import annotations

from pathlib import Path

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from .qt_compat import QtCore, QtWidgets
from ..core.wcs_resolver import resolve_wcs
from ..core.paths import get_output_dir, get_workspace_root
from ..slicing.step_to_waam_dsl import convert_step_to_waam, default_llm_waam_output_path
from ..mesh_export import export_objects_to_obj

_DEFAULT_DIALOG_WIDTH = 700
_DEFAULT_DIALOG_HEIGHT = 500


def _visible_parts():
    doc = App.ActiveDocument
    if doc is None:
        return []
    visible_parts = []
    for obj in doc.Objects:
        try:
            view = obj.ViewObject
            if hasattr(view, "Visibility") and not bool(view.Visibility):
                continue
        except Exception:
            pass
        shape = getattr(obj, "Shape", None)
        if shape is None:
            continue
        try:
            if shape.isNull():
                continue
        except Exception:
            continue
        visible_parts.append(obj)
    return visible_parts


def _default_step_path() -> Path:
    return get_workspace_root() / "input_step" / "part.step"


def _default_obj_path() -> Path:
    return get_output_dir() / "mesh_exports" / "part_visible.obj"


def _parent_window():
    return Gui.getMainWindow() if hasattr(Gui, "getMainWindow") else None


class WaamMeshDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("WAAM - Mesh Prep")
        self.resize(_DEFAULT_DIALOG_WIDTH, 440)
        self.setMinimumSize(_DEFAULT_DIALOG_WIDTH, 440)

        self.selection_label = QtWidgets.QLabel(self)
        self.selection_label.setWordWrap(True)
        refresh_button = QtWidgets.QPushButton("Refresh visible", self)
        refresh_button.clicked.connect(self._refresh_selection)

        selection_group = QtWidgets.QGroupBox("Active view", self)
        selection_layout = QtWidgets.QHBoxLayout(selection_group)
        selection_layout.addWidget(QtWidgets.QLabel("Visible solids:", self))
        selection_layout.addWidget(self.selection_label, 1)
        selection_layout.addWidget(refresh_button)

        self.obj_out_edit = QtWidgets.QLineEdit(str(_default_obj_path()), self)
        obj_out_browse = QtWidgets.QPushButton("Browse…", self)
        obj_out_browse.clicked.connect(self._browse_obj_out)

        self.linear_spin = QtWidgets.QDoubleSpinBox(self)
        self.linear_spin.setDecimals(3)
        self.linear_spin.setRange(0.01, 5.0)
        self.linear_spin.setSingleStep(0.05)
        self.linear_spin.setValue(0.25)

        self.angular_spin = QtWidgets.QDoubleSpinBox(self)
        self.angular_spin.setDecimals(3)
        self.angular_spin.setRange(0.05, 3.14)
        self.angular_spin.setSingleStep(0.05)
        self.angular_spin.setValue(0.349)

        export_group = QtWidgets.QGroupBox("1. Export OBJ", self)
        export_form = QtWidgets.QFormLayout(export_group)
        export_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)

        obj_row = QtWidgets.QHBoxLayout()
        obj_row.addWidget(self.obj_out_edit, 1)
        obj_row.addWidget(obj_out_browse)
        export_form.addRow("OBJ output:", obj_row)
        export_form.addRow("Linear deflection (mm):", self.linear_spin)
        export_form.addRow("Angular deflection (rad):", self.angular_spin)

        self.step_path_edit = QtWidgets.QLineEdit(str(_default_step_path()), self)
        step_browse = QtWidgets.QPushButton("Browse…", self)
        step_browse.clicked.connect(self._browse_step)

        self.llm_out_edit = QtWidgets.QLineEdit(str(default_llm_waam_output_path(str(_default_step_path()))), self)
        llm_browse = QtWidgets.QPushButton("Browse…", self)
        llm_browse.clicked.connect(self._browse_llm_out)

        self.layer_height_spin = QtWidgets.QDoubleSpinBox(self)
        self.layer_height_spin.setDecimals(3)
        self.layer_height_spin.setRange(0.01, 50.0)
        self.layer_height_spin.setSingleStep(0.1)
        self.layer_height_spin.setValue(1.5)

        self.bead_width_spin = QtWidgets.QDoubleSpinBox(self)
        self.bead_width_spin.setDecimals(3)
        self.bead_width_spin.setRange(0.01, 50.0)
        self.bead_width_spin.setSingleStep(0.5)
        self.bead_width_spin.setValue(6.0)

        self.samples_spin = QtWidgets.QSpinBox(self)
        self.samples_spin.setRange(4, 200)
        self.samples_spin.setValue(24)

        self.z_offset_spin = QtWidgets.QDoubleSpinBox(self)
        self.z_offset_spin.setDecimals(3)
        self.z_offset_spin.setRange(-1000.0, 1000.0)
        self.z_offset_spin.setSingleStep(0.1)
        self.z_offset_spin.setValue(0.75)

        self.min_area_spin = QtWidgets.QDoubleSpinBox(self)
        self.min_area_spin.setDecimals(3)
        self.min_area_spin.setRange(0.0, 1e6)
        self.min_area_spin.setSingleStep(1.0)
        self.min_area_spin.setValue(1.0)

        self.close_tol_spin = QtWidgets.QDoubleSpinBox(self)
        self.close_tol_spin.setDecimals(3)
        self.close_tol_spin.setRange(0.0, 100.0)
        self.close_tol_spin.setSingleStep(0.1)
        self.close_tol_spin.setValue(0.5)

        dsl_group = QtWidgets.QGroupBox("2. Generate LLM WAAM", self)
        dsl_form = QtWidgets.QFormLayout(dsl_group)
        dsl_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)

        step_row = QtWidgets.QHBoxLayout()
        step_row.addWidget(self.step_path_edit, 1)
        step_row.addWidget(step_browse)
        dsl_form.addRow("STEP input:", step_row)

        llm_row = QtWidgets.QHBoxLayout()
        llm_row.addWidget(self.llm_out_edit, 1)
        llm_row.addWidget(llm_browse)
        dsl_form.addRow("LLM WAAM output:", llm_row)
        dsl_form.addRow("Layer height (mm):", self.layer_height_spin)
        dsl_form.addRow("Bead width (mm):", self.bead_width_spin)
        dsl_form.addRow("Samples per edge:", self.samples_spin)
        dsl_form.addRow("Z offset (mm):", self.z_offset_spin)
        dsl_form.addRow("Min contour area:", self.min_area_spin)
        dsl_form.addRow("Close tolerance (mm):", self.close_tol_spin)

        self.status_label = QtWidgets.QLabel("Ready.", self)
        self.status_label.setWordWrap(True)

        export_button = QtWidgets.QPushButton("Export OBJ", self)
        export_button.clicked.connect(self._export_obj)
        generate_button = QtWidgets.QPushButton("Generate LLM WAAM", self)
        generate_button.clicked.connect(self._generate_llm_waam)

        buttons_row = QtWidgets.QHBoxLayout()
        buttons_row.addStretch(1)
        buttons_row.addWidget(export_button)
        buttons_row.addWidget(generate_button)

        close_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, self)
        close_box.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(selection_group)
        layout.addWidget(export_group)
        layout.addWidget(dsl_group)
        layout.addLayout(buttons_row)
        layout.addWidget(self.status_label)
        layout.addWidget(close_box)

        self._refresh_selection()

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)
        try:
            App.Console.PrintMessage(f"WAAM: {message}\n")
        except Exception:
            pass

    def _refresh_selection(self) -> None:
        visible_parts = _visible_parts()
        if not visible_parts:
            self.selection_label.setText("No visible solids in the active view.")
            return
        labels = [
            getattr(obj, "Label", None) or getattr(obj, "Name", None) or "Unnamed"
            for obj in visible_parts[:3]
        ]
        extra = f" (+{len(visible_parts) - 3} more)" if len(visible_parts) > 3 else ""
        self.selection_label.setText(f"{len(visible_parts)} visible solid(s): " + ", ".join(labels) + extra)

    def _browse_obj_out(self) -> None:
        start = self.obj_out_edit.text().strip() or str(_default_obj_path())
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select OBJ output",
            start,
            "OBJ files (*.obj);;All files (*.*)",
        )
        if file_path:
            self.obj_out_edit.setText(file_path)

    def _browse_step(self) -> None:
        start = self.step_path_edit.text().strip() or str(_default_step_path().parent)
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select STEP file",
            start,
            "STEP files (*.stp *.step);;All files (*.*)",
        )
        if not file_path:
            return
        self.step_path_edit.setText(file_path)
        self.llm_out_edit.setText(str(default_llm_waam_output_path(file_path)))

    def _browse_llm_out(self) -> None:
        step_hint = self.step_path_edit.text().strip()
        default_path = default_llm_waam_output_path(step_hint or str(_default_step_path()))
        start = self.llm_out_edit.text().strip() or str(default_path)
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select LLM WAAM output",
            start,
            "LLM WAAM (*.llm_waam *.txt);;All files (*.*)",
        )
        if file_path:
            self.llm_out_edit.setText(file_path)

    def _export_obj(self) -> Path | None:
        if App.ActiveDocument is None:
            QtWidgets.QMessageBox.information(self, "WAAM", "Open a document before exporting OBJ.")
            return None
        visible_parts = _visible_parts()
        if not visible_parts:
            QtWidgets.QMessageBox.information(self, "WAAM", "No visible solids to export.")
            return None

        target = Path(self.obj_out_edit.text().strip() or _default_obj_path())
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            mesh_path = export_objects_to_obj(
                visible_parts,
                out_dir=target.parent,
                filename=target.name,
                mesh_linear_deflection=self.linear_spin.value(),
                mesh_angular_deflection=self.angular_spin.value(),
            )
        except Exception as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.critical(self, "WAAM", f"OBJ export failed:\n{exc}")
            self._set_status("OBJ export failed.")
            return None
        QtWidgets.QApplication.restoreOverrideCursor()

        self.obj_out_edit.setText(str(mesh_path))
        self._set_status(f"OBJ exported to {mesh_path}")
        return mesh_path

    def _generate_llm_waam(self) -> Path | None:
        step_file = Path(self.step_path_edit.text().strip() or _default_step_path())
        if not step_file.is_file():
            QtWidgets.QMessageBox.information(self, "WAAM", f"STEP file not found:\n{step_file}")
            return None

        llm_out = Path(self.llm_out_edit.text().strip() or default_llm_waam_output_path(str(step_file)))
        llm_out.parent.mkdir(parents=True, exist_ok=True)

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            wcs_placement = None
            if App.ActiveDocument is not None:
                try:
                    wcs_placement = resolve_wcs(App.ActiveDocument).placement
                except Exception:
                    wcs_placement = None
            convert_step_to_waam(
                step_path=str(step_file),
                out_path=None,
                wcs_placement=wcs_placement,
                layer_height=self.layer_height_spin.value(),
                bead_width=self.bead_width_spin.value(),
                samples_per_edge=self.samples_spin.value(),
                z_offset=self.z_offset_spin.value(),
                min_contour_area=self.min_area_spin.value(),
                max_layers=None,
                stitch_close_tol=self.close_tol_spin.value(),
                emit_llm=True,
                llm_output_path=str(llm_out),
                units="mm",
            )
        except Exception as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.critical(self, "WAAM", f"LLM WAAM generation failed:\n{exc}")
            self._set_status("LLM WAAM generation failed.")
            return None
        QtWidgets.QApplication.restoreOverrideCursor()

        self.llm_out_edit.setText(str(llm_out))
        self._set_status(f"LLM WAAM saved to {llm_out}")
        return llm_out


def show_mesh_dialog():
    dlg = WaamMeshDialog(parent=_parent_window())
    dlg.exec_()
    return dlg


LiteMeshDialog = WaamMeshDialog
show_mesh_lite_dialog = show_mesh_dialog


__all__ = ["WaamMeshDialog", "LiteMeshDialog", "show_mesh_dialog", "show_mesh_lite_dialog"]
