# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/modifiers/panel_tool.py

Edit tool and task panel for the Panel SDF modifier.
"""
import FreeCAD
from PySide import QtWidgets
from freecad.fields.tools.fld_sdf_tool_base import FldSdfModifierToolBase
from freecad.fields.tools.modifiers import axis_combo, BaseModifierTaskPanel, block_signals
from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip, make_double_spinbox, make_int_spinbox
from freecad.fields.core.sdf.sdf.panel import board_layout, AXES


def _tip(text):
    return rich_tooltip("PanelTaskPanel", text)


class PanelTaskPanel(BaseModifierTaskPanel):
    def __init__(self, tool):
        self.tool = tool
        self.form = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.form)
        layout.setContentsMargins(10, 10, 10, 10)

        grp = QtWidgets.QGroupBox("Panel Parameters")
        fl = QtWidgets.QFormLayout(grp)
        layout.addWidget(grp)

        self.axis_combo = axis_combo()
        self.axis_combo.setToolTip(_tip(QT_TRANSLATE_NOOP(
            "PanelTaskPanel", "Axis the panels are stacked along. Each panel is flat across it.")))
        self.axis_combo.currentIndexChanged.connect(self._on_axis_changed)
        fl.addRow("Axis:", self.axis_combo)

        self.thickness_spin = make_double_spinbox(
            19.0, 0.1, 1000.0, step=1.0, decimals=2, suffix=" mm",
            tooltip=_tip(QT_TRANSLATE_NOOP(
                "PanelTaskPanel", "Board thickness along the axis. Constant regardless of the wall's slope.")))
        self.thickness_spin.setKeyboardTracking(False)
        self.thickness_spin.valueChanged.connect(self._on_thickness_changed)
        fl.addRow("Thickness:", self.thickness_spin)

        self.count_spin = make_int_spinbox(
            6, 1, 500, step=1,
            tooltip=_tip(QT_TRANSLATE_NOOP(
                "PanelTaskPanel", "Number of boards filling the part. Edit it and Spacing follows (auto).")))
        self.count_spin.setKeyboardTracking(False)
        self.count_spin.valueChanged.connect(self._on_count_changed)
        self.count_label = QtWidgets.QLabel("Count:")
        fl.addRow(self.count_label, self.count_spin)

        self.spacing_spin = make_double_spinbox(
            19.0, 0.0, 1000.0, step=1.0, decimals=2, suffix=" mm",
            tooltip=_tip(QT_TRANSLATE_NOOP(
                "PanelTaskPanel", "Clear gap between boards. Edit it and Count follows (auto).")))
        self.spacing_spin.setKeyboardTracking(False)
        self.spacing_spin.valueChanged.connect(self._on_spacing_changed)
        self.spacing_label = QtWidgets.QLabel("Spacing:")
        fl.addRow(self.spacing_label, self.spacing_spin)

        self.group_btn = QtWidgets.QPushButton("Additive")
        self.group_btn.clicked.connect(self._on_group_toggled)
        fl.addRow("Group:", self.group_btn)

        layout.addStretch()
        self.update_ui()

    def update_ui(self):
        obj = self.tool._target_obj
        if not obj:
            return
        with block_signals(self.axis_combo, self.thickness_spin, self.count_spin, self.spacing_spin):
            idx = self.axis_combo.findText(getattr(obj, "Axis", "Z"))
            if idx >= 0:
                self.axis_combo.setCurrentIndex(idx)
            self.thickness_spin.setValue(getattr(obj, "Thickness", 19.0))
            self.count_spin.setValue(getattr(obj, "Count", 6))
            self.spacing_spin.setValue(getattr(obj, "Spacing", 19.0))
            self.group_btn.setText(getattr(obj, "Group", "Additive"))
        self._sync_derived()

    def _sync_derived(self):
        obj = self.tool._target_obj
        if not obj:
            return
        layout = PanelTool._derived(obj)
        locked = getattr(obj, "LayoutLock", "Spacing")

        # The one the user edited last drives; the other is worked out and says "(auto)".
        gap = layout[3] if layout is not None else None
        self.count_label.setText("Count:" if locked == "Count" else "Count (auto):")
        if gap is None:
            self.spacing_label.setText("Spacing (one board):")
        else:
            self.spacing_label.setText("Spacing:" if locked == "Spacing" else "Spacing (auto):")

        if layout is not None:
            _offset, _pitch, count, gap = layout
            with block_signals(self.count_spin, self.spacing_spin):
                if locked == "Spacing":
                    self.count_spin.setValue(count)
                elif locked == "Count" and gap is not None:
                    self.spacing_spin.setValue(gap)

    def _on_axis_changed(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.Axis = self.axis_combo.currentText()
        self.tool._commit_changes()
        self._sync_derived()

    def _on_thickness_changed(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.Thickness = self.thickness_spin.value()
        self.tool._commit_changes()
        self._sync_derived()

    def _on_count_changed(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.LayoutLock = "Count"
        obj.Count = self.count_spin.value()
        self.tool._commit_changes()
        self._sync_derived()

    def _on_spacing_changed(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.LayoutLock = "Spacing"
        obj.Spacing = self.spacing_spin.value()
        self.tool._commit_changes()
        self._sync_derived()


class PanelTool(FldSdfModifierToolBase):
    SNAPSHOT_PROPERTIES = [
        ("Axis", "Z"),
        ("Thickness", 19.0),
        ("Count", 6),
        ("Spacing", 19.0),
        ("LayoutLock", "Spacing"),
        ("Group", "Additive"),
    ]

    def get_command_id(self):
        return "Fields_EditObject"

    def get_handled_types(self):
        return ["FldPanelProxy"]

    @staticmethod
    def _derived(obj):
        """Returns board_layout(...) from cached extent, or None if no field."""
        if not obj:
            return None
        proxy = getattr(obj, "Proxy", None)
        field = proxy._resolve_source_field(obj) if proxy and hasattr(proxy, "_resolve_source_field") else None
        if field is None:
            return None
        lo, hi = proxy._extent(field, obj.Axis)
        return board_layout(
            lo, hi, getattr(obj, "Thickness", 19.0), getattr(obj, "LayoutLock", "Spacing"),
            getattr(obj, "Count", 6), getattr(obj, "Spacing", 19.0))

    def _make_panel(self):
        return PanelTaskPanel(self)
