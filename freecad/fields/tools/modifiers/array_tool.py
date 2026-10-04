# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/modifiers/array_tool.py

Edit tool and task panel for the Array SDF modifier.
"""
import FreeCAD
from freecad.fields.core import fld_logger
from PySide import QtWidgets, QtCore
try:
    from pivy import coin
except ImportError:
    coin = None
from freecad.fields.tools.fld_sdf_tool_base import FldSdfModifierToolBase, DirectionGizmoMixin
from freecad.fields.core.input.fld_gui_utils import DynamicLimitSlider
from freecad.fields.tools.modifiers import axis_combo, BaseModifierTaskPanel, block_signals
from freecad.fields.tools.primitive_creator_base import GIZMO_HIT_TOLERANCE_MULT
from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip

_AXIS_VECTORS = {"X": FreeCAD.Vector(1, 0, 0), "Y": FreeCAD.Vector(0, 1, 0), "Z": FreeCAD.Vector(0, 0, 1)}


def _axis_vec(name):
    return _AXIS_VECTORS.get(name, FreeCAD.Vector(0, 0, 1))


class ArrayTaskPanel(BaseModifierTaskPanel):
    def __init__(self, tool):
        self.tool = tool
        self._pinned_overlap = None
        self.form = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.form)
        layout.setContentsMargins(10, 10, 10, 10)

        # Mode selector
        mode_grp = QtWidgets.QGroupBox("Array Mode")
        mode_fl = QtWidgets.QFormLayout(mode_grp)
        layout.addWidget(mode_grp)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Grid", "Step"])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_fl.addRow("Mode:", self.mode_combo)

        # Grid Group Box
        self.grid_grp = QtWidgets.QGroupBox("Grid Parameters")
        grid_fl = QtWidgets.QFormLayout(self.grid_grp)
        layout.addWidget(self.grid_grp)

        self.count_x_spin = QtWidgets.QSpinBox()
        self.count_x_spin.setRange(1, 1000)
        self.count_x_spin.valueChanged.connect(self._on_changed)
        grid_fl.addRow("Count X:", self.count_x_spin)

        self.count_y_spin = QtWidgets.QSpinBox()
        self.count_y_spin.setRange(1, 1000)
        self.count_y_spin.valueChanged.connect(self._on_changed)
        grid_fl.addRow("Count Y:", self.count_y_spin)

        self.count_z_spin = QtWidgets.QSpinBox()
        self.count_z_spin.setRange(1, 1000)
        self.count_z_spin.valueChanged.connect(self._on_changed)
        grid_fl.addRow("Count Z:", self.count_z_spin)

        self.spacing_x_slider = self._make_slider(grid_fl, "Spacing X:", value=20.0)
        self.spacing_y_slider = self._make_slider(grid_fl, "Spacing Y:", value=20.0)
        self.spacing_z_slider = self._make_slider(grid_fl, "Spacing Z:", value=20.0)

        # Step Group Box
        self.step_grp = QtWidgets.QGroupBox("Step Parameters")
        step_fl = QtWidgets.QFormLayout(self.step_grp)
        layout.addWidget(self.step_grp)

        self.step_count_spin = QtWidgets.QSpinBox()
        self.step_count_spin.setRange(1, 1000)
        self.step_count_spin.valueChanged.connect(self._on_changed)
        step_fl.addRow("Count:", self.step_count_spin)

        self.step_axis_combo = axis_combo()
        self.step_axis_combo.currentIndexChanged.connect(self._on_changed)
        step_fl.addRow("Axis:", self.step_axis_combo)

        self.step_angle_slider = DynamicLimitSlider(value=60.0, min_val=-360.0, max_val=360.0, step=1.0, decimals=1)
        self.step_angle_slider.valueChanged.connect(self._on_changed)
        self._connect_slider_drag(self.step_angle_slider)
        step_fl.addRow("Angle (deg):", self.step_angle_slider)

        self.step_x_slider = self._make_slider(step_fl, "Offset X:")
        self.step_y_slider = self._make_slider(step_fl, "Offset Y:")
        self.step_z_slider = self._make_slider(step_fl, "Offset Z:")

        self.step_cx_slider = self._make_slider(step_fl, "Center X:")
        self.step_cy_slider = self._make_slider(step_fl, "Center Y:")
        self.step_cz_slider = self._make_slider(step_fl, "Center Z:")

        self.step_scale_slider = DynamicLimitSlider(value=1.0, min_val=0.01, max_val=10.0, step=0.05, decimals=3)
        self.step_scale_slider.valueChanged.connect(self._on_changed)
        self._connect_slider_drag(self.step_scale_slider)
        step_fl.addRow("Scale:", self.step_scale_slider)

        # Formulas Collapsible Group Box
        self.formulas_grp = QtWidgets.QGroupBox("Formulas")
        self.formulas_grp.setCheckable(True)
        self.formulas_grp.setChecked(False)
        form_fl = QtWidgets.QFormLayout(self.formulas_grp)
        step_fl.addRow(self.formulas_grp)

        self.angle_formula_edit = QtWidgets.QLineEdit()
        self.angle_formula_edit.setToolTip(
            rich_tooltip(
                "ArrayTaskPanel",
                QT_TRANSLATE_NOOP(
                    "ArrayTaskPanel",
                    "Expression evaluated CPU-side. Variables in scope: i (0-based index), n (count), t (i/(n-1)). Result in degrees.",
                ),
            )
        )
        self.angle_formula_edit.editingFinished.connect(self._on_changed)
        form_fl.addRow("Angle:", self.angle_formula_edit)

        self.radius_formula_edit = QtWidgets.QLineEdit()
        self.radius_formula_edit.setToolTip(
            rich_tooltip(
                "ArrayTaskPanel",
                QT_TRANSLATE_NOOP(
                    "ArrayTaskPanel",
                    "Expression evaluated CPU-side. Variables in scope: i (0-based index), n (count), t (i/(n-1)). Result in mm.",
                ),
            )
        )
        self.radius_formula_edit.editingFinished.connect(self._on_changed)
        form_fl.addRow("Radius:", self.radius_formula_edit)

        self.rise_formula_edit = QtWidgets.QLineEdit()
        self.rise_formula_edit.setToolTip(
            rich_tooltip(
                "ArrayTaskPanel",
                QT_TRANSLATE_NOOP(
                    "ArrayTaskPanel",
                    "Expression evaluated CPU-side. Variables in scope: i (0-based index), n (count), t (i/(n-1)). Result in mm.",
                ),
            )
        )
        self.rise_formula_edit.editingFinished.connect(self._on_changed)
        form_fl.addRow("Rise:", self.rise_formula_edit)

        self.scale_formula_edit = QtWidgets.QLineEdit()
        self.scale_formula_edit.setToolTip(
            rich_tooltip(
                "ArrayTaskPanel",
                QT_TRANSLATE_NOOP(
                    "ArrayTaskPanel",
                    "Expression evaluated CPU-side. Variables in scope: i (0-based index), n (count), t (i/(n-1)). Result is uniform scale factor.",
                ),
            )
        )
        self.scale_formula_edit.editingFinished.connect(self._on_changed)
        form_fl.addRow("Scale:", self.scale_formula_edit)

        # Advanced Group Box
        self.advanced_grp = QtWidgets.QGroupBox("Advanced")
        self.advanced_grp.setCheckable(True)
        self.advanced_grp.setChecked(False)
        adv_fl = QtWidgets.QFormLayout(self.advanced_grp)
        layout.addWidget(self.advanced_grp)

        self.overlap_mode_combo = QtWidgets.QComboBox()
        self.overlap_mode_combo.addItems(["Auto", "Always", "Never"])
        self.overlap_mode_combo.currentIndexChanged.connect(self._on_changed)
        adv_fl.addRow("Overlap Mode:", self.overlap_mode_combo)

        self.strategy_lbl = QtWidgets.QLabel("Strategy: fold_grid")
        adv_fl.addRow("Active Strategy:", self.strategy_lbl)

        # Overlap Warning (Informational)
        self.warn_lbl = QtWidgets.QLabel("Copies overlap; using exact multi-cell evaluation (slower).")
        self.warn_lbl.setStyleSheet("color: #ffaa00; font-weight: bold;")
        self.warn_lbl.setWordWrap(True)
        self.warn_lbl.setVisible(False)
        layout.addWidget(self.warn_lbl)

        # Shared Group Button
        group_grp = QtWidgets.QGroupBox("Group Mode")
        group_fl = QtWidgets.QFormLayout(group_grp)
        layout.addWidget(group_grp)

        self.group_btn = QtWidgets.QPushButton("Additive")
        self.group_btn.clicked.connect(self._on_group_toggled)
        group_fl.addRow("Group:", self.group_btn)

        layout.addStretch()
        self.update_ui()

    def _make_slider(self, layout, label, value=0.0, min_val=-200.0, max_val=200.0, step=1.0, decimals=2):
        sl = DynamicLimitSlider(value=value, min_val=min_val, max_val=max_val, step=step, decimals=decimals)
        sl.valueChanged.connect(self._on_changed)
        self._connect_slider_drag(sl)
        layout.addRow(label, sl)
        return sl

    def _connect_slider_drag(self, dyn_slider):
        if hasattr(dyn_slider, "_slider"):
            dyn_slider._slider.sliderPressed.connect(self._on_slider_pressed)
            dyn_slider._slider.sliderReleased.connect(self._on_slider_released)

    def _on_slider_pressed(self):
        obj = self.tool._target_obj
        if obj and getattr(obj, "OverlapMode", "Auto") == "Auto":
            self._pinned_overlap = "Auto"
            obj.OverlapMode = "Always"

    def _on_slider_released(self):
        if self._pinned_overlap:
            obj = self.tool._target_obj
            if obj:
                obj.OverlapMode = self._pinned_overlap
            self._pinned_overlap = None
            self.tool._commit_changes()
            self._update_visibility()

    def update_ui(self):
        obj = self.tool._target_obj
        if not obj:
            return
        widgets_to_block = (
            self.mode_combo,
            self.count_x_spin,
            self.count_y_spin,
            self.count_z_spin,
            self.spacing_x_slider,
            self.spacing_y_slider,
            self.spacing_z_slider,
            self.step_count_spin,
            self.step_axis_combo,
            self.step_angle_slider,
            self.step_x_slider,
            self.step_y_slider,
            self.step_z_slider,
            self.step_cx_slider,
            self.step_cy_slider,
            self.step_cz_slider,
            self.step_scale_slider,
            self.angle_formula_edit,
            self.radius_formula_edit,
            self.rise_formula_edit,
            self.scale_formula_edit,
            self.overlap_mode_combo,
        )
        with block_signals(*widgets_to_block):
            mode = getattr(obj, "Mode", "Grid")
            idx = self.mode_combo.findText(mode)
            if idx >= 0:
                self.mode_combo.setCurrentIndex(idx)

            self.count_x_spin.setValue(getattr(obj, "CountX", 2))
            self.count_y_spin.setValue(getattr(obj, "CountY", 1))
            self.count_z_spin.setValue(getattr(obj, "CountZ", 1))
            self.spacing_x_slider.setValue(getattr(obj, "SpacingX", 20.0))
            self.spacing_y_slider.setValue(getattr(obj, "SpacingY", 20.0))
            self.spacing_z_slider.setValue(getattr(obj, "SpacingZ", 20.0))

            self.step_count_spin.setValue(getattr(obj, "StepCount", 6))
            ax_idx = self.step_axis_combo.findText(getattr(obj, "StepAxis", "Z"))
            if ax_idx >= 0:
                self.step_axis_combo.setCurrentIndex(ax_idx)
            self.step_angle_slider.setValue(getattr(obj, "StepAngle", 60.0))
            self.step_x_slider.setValue(getattr(obj, "StepX", 0.0))
            self.step_y_slider.setValue(getattr(obj, "StepY", 0.0))
            self.step_z_slider.setValue(getattr(obj, "StepZ", 0.0))
            self.step_cx_slider.setValue(getattr(obj, "StepCenterX", 0.0))
            self.step_cy_slider.setValue(getattr(obj, "StepCenterY", 0.0))
            self.step_cz_slider.setValue(getattr(obj, "StepCenterZ", 0.0))
            self.step_scale_slider.setValue(getattr(obj, "StepScale", 1.0))

            self.angle_formula_edit.setText(getattr(obj, "AngleFormula", ""))
            self.radius_formula_edit.setText(getattr(obj, "RadiusFormula", ""))
            self.rise_formula_edit.setText(getattr(obj, "RiseFormula", ""))
            self.scale_formula_edit.setText(getattr(obj, "ScaleFormula", ""))

            ov_mode = getattr(obj, "OverlapMode", "Auto")
            ov_idx = self.overlap_mode_combo.findText(ov_mode)
            if ov_idx >= 0:
                self.overlap_mode_combo.setCurrentIndex(ov_idx)

            self.group_btn.setText(getattr(obj, "Group", "Additive"))

        self._update_visibility()

    def _update_visibility(self):
        is_grid = self.mode_combo.currentText() == "Grid"
        self.grid_grp.setVisible(is_grid)
        self.step_grp.setVisible(not is_grid)

        obj = self.tool._target_obj
        if obj and hasattr(obj, "Proxy") and hasattr(obj.Proxy, "get_sdf_field"):
            field = obj.Proxy.get_sdf_field(obj)
            if field:
                strat = getattr(field, "strategy", lambda: "fold_grid")()
                self.strategy_lbl.setText(f"Strategy: {strat}")
                needs_warn = getattr(field, "needs_multi_cell", lambda: False)() and getattr(field, "use_multi_cell", lambda: False)()
                self.warn_lbl.setVisible(needs_warn)
            else:
                self.warn_lbl.setVisible(False)
        else:
            self.warn_lbl.setVisible(False)

    def _on_mode_changed(self):
        self._on_changed()
        self._update_visibility()

    def _on_changed(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.Mode = self.mode_combo.currentText()
        obj.CountX = self.count_x_spin.value()
        obj.CountY = self.count_y_spin.value()
        obj.CountZ = self.count_z_spin.value()
        obj.SpacingX = self.spacing_x_slider.value()
        obj.SpacingY = self.spacing_y_slider.value()
        obj.SpacingZ = self.spacing_z_slider.value()
        obj.StepCount = self.step_count_spin.value()
        obj.StepAxis = self.step_axis_combo.currentText()
        obj.StepAngle = self.step_angle_slider.value()
        obj.StepX = self.step_x_slider.value()
        obj.StepY = self.step_y_slider.value()
        obj.StepZ = self.step_z_slider.value()
        obj.StepCenterX = self.step_cx_slider.value()
        obj.StepCenterY = self.step_cy_slider.value()
        obj.StepCenterZ = self.step_cz_slider.value()
        obj.StepScale = self.step_scale_slider.value()
        obj.AngleFormula = self.angle_formula_edit.text()
        obj.RadiusFormula = self.radius_formula_edit.text()
        obj.RiseFormula = self.rise_formula_edit.text()
        obj.ScaleFormula = self.scale_formula_edit.text()
        obj.OverlapMode = self.overlap_mode_combo.currentText()
        self.tool._commit_changes()
        self._update_visibility()


class ArrayTool(DirectionGizmoMixin, FldSdfModifierToolBase):
    SNAPSHOT_PROPERTIES = [
        ("Mode", "Grid"),
        ("CountX", 2),
        ("CountY", 1),
        ("CountZ", 1),
        ("SpacingX", 20.0),
        ("SpacingY", 20.0),
        ("SpacingZ", 20.0),
        ("StepCount", 6),
        ("StepX", 0.0),
        ("StepY", 0.0),
        ("StepZ", 0.0),
        ("StepAxis", "Z"),
        ("StepAngle", 60.0),
        ("StepCenterX", 0.0),
        ("StepCenterY", 0.0),
        ("StepCenterZ", 0.0),
        ("StepScale", 1.0),
        ("AngleFormula", ""),
        ("RadiusFormula", ""),
        ("RiseFormula", ""),
        ("ScaleFormula", ""),
        ("SkipIndices", []),
        ("OverlapMode", "Auto"),
        ("Group", "Additive"),
    ]

    def __init__(self):
        super().__init__()
        self._gizmo = None
        self._arrow_root = None
        self._drag_orig_overlap_mode = None
        if coin is not None:
            self.points_root = coin.SoAnnotation()
            if getattr(self, "view", None) and self.view.getSceneGraph():
                self.view.getSceneGraph().addChild(self.points_root)
        else:
            self.points_root = None

    def get_command_id(self):
        return "Fields_EditObject"

    def get_handled_types(self):
        return ["FldArrayProxy"]

    def _make_panel(self):
        return ArrayTaskPanel(self)

    def edit_object(self, obj):
        super().edit_object(obj)
        self._init_gizmo()

    def _gizmo_pivot(self):
        """Return the world-space pivot on copy 1."""
        obj = getattr(self, "_target_obj", None)
        if not obj:
            return FreeCAD.Vector(0, 0, 0)
        source = getattr(obj, "Source", None)
        bb_min, bb_max = None, None
        if source and hasattr(source, "Proxy") and hasattr(source.Proxy, "get_sdf_field"):
            fld = source.Proxy.get_sdf_field(source)
            if fld:
                try:
                    bb_min, bb_max = fld.bounding_box()
                except Exception as exc:  # safe: bounding_box is optional; falls back to source.Placement.Base
                    fld_logger.debug(f"[array_tool] bounding_box failed: {exc}")
                    pass
        if bb_min is None or bb_max is None:
            c_src = FreeCAD.Vector(source.Placement.Base) if (source and hasattr(source, "Placement")) else FreeCAD.Vector(0, 0, 0)
        else:
            c_src = (bb_min + bb_max) * 0.5

        mode = getattr(obj, "Mode", "Grid")
        if mode == "Grid":
            cx = getattr(obj, "CountX", 2)
            cy = getattr(obj, "CountY", 1)
            cz = getattr(obj, "CountZ", 1)
            sx = getattr(obj, "SpacingX", 20.0) if cx > 1 else 0.0
            sy = getattr(obj, "SpacingY", 20.0) if cy > 1 else 0.0
            sz = getattr(obj, "SpacingZ", 20.0) if cz > 1 else 0.0
            return c_src + FreeCAD.Vector(sx, sy, sz)
        else:
            # Step mode: T_1(c_src)
            c = FreeCAD.Vector(getattr(obj, "StepCenterX", 0.0), getattr(obj, "StepCenterY", 0.0), getattr(obj, "StepCenterZ", 0.0))
            ax_name = getattr(obj, "StepAxis", "Z")
            a_hat = _axis_vec(ax_name).normalize()
            ang = getattr(obj, "StepAngle", 60.0)
            off = FreeCAD.Vector(getattr(obj, "StepX", 0.0), getattr(obj, "StepY", 0.0), getattr(obj, "StepZ", 0.0))
            scale = getattr(obj, "StepScale", 1.0)
            rot = FreeCAD.Rotation(a_hat, ang)
            rot_mat = rot.toMatrix()
            v = (c_src - c) - (c_src - c).dot(a_hat) * a_hat
            if v.Length > 1e-6:
                u_hat = v.normalize()
            else:
                from freecad.fields.core.input.fld_gizmo import _perp_pair
                ref, _ = _perp_pair(a_hat)
                u_hat = ref.normalize()
            rise_comp = off.dot(a_hat)
            planar_part = off - rise_comp * a_hat
            planar_len = planar_part.Length
            disp = u_hat * planar_len + a_hat * rise_comp - c * scale
            return c + rot_mat.multVec(disp + c_src * scale)

    def _gizmo_origin_prop_names(self):
        mode = getattr(self._target_obj, "Mode", "Grid")
        if mode == "Step":
            return ("StepX", "StepY", "StepZ")
        return ("SpacingX", "SpacingY", "SpacingZ")

    def _apply_gizmo_rotation(self, rot):
        """Update StepAxis and StepAngle from rotation ring drag."""
        import math
        obj = self._target_obj
        if not obj:
            return
        dragging = getattr(self, "_dragging_idx", "")
        if dragging.startswith("gizmo_rot_"):
            ax_key = dragging[len("gizmo_rot_"):].upper()
            if ax_key in ("X", "Y", "Z"):
                obj.StepAxis = ax_key
        sign = 1.0
        ax_name = getattr(obj, "StepAxis", "Z")
        ax_vec = _axis_vec(ax_name)
        if hasattr(rot, "Axis"):
            sign = 1.0 if rot.Axis.dot(ax_vec) >= 0 else -1.0
        delta_deg = math.degrees(rot.Angle) * sign
        obj.StepAngle = getattr(obj, "StepAngle", 60.0) + delta_deg

    def _on_direction_changed(self):
        pass

    def handle_move(self, event_dict):
        if not self._is_editing or getattr(self, "_is_dragging", False):
            return
        from freecad.fields.core.input.input_manager import FldInputManager
        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            self._restore_cursor()
            return
        tol = self._compute_handle_radius(self._gizmo_pivot()) * GIZMO_HIT_TOLERANCE_MULT
        if self._gizmo_handle_move(ray_p, ray_d, tol):
            return
        self._restore_cursor()

    def on_button1_down(self, event_dict):
        if not self._is_editing:
            return False
        if event_dict.get("Button") != QtCore.Qt.LeftButton:
            return False
        from freecad.fields.core.input.input_manager import FldInputManager
        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return False
        tol = self._compute_handle_radius(self._gizmo_pivot()) * GIZMO_HIT_TOLERANCE_MULT
        started = self._gizmo_try_start_drag(ray_p, ray_d, event_dict, tol)
        if started:
            obj = self._target_obj
            if obj and getattr(obj, "OverlapMode", "Auto") == "Auto":
                self._drag_orig_overlap_mode = "Auto"
                obj.OverlapMode = "Always"
        return started

    def _drag_update(self):
        if self._drag_check_lmb_released():
            if getattr(self, "_drag_orig_overlap_mode", None):
                if self._target_obj:
                    self._target_obj.OverlapMode = self._drag_orig_overlap_mode
                self._drag_orig_overlap_mode = None
                self._commit_changes()
            return
        if self._gizmo_drag_tick():
            if self._gizmo:
                self._gizmo.update(self._gizmo_pivot(), axes=self._gizmo._axes)
            return

    def _do_terminate(self):
        self._gizmo_teardown()
        super()._do_terminate()
