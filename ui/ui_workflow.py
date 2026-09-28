"""Slice-first planner and NC dialogs for WAAM."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from .qt_compat import QtCore, QtWidgets
from .wcs_marker import get_stored_wcs, get_wcs_label
from ..core.config_loader import resolve_standard_config_path
from ..core.paths import (
    _output_root_for,
    get_features_path,
    get_nc_output_path,
    get_output_dir,
    get_slice_plan_path,
)
from ..slicing import (
    DEFAULT_BEAD_WIDTH_MM,
    DEFAULT_LAYER_HEIGHT_MM,
    DEFAULT_OVERLAP_PCT,
    write_slice_plan_for_document,
)
from ..slicing.step_to_waam_dsl import default_llm_waam_output_path
from ..waamgen.pipeline import generate_nc as waamgen_generate

_DEFAULT_DIALOG_SIZE = (700, 500)


def _parent_window():
    return Gui.getMainWindow() if hasattr(Gui, "getMainWindow") else None


def _visible_parts():
    doc = App.ActiveDocument
    if doc is None:
        return []
    visible_parts = []
    for obj in getattr(doc, "Objects", []):
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


def _artifact_paths(plan_path: Path) -> dict[str, Path]:
    output_root = _output_root_for(plan_path)
    source_step_name = "part.step"
    try:
        payload = _load_json(plan_path)
        meta = payload.get("metadata") if isinstance(payload, Mapping) else None
        work_obj = meta.get("work_obj") if isinstance(meta, Mapping) else None
        if isinstance(work_obj, str) and work_obj.strip():
            source_step_name = f"{Path(work_obj).stem}.step"
    except Exception:
        pass
    llm_waam_path = default_llm_waam_output_path(source_step_name, output_root=output_root)
    return {
        "plan": plan_path,
        "features": get_features_path(output_root, create=False, prefer_existing=False),
        "llm_waam": llm_waam_path,
    }


def _load_json(path: Path) -> Mapping[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, Mapping):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def _has_structured_toolpaths(path: Path) -> bool:
    try:
        payload = _load_json(path)
    except Exception:
        return False
    llm_context = payload.get("llm_context")
    if not isinstance(llm_context, Mapping):
        return False
    structured = llm_context.get("waam_structured") or llm_context.get("toolpath")
    if not isinstance(structured, Mapping):
        return False
    return bool(structured.get("paths")) and bool(structured.get("layers"))


def _strategy_summary(cfg: Mapping[str, object]) -> str:
    process = cfg.get("process") if isinstance(cfg.get("process"), Mapping) else {}
    path_cfg = process.get("path") if isinstance(process.get("path"), Mapping) else {}
    deposition_cfg = process.get("deposition") if isinstance(process.get("deposition"), Mapping) else {}

    deposition_pattern = str(path_cfg.get("deposition_pattern") or "").strip().lower()
    if deposition_pattern not in {"contour_hatch", "hatch_only"}:
        strategy = str(deposition_cfg.get("strategy") or "").strip().lower()
        if strategy in {"slice_first_hatch_only", "hatch_only"}:
            deposition_pattern = "hatch_only"
        else:
            deposition_pattern = "contour_hatch"

    raw_angle_step = path_cfg.get("hatch_angle_step_deg", path_cfg.get("angle_step_deg"))
    try:
        hatch_angle_step_deg = float(raw_angle_step)
    except (TypeError, ValueError):
        hatch_angle_step_deg = 0.0 if deposition_pattern == "hatch_only" else 90.0
    fixed_longitudinal = abs(hatch_angle_step_deg) % 180.0 <= 1e-9

    if deposition_pattern == "hatch_only":
        if fixed_longitudinal:
            return "Per layer: longitudinal clipped passes only, with no contour deposition."
        return "Per layer: clipped parallel hatch passes only, with no contour deposition."
    return "Per layer: outer contours, inner contours, then hatch infill when the region is wide enough."


class _PipelineDialog(QtWidgets.QDialog):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(*_DEFAULT_DIALOG_SIZE)
        self.setMinimumSize(*_DEFAULT_DIALOG_SIZE)
        self.status_label = QtWidgets.QLabel("Ready.", self)
        self.status_label.setWordWrap(True)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)


class SlicePlannerDialog(_PipelineDialog):
    def __init__(self, parent=None) -> None:
        super().__init__("WAAM - Slice Planner", parent=parent)

        hint = QtWidgets.QLabel(
            "Slice the visible solids in the active document using WCS-local XY planes, "
            "then build clipped deposition paths for each layer using the configured WAAM strategy.",
            self,
        )
        hint.setWordWrap(True)

        inputs_group = QtWidgets.QGroupBox("Inputs", self)
        inputs_form = QtWidgets.QFormLayout(inputs_group)
        inputs_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)

        self.doc_label = QtWidgets.QLabel(self)
        self.doc_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.wcs_label = QtWidgets.QLabel(self)
        self.wcs_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        refresh_context = QtWidgets.QPushButton("Refresh", self)
        refresh_context.clicked.connect(self._refresh_context)
        wcs_row = QtWidgets.QHBoxLayout()
        wcs_row.addWidget(self.wcs_label, 1)
        wcs_row.addWidget(refresh_context)

        self.config_edit = QtWidgets.QLineEdit(self)
        self.config_edit.setPlaceholderText("standard_waam.json")
        config_browse = QtWidgets.QPushButton("Browse...", self)
        config_browse.clicked.connect(self._browse_config)
        config_row = QtWidgets.QHBoxLayout()
        config_row.addWidget(self.config_edit, 1)
        config_row.addWidget(config_browse)

        inputs_form.addRow("Active document:", self.doc_label)
        inputs_form.addRow("Current WCS:", wcs_row)
        inputs_form.addRow("Config:", config_row)

        params_group = QtWidgets.QGroupBox("Planning Parameters", self)
        params_form = QtWidgets.QFormLayout(params_group)
        params_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)

        self.layer_height_spin = QtWidgets.QDoubleSpinBox(self)
        self.layer_height_spin.setDecimals(3)
        self.layer_height_spin.setRange(0.05, 20.0)
        self.layer_height_spin.setSingleStep(0.1)
        self.layer_height_spin.setValue(DEFAULT_LAYER_HEIGHT_MM)
        self.layer_height_spin.setSuffix(" mm")

        self.bead_width_spin = QtWidgets.QDoubleSpinBox(self)
        self.bead_width_spin.setDecimals(3)
        self.bead_width_spin.setRange(0.1, 50.0)
        self.bead_width_spin.setSingleStep(0.5)
        self.bead_width_spin.setValue(DEFAULT_BEAD_WIDTH_MM)
        self.bead_width_spin.setSuffix(" mm")

        self.overlap_spin = QtWidgets.QDoubleSpinBox(self)
        self.overlap_spin.setDecimals(1)
        self.overlap_spin.setRange(0.0, 95.0)
        self.overlap_spin.setSingleStep(5.0)
        self.overlap_spin.setValue(DEFAULT_OVERLAP_PCT)
        self.overlap_spin.setSuffix(" %")

        self.strategy_label = QtWidgets.QLabel(
            "Per layer: longitudinal clipped passes only, with no contour deposition.",
            self,
        )
        self.strategy_label.setWordWrap(True)

        params_form.addRow("Layer height:", self.layer_height_spin)
        params_form.addRow("Bead width:", self.bead_width_spin)
        params_form.addRow("Overlap:", self.overlap_spin)
        params_form.addRow("Strategy:", self.strategy_label)

        outputs_group = QtWidgets.QGroupBox("Outputs", self)
        outputs_form = QtWidgets.QFormLayout(outputs_group)
        outputs_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)

        self.plan_edit = QtWidgets.QLineEdit(str(get_slice_plan_path(create=True, prefer_existing=False)), self)
        self.plan_edit.setPlaceholderText("waam_slice_plan.json")
        self.plan_edit.textChanged.connect(self._refresh_artifact_preview)
        plan_browse = QtWidgets.QPushButton("Browse...", self)
        plan_browse.clicked.connect(self._browse_plan)
        plan_row = QtWidgets.QHBoxLayout()
        plan_row.addWidget(self.plan_edit, 1)
        plan_row.addWidget(plan_browse)

        self.features_label = QtWidgets.QLabel(self)
        self.features_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.llm_waam_label = QtWidgets.QLabel(self)
        self.llm_waam_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        outputs_form.addRow("Slice plan JSON:", plan_row)
        outputs_form.addRow("features.json:", self.features_label)
        outputs_form.addRow("LLM WAAM:", self.llm_waam_label)

        run_button = QtWidgets.QPushButton("Create Slice Plan", self)
        run_button.clicked.connect(self._run_planner)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(inputs_group)
        layout.addWidget(params_group)
        layout.addWidget(outputs_group)
        layout.addWidget(run_button, alignment=QtCore.Qt.AlignRight)
        layout.addWidget(self.status_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_default_config_path()
        self._load_config_defaults()
        self._refresh_context()
        self._refresh_artifact_preview()

    def _load_default_config_path(self) -> None:
        try:
            self.config_edit.setText(str(resolve_standard_config_path()))
        except FileNotFoundError:
            self.config_edit.clear()

    def _load_config_defaults(self) -> None:
        cfg_text = self.config_edit.text().strip()
        if not cfg_text:
            return
        cfg_path = Path(cfg_text)
        if not cfg_path.is_file():
            return
        try:
            cfg = _load_json(cfg_path)
        except Exception:
            return
        self.strategy_label.setText(_strategy_summary(cfg))
        dep = ((cfg.get("process") or {}).get("deposition") or {}) if isinstance(cfg.get("process"), Mapping) else {}
        if isinstance(dep, Mapping):
            if isinstance(dep.get("layer_height_mm"), (int, float)):
                self.layer_height_spin.setValue(float(dep["layer_height_mm"]))
            if isinstance(dep.get("bead_width_mm"), (int, float)):
                self.bead_width_spin.setValue(float(dep["bead_width_mm"]))
            if isinstance(dep.get("overlap_pct"), (int, float)):
                self.overlap_spin.setValue(float(dep["overlap_pct"]))

    def _refresh_context(self) -> None:
        doc = App.ActiveDocument
        doc_text = getattr(doc, "Label", None) or getattr(doc, "Name", None) or "[None]"
        self.doc_label.setText(str(doc_text))
        wcs_text = "[Not set]"
        if doc is not None and get_stored_wcs(doc) is not None:
            wcs_text = get_wcs_label(doc) or "G54"
        self.wcs_label.setText(wcs_text)

    def _refresh_artifact_preview(self, *_args) -> None:
        raw = self.plan_edit.text().strip()
        try:
            plan_path = Path(raw).expanduser() if raw else get_slice_plan_path(create=True, prefer_existing=False)
        except Exception:
            plan_path = get_slice_plan_path(create=True, prefer_existing=False)
        artifacts = _artifact_paths(plan_path)
        self.features_label.setText(str(artifacts["features"]))
        self.llm_waam_label.setText(str(artifacts["llm_waam"]))

    def _browse_config(self) -> None:
        start = self.config_edit.text().strip() or str(get_output_dir() / "config")
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select standard_waam.json",
            start,
            "JSON files (*.json);;All files (*.*)",
        )
        if not file_path:
            return
        self.config_edit.setText(file_path)
        self._load_config_defaults()

    def _browse_plan(self) -> None:
        start = self.plan_edit.text().strip() or str(get_slice_plan_path(create=True, prefer_existing=False))
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select slice plan output",
            start,
            "JSON files (*.json);;All files (*.*)",
        )
        if file_path:
            self.plan_edit.setText(file_path)

    def _run_planner(self) -> None:
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.warning(self, "WAAM", "Open a document before planning.")
            return
        if get_stored_wcs(doc) is None:
            QtWidgets.QMessageBox.warning(
                self,
                "WAAM",
                "No WAAM WCS found.\n\nSet the WCS before creating the slice plan.",
            )
            return

        visible_parts = _visible_parts()
        if not visible_parts:
            QtWidgets.QMessageBox.warning(self, "WAAM", "No visible solids were found to slice.")
            return

        cfg_text = self.config_edit.text().strip()
        if not cfg_text:
            QtWidgets.QMessageBox.warning(self, "WAAM", "Select standard_waam.json first.")
            return
        cfg_path = Path(cfg_text)
        if not cfg_path.is_file():
            QtWidgets.QMessageBox.warning(self, "WAAM", f"WAAM config not found:\n{cfg_path}")
            return

        plan_text = self.plan_edit.text().strip()
        plan_path = Path(plan_text) if plan_text else get_slice_plan_path(create=True, prefer_existing=False)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_paths = _artifact_paths(plan_path)

        try:
            cfg = _load_json(cfg_path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Failed to read config:\n{exc}")
            return

        config_override = {
            "process": {
                "auto_scale": {"enabled": False},
                "deposition": {
                    "layer_height_mm": float(self.layer_height_spin.value()),
                    "bead_width_mm": float(self.bead_width_spin.value()),
                    "overlap_pct": float(self.overlap_spin.value()),
                },
            }
        }

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            artifacts = write_slice_plan_for_document(
                doc=doc,
                objects=visible_parts,
                config=cfg,
                output_path=artifact_paths["plan"],
                config_path=cfg_path,
                config_override=config_override,
                features_path=artifact_paths["features"],
                llm_waam_output_path=artifact_paths["llm_waam"],
                layer_height_mm=float(self.layer_height_spin.value()),
                bead_width_mm=float(self.bead_width_spin.value()),
                overlap_pct=float(self.overlap_spin.value()),
            )
            doc.recompute()
        except Exception as exc:  # pragma: no cover - GUI reporting
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Slice planning failed:\n{exc}")
            self._set_status("Slice planning failed.")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self._refresh_context()
        self._refresh_artifact_preview()
        message = (
            f"Slice plan saved to:\n{artifacts.plan_path}\n\n"
            f"features.json saved to:\n{artifacts.features_path}\n\n"
            f"LLM WAAM saved to:\n{artifacts.llm_waam_path}\n\n"
            f"Layers: {artifacts.layer_count}\n"
            f"Paths: {artifacts.path_count}"
        )
        App.Console.PrintMessage(f"WAAM: Slice plan saved to {artifacts.plan_path}\n")
        QtWidgets.QMessageBox.information(self, "WAAM", message)
        self._set_status(
            f"Slice plan saved to {artifacts.plan_path} ({artifacts.layer_count} layers, {artifacts.path_count} paths)"
        )


class GenerateNcDialog(_PipelineDialog):
    def __init__(self, parent=None) -> None:
        super().__init__("WAAM - NC Creator", parent=parent)

        out_dir = get_output_dir()
        self.plan_edit = QtWidgets.QLineEdit(self._default_plan_path(out_dir), self)
        self.plan_edit.setPlaceholderText("waam_slice_plan.json")
        self.nc_edit = QtWidgets.QLineEdit(str(get_nc_output_path(out_dir, create=True, prefer_existing=False)), self)
        self.nc_edit.setPlaceholderText("waam_baseline.nc")

        self.max_iters_spin = QtWidgets.QSpinBox(self)
        self.max_iters_spin.setRange(1, 10)
        self.max_iters_spin.setValue(1)
        self.max_iters_spin.setToolTip("Maximum waamgen iterations before giving up.")

        self.geom_tol_spin = QtWidgets.QDoubleSpinBox(self)
        self.geom_tol_spin.setDecimals(2)
        self.geom_tol_spin.setRange(0.1, 5.0)
        self.geom_tol_spin.setSingleStep(0.1)
        self.geom_tol_spin.setValue(0.8)
        self.geom_tol_spin.setSuffix(" mm")
        self.geom_tol_spin.setToolTip("Geometry tolerance used for validation and retries.")

        self.chunk_dir_edit = QtWidgets.QLineEdit(str(self._default_chunk_dir()), self)
        self.chunk_dir_edit.setPlaceholderText("chunks")

        hint = QtWidgets.QLabel(
            "Generate NC directly from a slice plan JSON that already contains WCS-aligned structured toolpaths.",
            self,
        )
        hint.setWordWrap(True)

        plan_browse = QtWidgets.QPushButton("Browse...", self)
        plan_browse.clicked.connect(self._browse_plan)
        nc_browse = QtWidgets.QPushButton("Browse...", self)
        nc_browse.clicked.connect(self._browse_nc)
        chunk_browse = QtWidgets.QPushButton("Browse...", self)
        chunk_browse.clicked.connect(self._browse_chunks)

        row_plan = QtWidgets.QHBoxLayout()
        row_plan.addWidget(self.plan_edit, 1)
        row_plan.addWidget(plan_browse)

        row_nc = QtWidgets.QHBoxLayout()
        row_nc.addWidget(self.nc_edit, 1)
        row_nc.addWidget(nc_browse)

        row_chunk = QtWidgets.QHBoxLayout()
        row_chunk.addWidget(self.chunk_dir_edit, 1)
        row_chunk.addWidget(chunk_browse)

        paths_group = QtWidgets.QGroupBox("Paths", self)
        paths_form = QtWidgets.QFormLayout(paths_group)
        paths_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        paths_form.addRow("Slice plan JSON:", row_plan)
        paths_form.addRow("Baseline NC output:", row_nc)
        paths_form.addRow("Chunk output dir:", row_chunk)

        options_group = QtWidgets.QGroupBox("Options", self)
        options_form = QtWidgets.QFormLayout(options_group)
        options_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        options_form.addRow("Max iterations:", self.max_iters_spin)
        options_form.addRow("Geometry tolerance:", self.geom_tol_spin)

        run_button = QtWidgets.QPushButton("Generate NC", self)
        run_button.clicked.connect(self._run_nc)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(paths_group)
        layout.addWidget(options_group)
        layout.addWidget(run_button, alignment=QtCore.Qt.AlignRight)
        layout.addWidget(self.status_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_plan(self) -> None:
        start = self.plan_edit.text().strip() or str(get_output_dir())
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select slice plan JSON",
            start,
            "JSON files (*.json);;All files (*.*)",
        )
        if file_path:
            self.plan_edit.setText(file_path)

    def _browse_nc(self) -> None:
        start = self.nc_edit.text().strip() or str(get_nc_output_path(create=True, prefer_existing=False))
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select NC output file",
            start,
            "NC files (*.nc *.tap *.gcode);;All files (*.*)",
        )
        if file_path:
            self.nc_edit.setText(file_path)

    def _browse_chunks(self) -> None:
        start = self.chunk_dir_edit.text().strip() or str(self._default_chunk_dir())
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select chunk output directory",
            start,
        )
        if dir_path:
            self.chunk_dir_edit.setText(dir_path)

    def _default_plan_path(self, out_dir: Path) -> str:
        return str(get_slice_plan_path(out_dir, create=True, prefer_existing=False))

    def _default_chunk_dir(self) -> Path:
        target_nc = Path(self.nc_edit.text().strip() or get_nc_output_path(create=True, prefer_existing=False))
        return target_nc.parent / "chunks"

    def _run_nc(self) -> None:
        plan_path = Path(self.plan_edit.text().strip())
        if not plan_path.is_file():
            QtWidgets.QMessageBox.warning(
                self,
                "WAAM",
                f"Slice plan JSON not found:\n{plan_path}",
            )
            return
        if not _has_structured_toolpaths(plan_path):
            QtWidgets.QMessageBox.warning(
                self,
                "WAAM",
                "The selected JSON does not contain structured slice toolpaths.\n\n"
                "Create the slice plan first, then retry NC generation.",
            )
            self._set_status("NC generation cancelled: slice plan missing structured toolpaths.")
            return

        target_nc = Path(self.nc_edit.text().strip())
        target_nc.parent.mkdir(parents=True, exist_ok=True)

        chunk_dir = self.chunk_dir_edit.text().strip() or None
        if chunk_dir:
            chunk_dir = Path(chunk_dir)
            chunk_dir.mkdir(parents=True, exist_ok=True)

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            output_path = waamgen_generate(
                plan_path,
                output_path=target_nc,
                max_iters=int(self.max_iters_spin.value()),
                geom_tol=float(self.geom_tol_spin.value()),
                attempt_dir=chunk_dir,
                plot=False,
            )
        except FileNotFoundError:
            QtWidgets.QMessageBox.warning(self, "WAAM", f"Slice plan missing:\n{plan_path}")
            self._set_status("NC generation failed.")
            return
        except Exception as exc:  # pragma: no cover - GUI reporting
            QtWidgets.QMessageBox.critical(self, "WAAM", f"Unexpected NC error:\n{exc}")
            self._set_status("NC generation failed.")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        App.Console.PrintMessage(f"WAAM: NC saved to {output_path}\n")
        QtWidgets.QMessageBox.information(self, "WAAM", f"NC saved to:\n{output_path}")
        self._set_status(f"NC saved to {output_path}")


def show_workflow_dialog(parent=None):
    dlg = SlicePlannerDialog(parent=parent if parent is not None else _parent_window())
    dlg.exec_()


def show_generate_nc_dialog(parent=None):
    dlg = GenerateNcDialog(parent=parent if parent is not None else _parent_window())
    dlg.exec_()


__all__ = [
    "show_workflow_dialog",
    "show_generate_nc_dialog",
]
