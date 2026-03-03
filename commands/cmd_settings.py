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
        from core.dm_object import get_picking_radius
        current_pr = get_picking_radius()

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

        # Buttons
        btn_box = QtGui.QDialogButtonBox(
            QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

    def _on_accept(self):
        from core.dm_object import set_show_wireframe, set_line_width, set_point_size, set_picking_radius, refresh_all_dm_objects
        wire = self._wire_check.isChecked()
        lw = self._lw_spin.value()
        ps = self._ps_spin.value()
        pr = self._pr_spin.value()

        set_show_wireframe(wire)
        set_line_width(lw)
        set_point_size(ps)
        set_picking_radius(pr)
        
        from core.dm_logger import set_enable_crash_log
        set_enable_crash_log(self._log_check.isChecked())
        
        # Apply to all existing objects
        refresh_all_dm_objects()

        dm_logger.info(f"DM Settings: wire={wire}, lw={lw}, ps={ps}, pr={pr}")
        self.accept()


FreeCADGui.addCommand('DM_Settings', CommandDMSettings())
