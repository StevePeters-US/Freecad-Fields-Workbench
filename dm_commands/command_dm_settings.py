"""
DM Settings Command — configure the SDF meshing algorithm and resolution.
"""

import FreeCAD
import FreeCADGui
from PySide import QtGui, QtCore


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

        from FCDirectModeling.dm_object import get_show_wireframe, get_line_width, get_point_size
        current_wire = get_show_wireframe()
        current_lw = get_line_width()
        current_ps = get_point_size()

        layout = QtGui.QFormLayout(self)

        # Show Wireframe checkbox
        self._wire_check = QtGui.QCheckBox()
        self._wire_check.setChecked(current_wire)
        self._wire_check.setToolTip("Show triangle wireframe on NURBS objects")
        layout.addRow("Show Wireframe:", self._wire_check)

        # Line Width spinbox
        self._lw_spin = QtGui.QDoubleSpinBox()
        self._lw_spin.setRange(0.5, 20.0)
        self._lw_spin.setValue(current_lw)
        layout.addRow("Line Width:", self._lw_spin)

        # Point Size spinbox
        self._ps_spin = QtGui.QDoubleSpinBox()
        self._ps_spin.setRange(1.0, 50.0)
        self._ps_spin.setValue(current_ps)
        layout.addRow("Point Size:", self._ps_spin)

        # Buttons
        btn_box = QtGui.QDialogButtonBox(
            QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

    def _on_accept(self):
        from FCDirectModeling.dm_object import set_show_wireframe, set_line_width, set_point_size
        wire = self._wire_check.isChecked()
        lw = self._lw_spin.value()
        ps = self._ps_spin.value()

        set_show_wireframe(wire)
        set_line_width(lw)
        set_point_size(ps)

        FreeCAD.Console.PrintMessage(f"DM Settings: wire={wire}, lw={lw}, ps={ps}\n")
        self.accept()


FreeCADGui.addCommand('DM_Settings', CommandDMSettings())
