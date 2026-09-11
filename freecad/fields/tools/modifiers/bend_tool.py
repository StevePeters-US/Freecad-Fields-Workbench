# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/modifiers/bend_tool.py

Edit tool and task panel for the Bend SDF modifier.
"""
from PySide import QtWidgets
from freecad.fields.tools.fld_sdf_tool_base import FldSdfModifierToolBase
from freecad.fields.core.input.fld_gui_utils import DynamicLimitSlider
from freecad.fields.tools.modifiers import axis_combo, BaseModifierTaskPanel


class BendTaskPanel(BaseModifierTaskPanel):
    def __init__(self, tool):
        self.tool = tool
        self.form = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.form)
        layout.setContentsMargins(10, 10, 10, 10)

        grp = QtWidgets.QGroupBox("Bend Parameters")
        fl = QtWidgets.QFormLayout(grp)
        layout.addWidget(grp)

        self.axis_combo = axis_combo()
        self.axis_combo.currentIndexChanged.connect(self._on_changed)
        fl.addRow("Axis:", self.axis_combo)

        self.angle_slider = DynamicLimitSlider(value=45.0, min_val=-360.0, max_val=360.0,
                                               step=1.0, decimals=1)
        self.angle_slider.valueChanged.connect(self._on_changed)
        fl.addRow("Bend Angle:", self.angle_slider)

        self.origin_slider = DynamicLimitSlider(value=0.0, min_val=-500.0, max_val=500.0,
                                                step=1.0, decimals=1)
        self.origin_slider.valueChanged.connect(self._on_changed)
        fl.addRow("Bend Origin:", self.origin_slider)

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
        self.angle_slider.blockSignals(True)
        self.origin_slider.blockSignals(True)
        idx = self.axis_combo.findText(getattr(obj, "Axis", "Z"))
        if idx >= 0:
            self.axis_combo.setCurrentIndex(idx)
        self.angle_slider.setValue(getattr(obj, "BendAngle", 45.0))
        self.origin_slider.setValue(getattr(obj, "BendOrigin", 0.0))
        self.group_btn.setText(getattr(obj, "Group", "Additive"))
        self.axis_combo.blockSignals(False)
        self.angle_slider.blockSignals(False)
        self.origin_slider.blockSignals(False)

    def _on_changed(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.Axis = self.axis_combo.currentText()
        obj.BendAngle = self.angle_slider.value()
        obj.BendOrigin = self.origin_slider.value()
        self.tool._commit_changes()


class BendTool(FldSdfModifierToolBase):
    SUPPORTS_MODAL = False
    SNAPSHOT_PROPERTIES = [
        ("Axis", "Z"),
        ("BendAngle", 45.0),
        ("BendOrigin", 0.0),
        ("Group", "Additive"),
    ]

    def get_command_id(self):
        return "Fields_EditObject"

    def get_handled_types(self):
        return ["FldBendProxy"]

    def _make_panel(self):
        return BendTaskPanel(self)
