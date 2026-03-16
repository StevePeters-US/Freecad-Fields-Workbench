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
                                    get_interactive_throttle_interval, get_meshing_type,
                                    get_meshing_cell_size, get_picking_radius, get_max_bounds,
                                    get_curvature_threshold, get_decimate_enabled)
        current_wire = get_show_wireframe()
        current_lw = get_line_width()
        current_ps = get_point_size()
        current_meshing = get_meshing_type()
        current_pr = get_picking_radius()
        current_mb = get_max_bounds()
        current_curv = get_curvature_threshold()

        # Near Clip Distance spinbox
        from core.dm_object import get_near_clip_distance
        self._near_clip_spin = QtGui.QDoubleSpinBox()
        self._near_clip_spin.setRange(0.0, 10000.0)
        self._near_clip_spin.setSingleStep(1.0)
        self._near_clip_spin.setDecimals(1)
        self._near_clip_spin.setValue(get_near_clip_distance())
        self._near_clip_spin.setToolTip(
            "Override camera near clipping distance in mm.\n"
            "Set to 0 for automatic (FreeCAD default).\n"
            "Increase if F-Rep objects are clipped when zoomed in."
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

        # Decimation checkbox
        self._decimate_check = QtGui.QCheckBox()
        self._decimate_check.setChecked(get_decimate_enabled())
        self._decimate_check.setToolTip("Remove redundant triangles from flat faces")
        layout.addRow("Enable Decimation:", self._decimate_check)

        # Render Debug Mode
        from core.dm_object import get_render_debug_mode
        self._debug_check = QtGui.QCheckBox()
        self._debug_check.setChecked(get_render_debug_mode())
        self._debug_check.setToolTip("Show SDF bounding boxes and enable shader debug views")
        layout.addRow("Render Debug Mode:", self._debug_check)


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

        # Meshing Type ComboBox
        self._meshing_combo = QtGui.QComboBox()
        self._meshing_combo.addItems([
            "Marching Cubes (Standard SDF)",
            "Adaptive Marching Cubes",
            "Surface Nets",
            "Dual Contouring"
        ])
        self._meshing_combo.setCurrentIndex(current_meshing)
        layout.addRow("Meshing Type:", self._meshing_combo)

        # Meshing Cell Size spinbox
        self._res_spin = QtGui.QDoubleSpinBox()
        self._res_spin.setRange(0.1, 100.0)
        self._res_spin.setSingleStep(1.0)
        self._res_spin.setDecimals(2)
        self._res_spin.setValue(get_meshing_cell_size())
        self._res_spin.setToolTip("Global cell size for SDF meshing in millimeters (smaller = more detail)")
        layout.addRow("Meshing Cell Size (mm):", self._res_spin)

        # Curvature Threshold spinbox
        self._curv_spin = QtGui.QDoubleSpinBox()
        self._curv_spin.setRange(0.01, 1.0)
        self._curv_spin.setSingleStep(0.01)
        self._curv_spin.setDecimals(2)
        self._curv_spin.setValue(current_curv)
        self._curv_spin.setToolTip("Adaptive MC curvature threshold (lower = more detail on edges)")
        layout.addRow("Curvature Threshold:", self._curv_spin)

        # Interactive Throttle spinbox
        self._throttle_spin = QtGui.QDoubleSpinBox()
        self._throttle_spin.setRange(0.0, 1.0)
        self._throttle_spin.setSingleStep(0.005)
        self._throttle_spin.setDecimals(3)
        self._throttle_spin.setValue(get_interactive_throttle_interval())
        self._throttle_spin.setToolTip("Interactive update throttle interval in seconds (lower = more frequent updates but higher CPU)")
        layout.addRow("Interactive Throttle (s):", self._throttle_spin)

        layout.addRow("Near Clip Distance (mm):", self._near_clip_spin)

        # Buttons
        btn_box = QtGui.QDialogButtonBox(
            QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

    def _on_accept(self):
        from core.dm_object import (set_show_wireframe, set_line_width, set_point_size,
                                    set_picking_radius, set_meshing_type, set_meshing_cell_size,
                                    set_max_bounds, set_perf_profiler_enabled, set_curvature_threshold,
                                    set_decimate_enabled, refresh_all_dm_objects,
                                    set_near_clip_distance, apply_near_clip_override,
                                    set_render_debug_mode, set_interactive_throttle_interval)
        from core.dm_logger import set_enable_crash_log
        wire = self._wire_check.isChecked()
        lw = self._lw_spin.value()
        ps = self._ps_spin.value()
        pr = self._pr_spin.value()
        meshing = self._meshing_combo.currentIndex()
        res = self._res_spin.value()
        mb = self._mb_spin.value()
        curv = self._curv_spin.value()

        set_show_wireframe(wire)
        set_line_width(lw)
        set_point_size(ps)
        set_picking_radius(pr)
        set_meshing_type(meshing)
        set_meshing_cell_size(self._res_spin.value())
        set_max_bounds(mb)
        set_curvature_threshold(curv)
        set_decimate_enabled(self._decimate_check.isChecked())
        set_perf_profiler_enabled(self._perf_check.isChecked())
        set_render_debug_mode(self._debug_check.isChecked())
        set_interactive_throttle_interval(self._throttle_spin.value())

        
        set_enable_crash_log(self._log_check.isChecked())
        
        set_near_clip_distance(self._near_clip_spin.value())
        apply_near_clip_override()
        
        # Apply to all existing objects
        refresh_all_dm_objects()

        dm_logger.info(f"DM Settings: wire={wire}, lw={lw}, ps={ps}, pr={pr}, meshing={meshing}, res={res}, mb={mb}, curv={curv}")
        self.accept()


FreeCADGui.addCommand('DM_Settings', CommandDMSettings())
