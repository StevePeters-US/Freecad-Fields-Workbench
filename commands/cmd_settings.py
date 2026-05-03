"""
DM Settings Command — configure the SDF meshing algorithm and resolution.
"""

import FreeCAD
import FreeCADGui
from PySide import QtGui, QtCore
from core import dm_logger


class CommandDMSettings:
    def GetResources(self):
        return {
            'Pixmap':   'preferences-system',
            'MenuText': 'DM Settings',
            'ToolTip':  'Configure Direct Modeling settings (mesh algorithm, resolution…)',
        }

    def IsActive(self):
        return True

    def Activated(self):
        dlg = _SettingsDialog(FreeCADGui.getMainWindow())
        dlg.exec_()


class _SettingsDialog(QtGui.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Direct Modeling Settings")
        self.setMinimumWidth(320)

        from core.dm_object import (get_show_wireframe, get_line_width, get_point_size,
                                    get_interactive_throttle_interval, get_picking_radius, get_max_bounds)
        current_wire = get_show_wireframe()
        current_lw = get_line_width()
        current_ps = get_point_size()
        current_pr = get_picking_radius()
        current_mb = get_max_bounds()
        from core.dm_object import get_max_sdf_render_size
        current_mss = get_max_sdf_render_size()

        # Near Clip Distance spinbox
        from core.dm_object import get_near_clip_distance, get_ray_march_cell_size
        self._near_clip_spin = QtGui.QDoubleSpinBox()
        self._near_clip_spin.setRange(0.0, 10000.0)
        self._near_clip_spin.setSingleStep(1.0)
        self._near_clip_spin.setDecimals(1)
        self._near_clip_spin.setValue(get_near_clip_distance())
        self._near_clip_spin.setToolTip(
            "Override camera near clipping distance in mm.\n"
            "Set to 0 for automatic (FreeCAD default).\n"
            "Increase if SDF objects are clipped when zoomed in."
        )

        self._rm_res_spin = QtGui.QDoubleSpinBox()
        self._rm_res_spin.setRange(0.1, 20.0)
        self._rm_res_spin.setSingleStep(0.5)
        self._rm_res_spin.setDecimals(1)
        self._rm_res_spin.setValue(get_ray_march_cell_size())
        self._rm_res_spin.setToolTip(
            "SDF baking resolution for GPU ray march renderer in mm.\n"
            "Smaller = smoother surface, higher GPU memory. Default: 2.0 mm."
        )

        layout = QtGui.QFormLayout(self)

        # Show Wireframe checkbox
        self._wire_check = QtGui.QCheckBox()
        self._wire_check.setChecked(current_wire)
        self._wire_check.setToolTip("Show triangle wireframe on NURBS objects")
        layout.addRow("Show Wireframe:", self._wire_check)

        # Crash Logging
        from core.dm_logger import get_enable_crash_log
        self._log_check = QtGui.QCheckBox()
        self._log_check.setChecked(get_enable_crash_log())
        self._log_check.setToolTip("Enable persistent crash logging to DirectModeling.log")
        layout.addRow("Enable Crash Logs:", self._log_check)

        # Performance Profiler
        from core.dm_object import get_perf_profiler_enabled
        self._perf_check = QtGui.QCheckBox()
        self._perf_check.setChecked(get_perf_profiler_enabled())
        self._perf_check.setToolTip("Enable detailed mesh generation performance logging")
        layout.addRow("Enable Performance Profiler:", self._perf_check)

        # Line Width spinbox
        self._lw_spin = QtGui.QDoubleSpinBox()
        self._lw_spin.setRange(0.5, 20.0)
        self._lw_spin.setValue(current_lw)
        layout.addRow("Line Width:", self._lw_spin)

        # Picking Radius spinbox
        self._pr_spin = QtGui.QDoubleSpinBox()
        self._pr_spin.setRange(1.0, 50.0)
        self._pr_spin.setValue(current_pr)
        layout.addRow("Picking Radius (mm):", self._pr_spin)

        # Point Size spinbox
        self._ps_spin = QtGui.QDoubleSpinBox()
        self._ps_spin.setRange(1.0, 50.0)
        self._ps_spin.setValue(current_ps)
        layout.addRow("Point Size:", self._ps_spin)

        # Max Bounds spinbox
        self._mb_spin = QtGui.QDoubleSpinBox()
        self._mb_spin.setRange(100.0, 1000000.0) # 100mm to 1km
        self._mb_spin.setValue(current_mb)
        layout.addRow("Max Bounds (mm):", self._mb_spin)

        # Max SDF Render Size spinbox
        self._mss_spin = QtGui.QDoubleSpinBox()
        self._mss_spin.setRange(10.0, 100000.0) # 10mm to 100m
        self._mss_spin.setValue(current_mss)
        self._mss_spin.setToolTip("Maximum allowed dimension for an individual SDF field (mm). Larger fields will be clipped during preview rendering.")
        layout.addRow("Max SDF Render Size (mm):", self._mss_spin)

        # Interactive Throttle spinbox
        self._throttle_spin = QtGui.QDoubleSpinBox()
        self._throttle_spin.setRange(0.0, 1.0)
        self._throttle_spin.setSingleStep(0.005)
        self._throttle_spin.setDecimals(3)
        self._throttle_spin.setValue(get_interactive_throttle_interval())
        self._throttle_spin.setToolTip("Interactive update throttle interval in seconds (lower = more frequent updates but higher CPU)")
        layout.addRow("Interactive Throttle (s):", self._throttle_spin)

        layout.addRow("Near Clip Distance (mm):", self._near_clip_spin)
        layout.addRow("Ray March Resolution (mm):", self._rm_res_spin)

        # VDB Support
        from core.sdf.sdf_baker import has_openvdb
        vdb_status = "Installed" if has_openvdb() else "Not Installed"
        self._vdb_status_label = QtGui.QLabel(vdb_status)
        layout.addRow("OpenVDB Status:", self._vdb_status_label)

        if not has_openvdb():
            self._install_vdb_btn = QtGui.QPushButton("Install pyopenvdb via pip")
            self._install_vdb_btn.clicked.connect(self._on_install_vdb)
            layout.addRow("", self._install_vdb_btn)

        # Buttons
        btn_box = QtGui.QDialogButtonBox(
            QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

    def _on_accept(self):
        from core.dm_object import (set_show_wireframe, set_line_width, set_point_size,
                                    set_picking_radius, 
                                    set_max_bounds, set_perf_profiler_enabled,
                                    refresh_all_dm_objects,
                                     set_near_clip_distance, apply_near_clip_override,
                                     set_interactive_throttle_interval, set_ray_march_cell_size,
                                     set_max_sdf_render_size)
        from core.dm_logger import set_enable_crash_log
        wire = self._wire_check.isChecked()
        lw = self._lw_spin.value()
        ps = self._ps_spin.value()
        pr = self._pr_spin.value()
        pr = self._pr_spin.value()
        mb = self._mb_spin.value()

        set_show_wireframe(wire)
        set_line_width(lw)
        set_point_size(ps)
        set_picking_radius(pr)
        set_max_bounds(mb)
        set_max_sdf_render_size(self._mss_spin.value())
        set_perf_profiler_enabled(self._perf_check.isChecked())
        set_interactive_throttle_interval(self._throttle_spin.value())

        
        set_enable_crash_log(self._log_check.isChecked())
        
        set_near_clip_distance(self._near_clip_spin.value())
        apply_near_clip_override()

        set_ray_march_cell_size(self._rm_res_spin.value())

        # Apply to all existing objects
        refresh_all_dm_objects()

        dm_logger.info(f"DM Settings: wire={wire}, lw={lw}, ps={ps}, pr={pr}, mb={mb}")
        self.accept()

    def _on_install_vdb(self):
        """Try to install pyopenvdb via pip."""
        import subprocess
        import sys
        
        reply = QtGui.QMessageBox.question(
            self, "Install OpenVDB",
            "This will attempt to run 'pip install pyopenvdb'.\n\n"
            "This may take a few minutes. Continue?",
            QtGui.QMessageBox.Yes | QtGui.QMessageBox.No
        )
        if reply == QtGui.QMessageBox.No:
            return

        QtGui.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            # We use -m pip to ensure it uses the pip associated with this python executable
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pyopenvdb"])
            QtGui.QMessageBox.information(
                self, "Success",
                "pyopenvdb installed successfully.\nPlease restart FreeCAD to enable VDB support."
            )
            # Update status
            self._vdb_status_label.setText("Installed (Restart required)")
            if hasattr(self, "_install_vdb_btn"):
                self._install_vdb_btn.setEnabled(False)
        except Exception as e:
            dm_logger.error(f"VDB Installation failed: {e}")
            QtGui.QMessageBox.critical(
                self, "Installation Failed",
                f"Failed to install pyopenvdb:\n\n{e}\n\n"
                "You may need to run FreeCAD with administrator/root privileges or "
                "manually install pyopenvdb."
            )
        finally:
            QtGui.QApplication.restoreOverrideCursor()


FreeCADGui.addCommand('DM_Settings', CommandDMSettings())
