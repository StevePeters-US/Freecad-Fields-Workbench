# SPDX-License-Identifier: CC-BY-NC-SA-4.0
from PySide import QtWidgets
from freecad.fields.core.input.fld_gui_utils import DynamicLimitSlider
from freecad.fields.core.sdf.sdf_curve_sweep import SdfCurveSweepField
from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip


class PathExtrudeTaskPanel(QtWidgets.QWidget):
    """Task panel for Extrude Along Path parameters."""

    # Panel label -> the ProfileType stored on the object.
    PROFILE_TYPES = (("Rectangle", "Rectangle"), ("Circle", "Circle"), ("Source Solid", "Source"))

    def __init__(self, tool):
        super().__init__()
        self.tool = tool
        self.form = self
        self._loading = False
        self._init_ui()
        self.load_from_tool()

    def _init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        prof_box = QtWidgets.QGroupBox("Profile Shape", self)
        prof_layout = QtWidgets.QFormLayout(prof_box)
        self.combo_type = QtWidgets.QComboBox(self)
        self.combo_type.addItems([label for label, _ in self.PROFILE_TYPES])
        prof_layout.addRow("Shape:", self.combo_type)

        self.w_slider = DynamicLimitSlider(value=10.0, min_val=0.5, max_val=100.0, step=1.0, decimals=2)
        self.h_slider = DynamicLimitSlider(value=6.0, min_val=0.5, max_val=100.0, step=1.0, decimals=2)
        self.r_slider = DynamicLimitSlider(value=5.0, min_val=0.5, max_val=100.0, step=1.0, decimals=2)
        prof_layout.addRow("Width (mm):", self.w_slider)
        prof_layout.addRow("Height (mm):", self.h_slider)
        prof_layout.addRow("Radius (mm):", self.r_slider)
        layout.addWidget(prof_box)

        deform_box = QtWidgets.QGroupBox("Deformation", self)
        deform_layout = QtWidgets.QFormLayout(deform_box)
        self.scale_start_slider = DynamicLimitSlider(value=1.0, min_val=0.1, max_val=5.0, step=0.05, decimals=3)
        self.scale_end_slider = DynamicLimitSlider(value=1.0, min_val=0.1, max_val=5.0, step=0.05, decimals=3)
        self.twist_slider = DynamicLimitSlider(value=0.0, min_val=-720.0, max_val=720.0, step=5.0, decimals=1)
        deform_layout.addRow("Start Scale:", self.scale_start_slider)
        deform_layout.addRow("End Scale:", self.scale_end_slider)
        deform_layout.addRow("Twist (°):", self.twist_slider)
        layout.addWidget(deform_box)

        cap_box = QtWidgets.QGroupBox("End Caps", self)
        cap_layout = QtWidgets.QFormLayout(cap_box)
        self.combo_cap = QtWidgets.QComboBox(self)
        self.combo_cap.addItems(list(SdfCurveSweepField.CAP_TYPES))
        self.combo_cap.setToolTip(
            rich_tooltip(
                "PathExtrudeTaskPanel",
                QT_TRANSLATE_NOOP(
                    "PathExtrudeTaskPanel",
                    "Shape of the caps at each end of the sweep.\nFlat ends squarely at each end point; Round adds spherical caps.",
                ),
            )
        )
        cap_layout.addRow("Ends:", self.combo_cap)
        layout.addWidget(cap_box)

        layout.addStretch(1)

        for slider in (self.w_slider, self.h_slider, self.r_slider,
                       self.scale_start_slider, self.scale_end_slider, self.twist_slider):
            slider.valueChanged.connect(self._on_param_changed)
        self.combo_cap.currentIndexChanged.connect(self._on_param_changed)
        self.combo_type.currentIndexChanged.connect(self._on_type_changed)
        self._update_visibility()

    def load_from_tool(self):
        """Seed the widgets from the tool, e.g. when editing an existing object."""
        params = self.tool.get_parameters()
        if not params:
            return
        self._loading = True
        try:
            labels = {stored: label for label, stored in self.PROFILE_TYPES}
            label = labels.get(params.get("ProfileType"), "Rectangle")
            self.combo_type.setCurrentIndex(max(0, self.combo_type.findText(label)))
            self.combo_cap.setCurrentIndex(max(0, self.combo_cap.findText(params.get("CapType", "Flat"))))
            self.w_slider.setValue(params.get("Width", 10.0))
            self.h_slider.setValue(params.get("Height", 6.0))
            self.r_slider.setValue(params.get("Radius", 5.0))
            self.scale_start_slider.setValue(params.get("StartScale", 1.0))
            self.scale_end_slider.setValue(params.get("EndScale", 1.0))
            self.twist_slider.setValue(params.get("Twist", 0.0))
        finally:
            self._loading = False
        self._update_visibility()

    def _update_visibility(self):
        """Grey the dimensions the chosen profile does not use.

        Hiding the widget alone would leave its QFormLayout label behind, so
        both halves are disabled instead -- the row stays, the value is clearly
        not in play.
        """
        is_rect = self.combo_type.currentText() == "Rectangle"
        is_circ = self.combo_type.currentText() == "Circle"
        for widget, enabled in ((self.w_slider, is_rect), (self.h_slider, is_rect),
                                (self.r_slider, is_circ)):
            widget.setEnabled(enabled)

    def _on_type_changed(self):
        self._update_visibility()
        self._on_param_changed()

    def current_parameters(self):
        profile_types = dict(self.PROFILE_TYPES)
        return {
            "ProfileType": profile_types.get(self.combo_type.currentText(), "Rectangle"),
            "Width": self.w_slider.value(),
            "Height": self.h_slider.value(),
            "Radius": self.r_slider.value(),
            "StartScale": self.scale_start_slider.value(),
            "EndScale": self.scale_end_slider.value(),
            "Twist": self.twist_slider.value(),
            "CapType": self.combo_cap.currentText(),
        }

    def _on_param_changed(self):
        if self._loading:
            return
        # set_parameters is PrimitiveCreatorBase's entry point: it applies the
        # values, pins them so the drag preview cannot overwrite them, and
        # redraws. The tool's own applier would skip all three.
        self.tool.set_parameters(self.current_parameters())

    def accept(self):
        self.tool._dialog_open = False
        self.tool.finish()
        return True

    def reject(self):
        self.tool._dialog_open = False
        self.tool.cancel()
        return True
