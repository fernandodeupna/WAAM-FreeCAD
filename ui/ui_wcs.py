import FreeCAD as App, FreeCADGui as Gui
from .qt_compat import QtCore, QtWidgets
from ..core.path_compat import ensure_setup_sheet_placement
from ..core.wcs_resolver import resolve_wcs
from .wcs_marker import ensure_tool_wcs_marker, get_stored_wcs, get_wcs_label
from .pipeline_runner import run_full_pipeline
import json
from pathlib import Path
from ..core.config_loader import resolve_standard_config_path
from ..core.paths import get_output_dir

_DEFAULT_DIALOG_SIZE = (700, 500)

def get_first_solid():
    doc = App.ActiveDocument
    if doc is None:
        return None
    for o in doc.Objects:
        if hasattr(o, 'Shape') and o.Shape and o.Shape.Solids:
            return o
    return None

class SelectionObserver:
    def __init__(self, panel): self.panel = panel
    def addSelection(self, doc, obj, sub, pnt):
        v = App.Vector(*pnt)
        self.panel.gotPoint(v)

class WcsPanel(QtWidgets.QWidget):
    def __init__(self, *, compact: bool = False):
        super().__init__()
        self._compact = bool(compact)
        lay = QtWidgets.QVBoxLayout(self)
        self.info = QtWidgets.QLabel("Set the WAAM work coordinate system (WCS).", self)
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        default_label = "G54"
        doc = App.ActiveDocument
        if doc:
            existing = get_wcs_label(doc)
            if existing:
                default_label = existing
        form = QtWidgets.QFormLayout()
        self.label_edit = QtWidgets.QLineEdit(default_label, self)
        form.addRow("WCS label:", self.label_edit)
        lay.addLayout(form)

        tabs = QtWidgets.QTabWidget(self)

        presets_tab = QtWidgets.QWidget(self)
        presets_layout = QtWidgets.QVBoxLayout(presets_tab)
        presets_group = QtWidgets.QGroupBox("Quick presets", presets_tab)
        presets_group_layout = QtWidgets.QVBoxLayout(presets_group)
        b1 = QtWidgets.QPushButton("Top-&Front-Left of Part", presets_tab)
        b1.setToolTip("Origin at the minimum X/Y/Z corner of the part.")
        b1.clicked.connect(self.use_tfl)
        presets_group_layout.addWidget(b1)
        b2 = QtWidgets.QPushButton("Top-Center of Part", presets_tab)
        b2.setToolTip("Origin at the center of X/Y, minimum Z.")
        b2.clicked.connect(self.use_tc)
        presets_group_layout.addWidget(b2)
        presets_layout.addWidget(presets_group)
        presets_layout.addStretch(1)
        tabs.addTab(presets_tab, "Pre&sets")

        custom_tab = QtWidgets.QWidget(self)
        custom_layout = QtWidgets.QVBoxLayout(custom_tab)
        custom_group = QtWidgets.QGroupBox("Custom placement", custom_tab)
        custom_group_layout = QtWidgets.QVBoxLayout(custom_group)
        hint = QtWidgets.QLabel(
            "Pick 3 vertices in order: Origin, +X, +Y. Or pick +X, +Y, +Z to keep the current WCS origin.",
            custom_tab,
        )
        hint.setWordWrap(True)
        custom_group_layout.addWidget(hint)
        b3 = QtWidgets.QPushButton("Pick 3 Points", custom_tab)
        b3.setToolTip("Select vertices in order: origin, +X, +Y.")
        b3.clicked.connect(self.pick3)
        custom_group_layout.addWidget(b3)
        b3b = QtWidgets.QPushButton("Pick 3 Vertices (+X, +Y, +Z)", custom_tab)
        b3b.setToolTip("Select vertices in order: +X, +Y, +Z (origin stays at current WCS).")
        b3b.clicked.connect(self.pick3_vertices)
        custom_group_layout.addWidget(b3b)

        custom_layout.addWidget(custom_group)

        adjust_group = QtWidgets.QGroupBox("Adjust axes (rotate about axis)", custom_tab)
        adjust_layout = QtWidgets.QVBoxLayout(adjust_group)
        self._add_axis_rotation_row(adjust_layout, "X")
        self._add_axis_rotation_row(adjust_layout, "Y")
        self._add_axis_rotation_row(adjust_layout, "Z")
        custom_layout.addWidget(adjust_group)
        custom_layout.addStretch(1)
        tabs.addTab(custom_tab, "Custom WCS")

        # --- Initial Parameters Tab ---
        # Keep this tab scrollable because the stacked forms can exceed the
        # default dialog height on some platforms/font scales.
        params_scroll = QtWidgets.QScrollArea(self)
        params_scroll.setWidgetResizable(True)
        params_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        params_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        params_tab = QtWidgets.QWidget(params_scroll)
        params_layout = QtWidgets.QVBoxLayout(params_tab)
        params_layout.setSizeConstraint(QtWidgets.QLayout.SetMinAndMaxSize)
        
        # 1. Bead Geometry Group
        geo_group = QtWidgets.QGroupBox("Bead Geometry and Process", params_tab)
        geo_layout = QtWidgets.QFormLayout(geo_group)
        geo_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)

        self.wire_edit = QtWidgets.QLineEdit("ER70S-6", geo_group)
        geo_layout.addRow("Wire Material:", self.wire_edit)

        self.wire_dia_spin = QtWidgets.QDoubleSpinBox(geo_group)
        self.wire_dia_spin.setRange(0.1, 5.0)
        self.wire_dia_spin.setValue(1.2)
        self.wire_dia_spin.setSuffix(" mm")
        geo_layout.addRow("Wire Diameter:", self.wire_dia_spin)

        self.bead_width_spin = QtWidgets.QDoubleSpinBox(geo_group)
        self.bead_width_spin.setRange(1.0, 30.0)
        self.bead_width_spin.setValue(6.0)
        self.bead_width_spin.setSuffix(" mm")
        geo_layout.addRow("Bead Width:", self.bead_width_spin)

        self.overlap_spin = QtWidgets.QDoubleSpinBox(geo_group)
        self.overlap_spin.setRange(0.0, 100.0)
        self.overlap_spin.setValue(40.0)
        self.overlap_spin.setSuffix(" %")
        geo_layout.addRow("Overlap:", self.overlap_spin)

        self.layer_height_spin = QtWidgets.QDoubleSpinBox(geo_group)
        self.layer_height_spin.setRange(0.1, 10.0)
        self.layer_height_spin.setValue(1.5)
        self.layer_height_spin.setSuffix(" mm")
        geo_layout.addRow("Layer Height:", self.layer_height_spin)

        self.travel_speed_spin = QtWidgets.QDoubleSpinBox(geo_group)
        self.travel_speed_spin.setRange(10.0, 5000.0)
        self.travel_speed_spin.setValue(320.0)
        self.travel_speed_spin.setSuffix(" mm/min")
        geo_layout.addRow("Travel Speed:", self.travel_speed_spin)

        self.wfs_spin = QtWidgets.QDoubleSpinBox(geo_group)
        self.wfs_spin.setRange(100.0, 20000.0)
        self.wfs_spin.setValue(3000.0)
        self.wfs_spin.setSuffix(" mm/min")
        geo_layout.addRow("Wire Feed Speed:", self.wfs_spin)

        self.voltage_spin = QtWidgets.QDoubleSpinBox(geo_group)
        self.voltage_spin.setRange(0.0, 50.0)
        self.voltage_spin.setValue(0.0)
        self.voltage_spin.setSuffix(" V")
        geo_layout.addRow("Voltage:", self.voltage_spin)

        self.current_spin = QtWidgets.QDoubleSpinBox(geo_group)
        self.current_spin.setRange(0.0, 500.0)
        self.current_spin.setValue(0.0)
        self.current_spin.setSuffix(" A")
        geo_layout.addRow("Current:", self.current_spin)

        self.torch_config_edit = QtWidgets.QLineEdit("Vertical", geo_group)
        self.torch_config_edit.setPlaceholderText("Angle/Position description")
        geo_layout.addRow("Torch Config:", self.torch_config_edit)
        
        params_layout.addWidget(geo_group)

        # 2. Thermomechanical Group
        thermo_group = QtWidgets.QGroupBox("Thermomechanical", params_tab)
        thermo_layout = QtWidgets.QFormLayout(thermo_group)
        thermo_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)

        self.heat_input_disp = QtWidgets.QLineEdit(thermo_group)
        self.heat_input_disp.setReadOnly(True)
        self.heat_input_disp.setPlaceholderText("calc: V * I * 60 / TS")
        thermo_layout.addRow("Heat Input (J/mm):", self.heat_input_disp)

        self.interpass_spin = QtWidgets.QDoubleSpinBox(thermo_group)
        self.interpass_spin.setRange(0.0, 1000.0)
        self.interpass_spin.setValue(150.0)
        self.interpass_spin.setSuffix(" °C")
        thermo_layout.addRow("Max Interpass Temp:", self.interpass_spin)

        self.substrate_edit = QtWidgets.QLineEdit("Mild Steel S355", thermo_group)
        thermo_layout.addRow("Substrate details:", self.substrate_edit)

        self.gas_edit = QtWidgets.QLineEdit("Ar + 2% CO2", thermo_group)
        thermo_layout.addRow("Shielding Gas:", self.gas_edit)

        params_layout.addWidget(thermo_group)

        self.auto_scale_cb = QtWidgets.QCheckBox("Auto-scale parameters based on geometry", params_tab)
        self.auto_scale_cb.setChecked(True)
        params_layout.addWidget(self.auto_scale_cb)

        params_layout.addStretch(1)
        params_scroll.setWidget(params_tab)
        tabs.addTab(params_scroll, "Initial Parameters")

        # --- New Pipeline Tab ---
        pipeline_tab = QtWidgets.QWidget(self)
        pipeline_layout = QtWidgets.QVBoxLayout(pipeline_tab)
        
        pipe_group = QtWidgets.QGroupBox("Run WAAM Pipeline", pipeline_tab)
        pipe_layout = QtWidgets.QVBoxLayout(pipe_group)
        
        pipeline_hint = QtWidgets.QLabel(
            "Execute the full WAAM workflow: export STEP, slice the part in WCS XY layers, "
            "build deposition paths, and generate NC.\n"
            "Ensure you have set a valid WAAM WCS in the other tabs first.",
            pipeline_tab
        )
        pipeline_hint.setWordWrap(True)
        pipe_layout.addWidget(pipeline_hint)
        
        pipeline_btn = QtWidgets.QPushButton("Run F&ull Pipeline", pipeline_tab)
        pipeline_btn.setToolTip("Export STEP -> WCS slice plan -> deposition paths -> NC (using current WCS/settings)")
        pipeline_btn.setStyleSheet("font-weight: bold; color: blue; padding: 10px;")
        
        def _on_run_pipeline():
            overrides = {
                "auto_scale": {"enabled": self.auto_scale_cb.isChecked()},
                "material": {
                    "substrate": self.substrate_edit.text(),
                    "wire": self.wire_edit.text(),
                    "gas": self.gas_edit.text()
                },
                "process": {
                    "deposition": {
                        "bead_width_mm": self.bead_width_spin.value(),
                        "overlap_pct": self.overlap_spin.value(),
                        "wire_diameter_mm": self.wire_dia_spin.value(),
                        "layer_height_mm": self.layer_height_spin.value(),
                        "travel_speed_mm_min": self.travel_speed_spin.value(),
                        "wire_feed_mm_min": self.wfs_spin.value(),
                        "voltage_v": self.voltage_spin.value(),
                        "current_a": self.current_spin.value(),
                        "torch_config": self.torch_config_edit.text()
                    },
                    "thermal": {
                        "max_interpass_temp_c": self.interpass_spin.value()
                    }
                }
            }
            run_full_pipeline(self, config_override=overrides)

        def _on_run_pipeline_experimental_nc():
            overrides = {
                "auto_scale": {"enabled": self.auto_scale_cb.isChecked()},
                "material": {
                    "substrate": self.substrate_edit.text(),
                    "wire": self.wire_edit.text(),
                    "gas": self.gas_edit.text()
                },
                "process": {
                    "deposition": {
                        "bead_width_mm": self.bead_width_spin.value(),
                        "overlap_pct": self.overlap_spin.value(),
                        "wire_diameter_mm": self.wire_dia_spin.value(),
                        "layer_height_mm": self.layer_height_spin.value(),
                        "travel_speed_mm_min": self.travel_speed_spin.value(),
                        "wire_feed_mm_min": self.wfs_spin.value(),
                        "voltage_v": self.voltage_spin.value(),
                        "current_a": self.current_spin.value(),
                        "torch_config": self.torch_config_edit.text()
                    },
                    "thermal": {
                        "max_interpass_temp_c": self.interpass_spin.value()
                    }
                }
            }
            run_full_pipeline(self, config_override=overrides, experimental_planner="")

        pipeline_btn.clicked.connect(_on_run_pipeline)
        pipe_layout.addWidget(pipeline_btn)

        pipeline_exp_btn = QtWidgets.QPushButton("Run Pipeline + Experimental NC", self)
        pipeline_exp_btn.setToolTip("Run the slice-first WAAM pipeline, regenerate waam_baseline.nc as the control output, then export waam_baseline_experimental.nc from the selected per-part experimental runtime workspace under Waam_tech/experimental/runtime/parts/. The runtime/active/ folder is kept only as a disposable mirror of the current workspace.")
        pipeline_exp_btn.clicked.connect(_on_run_pipeline_experimental_nc)
        pipe_layout.addWidget(pipeline_exp_btn)
        
        pipeline_layout.addWidget(pipe_group)
        pipeline_layout.addStretch(1)
        tabs.addTab(pipeline_tab, "Run &Pipeline")

        lay.addWidget(tabs)

        self.points = []
        self.sel_obs = None
        self.pick_mode = None

        # Load initial values from config
        self._load_config_values()

        # Connect signals
        self.voltage_spin.valueChanged.connect(self._update_heat_input)
        self.current_spin.valueChanged.connect(self._update_heat_input)
        self.travel_speed_spin.valueChanged.connect(self._update_heat_input)
        
        # Save signals
        self.auto_scale_cb.toggled.connect(self._save_config_values)
        self.substrate_edit.textChanged.connect(self._save_config_values)
        self.wire_edit.textChanged.connect(self._save_config_values)
        self.gas_edit.textChanged.connect(self._save_config_values)
        self.torch_config_edit.textChanged.connect(self._save_config_values)
        self.bead_width_spin.valueChanged.connect(self._save_config_values)
        self.overlap_spin.valueChanged.connect(self._save_config_values)
        self.wire_dia_spin.valueChanged.connect(self._save_config_values)
        self.layer_height_spin.valueChanged.connect(self._save_config_values)
        self.travel_speed_spin.valueChanged.connect(self._save_config_values)
        self.wfs_spin.valueChanged.connect(self._save_config_values)
        self.voltage_spin.valueChanged.connect(self._save_config_values)
        self.current_spin.valueChanged.connect(self._save_config_values)
        self.interpass_spin.valueChanged.connect(self._save_config_values)

        # Trigger initial calc
        self._update_heat_input()

    def _update_heat_input(self):
        v = self.voltage_spin.value()
        i = self.current_spin.value()
        ts = self.travel_speed_spin.value()
        if ts > 0:
            hi = (v * i * 60.0) / ts
            self.heat_input_disp.setText(f"{hi:.2f}")
        else:
            self.heat_input_disp.setText("---")

    def _get_config_path(self) -> Path:
        try:
            return resolve_standard_config_path()
        except Exception:
            return get_output_dir() / "config" / "standard_waam.json"

    def _load_config_values(self):
        path = self._get_config_path()
        if not path.is_file():
            return
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Substrate/Wire
            mat = data.get("material", {})
            if "substrate" in mat: self.substrate_edit.setText(str(mat["substrate"]))
            if "wire" in mat: self.wire_edit.setText(str(mat["wire"]))
            if "gas" in mat: self.gas_edit.setText(str(mat["gas"]))

            # Process
            proc = data.get("process", {})
            
            # Auto-scale
            auto = proc.get("auto_scale", {})
            if "enabled" in auto: self.auto_scale_cb.setChecked(bool(auto["enabled"]))

            # Deposition
            dep = proc.get("deposition", {})
            if "bead_width_mm" in dep: self.bead_width_spin.setValue(float(dep["bead_width_mm"]))
            if "overlap_pct" in dep: self.overlap_spin.setValue(float(dep["overlap_pct"]))
            if "wire_diameter_mm" in dep: self.wire_dia_spin.setValue(float(dep["wire_diameter_mm"]))
            if "layer_height_mm" in dep: self.layer_height_spin.setValue(float(dep["layer_height_mm"]))
            if "travel_speed_mm_min" in dep: self.travel_speed_spin.setValue(float(dep["travel_speed_mm_min"]))
            if "wire_feed_mm_min" in dep: self.wfs_spin.setValue(float(dep["wire_feed_mm_min"]))
            if "voltage_v" in dep: self.voltage_spin.setValue(float(dep["voltage_v"]))
            if "current_a" in dep: self.current_spin.setValue(float(dep["current_a"]))
            if "torch_config" in dep: self.torch_config_edit.setText(str(dep["torch_config"]))

            # Thermal
            therm = proc.get("thermal", {})
            if "max_interpass_temp_c" in therm: self.interpass_spin.setValue(float(therm["max_interpass_temp_c"]))

        except Exception as e:
            App.Console.PrintError(f"WAAM: Failed to load config: {e}\n")

    def _save_config_values(self):
        path = self._get_config_path()
        data = {}
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        
        # Ensure structure
        if "material" not in data: data["material"] = {}
        if "process" not in data: data["process"] = {}
        if "deposition" not in data["process"]: data["process"]["deposition"] = {}
        if "thermal" not in data["process"]: data["process"]["thermal"] = {}
        if "auto_scale" not in data["process"]: data["process"]["auto_scale"] = {}

        # Update values
        data["material"]["substrate"] = self.substrate_edit.text()
        data["material"]["wire"] = self.wire_edit.text()
        data["material"]["gas"] = self.gas_edit.text()
        
        data["process"]["auto_scale"]["enabled"] = self.auto_scale_cb.isChecked()
        
        dep = data["process"]["deposition"]
        dep["bead_width_mm"] = self.bead_width_spin.value()
        dep["overlap_pct"] = self.overlap_spin.value()
        dep["wire_diameter_mm"] = self.wire_dia_spin.value()
        dep["layer_height_mm"] = self.layer_height_spin.value()
        dep["travel_speed_mm_min"] = self.travel_speed_spin.value()
        dep["wire_feed_mm_min"] = self.wfs_spin.value()
        dep["voltage_v"] = self.voltage_spin.value()
        dep["current_a"] = self.current_spin.value()
        dep["torch_config"] = self.torch_config_edit.text()

        data["process"]["thermal"]["max_interpass_temp_c"] = self.interpass_spin.value()

        # Save
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            App.Console.PrintMessage(f"WAAM: Config saved to {path}\n")
        except Exception as e:
            App.Console.PrintError(f"WAAM: Failed to save config: {e}\n")

    def cleanup(self):
        if self.sel_obs is not None:
            try:
                Gui.Selection.removeObserver(self.sel_obs)
            except Exception:
                pass
            self.sel_obs = None
        self.close()

    def _raise_dialog(self):
        dialog = self.window()
        try:
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
        except Exception:
            pass

    def _show_info(self, title, message):
        QtWidgets.QMessageBox.information(self, title, message)
        self._raise_dialog()

    def _apply_placement(self, origin, rot, *, start_point=None, clear_start=True):
        obj = get_first_solid()
        if not obj:
            QtWidgets.QMessageBox.warning(self, 'No solid','Open a document with a solid.')
            return
        doc = App.ActiveDocument
        job = None
        if doc:
            for o in doc.Objects:
                if hasattr(o, 'Proxy') and o.TypeId.startswith('Path::Feature'):
                    job = o
                    break
        placement = App.Placement(origin, rot)
        label = self.label_edit.text().strip() or "G54"
        ensure_tool_wcs_marker(doc, placement, label=label, start_point=start_point, clear_start=clear_start)
        try:
            if job is None:
                App.Console.PrintMessage("WAAM: Stored WCS placement (no Path job yet).\n")
                msg = f'WCS "{label}" stored for WAAM (no Path job yet).'
            else:
                ensure_setup_sheet_placement(job)
                job.SetupSheet.Placement = placement
                App.Console.PrintMessage("WAAM: WCS applied to Path job.\n")
                msg = f'WCS "{label}" applied to Path Job.'
        except Exception as exc:
            App.Console.PrintWarning(f"WAAM: Unable to apply WCS - {exc}\n")
            msg = f"WCS update failed:\n{exc}"
        App.ActiveDocument.recompute()
        self._show_info('WCS', msg)

    def _current_wcs_placement(self, doc):
        resolution = resolve_wcs(doc)
        return resolution.placement

    def use_tfl(self):
        obj = get_first_solid(); 
        if not obj: return
        bb = obj.Shape.BoundBox
        origin = App.Vector(
            bb.XMin,
            bb.YMin,
            bb.ZMin,
        )
        self._apply_placement(origin, App.Rotation(App.Vector(0,0,1),0), clear_start=True)

    def use_tc(self):
        obj = get_first_solid(); 
        if not obj: return
        bb = obj.Shape.BoundBox
        origin = App.Vector(
            0.5 * (bb.XMin + bb.XMax),
            0.5 * (bb.YMin + bb.YMax),
            bb.ZMin,
        )
        self._apply_placement(origin, App.Rotation(App.Vector(0,0,1),0), clear_start=True)

    def _start_pick(self, mode, prompt):
        if self.sel_obs is not None:
            try:
                Gui.Selection.removeObserver(self.sel_obs)
            except Exception:
                pass
        self.points = []
        self.pick_mode = mode
        self.sel_obs = SelectionObserver(self)
        Gui.Selection.addObserver(self.sel_obs)
        QtWidgets.QMessageBox.information(self, 'Pick', prompt)

    def pick3(self):
        self._start_pick("origin_xy", "Select 3 vertices in order: Origin, +X, +Y")

    def pick3_vertices(self):
        self._start_pick("axes_xyz", "Select 3 vertices in order: +X, +Y, +Z (origin stays at current WCS)")

    def _add_axis_rotation_row(self, parent_layout, axis_label: str):
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel(f"Rotate about {axis_label}:"))
        for label, degrees in (("-90°", -90.0), ("+90°", 90.0), ("180°", 180.0)):
            button = QtWidgets.QPushButton(label, self)
            button.clicked.connect(lambda _=False, a=axis_label, d=degrees: self._rotate_wcs(a, d))
            row.addWidget(button)
        parent_layout.addLayout(row)

    def _rotate_wcs(self, axis_label: str, degrees: float):
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.warning(self, 'WCS', 'Open a document first.')
            return
        placement = self._current_wcs_placement(doc)
        rot = placement.Rotation
        axis_map = {
            "X": App.Vector(1, 0, 0),
            "Y": App.Vector(0, 1, 0),
            "Z": App.Vector(0, 0, 1),
        }
        axis_local = axis_map.get(axis_label)
        if axis_local is None:
            return
        axis_world = rot.multVec(axis_local)
        if axis_world.Length == 0:
            QtWidgets.QMessageBox.warning(self, 'WCS', 'Unable to rotate: axis has zero length.')
            return
        axis_world.normalize()
        delta = App.Rotation(axis_world, float(degrees))
        x = delta.multVec(rot.multVec(App.Vector(1, 0, 0)))
        y = delta.multVec(rot.multVec(App.Vector(0, 1, 0)))
        z = delta.multVec(rot.multVec(App.Vector(0, 0, 1)))
        import FreeCAD as FC
        m = FC.Matrix()
        m.A11, m.A12, m.A13 = x.x, y.x, z.x
        m.A21, m.A22, m.A23 = x.y, y.y, z.y
        m.A31, m.A32, m.A33 = x.z, y.z, z.z
        self._apply_placement(placement.Base, FC.Rotation(m), clear_start=False)



    def gotPoint(self, v):
        self.points.append(v)
        if len(self.points) != 3:
            return

        if self.sel_obs is not None:
            try:
                Gui.Selection.removeObserver(self.sel_obs)
            except Exception:
                pass
            self.sel_obs = None

        mode = self.pick_mode or "origin_xy"
        self.pick_mode = None

        if mode == "axes_xyz":
            doc = App.ActiveDocument
            if doc is None:
                QtWidgets.QMessageBox.warning(self, 'WCS', 'Open a document first.')
                return
            placement = self._current_wcs_placement(doc)
            origin = placement.Base
            p_x, p_y, p_z = self.points
            x = (p_x - origin)
            y = (p_y - origin)
            z_hint = (p_z - origin)
            if x.Length == 0 or y.Length == 0 or z_hint.Length == 0:
                QtWidgets.QMessageBox.warning(self, 'WCS', 'Select 3 distinct vertices for +X, +Y, +Z.')
                return
            x.normalize()
            y.normalize()
            z = x.cross(y)
            if z.Length == 0:
                QtWidgets.QMessageBox.warning(self, 'WCS', 'Unable to compute WCS axes from the selected vertices.')
                return
            z.normalize()
            if z.dot(z_hint) < 0:
                z = z.multiply(-1)
            y = z.cross(x)
            y.normalize()
            import FreeCAD as FC
            m = FC.Matrix()
            m.A11, m.A12, m.A13 = x.x, y.x, z.x
            m.A21, m.A22, m.A23 = x.y, y.y, z.y
            m.A31, m.A32, m.A33 = x.z, y.z, z.z
            self._apply_placement(origin, FC.Rotation(m), clear_start=True)
            return

        p0, p1, p2 = self.points
        x = (p1 - p0)
        y = (p2 - p0)
        if x.Length == 0 or y.Length == 0:
            return
        x.normalize()
        z = x.cross(y)
        if z.Length == 0:
            return
        z.normalize()
        y = z.cross(x)
        y.normalize()
        import FreeCAD as FC
        m = FC.Matrix()
        m.A11, m.A12, m.A13 = x.x, y.x, z.x
        m.A21, m.A22, m.A23 = x.y, y.y, z.y
        m.A31, m.A32, m.A33 = x.z, y.z, z.z
        self._apply_placement(p0, FC.Rotation(m), clear_start=True)


def _parent_window():
    return Gui.getMainWindow() if hasattr(Gui, "getMainWindow") else None


def show_wcs_dialog(parent=None, *, compact: bool = False) -> bool:
    dialog_parent = parent if parent is not None else _parent_window()
    dialog = QtWidgets.QDialog(dialog_parent)
    dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
    dialog.setWindowTitle("WAAM - Set WCS")
    dialog.setModal(False)
    dialog.setWindowModality(QtCore.Qt.NonModal)
    dialog_size = _DEFAULT_DIALOG_SIZE if compact else _DEFAULT_DIALOG_SIZE
    dialog.resize(*dialog_size)
    dialog.setMinimumSize(*dialog_size)

    layout = QtWidgets.QVBoxLayout(dialog)
    panel = WcsPanel(compact=compact)
    layout.addWidget(panel)

    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    layout.addWidget(buttons)

    loop = QtCore.QEventLoop()
    result = {"accepted": False}

    def accept():
        result["accepted"] = True
        if loop.isRunning():
            loop.quit()
        dialog.close()

    def reject():
        result["accepted"] = False
        if loop.isRunning():
            loop.quit()
        dialog.close()

    buttons.accepted.connect(accept)
    buttons.rejected.connect(reject)
    dialog.finished.connect(lambda *_: loop.quit() if loop.isRunning() else None)

    dialog.show()
    loop.exec_()
    panel.cleanup()
    dialog.deleteLater()
    return result["accepted"]
