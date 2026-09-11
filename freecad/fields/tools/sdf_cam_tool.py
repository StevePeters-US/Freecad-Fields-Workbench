# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/sdf_cam_tool.py

Interactive tool, task panel, and path-feature helper for generating
Z-level CNC toolpaths directly from an SDF field. See
commands/cmd_sdf_cam.py for the thin command-registration wrapper
(CommandFldSdfCam).
"""

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtWidgets
from freecad.fields.core import fld_logger
from freecad.fields.tools.fld_sdf_tool_base import FldSdfToolBase


def create_path_feature(doc, label, path):
    """Create a Path::Feature document object with the given path.

    Returns the created document object.
    """
    feature = doc.addObject("Path::Feature", label)
    feature.Label = label
    feature.Path = path
    doc.recompute()
    return feature


class SdfCamTaskPanel:
    """Task panel for configuring and generating Z-level profiling toolpaths."""

    def __init__(self, tool):
        self.tool = tool
        self.generated_path = None
        self.form = QtWidgets.QWidget()
        self.setup_ui()

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self.form)
        form_layout = QtWidgets.QFormLayout()

        # Strategy
        self.strategy_combo = QtWidgets.QComboBox()
        self.strategy_combo.addItems(["Z-Level Profile", "Cage Flowline 3D"])
        self.strategy_combo.currentIndexChanged.connect(self.toggle_strategy)
        form_layout.addRow("Strategy:", self.strategy_combo)

        # Tool Radius
        self.tool_radius_spin = QtWidgets.QDoubleSpinBox()
        self.tool_radius_spin.setRange(0.0, 50.0)
        self.tool_radius_spin.setDecimals(2)
        self.tool_radius_spin.setValue(0.0)
        self.tool_radius_spin.setSuffix(" mm")
        form_layout.addRow("Tool Radius:", self.tool_radius_spin)

        # Step Over (Flowline 3D)
        self.step_over_spin = QtWidgets.QDoubleSpinBox()
        self.step_over_spin.setRange(0.01, 50.0)
        self.step_over_spin.setDecimals(2)
        self.step_over_spin.setValue(1.0)
        self.step_over_spin.setSuffix(" mm")
        self.step_over_spin.setEnabled(False)
        form_layout.addRow("Step Over:", self.step_over_spin)

        # Start Depth
        self.start_depth_spin = QtWidgets.QDoubleSpinBox()
        self.start_depth_spin.setRange(-500.0, 500.0)
        self.start_depth_spin.setDecimals(2)
        self.start_depth_spin.setValue(0.0)
        self.start_depth_spin.setSuffix(" mm")
        form_layout.addRow("Start Depth:", self.start_depth_spin)

        # Final Depth
        self.final_depth_spin = QtWidgets.QDoubleSpinBox()
        self.final_depth_spin.setRange(-500.0, 0.0)
        self.final_depth_spin.setDecimals(2)
        self.final_depth_spin.setValue(-5.0)
        self.final_depth_spin.setSuffix(" mm")
        form_layout.addRow("Final Depth:", self.final_depth_spin)

        # Step Down
        self.step_down_spin = QtWidgets.QDoubleSpinBox()
        self.step_down_spin.setRange(0.1, 100.0)
        self.step_down_spin.setDecimals(2)
        self.step_down_spin.setValue(2.0)
        self.step_down_spin.setSuffix(" mm")
        form_layout.addRow("Step Down:", self.step_down_spin)

        # Finish Step
        self.finish_step_spin = QtWidgets.QDoubleSpinBox()
        self.finish_step_spin.setRange(0.0, 10.0)
        self.finish_step_spin.setDecimals(2)
        self.finish_step_spin.setValue(0.0)
        self.finish_step_spin.setSuffix(" mm")
        form_layout.addRow("Finish Step:", self.finish_step_spin)

        # Clearance Z
        self.clearance_z_spin = QtWidgets.QDoubleSpinBox()
        self.clearance_z_spin.setRange(-500.0, 500.0)
        self.clearance_z_spin.setDecimals(2)
        self.clearance_z_spin.setValue(5.0)
        self.clearance_z_spin.setSuffix(" mm")
        form_layout.addRow("Clearance Z:", self.clearance_z_spin)

        # Feed Rate
        self.feed_rate_spin = QtWidgets.QDoubleSpinBox()
        self.feed_rate_spin.setRange(1.0, 10000.0)
        self.feed_rate_spin.setDecimals(1)
        self.feed_rate_spin.setValue(600.0)
        self.feed_rate_spin.setSuffix(" mm/min")
        form_layout.addRow("Feed Rate:", self.feed_rate_spin)

        # Plunge Feed
        self.plunge_feed_spin = QtWidgets.QDoubleSpinBox()
        self.plunge_feed_spin.setRange(1.0, 10000.0)
        self.plunge_feed_spin.setDecimals(1)
        self.plunge_feed_spin.setValue(150.0)
        self.plunge_feed_spin.setSuffix(" mm/min")
        form_layout.addRow("Plunge Feed:", self.plunge_feed_spin)

        # Tolerance — seeded from the global Model Tolerance (Fields Settings)
        from freecad.fields.core.objects.fld_object import get_model_tolerance
        self.tolerance_spin = QtWidgets.QDoubleSpinBox()
        self.tolerance_spin.setRange(0.001, 10.0)
        self.tolerance_spin.setDecimals(3)
        self.tolerance_spin.setValue(get_model_tolerance())
        self.tolerance_spin.setSuffix(" mm")
        form_layout.addRow("Tolerance:", self.tolerance_spin)

        # Max Control Points
        self.max_pts_spin = QtWidgets.QSpinBox()
        self.max_pts_spin.setRange(4, 1000)
        self.max_pts_spin.setValue(200)
        form_layout.addRow("Max Control Points:", self.max_pts_spin)

        # Check Gouging
        self.check_gouges_checkbox = QtWidgets.QCheckBox()
        self.check_gouges_checkbox.setChecked(True)
        form_layout.addRow("Check Gouging:", self.check_gouges_checkbox)

        # Adaptive Step Down
        self.adaptive_checkbox = QtWidgets.QCheckBox()
        self.adaptive_checkbox.setChecked(False)
        self.adaptive_checkbox.stateChanged.connect(self.toggle_adaptive)
        form_layout.addRow("Adaptive Step Down:", self.adaptive_checkbox)

        # Scallop Height
        self.scallop_height_spin = QtWidgets.QDoubleSpinBox()
        self.scallop_height_spin.setRange(0.001, 2.0)
        self.scallop_height_spin.setDecimals(3)
        self.scallop_height_spin.setValue(0.1)
        self.scallop_height_spin.setSuffix(" mm")
        self.scallop_height_spin.setEnabled(False)
        form_layout.addRow("Scallop Height:", self.scallop_height_spin)

        # Min Step Down
        self.min_step_down_spin = QtWidgets.QDoubleSpinBox()
        self.min_step_down_spin.setRange(0.01, 10.0)
        self.min_step_down_spin.setDecimals(2)
        self.min_step_down_spin.setValue(0.2)
        self.min_step_down_spin.setSuffix(" mm")
        self.min_step_down_spin.setEnabled(False)
        form_layout.addRow("Min Step Down:", self.min_step_down_spin)

        layout.addLayout(form_layout)

        # Generate Button
        self.btn_generate = QtWidgets.QPushButton("Generate Toolpath")
        self.btn_generate.clicked.connect(self.do_generate)
        layout.addWidget(self.btn_generate)

        # Export Button
        self.btn_export = QtWidgets.QPushButton("Export G-code...")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self.do_export)
        layout.addWidget(self.btn_export)

        layout.addStretch()

    def toggle_strategy(self, idx):
        is_flowline = (self.strategy_combo.currentText() == "Cage Flowline 3D")
        self.step_over_spin.setEnabled(is_flowline)
        self.start_depth_spin.setEnabled(not is_flowline)
        self.final_depth_spin.setEnabled(not is_flowline)
        self.step_down_spin.setEnabled(not is_flowline)
        self.finish_step_spin.setEnabled(not is_flowline)
        self.adaptive_checkbox.setEnabled(not is_flowline)
        if is_flowline:
            self.scallop_height_spin.setEnabled(True)
        else:
            self.toggle_adaptive(None)

    def toggle_adaptive(self, state):
        is_adaptive = self.adaptive_checkbox.isChecked()
        self.scallop_height_spin.setEnabled(is_adaptive)
        self.min_step_down_spin.setEnabled(is_adaptive)

    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.Close

    def reject(self):
        self.tool.cancel()
        return True

    def accept(self):
        self.tool._dialog_open = False
        self.tool.finish()
        return True

    def do_generate(self):
        try:
            strategy = self.strategy_combo.currentText()
            params = {
                "tool_radius": self.tool_radius_spin.value(),
                "step_over": self.step_over_spin.value(),
                "start_depth": self.start_depth_spin.value(),
                "final_depth": self.final_depth_spin.value(),
                "step_down": self.step_down_spin.value(),
                "finish_step": self.finish_step_spin.value(),
                "clearance_z": self.clearance_z_spin.value(),
                "feed_rate": self.feed_rate_spin.value(),
                "plunge_feed": self.plunge_feed_spin.value(),
                "tolerance": self.tolerance_spin.value(),
                "max_ctrl_pts": self.max_pts_spin.value(),
                "adaptive": self.adaptive_checkbox.isChecked(),
                "scallop_height": self.scallop_height_spin.value(),
                "min_step_down": self.min_step_down_spin.value(),
            }

            from freecad.fields.core.sdf.sdf_cam import build_cam_path, flowline_paths, detect_gouges

            # Generate path
            if strategy == "Cage Flowline 3D":
                path = flowline_paths(self.tool.field, params)
            else:
                path = build_cam_path(self.tool.field, params)
            self.generated_path = path

            # Run gouge detection if checked
            if self.check_gouges_checkbox.isChecked():
                gouges = detect_gouges(self.tool.field, path, params["tool_radius"])
                if gouges:
                    msg = f"Gouge Warning: {len(gouges)} gouge(s) detected. Please check log for details."
                    QtWidgets.QMessageBox.warning(self.form, "Gouge Warning", msg)
                else:
                    fld_logger.info("CAM: Gouge check passed successfully.")

            # Create Path::Feature deferred on the event loop for safety
            doc = FreeCAD.activeDocument()
            label = f"{self.tool.obj.Label}_CamProfile"

            def deferred_create():
                try:
                    create_path_feature(doc, label, path)
                    fld_logger.info(f"SDFCam: Created Path::Feature '{label}' successfully.")
                    self.btn_export.setEnabled(True)
                except Exception as e:
                    fld_logger.exception(f"SDFCam: Failed to create Path::Feature: {e}")

            QtCore.QTimer.singleShot(0, deferred_create)

        except Exception as e:
            fld_logger.exception(f"SDFCam: Error in generation: {e}")

    def do_export(self):
        if not self.generated_path:
            QtWidgets.QMessageBox.warning(self.form, "Export Error", "No path has been generated yet.")
            return

        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.form,
            "Export G-code",
            "",
            "G-code Files (*.gcode *.ngc);;All Files (*)"
        )

        if filename:
            try:
                from freecad.fields.core.sdf.sdf_cam import export_to_gcode
                export_to_gcode(self.generated_path, filename)
                QtWidgets.QMessageBox.information(self.form, "Export Successful", f"G-code exported to {filename} successfully.")
            except Exception as e:
                fld_logger.exception(f"SDFCam: Failed to export G-code: {e}")
                QtWidgets.QMessageBox.critical(self.form, "Export Error", f"Failed to export G-code: {e}")


class SdfCamTool(FldSdfToolBase):
    """Tool class coordinating the task panel dialog lifecycle."""

    SUPPORTS_MODAL = False

    def get_command_id(self):
        return "Fields_SDFCamProfile"

    def __init__(self):
        super().__init__()
        self.obj = None
        self.field = None

    def _get_sdf_obj(self):
        return self.obj

    def start_cam_panel(self, obj, field):
        self.obj = obj
        self.field = field
        self._is_editing = True

        self.panel = SdfCamTaskPanel(self)
        FreeCADGui.Control.showDialog(self.panel)
        self._dialog_open = True

    def finish(self):
        self._is_editing = False
        self.terminate()

    def update_preview(self):
        pass

