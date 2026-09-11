# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/modifiers/twist_tool.py

Edit tool and task panel for the Twist SDF modifier.
"""
from PySide import QtWidgets
from freecad.fields.tools.fld_sdf_tool_base import FldSdfModifierToolBase
from freecad.fields.core.input.fld_gui_utils import DynamicLimitSlider
from freecad.fields.tools.modifiers import axis_combo, BaseModifierTaskPanel


class TwistTaskPanel(BaseModifierTaskPanel):
    def __init__(self, tool):
        self.tool = tool
        self.form = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.form)
        layout.setContentsMargins(10, 10, 10, 10)

        grp = QtWidgets.QGroupBox("Twist Parameters")
        fl = QtWidgets.QFormLayout(grp)
        layout.addWidget(grp)

        self.axis_combo = axis_combo()
        self.axis_combo.currentIndexChanged.connect(self._on_changed)
        fl.addRow("Axis:", self.axis_combo)

        self.rate_slider = DynamicLimitSlider(value=1.0, min_val=-180.0, max_val=180.0,
                                              step=0.5, decimals=2)
        self.rate_slider.valueChanged.connect(self._on_changed)
        fl.addRow("Angle/mm:", self.rate_slider)

        self.group_btn = QtWidgets.QPushButton("Additive")
        self.group_btn.clicked.connect(self._on_group_toggled)
        fl.addRow("Group:", self.group_btn)

        layout.addStretch()
        self.update_ui()

    def update_ui(self):
        obj = self.tool._target_obj
        if not obj:
            return
        self.axis_combo.blockSignals(True)
        self.rate_slider.blockSignals(True)
        idx = self.axis_combo.findText(getattr(obj, "Axis", "Z"))
        if idx >= 0:
            self.axis_combo.setCurrentIndex(idx)
        self.rate_slider.setValue(getattr(obj, "AnglePerUnit", 1.0))
        self.group_btn.setText(getattr(obj, "Group", "Additive"))
        self.axis_combo.blockSignals(False)
        self.rate_slider.blockSignals(False)

    def _on_changed(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.Axis = self.axis_combo.currentText()
        obj.AnglePerUnit = self.rate_slider.value()
        self.tool._commit_changes()


class TwistTool(FldSdfModifierToolBase):
    SUPPORTS_MODAL = False
    SNAPSHOT_PROPERTIES = [
        ("Axis", "Z"),
        ("AnglePerUnit", 1.0),
        ("Group", "Additive"),
    ]

    def get_command_id(self):
        return "Fields_EditObject"

    def get_handled_types(self):
        return ["FldTwistProxy"]

    def _make_panel(self):
        return TwistTaskPanel(self)
