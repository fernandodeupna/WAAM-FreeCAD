"""Shared WAAM pipeline logic used by multiple UI dialogs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

import FreeCAD as App  # type: ignore

from .qt_compat import QtCore, QtWidgets
from .wcs_marker import get_stored_wcs
from ..core.config_loader import resolve_standard_config_path
from ..core.paths import (
    get_features_path,
    get_nc_output_path,
    get_output_dir,
    get_slice_plan_path,
    get_workspace_root,
)
from ..automation.headless_pipeline import _ensure_headless_config, CONFIG_PATH
from ..experimental.base import ExperimentalPlannerContext
from ..experimental.registry import get_experimental_planner, select_experimental_planner
from ..experimental.runtime import derive_part_profile_from_features_path, new_run_id
from ..slicing import write_slice_plan_for_document
from ..slicing.step_to_waam_dsl import default_llm_waam_output_path
from ..waamgen import generate_nc as waamgen_generate


def run_full_pipeline(
    parent_widget: QtWidgets.QWidget,
    status_callback=None,
    config_override=None,
    experimental_planner: Optional[str] = None,
) -> None:
    """Execute the WAAM flow: WCS -> XY slices -> deposition paths -> NC."""

    def set_status(message: str) -> None:
        if status_callback:
            status_callback(message)
        App.Console.PrintMessage(f"WAAM: {message}\n")

    doc = App.ActiveDocument
    if doc is None:
        QtWidgets.QMessageBox.warning(parent_widget, "WAAM", "Open a document before running the workflow.")
        return

    if get_stored_wcs(doc) is None:
        QtWidgets.QMessageBox.warning(
            parent_widget,
            "WAAM",
            "No WAAM WCS found.\n\nSet the WCS before running the pipeline.",
        )
        return

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

    if not visible_parts:
        QtWidgets.QMessageBox.warning(parent_widget, "WAAM", "No visible solids to process.")
        return

    step_dir = get_workspace_root() / "input_step" / "step_files"
    step_dir.mkdir(parents=True, exist_ok=True)
    headless_cfg = _ensure_headless_config(CONFIG_PATH)
    step_filename = str(headless_cfg.get("step_filename") or "part.step").strip() or "part.step"
    step_path = step_dir / step_filename

    try:
        import ImportGui

        ImportGui.export(visible_parts, str(step_path))
        set_status(f"Exported STEP to {step_path}")
    except Exception as exc:
        QtWidgets.QMessageBox.critical(parent_widget, "WAAM", f"Failed to export STEP:\n{exc}")
        return

    QtWidgets.QApplication.processEvents()

    try:
        config_path = resolve_standard_config_path()
    except FileNotFoundError:
        QtWidgets.QMessageBox.warning(parent_widget, "WAAM", "standard_waam.json not found.")
        return

    try:
        with config_path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
    except Exception as exc:
        QtWidgets.QMessageBox.critical(parent_widget, "WAAM", f"Failed to read config:\n{exc}")
        return

    plan_path = get_slice_plan_path(create=True, prefer_existing=False)
    features_path = get_features_path(create=True, prefer_existing=False)
    llm_waam_out = default_llm_waam_output_path(str(step_path))

    try:
        set_status("Creating WCS slice plan...")
        QtWidgets.QApplication.processEvents()
        artifacts = write_slice_plan_for_document(
            doc=doc,
            objects=visible_parts,
            config=config,
            output_path=plan_path,
            config_path=config_path,
            config_override=config_override,
            features_path=features_path,
            llm_waam_output_path=llm_waam_out,
        )
        set_status(
            f"Slice plan ready ({artifacts.layer_count} layers, {artifacts.path_count} paths)"
        )
    except Exception as exc:
        QtWidgets.QMessageBox.critical(parent_widget, "WAAM", f"Slice planning failed:\n{exc}")
        return

    QtWidgets.QApplication.processEvents()

    nc_out = get_nc_output_path(create=True, prefer_existing=False)
    try:
        if experimental_planner is not None:
            set_status("Generating control NC for experimental export...")
        else:
            set_status("Generating baseline NC...")
        QtWidgets.QApplication.processEvents()
        waamgen_generate(
            artifacts.plan_path,
            output_path=nc_out,
            max_iters=1,
            geom_tol=0.8,
            plot=False,
        )
    except Exception as exc:
        QtWidgets.QMessageBox.critical(
            parent_widget,
            "WAAM",
            f"Slice plan was created, but NC generation failed:\n{exc}\n\nPlan saved to:\n{artifacts.plan_path}",
        )
        set_status("Pipeline complete (NC generation failed).")
        return

    if experimental_planner is not None:
        experimental_out = get_nc_output_path(
            filename="waam_baseline_experimental.nc",
            create=True,
            prefer_existing=False,
        )
        try:
            if experimental_out.resolve() != nc_out.resolve():
                shutil.copyfile(nc_out, experimental_out)
            set_status(f"Prepared experimental NC scaffold at {experimental_out.name}")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                parent_widget,
                "WAAM",
                f"Pipeline finished and control NC was generated, but the experimental NC scaffold could not be created:\n{exc}\n\nControl NC saved to:\n{nc_out}",
            )
            set_status("Pipeline complete (experimental scaffold failed).")
            return

        planner = get_experimental_planner(experimental_planner) if experimental_planner else None
        if planner is None:
            planner = select_experimental_planner(
                plan_path=artifacts.plan_path,
                features_path=features_path,
            )

        if planner is None:
            QtWidgets.QMessageBox.warning(
                parent_widget,
                "WAAM",
                f"Pipeline finished, but no experimental planner was available.\n\nPlan saved to:\n{artifacts.plan_path}\n\nControl NC saved to:\n{nc_out}\n\nExperimental NC scaffold saved to:\n{experimental_out}",
            )
            set_status("Pipeline complete (no experimental planner selected).")
            return

        try:
            part_label, part_key, part_family, part_family_signals = derive_part_profile_from_features_path(
                features_path,
                step_path=step_path,
            )
            context = ExperimentalPlannerContext(
                output_dir=get_output_dir(),
                plan_path=artifacts.plan_path,
                features_path=features_path,
                config_override=config_override,
                part_key=part_key,
                part_label=part_label,
                part_family=part_family,
                part_family_signals=part_family_signals,
                step_path=step_path,
                baseline_nc_path=nc_out,
                run_id=new_run_id(),
            )
            exp_nc = Path(planner.export_nc(context))
            if exp_nc.resolve() == nc_out.resolve():
                shutil.copyfile(nc_out, experimental_out)
                exp_nc = experimental_out
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                parent_widget,
                "WAAM",
                f"Pipeline finished and control NC was generated, but experimental NC export failed:\n{exc}\n\nPlan saved to:\n{artifacts.plan_path}\n\nControl NC saved to:\n{nc_out}\n\nExperimental NC scaffold saved to:\n{experimental_out}",
            )
            set_status("Pipeline complete (experimental export failed).")
            return

        set_status(f"Pipeline complete ({planner.label} exported).")
        QtWidgets.QMessageBox.information(
            parent_widget,
            "WAAM",
            f"Pipeline finished.\nPlan saved to:\n{artifacts.plan_path}\n\nControl NC saved to:\n{nc_out}\n\nExperimental NC saved to:\n{exp_nc}\n\nPlanner used: {planner.label}",
        )
        return

    set_status("Pipeline complete.")
    QtWidgets.QMessageBox.information(
        parent_widget,
        "WAAM",
        f"Pipeline finished.\nPlan saved to:\n{artifacts.plan_path}\n\nBaseline NC saved to:\n{nc_out}",
    )
