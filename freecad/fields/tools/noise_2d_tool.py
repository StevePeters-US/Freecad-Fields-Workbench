# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/noise_2d_tool.py

Edit tool and task panel for the 2D projected procedural noise SDF modifier.
"""
from PySide import QtWidgets, QtCore
from pivy import coin
from freecad.fields.tools.fld_base import DragTimerMixin
from freecad.fields.tools.fld_sdf_tool_base import DirectionGizmoMixin
from freecad.fields.tools.noise_base_tool import BaseNoiseTaskPanel, BaseNoiseTool
from freecad.fields.core import fld_logger
from freecad.fields.core.input.fld_gui_utils import NumericLineEdit

_HINT_2D = (
    "Variables: <b>u</b>, <b>w</b> (in-plane coords), <b>r</b> (radial "
    "distance), <b>d</b> (= r if Radial is on, else u), <b>radial</b> "
    "(0.0/1.0), <b>amp</b>, <b>freq</b> — all relative to <b>Center</b><br>"
    "Functions: sin cos tan sqrt abs pow exp log min max radians<br>"
    "fract floor ceil clamp mix mod step smoothstep sign pi<br>"
    "Custom params: <b>// @param slider speed 1.5 0.0 5.0 0.1</b><br>"
    "Tip: presets use <b>angle</b>/<b>angle2</b>/<b>amp2</b>/<b>freq2</b> "
    "for a second, rotated wave — try <b>mix(u*cos(radians(angle)) + "
    "w*sin(radians(angle)), r, radial)</b> in a Custom formula for the "
    "same rotate-or-radial trick"
)


class Noise2DTaskPanel(BaseNoiseTaskPanel):
    def __init__(self, tool):
        from freecad.fields.core.sdf.sdf.noise import SdfNoise2DField
        self._presets2d = SdfNoise2DField.PRESETS
        super().__init__(tool, "2D Noise Parameters", _HINT_2D)
        self.is_2d = True
        self.type_combo.blockSignals(True)
        self.type_combo.addItems(SdfNoise2DField.PRESET_NAMES)
        self.type_combo.blockSignals(False)
        self.update_ui()

    def _setup_extra_widgets(self):
        from freecad.fields.core.sdf.sdf.noise import SdfNoise2DField
        self.bias_combo = QtWidgets.QComboBox()
        self.bias_combo.addItems(SdfNoise2DField.BIAS_MODES)
        self.bias_combo.setToolTip(
            "Where the original surface sits on the waveform.\n"
            "Middle: displaced both ways — the stock grows by one amplitude.\n"
            "Top: the wave hangs inside the material — removal only, the stock never grows.\n"
            "Bottom: the wave sits outside — material is only added.\n"
            "The wave keeps its peak-to-peak size — this shifts it, it does not rescale it.\n"
            "Only the face the direction arrow points at is anchored: the far face keeps\n"
            "the plain wave running through it unless 'Ignore back face' holds it still."
        )
        self.bias_combo.currentIndexChanged.connect(self._on_bias_changed)
        self.p_layout.addRow("Bias:", self.bias_combo)

        self.back_face_chk = QtWidgets.QCheckBox("Ignore back face")
        self.back_face_chk.setToolTip(
            "When checked, noise only perturbs the surface that faces the direction arrow\n"
            "(the first surface hit along the direction). The opposite face is unaffected."
        )
        self.back_face_chk.stateChanged.connect(self._on_back_face_changed)
        self.p_layout.addRow("", self.back_face_chk)

    def _add_extra_sections(self):
        from freecad.fields.core.input.fld_gui_utils import CompactVector3Widget, CollapsibleSection

        self.proj_section = CollapsibleSection("Projection & Center")
        self.proj_section.setExpanded(False)
        self.layout.addWidget(self.proj_section)

        self.dir_vec = CompactVector3Widget(step=0.1, decimals=3)
        self.dir_x = self.dir_vec.x_input
        self.dir_y = self.dir_vec.y_input
        self.dir_z = self.dir_vec.z_input
        self.dir_vec.valuesChanged.connect(self._on_dir_vec_changed)
        self.proj_section.addRow("Direction:", self.dir_vec)

        self.roll_input = NumericLineEdit()
        self.roll_input.step = 5.0
        self.roll_input.setToolTip(
            "Spin of the pattern about the direction arrow, in degrees.\n"
            "Driven by the gizmo ring that lies around the arrow."
        )
        self.roll_input.editingFinished.connect(self._on_roll_changed)
        self.proj_section.addRow("Roll (°):", self.roll_input)

        self.center_vec = CompactVector3Widget(step=1.0, decimals=3)
        self.center_x = self.center_vec.x_input
        self.center_y = self.center_vec.y_input
        self.center_z = self.center_vec.z_input
        self.center_vec.valuesChanged.connect(self._on_center_vec_changed)
        self.proj_section.addRow("Center:", self.center_vec)

    def _on_dir_vec_changed(self, x, y, z):
        obj = self.tool._target_obj
        if not obj:
            return
        import math
        length = math.sqrt(x * x + y * y + z * z)
        if length < 1e-8:
            return
        nx, ny, nz = x / length, y / length, z / length
        obj.DirectionX = nx
        obj.DirectionY = ny
        obj.DirectionZ = nz
        for w in (self.dir_x, self.dir_y, self.dir_z):
            w.blockSignals(True)
        self.dir_x.setText(f"{nx:.3f}")
        self.dir_y.setText(f"{ny:.3f}")
        self.dir_z.setText(f"{nz:.3f}")
        for w in (self.dir_x, self.dir_y, self.dir_z):
            w.blockSignals(False)
        if hasattr(self.tool, "_draw_direction_arrow"):
            self.tool._draw_direction_arrow()
        self.tool._commit_changes()

    def _on_center_vec_changed(self, x, y, z):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.CenterX = x
        obj.CenterY = y
        obj.CenterZ = z
        if hasattr(self.tool, "_gizmo") and self.tool._gizmo:
            self.tool._gizmo.update(self.tool._gizmo_pivot())
        if hasattr(self.tool, "_draw_direction_arrow"):
            self.tool._draw_direction_arrow()
        self.tool._commit_changes()

    def _get_presets_and_names(self):
        from freecad.fields.core.sdf.sdf.noise import SdfNoise2DField
        return SdfNoise2DField.PRESETS, SdfNoise2DField.PRESET_NAMES

    def _get_preset_complex_presets(self):
        from freecad.fields.core.sdf.sdf.noise import SdfNoise2DField
        return SdfNoise2DField.COMPLEX_PRESETS

    def _update_extra_widgets(self, obj):
        from freecad.fields.core.sdf.sdf.noise import SdfNoise2DField
        bias_idx = self.bias_combo.findText(getattr(obj, "Bias", SdfNoise2DField.DEFAULT_BIAS))
        if bias_idx >= 0:
            self.bias_combo.setCurrentIndex(bias_idx)
        self.dir_x.setText(f"{getattr(obj, 'DirectionX', 0.0):.3f}")
        self.dir_y.setText(f"{getattr(obj, 'DirectionY', 0.0):.3f}")
        self.dir_z.setText(f"{getattr(obj, 'DirectionZ', -1.0):.3f}")
        self.roll_input.setText(f"{getattr(obj, 'Roll', 0.0):.1f}")
        self.back_face_chk.setChecked(getattr(obj, "IgnoreBackFace", False))
        self.center_x.setText(f"{getattr(obj, 'CenterX', 0.0):.3f}")
        self.center_y.setText(f"{getattr(obj, 'CenterY', 0.0):.3f}")
        self.center_z.setText(f"{getattr(obj, 'CenterZ', 0.0):.3f}")

    def _block_extra_widgets_signals(self, block):
        self.bias_combo.blockSignals(block)
        self.back_face_chk.blockSignals(block)
        for w in (self.dir_x, self.dir_y, self.dir_z, self.roll_input,
                  self.center_x, self.center_y, self.center_z):
            w.blockSignals(block)

    def _on_bias_changed(self, _index):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.Bias = self.bias_combo.currentText()
        self.tool._commit_changes()

    def _on_back_face_changed(self, state):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.IgnoreBackFace = bool(state)
        self.tool._commit_changes()

    def _on_radial_changed(self, state):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.Radial = bool(state)
        self.tool._commit_changes()

    def _on_roll_changed(self):
        try:
            obj = self.tool._target_obj
            if not obj or not hasattr(obj, "Roll"):
                return
            obj.Roll = float(self.roll_input.text())
            self.tool._commit_changes()
        except Exception as e:
            fld_logger.debug(f"Noise2DTaskPanel._on_roll_changed: Failed to commit roll change: {e}")


class Noise2DTool(BaseNoiseTool, DirectionGizmoMixin):
    SNAPSHOT_PROPERTIES = BaseNoiseTool.SNAPSHOT_PROPERTIES + [
        ("DirectionX", 0.0),
        ("DirectionY", 0.0),
        ("DirectionZ", -1.0),
        ("IgnoreBackFace", False),
        ("CenterX", 0.0),
        ("CenterY", 0.0),
        ("CenterZ", 0.0),
        ("Radial", False),
        ("Roll", 0.0),
        ("Bias", "Middle"),
    ]

    def get_handled_types(self):
        return ["FldNoise2DProxy"]

    def _apply_gizmo_rotation(self, rot):
        """Rotate the whole projection frame, not just the direction vector.

        The pattern's in-plane orientation lives in Roll (SdfNoise2DField spins its
        u/w basis by it), so the ring parallel to Direction — a no-op on the vector
        alone — must land there or that ring does nothing at all.
        """
        import FreeCAD
        from freecad.fields.core.sdf.sdf.noise import SdfNoise2DField
        obj = self._target_obj
        direction = FreeCAD.Vector(obj.DirectionX, obj.DirectionY, obj.DirectionZ)
        if direction.Length < 1e-8:
            direction = FreeCAD.Vector(0, 0, -1)
        else:
            direction.normalize()
        u_axis, _ = SdfNoise2DField._make_basis(direction, getattr(obj, "Roll", 0.0))
        new_dir = rot.multVec(direction)
        new_dir.normalize()
        new_u = rot.multVec(u_axis)
        obj.DirectionX = new_dir.x
        obj.DirectionY = new_dir.y
        obj.DirectionZ = new_dir.z
        if hasattr(obj, "Roll"):
            roll = SdfNoise2DField.roll_from_basis(new_dir, new_u)
            from freecad.fields.core.input import fld_snap
            from freecad.fields.core.fld_settings import (
                get_snap_angle_step, get_snap_during_direct_drag)
            if get_snap_during_direct_drag() and fld_snap.snap_active():
                step = get_snap_angle_step()
                if step > 0:
                    roll = fld_snap.snap_angle_deg(roll, step)
            obj.Roll = roll

    def _gizmo_pivot(self):
        obj = self._target_obj
        if obj and hasattr(obj, "CenterX"):
            import FreeCAD
            return FreeCAD.Vector(obj.CenterX, obj.CenterY, obj.CenterZ)
        return super()._gizmo_pivot()

    def _gizmo_origin_prop_names(self):
        return ("CenterX", "CenterY", "CenterZ")

    def __init__(self):
        super().__init__(root_class=coin.SoAnnotation)

    def _make_panel(self):
        return Noise2DTaskPanel(self)

    def _after_group_toggle(self, obj):
        super()._after_group_toggle(obj)
        self._draw_direction_arrow()

    def edit_object(self, obj):
        super().edit_object(obj)
        self._init_gizmo()
        self._draw_direction_arrow()

    def _on_before_redraw(self):
        self._draw_direction_arrow()

    def handle_move(self, event_dict):
        if not self._is_editing or getattr(self, "_is_dragging", False):
            return
        from freecad.fields.core.input.input_manager import FldInputManager
        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if ray_p and ray_d:
            tol = self._compute_handle_radius(self._gizmo_pivot()) * 2.5
            if self._gizmo_handle_move(ray_p, ray_d, tol):
                return
        self._restore_cursor()

    def on_button1_down(self, event_dict):
        if not self._is_editing or event_dict.get("Button") != QtCore.Qt.LeftButton:
            return False
        from freecad.fields.core.input.input_manager import FldInputManager
        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return False
        tol = self._compute_handle_radius(self._gizmo_pivot()) * 2.5
        return self._gizmo_try_start_drag(ray_p, ray_d, event_dict, tol)

    def _drag_update(self):
        if self._drag_check_lmb_released():
            return
        self._gizmo_drag_tick()

    def _do_terminate(self):
        try:
            self._gizmo_teardown()
        except Exception as e:
            fld_logger.debug(f"Noise2DTool._do_terminate: Failed to teardown gizmo: {e}")
        super()._do_terminate()
