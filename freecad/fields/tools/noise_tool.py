# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/noise_tool.py

Unified edit tool and task panel for procedural noise SDF modifiers.
"""
from PySide import QtWidgets, QtCore
from pivy import coin
import FreeCAD
from freecad.fields.tools.fld_base import DragTimerMixin
from freecad.fields.tools.fld_sdf_tool_base import FldSdfModifierToolBase, DirectionGizmoMixin
from freecad.fields.core import fld_logger
from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip
from freecad.fields.core.input.fld_gui_utils import (
    DynamicLimitSlider, DynamicLimitIntSlider, NumericLineEdit,
    CompactVector3Widget, CollapsibleSection,
)
from freecad.fields.core.sdf.sdf.noise import SdfNoiseField
from freecad.fields.tools.primitive_creator_base import GIZMO_HIT_TOLERANCE_MULT


class NoiseTaskPanel:
    def __init__(self, tool):
        self.tool = tool
        self._node_editor_dlg = None
        self.form = QtWidgets.QWidget()
        self.layout = QtWidgets.QVBoxLayout(self.form)
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.layout.setSpacing(6)

        # 1. Top bar: Type + Additive/Subtractive Group
        top_group = QtWidgets.QGroupBox()
        top_layout = QtWidgets.QHBoxLayout(top_group)
        top_layout.setContentsMargins(6, 4, 6, 4)
        top_layout.setSpacing(6)

        top_layout.addWidget(QtWidgets.QLabel("Type:"))
        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.addItems(SdfNoiseField.PRESET_NAMES)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        top_layout.addWidget(self.type_combo, 1)

        self.group_btn = QtWidgets.QPushButton("Additive")
        self.group_btn.setCheckable(False)
        self.group_btn.clicked.connect(self._on_group_toggled)
        self.group_btn.setToolTip(
            rich_tooltip(
                "NoiseTaskPanel",
                QT_TRANSLATE_NOOP(
                    "NoiseTaskPanel",
                    "Switches the object between the additive and subtractive group.",
                ),
            )
        )
        self.group_btn.setStyleSheet("font-weight: bold; padding: 3px 8px;")
        top_layout.addWidget(self.group_btn)
        self.layout.addWidget(top_group)

        # 2. Main Parameters Section
        self.params_section = CollapsibleSection("Noise Parameters")
        self.params_section.setExpanded(True)
        self.p_layout = self.params_section.content_layout
        self.layout.addWidget(self.params_section)

        self.amp_slider = DynamicLimitSlider(value=1.0, min_val=0.0, max_val=1.0, step=0.05, decimals=2)
        self.amp_slider.valueChanged.connect(self._on_params_changed)
        self.amp_slider.limitsChanged.connect(self._on_limits_changed)
        self.amp_slider._slider.sliderPressed.connect(self._on_slider_pressed)
        self.amp_slider._slider.sliderReleased.connect(self._on_slider_released)
        self.p_layout.addRow("Amplitude:", self.amp_slider)

        self.freq_slider = DynamicLimitSlider(value=0.1, min_val=0.001, max_val=1.0, step=0.005, decimals=3)
        self.freq_slider.valueChanged.connect(self._on_params_changed)
        self.freq_slider.limitsChanged.connect(self._on_limits_changed)
        self.freq_slider._slider.sliderPressed.connect(self._on_slider_pressed)
        self.freq_slider._slider.sliderReleased.connect(self._on_slider_released)
        self.p_layout.addRow("Frequency:", self.freq_slider)

        self.norm_input = NumericLineEdit()
        self.norm_input.step = 0.1
        self.norm_input.editingFinished.connect(self._on_params_changed)
        self.p_layout.addRow("Normalization:", self.norm_input)

        # 3. Projection & Center Section
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
            rich_tooltip(
                "NoiseTaskPanel",
                QT_TRANSLATE_NOOP(
                    "NoiseTaskPanel",
                    "Spin of the pattern about the direction arrow, in degrees.\nDriven by the gizmo ring that lies around the arrow.",
                ),
            )
        )
        self.roll_input.editingFinished.connect(self._on_roll_changed)
        self.proj_section.addRow("Roll (°):", self.roll_input)

        self.center_vec = CompactVector3Widget(step=1.0, decimals=3)
        self.center_x = self.center_vec.x_input
        self.center_y = self.center_vec.y_input
        self.center_z = self.center_vec.z_input
        self.center_vec.valuesChanged.connect(self._on_center_vec_changed)
        self.proj_section.addRow("Center:", self.center_vec)

        # 4. Custom Parameters Section
        self.custom_section = CollapsibleSection("Custom Parameters")
        self.custom_section.setExpanded(True)
        self.custom_layout = self.custom_section.content_layout
        self.custom_group = self.custom_section
        self.layout.addWidget(self.custom_section)
        self.custom_section.setVisible(False)
        self._custom_widgets = {}

        # 5. Visual Node Editor
        self.node_editor_btn = QtWidgets.QPushButton("Visual Node Editor...")
        self.node_editor_btn.setStyleSheet(
            "font-weight: bold; padding: 7px; background-color: #0284c7; color: white; border-radius: 4px;"
        )
        self.node_editor_btn.setToolTip(
            rich_tooltip(
                "NoiseTaskPanel",
                QT_TRANSLATE_NOOP(
                    "NoiseTaskPanel",
                    "Opens the visual node editor to build the formula as a graph.",
                ),
            )
        )
        self.node_editor_btn.clicked.connect(self._open_node_editor)
        self.layout.addWidget(self.node_editor_btn)

        self.layout.addStretch()
        self.update_ui()

    def _on_slider_pressed(self):
        if self.tool:
            self.tool._is_dragging = True

    def _on_slider_released(self):
        if self.tool:
            self.tool._is_dragging = False
            self.tool._commit_changes(force=True)

    def _current_direction_roll(self):
        """Direction/Roll as currently typed in the panel (not yet applied)."""
        try:
            d = FreeCAD.Vector(float(self.dir_x.text()), float(self.dir_y.text()), float(self.dir_z.text()))
        except ValueError:
            d = FreeCAD.Vector(0, 0, -1)
        if d.Length < 1e-8:
            d = FreeCAD.Vector(0, 0, -1)
        else:
            d.normalize()
        try:
            roll = float(self.roll_input.text())
        except ValueError:
            roll = 0.0
        return d, roll

    def _apply_direction_roll(self, direction, roll):
        """Direction + Roll fully determine orientation; Center is untouched.

        `u` comes from `_make_basis` so this round-trips exactly through
        `roll_from_basis` in `update_ui` (which measures roll purely off `u`
        relative to `_make_basis`'s own u0). `w` is rebuilt as `direction x u`
        rather than taken from `_make_basis` directly: that function's (u, w,
        direction) triad is deliberately LEFT-handed (see its docstring), and
        FreeCAD.Rotation can only represent a proper right-handed frame --
        feeding it a left-handed triad silently produces a nonsense rotation.
        `direction x u` is `_make_basis`'s `w` with the sign flipped, which is
        invisible here since nothing downstream reads `w`.
        One Placement assignment: mutating `obj.Placement.Rotation` in place
        does not mark the document touched and the recompute never fires.
        """
        obj = self.tool._target_obj
        if not obj:
            return
        u, _ = SdfNoiseField._make_basis(direction, roll)
        w = direction.cross(u)
        rot = FreeCAD.Rotation(u, w, direction, "ZXY")
        obj.Placement = FreeCAD.Placement(obj.Placement.Base, rot)

    def _on_dir_vec_changed(self, x, y, z):
        obj = self.tool._target_obj
        if not obj:
            return
        import math
        length = math.sqrt(x * x + y * y + z * z)
        if length < 1e-8:
            return
        nx, ny, nz = x / length, y / length, z / length
        _, roll = self._current_direction_roll()
        self._apply_direction_roll(FreeCAD.Vector(nx, ny, nz), roll)
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
        obj.Placement = FreeCAD.Placement(FreeCAD.Vector(x, y, z), obj.Placement.Rotation)
        if hasattr(self.tool, "_gizmo") and self.tool._gizmo:
            self.tool._gizmo.update(self.tool._gizmo_pivot())
        if hasattr(self.tool, "_draw_direction_arrow"):
            self.tool._draw_direction_arrow()
        self.tool._commit_changes()

    def _on_roll_changed(self):
        try:
            obj = self.tool._target_obj
            if not obj:
                return
            direction, _ = self._current_direction_roll()
            roll = float(self.roll_input.text())
            self._apply_direction_roll(direction, roll)
            self.tool._commit_changes()
        except Exception as e:
            fld_logger.debug(f"NoiseTaskPanel._on_roll_changed: Failed to commit roll change: {e}")

    def update_ui(self):
        obj = self.tool._target_obj
        if not obj:
            return
        self.amp_slider.blockSignals(True)
        self.freq_slider.blockSignals(True)
        self.norm_input.blockSignals(True)
        for w in (self.dir_x, self.dir_y, self.dir_z, self.roll_input,
                  self.center_x, self.center_y, self.center_z):
            w.blockSignals(True)

        for prop, default in [("AmplitudeMin", 0.0), ("AmplitudeMax", 10.0),
                              ("FrequencyMin", 0.001), ("FrequencyMax", 1.0)]:
            if not hasattr(obj, prop):
                obj.addProperty("App::PropertyFloat", prop, "Noise", "Persistent slider limit")
                setattr(obj, prop, default)

        self.amp_slider.setLimits(obj.AmplitudeMin, obj.AmplitudeMax)
        self.freq_slider.setLimits(obj.FrequencyMin, obj.FrequencyMax)

        self.amp_slider.setValue(getattr(obj, "Amplitude", 1.0))
        self.freq_slider.setValue(getattr(obj, "Frequency", 0.1))
        self.norm_input.setText(f"{getattr(obj, 'Normalization', 0.0):.3f}")

        rot = obj.Placement.Rotation
        d = rot.multVec(FreeCAD.Vector(0, 0, 1))
        u = rot.multVec(FreeCAD.Vector(1, 0, 0))
        roll = SdfNoiseField.roll_from_basis(d, u)
        self.dir_x.setText(f"{d.x:.3f}")
        self.dir_y.setText(f"{d.y:.3f}")
        self.dir_z.setText(f"{d.z:.3f}")
        self.roll_input.setText(f"{roll:.1f}")
        b = obj.Placement.Base
        self.center_x.setText(f"{b.x:.3f}")
        self.center_y.setText(f"{b.y:.3f}")
        self.center_z.setText(f"{b.z:.3f}")

        self.amp_slider.blockSignals(False)
        self.freq_slider.blockSignals(False)
        self.norm_input.blockSignals(False)
        for w in (self.dir_x, self.dir_y, self.dir_z, self.roll_input,
                  self.center_x, self.center_y, self.center_z):
            w.blockSignals(False)

        grp = getattr(obj, "Group", "Additive")
        self.group_btn.setText(grp)

        noise_type = getattr(obj, "NoiseType", "Waves")
        self.type_combo.blockSignals(True)
        idx = self.type_combo.findText(noise_type)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        self.type_combo.blockSignals(False)
        self._update_custom_widgets()

    def _update_custom_widgets(self):
        obj = self.tool._target_obj
        if not obj:
            return

        while self.custom_layout.count() > 0:
            item = self.custom_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        from freecad.fields.core.sdf.sdf.formula_eval import parse_custom_params
        formula = getattr(obj, "Formula", "")
        params = []
        if self.type_combo.currentText() not in SdfNoiseField.COMPLEX_PRESETS:
            params.extend(parse_custom_params(formula))

        if not params:
            self.custom_group.hide()
            self._custom_widgets = {}
            return

        self.custom_group.show()
        self._custom_widgets = {}

        for param in params:
            name = param['name']
            ptype = param['type']
            default = param['default']
            min_val = param['min']
            max_val = param['max']
            step_val = param['step']

            prop_name = "Custom_" + name
            if not hasattr(obj, prop_name):
                if ptype == 'bool':
                    prop_type = "App::PropertyBool"
                elif ptype == 'int':
                    prop_type = "App::PropertyInteger"
                else:
                    prop_type = "App::PropertyFloat"
                try:
                    obj.addProperty(prop_type, prop_name, "Custom Params", f"Custom parameter {prop_name}")
                    setattr(obj, prop_name, bool(default) if ptype == 'bool' else default)
                except Exception as e:
                    fld_logger.warn(f"Failed to add dynamic property {prop_name}: {e}")
            current_val = getattr(obj, prop_name, default)

            if ptype == 'bool':
                chk = QtWidgets.QCheckBox()
                chk.setChecked(bool(current_val))
                chk.toggled.connect(lambda v, n=name: self._on_custom_param_changed(n, bool(v)))
                self.custom_layout.addRow(f"{name}:", chk)
                self._custom_widgets[name] = chk
            elif ptype == 'int':
                min_v = int(min_val) if min_val is not None else 1
                max_v = int(max_val) if max_val is not None else 10
                step = int(step_val) if step_val is not None else 1
                widget = DynamicLimitIntSlider(value=int(current_val), min_val=min_v, max_val=max_v, step=step)
                widget._slider.sliderPressed.connect(self._on_slider_pressed)
                widget._slider.sliderReleased.connect(self._on_slider_released)
                widget.valueChanged.connect(lambda v, n=name: self._on_custom_param_changed(n, int(v)))
                widget.limitsChanged.connect(lambda mi, ma, n=name, t=ptype, s=step: self._on_custom_param_limits_changed(n, t, int(mi), int(ma), int(s)))
                self.custom_layout.addRow(f"{name}:", widget)
                self._custom_widgets[name] = widget
            else:
                min_v = min_val if min_val is not None else 0.0
                max_v = max_val if max_val is not None else 5.0
                step = step_val if step_val is not None else 0.1
                decimals = 3
                if isinstance(step, float):
                    s = str(step)
                    if '.' in s:
                        decimals = len(s.split('.')[1])
                widget = DynamicLimitSlider(value=current_val, min_val=min_v, max_val=max_v, step=step, decimals=decimals)
                widget._slider.sliderPressed.connect(self._on_slider_pressed)
                widget._slider.sliderReleased.connect(self._on_slider_released)
                widget.valueChanged.connect(lambda v, n=name: self._on_custom_param_changed(n, float(v)))
                widget.limitsChanged.connect(lambda mi, ma, n=name, t=ptype, s=step: self._on_custom_param_limits_changed(n, t, float(mi), float(ma), float(s)))
                self.custom_layout.addRow(f"{name}:", widget)
                self._custom_widgets[name] = widget

    def _on_custom_param_changed(self, name, val):
        obj = self.tool._target_obj
        if not obj:
            return
        prop_name = "Custom_" + name
        if not hasattr(obj, prop_name):
            prop_type = "App::PropertyInteger" if isinstance(val, int) else "App::PropertyFloat"
            try:
                obj.addProperty(prop_type, prop_name, "Custom Params", f"Custom parameter {prop_name}")
            except Exception as e:
                fld_logger.warn(f"Failed to add dynamic property {prop_name}: {e}")
        setattr(obj, prop_name, val)
        self.tool._commit_changes()

    def _on_custom_param_limits_changed(self, name, ptype, min_val, max_val, step_val):
        obj = self.tool._target_obj
        if not obj:
            return
        formula = getattr(obj, "Formula", "")
        from freecad.fields.core.sdf.sdf.formula_eval import update_formula_param_comment
        new_formula = update_formula_param_comment(
            formula, name, ptype, getattr(obj, "Custom_" + name, 0.0), min_val, max_val, step_val
        )
        if new_formula != formula:
            obj.Formula = new_formula
            self.tool._commit_changes()

    def _on_params_changed(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.Amplitude = self.amp_slider.value()
        obj.Frequency = self.freq_slider.value()
        try:
            obj.Normalization = float(self.norm_input.text())
        except Exception as e:
            fld_logger.debug(f"NoiseTaskPanel._on_params_changed: Failed to set normalization: {e}")
        self.tool._commit_changes()

    def _on_limits_changed(self, min_val, max_val):
        obj = self.tool._target_obj
        if not obj:
            return
        for prop in ["AmplitudeMin", "AmplitudeMax", "FrequencyMin", "FrequencyMax"]:
            if not hasattr(obj, prop):
                obj.addProperty("App::PropertyFloat", prop, "Noise", "Persistent slider limit")
        obj.AmplitudeMin = self.amp_slider._min_spin.value()
        obj.AmplitudeMax = self.amp_slider._max_spin.value()
        obj.FrequencyMin = self.freq_slider._min_spin.value()
        obj.FrequencyMax = self.freq_slider._max_spin.value()
        self.tool._commit_changes()

    def _on_group_toggled(self):
        obj = self.tool._target_obj
        if not obj or not hasattr(obj, "Group"):
            return
        obj.Group = "Subtractive" if getattr(obj, "Group", "Additive") == "Additive" else "Additive"
        self.group_btn.setText(obj.Group)
        self.tool._commit_changes()

    def _on_type_changed(self, _index):
        obj = self.tool._target_obj
        if not obj:
            return
        name = self.type_combo.currentText()
        obj.NoiseType = name
        if name not in SdfNoiseField.COMPLEX_PRESETS:
            formula = SdfNoiseField.PRESETS.get(name, getattr(obj, "Formula", ""))
            try:
                import json
                from freecad.fields.core.gui.node_editor.nodes import (
                    get_template_graph, compile_graph,
                )
                tmpl = get_template_graph(name)
                if tmpl:
                    # The graph is the source of truth: compile the formula OUT of it
                    # rather than pasting PRESETS[name] beside it. Two independently
                    # written copies of the same formula drift, and the drift shows up
                    # as @params disappearing the first time the node editor is opened.
                    compiled, _comments, err = compile_graph(tmpl)
                    if err:
                        fld_logger.warn(
                            f"NoiseTaskPanel: template graph for {name!r} does not "
                            f"compile ({err}); falling back to the preset string")
                    else:
                        formula = compiled
                        obj.NodeGraphJson = json.dumps(tmpl)
            except Exception as e:
                fld_logger.debug(f"NoiseTaskPanel._on_type_changed: failed to set template graph: {e}")
            obj.Formula = formula
        self.tool._commit_changes()
        self._update_custom_widgets()

    def _open_node_editor(self):
        if self._node_editor_dlg:
            try:
                self._node_editor_dlg.show()
                self._node_editor_dlg.raise_()
                self._node_editor_dlg.activateWindow()
                return
            except Exception:
                self._node_editor_dlg = None

        from freecad.fields.core.gui.node_editor.node_editor_dialog import FldNoiseNodeEditorDialog
        obj = self.tool._target_obj if self.tool else None
        self._node_editor_dlg = FldNoiseNodeEditorDialog(
            target_obj=obj, tool=self.tool, parent=self.form
        )
        self._node_editor_dlg.show()

    def accept(self):
        if self._node_editor_dlg:
            try:
                self._node_editor_dlg.close()
            except Exception as exc:  # safe: best-effort dialog cleanup on accept
                fld_logger.debug(f"[noise_tool] _node_editor_dlg.close on accept failed: {exc}")
                pass
            self._node_editor_dlg = None

        self.tool._dialog_open = False
        self.tool.finish()
        return True

    def reject(self):
        if self._node_editor_dlg:
            try:
                self._node_editor_dlg.close()
            except Exception as exc:  # safe: best-effort dialog cleanup on reject
                fld_logger.debug(f"[noise_tool] _node_editor_dlg.close on reject failed: {exc}")
                pass
            self._node_editor_dlg = None

        self.tool._dialog_open = False
        self.tool.cancel()
        return True


class NoiseTool(FldSdfModifierToolBase, DirectionGizmoMixin, DragTimerMixin):
    SNAPSHOT_PROPERTIES = [
        ("Amplitude", 1.0),
        ("Frequency", 0.1),
        ("Normalization", 0.0),
        ("NoiseType", "Waves"),
        ("Formula", ""),
        ("Group", "Additive"),
        ("Placement", FreeCAD.Placement()),
    ]
    SNAPSHOT_CUSTOM_PREFIX = "Custom_"

    def get_command_id(self):
        return "Fields_EditObject"

    def get_handled_types(self):
        return ["FldNoiseProxy"]

    def __init__(self):
        super().__init__()
        self.fld_points = []
        self.points_root = coin.SoAnnotation()
        if self.view and self.view.getSceneGraph():
            self.view.getSceneGraph().addChild(self.points_root)

    def _make_panel(self):
        return NoiseTaskPanel(self)

    def _apply_gizmo_rotation(self, rot):
        """Apply one ring-drag world rotation to the whole Placement.

        Rebuilt from `_make_basis(new_dir, snapped_roll)` rather than composing
        `rot` directly onto `obj.Placement.Rotation`, so the result agrees with
        `roll_from_basis` (`update_ui`) bit-for-bit and snapping lands on the
        same roll value the panel displays.
        """
        obj = self._target_obj
        if not obj:
            return
        old_rot = obj.Placement.Rotation
        direction = old_rot.multVec(FreeCAD.Vector(0, 0, 1))
        u_axis = old_rot.multVec(FreeCAD.Vector(1, 0, 0))
        new_dir = rot.multVec(direction)
        new_dir.normalize()
        new_u = rot.multVec(u_axis)
        roll = SdfNoiseField.roll_from_basis(new_dir, new_u)
        from freecad.fields.core.input import fld_snap
        from freecad.fields.core.fld_settings import (
            get_snap_angle_step, get_snap_during_direct_drag)
        if get_snap_during_direct_drag() and fld_snap.snap_active():
            step = get_snap_angle_step()
            if step > 0:
                roll = fld_snap.snap_angle_deg(roll, step)
        u, _ = SdfNoiseField._make_basis(new_dir, roll)
        w = new_dir.cross(u)   # right-handed companion -- see _apply_direction_roll
        new_rotation = FreeCAD.Rotation(u, w, new_dir, "ZXY")
        obj.Placement = FreeCAD.Placement(obj.Placement.Base, new_rotation)

    def _gizmo_pivot(self):
        obj = self._target_obj
        if obj and hasattr(obj, "Placement"):
            return FreeCAD.Vector(obj.Placement.Base)
        return super()._gizmo_pivot()

    def _gizmo_origin_prop_names(self):
        return None

    def _gizmo_translate_target(self):
        # Center is this object's own Placement.Base, not the Source shape's.
        return self._target_obj

    def _gizmo_direction_vector(self):
        obj = self._target_obj
        return obj.Placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))

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
            tol = self._compute_handle_radius(self._gizmo_pivot()) * GIZMO_HIT_TOLERANCE_MULT
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
        tol = self._compute_handle_radius(self._gizmo_pivot()) * GIZMO_HIT_TOLERANCE_MULT
        return self._gizmo_try_start_drag(ray_p, ray_d, event_dict, tol)

    def _drag_update(self):
        if self._drag_check_lmb_released():
            return
        self._gizmo_drag_tick()

    def _do_terminate(self):
        try:
            self._gizmo_teardown()
        except Exception as e:
            fld_logger.debug(f"NoiseTool._do_terminate: Failed to teardown gizmo: {e}")
        try:
            if self.points_root and self.view and self.view.getSceneGraph():
                self.view.getSceneGraph().removeChild(self.points_root)
        except Exception as e:
            fld_logger.debug(f"NoiseTool._do_terminate exception: {e}")
        super()._do_terminate()

