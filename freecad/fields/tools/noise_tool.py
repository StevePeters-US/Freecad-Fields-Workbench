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
from freecad.fields.core.input.gizmo.transform import delta_rotation
from freecad.fields.core import fld_logger
from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip
from freecad.fields.core.input.fld_gui_utils import (
    DynamicLimitSlider, DynamicLimitIntSlider, NumericLineEdit,
    CompactVector3Widget, CollapsibleSection,
)
from freecad.fields.core.sdf.sdf.noise import SdfNoiseField


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
        self._fill_type_combo()
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

        self.wl_slider = DynamicLimitSlider(value=20.0, min_val=1.0, max_val=100.0, step=0.5, decimals=2)
        self.wl_slider.valueChanged.connect(self._on_params_changed)
        self.wl_slider.limitsChanged.connect(self._on_limits_changed)
        self.wl_slider._slider.sliderPressed.connect(self._on_slider_pressed)
        self.wl_slider._slider.sliderReleased.connect(self._on_slider_released)
        self.p_layout.addRow("Wavelength (mm):", self.wl_slider)

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
        self.wl_slider.blockSignals(True)
        self.norm_input.blockSignals(True)
        for w in (self.dir_x, self.dir_y, self.dir_z, self.roll_input,
                  self.center_x, self.center_y, self.center_z):
            w.blockSignals(True)

        self.amp_slider.setLimits(obj.AmplitudeMin, obj.AmplitudeMax)
        self.wl_slider.setLimits(obj.WavelengthMin, obj.WavelengthMax)

        self.amp_slider.setValue(obj.Amplitude)
        self.wl_slider.setValue(obj.Wavelength)
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
        self.wl_slider.blockSignals(False)
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
        params = parse_custom_params(formula)

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
            fld_logger.warn(f"NoiseTaskPanel._on_custom_param_changed: property {prop_name} does not exist")
            return
        setattr(obj, prop_name, val)
        self.tool._commit_changes()

    def _on_custom_param_limits_changed(self, name, ptype, min_val, max_val, step_val):
        obj = self.tool._target_obj
        if not obj:
            return
        import json
        from freecad.fields.core.objects.fld_noise_shared import apply_graph, set_graph_param_limits
        raw_graph = getattr(obj, "NodeGraphJson", "")
        if not raw_graph:
            fld_logger.warn("NoiseTaskPanel._on_custom_param_limits_changed: object has no NodeGraphJson")
            return
        try:
            graph = json.loads(raw_graph)
        except Exception as e:
            fld_logger.warn(f"NoiseTaskPanel._on_custom_param_limits_changed: invalid NodeGraphJson: {e}")
            return
        if set_graph_param_limits(graph, name, min_val, max_val, step_val):
            err = apply_graph(obj, graph, getattr(obj, "NoiseType", "Custom"))
            if err:
                fld_logger.warn(f"NoiseTaskPanel._on_custom_param_limits_changed: apply_graph failed: {err}")
            else:
                self.tool._commit_changes()
        else:
            fld_logger.warn(f"NoiseTaskPanel._on_custom_param_limits_changed: param {name!r} not found in graph")

    def _on_params_changed(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.Amplitude = self.amp_slider.value()
        obj.Wavelength = self.wl_slider.value()
        try:
            obj.Normalization = float(self.norm_input.text())
        except Exception as e:
            fld_logger.debug(f"NoiseTaskPanel._on_params_changed: Failed to set normalization: {e}")
        self.tool._commit_changes()

    def _on_limits_changed(self, min_val, max_val):
        obj = self.tool._target_obj
        if not obj:
            return
        for prop in ["AmplitudeMin", "AmplitudeMax", "WavelengthMin", "WavelengthMax"]:
            if not hasattr(obj, prop):
                obj.addProperty("App::PropertyFloat", prop, "Noise", "Persistent slider limit")
        obj.AmplitudeMin = self.amp_slider._min_spin.value()
        obj.AmplitudeMax = self.amp_slider._max_spin.value()
        obj.WavelengthMin = self.wl_slider._min_spin.value()
        obj.WavelengthMax = self.wl_slider._max_spin.value()
        self.tool._commit_changes()

    def _on_group_toggled(self):
        obj = self.tool._target_obj
        if not obj or not hasattr(obj, "Group"):
            return
        obj.Group = "Subtractive" if getattr(obj, "Group", "Additive") == "Additive" else "Additive"
        self.group_btn.setText(obj.Group)
        self.tool._commit_changes()

    def _fill_type_combo(self):
        from freecad.fields.core.gui.node_editor.nodes import NOISE_TYPE_NAMES
        from freecad.fields.core.objects.fld_noise_user_presets import user_preset_names
        self.type_combo.blockSignals(True)
        self.type_combo.clear()
        self.type_combo.addItems(NOISE_TYPE_NAMES)
        u_names = user_preset_names()
        if u_names:
            self.type_combo.insertSeparator(self.type_combo.count())
            self.type_combo.addItems(u_names)
        self.type_combo.blockSignals(False)

    def _on_type_changed(self, _index):
        obj = self.tool._target_obj
        if not obj:
            return
        name = self.type_combo.currentText()
        if not name:
            return

        import json
        from freecad.fields.tools.noise_preset_dialogs import confirm_graph_replace
        current_graph = None
        current_formula = getattr(obj, "Formula", "")
        if self._node_editor_dlg and getattr(self._node_editor_dlg, "scene", None):
            try:
                from freecad.fields.core.gui.node_editor.nodes import compile_graph
                sc = self._node_editor_dlg.scene
                current_graph = sc.get_graph_data()
                res = compile_graph(current_graph)
                if res and not res.err:
                    current_formula = res.formula
            except Exception:
                current_graph = None
        if current_graph is None:
            raw_json = getattr(obj, "NodeGraphJson", "")
            try:
                current_graph = json.loads(raw_json) if raw_json else None
            except Exception:
                current_graph = None

        if not confirm_graph_replace(self.form, obj, current_graph, current_formula):
            self.update_ui()
            return

        if name == "Custom":
            obj.NoiseType = "Custom"   # a label: the graph stays as it is
        else:
            from freecad.fields.core.objects.fld_noise_types import apply_picked
            err = apply_picked(obj, name)
            if err:
                fld_logger.warn(f"NoiseTaskPanel: {name!r} failed to apply ({err})")
        self._fill_type_combo()
        if self._node_editor_dlg:
            # An open editor still holds the previous graph; its next Apply would
            # write that back over the template just chosen.
            self._node_editor_dlg.load_from_object()
        self.tool._commit_changes()
        self.update_ui()
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


class NoiseTool(DirectionGizmoMixin, FldSdfModifierToolBase, DragTimerMixin):
    SNAPSHOT_PROPERTIES = [
        ("Amplitude", 1.0),
        ("Wavelength", 20.0),
        ("Normalization", 0.0),
        ("NoiseType", "Waves"),
        ("Formula", ""),
        ("NodeGraphJson", ""),
        ("Group", "Additive"),
        ("Placement", FreeCAD.Placement()),
    ]
    SNAPSHOT_CUSTOM_PREFIX = "Custom_"

    def restore_original(self):
        """apply_graph may have added or removed Custom_* properties since the
        snapshot, which the base restore (setattr on names that still exist) cannot
        undo. Re-apply the snapshot's graph first, then let the base restore the
        scalars (NV-008)."""
        obj = self._target_obj
        snap = getattr(self, "_original_props", None)
        if obj and snap and snap.get("NodeGraphJson"):
            import json
            from freecad.fields.core.objects.fld_noise_shared import apply_graph
            values = {k[len("Custom_"):]: v for k, v in snap.items() if k.startswith("Custom_")}
            err = apply_graph(obj, json.loads(snap["NodeGraphJson"]),
                              snap.get("NoiseType", "Custom"), values=values)
            if err:
                fld_logger.warn(f"NoiseTool.restore_original: snapshot graph not applied ({err})")
        super().restore_original()

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

    def _gizmo_apply_rotation(self, snap, delta):
        """Ring: the Placement's rotation = the snapshot's, turned by the total.

        Rebuilt from `_make_basis(new_dir, roll)` rather than composing the rotation onto
        the snapshot's, so the result agrees with `roll_from_basis` (`update_ui`)
        bit-for-bit, and Ctrl snapping lands on the roll the panel shows. A typed value is
        exact: never snapped.
        """
        pl0 = snap["_placement"]
        rot = delta_rotation(delta)
        new_dir = rot.multVec(pl0.Rotation.multVec(FreeCAD.Vector(0, 0, 1)))
        new_dir.normalize()
        new_u = rot.multVec(pl0.Rotation.multVec(FreeCAD.Vector(1, 0, 0)))
        roll = SdfNoiseField.roll_from_basis(new_dir, new_u)
        from freecad.fields.core.input import fld_snap
        from freecad.fields.core.fld_settings import get_snap_angle_step
        if not delta.typed and fld_snap.snap_active():
            step = get_snap_angle_step()
            if step > 0:
                roll = fld_snap.snap_angle_deg(roll, step)
        u, _ = SdfNoiseField._make_basis(new_dir, roll)
        w = new_dir.cross(u)   # right-handed companion -- see _apply_direction_roll
        self._target_obj.Placement = FreeCAD.Placement(
            pl0.Base, FreeCAD.Rotation(u, w, new_dir, "ZXY"))

    def _gizmo_snapshot_props(self):
        # No Direction properties: the whole state is the Placement (`_placement`).
        return ()

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
        super()._on_before_redraw()

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

