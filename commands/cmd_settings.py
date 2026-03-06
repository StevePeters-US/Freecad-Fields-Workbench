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

        from core.dm_object import get_show_wireframe, get_line_width, get_point_size
        current_wire = get_show_wireframe()
        current_lw = get_line_width()
        current_ps = get_point_size()
        from core.dm_object import get_picking_radius, get_meshing_type, get_max_bounds, get_meshing_resolution
        current_pr = get_picking_radius()
        current_meshing = get_meshing_type()
        current_res = get_meshing_resolution()
        current_mb = get_max_bounds()

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

        # Meshing Type ComboBox
        self._meshing_combo = QtGui.QComboBox()
        self._meshing_combo.addItems([
            "Marching Cubes (Standard SDF)",
            "Adaptive Marching Cubes",
            "NURBS based F-Rep approach"
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

        # Buttons
        btn_box = QtGui.QDialogButtonBox(
            QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

    def _on_accept(self):
        from core.dm_object import (set_show_wireframe, set_line_width, set_point_size, 
                                    set_picking_radius, set_meshing_type, set_meshing_resolution,
                                    set_max_bounds, set_perf_profiler_enabled, refresh_all_dm_objects)
        wire = self._wire_check.isChecked()
        lw = self._lw_spin.value()
        ps = self._ps_spin.value()
        pr = self._pr_spin.value()
        meshing = self._meshing_combo.currentIndex()
        res = self._res_spin.value()
        mb = self._mb_spin.value()

        set_show_wireframe(wire)
        set_line_width(lw)
        set_point_size(ps)
        set_picking_radius(pr)
        set_meshing_type(meshing)
        set_meshing_cell_size(self._res_spin.value())
        set_max_bounds(mb)
        set_perf_profiler_enabled(self._perf_check.isChecked())
        
        from core.dm_logger import set_enable_crash_log
        set_enable_crash_log(self._log_check.isChecked())
        
        # Apply to all existing objects
        refresh_all_dm_objects()

        dm_logger.info(f"DM Settings: wire={wire}, lw={lw}, ps={ps}, pr={pr}, meshing={meshing}, res={res}, mb={mb}")
        self.accept()


FreeCADGui.addCommand('DM_Settings', CommandDMSettings())
