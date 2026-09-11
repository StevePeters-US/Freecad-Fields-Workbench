# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/noise_base_tool.py

Shared base edit tool and task panel for 2D and 3D procedural noise SDF modifiers.
"""
from PySide import QtWidgets, QtCore
from pivy import coin
from freecad.fields.tools.fld_base import DragTimerMixin
from freecad.fields.tools.fld_sdf_tool_base import FldSdfModifierToolBase
from freecad.fields.core import fld_logger
from freecad.fields.core.input.fld_gui_utils import (
    DynamicLimitSlider, DynamicLimitIntSlider, NumericLineEdit, CollapsibleSection,
)
from freecad.fields.core.input.formula_editor_widget import make_formula_editor


class BaseNoiseTaskPanel:
    def __init__(self, tool, group_title, hint_text):
        self.tool = tool
        self.is_2d = False
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
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        top_layout.addWidget(self.type_combo, 1)

        self.group_btn = QtWidgets.QPushButton("Additive")
        self.group_btn.setCheckable(False)
        self.group_btn.clicked.connect(self._on_group_toggled)
        self.group_btn.setToolTip("Toggle rendering group (Q)")
        self.group_btn.setStyleSheet("font-weight: bold; padding: 3px 8px;")
        top_layout.addWidget(self.group_btn)
        self.layout.addWidget(top_group)

        # 2. Main Parameters Section
        self.params_section = CollapsibleSection(group_title)
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

        # Hook to inject subclass-specific widgets
        self._setup_extra_widgets()

        # 3. Subclass Extra Sections (e.g. 2D projection & center)
        self._add_extra_sections()

        # 4. Custom Parameters Section
        self.custom_section = CollapsibleSection("Custom Parameters")
        self.custom_section.setExpanded(True)
        self.custom_layout = self.custom_section.content_layout
        self.custom_group = self.custom_section
        self.layout.addWidget(self.custom_section)
        self.custom_section.setVisible(False)
        self._custom_widgets = {}

        # 5. Visual Node Editor
        self._formula_edit = None
        self.node_editor_btn = QtWidgets.QPushButton("Visual Node Editor...")
        self.node_editor_btn.setStyleSheet(
            "font-weight: bold; padding: 7px; background-color: #0284c7; color: white; border-radius: 4px;"
        )
        self.node_editor_btn.setToolTip("Open visual node editor to design noise formulas and generator graphs interactively")
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

    def _setup_extra_widgets(self):
        pass

    def _add_extra_sections(self):
        pass

    def _get_presets_and_names(self):
        raise NotImplementedError

    def _get_preset_complex_presets(self):
        raise NotImplementedError

    def _update_extra_widgets(self, obj):
        pass

    def update_ui(self):
        obj = self.tool._target_obj
        if not obj:
            return
        self.amp_slider.blockSignals(True)
        self.freq_slider.blockSignals(True)
        self.norm_input.blockSignals(True)

        self._block_extra_widgets_signals(True)

        # Ensure properties exist
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

        self._update_extra_widgets(obj)

        self.amp_slider.blockSignals(False)
        self.freq_slider.blockSignals(False)
        self.norm_input.blockSignals(False)

        self._block_extra_widgets_signals(False)

        grp = getattr(obj, "Group", "Additive")
        self.group_btn.setText(grp)

        # Fall back to the tool's own first preset, not a hard-coded name --
        # "Sine" was the 3D default until the non-3D presets were removed, and
        # was never valid for the 2D tool at all. A name absent from the combo
        # leaves findText() at -1 and silently selects nothing.
        _, preset_names = self._get_presets_and_names()
        noise_type = getattr(obj, "NoiseType", preset_names[0] if preset_names else "Custom")
        self.type_combo.blockSignals(True)
        idx = self.type_combo.findText(noise_type)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        self.type_combo.blockSignals(False)
        self._refresh_formula(noise_type)
        self._update_custom_widgets()

    def _block_extra_widgets_signals(self, block):
        pass

    def _update_custom_widgets(self):
        obj = self.tool._target_obj
        if not obj:
            return

        while self.custom_layout.count() > 0:
            item = self.custom_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if self.type_combo.currentText() in self._get_preset_complex_presets():
            self.custom_group.hide()
            self._custom_widgets = {}
            return

        formula = getattr(obj, "Formula", "")
        from freecad.fields.core.sdf.sdf.noise import parse_custom_params
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
            else: # float or legacy slider
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
        from freecad.fields.core.sdf.sdf.noise import update_formula_param_comment
        new_formula = update_formula_param_comment(
            formula, name, ptype, getattr(obj, "Custom_" + name, 0.0), min_val, max_val, step_val
        )
        if new_formula != formula:
            obj.Formula = new_formula
            if hasattr(self, "_formula_edit") and self._formula_edit:
                self._formula_edit.blockSignals(True)
                self._formula_edit.setPlainText(new_formula)
                self._formula_edit.blockSignals(False)
            self.tool._commit_changes()

    def _refresh_formula(self, noise_type):
        presets, _ = self._get_presets_and_names()
        complex_presets = self._get_preset_complex_presets()
        obj = self.tool._target_obj
        if hasattr(self, "_formula_edit") and self._formula_edit:
            self._formula_edit.blockSignals(True)
            if noise_type in complex_presets:
                self._formula_edit.setReadOnly(True)
                self._formula_edit.setPlainText(complex_presets[noise_type].display)
            else:
                self._formula_edit.setReadOnly(False)
                self._formula_edit.setPlainText(
                    getattr(obj, "Formula", presets.get(noise_type, "")) if obj else ""
                )
            self._formula_edit.blockSignals(False)

    def _on_params_changed(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.Amplitude = self.amp_slider.value()
        obj.Frequency = self.freq_slider.value()
        try:
            obj.Normalization = float(self.norm_input.text())
        except Exception as e:
            fld_logger.debug(f"{type(self).__name__}._on_params_changed: Failed to set normalization: {e}")
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
        complex_presets = self._get_preset_complex_presets()
        if name not in complex_presets:
            presets, _ = self._get_presets_and_names()
            obj.Formula = presets.get(name, getattr(obj, "Formula", ""))
            try:
                from freecad.fields.core.gui.node_editor.node_definitions import get_template_graph
                tmpl = get_template_graph(name, getattr(self, "is_2d", False))
                if tmpl:
                    import json
                    obj.NodeGraphJson = json.dumps(tmpl)
            except Exception:
                pass
        self._refresh_formula(name)
        self.tool._commit_changes()
        self._update_custom_widgets()

    def _on_formula_applied(self):
        obj = self.tool._target_obj
        if not obj:
            return
        if hasattr(self, "_formula_edit") and self._formula_edit:
            obj.Formula = self._formula_edit.toPlainText().strip()
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
        is_2d = getattr(self, "is_2d", False)
        self._node_editor_dlg = FldNoiseNodeEditorDialog(
            target_obj=obj, tool=self.tool, is_2d=is_2d, parent=self.form
        )
        self._node_editor_dlg.show()

    def accept(self):
        if self._node_editor_dlg:
            try:
                self._node_editor_dlg.close()
            except Exception:
                pass
            self._node_editor_dlg = None

        if hasattr(self, "_formula_edit") and self._formula_edit and self.type_combo.currentText() not in self._get_preset_complex_presets():
            self._on_formula_applied()
        self.tool._dialog_open = False
        self.tool.finish()
        return True

    def reject(self):
        if self._node_editor_dlg:
            try:
                self._node_editor_dlg.close()
            except Exception:
                pass
            self._node_editor_dlg = None

        self.tool._dialog_open = False
        self.tool.cancel()
        return True


class BaseNoiseTool(FldSdfModifierToolBase, DragTimerMixin):
    SNAPSHOT_PROPERTIES = [
        ("Amplitude", 1.0),
        ("Frequency", 0.1),
        ("Normalization", 0.0),
        # "Custom" rather than a preset name: this default only fires for an
        # object with no NoiseType property at all, and it has to be valid in
        # both the 2D and 3D tools, which share no preset names.
        ("NoiseType", "Custom"),
        ("Formula", ""),
        ("Group", "Additive"),
    ]
    SNAPSHOT_CUSTOM_PREFIX = "Custom_"

    def get_command_id(self):
        return "Fields_EditObject"

    def __init__(self, root_class=coin.SoSeparator):
        super().__init__()
        self.fld_points = []
        self.points_root = root_class()
        if self.view and self.view.getSceneGraph():
            self.view.getSceneGraph().addChild(self.points_root)

    def _do_terminate(self):
        try:
            if self.points_root and self.view and self.view.getSceneGraph():
                self.view.getSceneGraph().removeChild(self.points_root)
        except Exception as e:
            fld_logger.debug(f"{type(self).__name__}._do_terminate exception: {e}")
        super()._do_terminate()
