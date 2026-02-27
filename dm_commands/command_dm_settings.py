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

        from FCDirectModeling.dm_object import get_show_wireframe
        current_wire = get_show_wireframe()

        layout = QtGui.QFormLayout(self)

        # Show Wireframe checkbox
        self._wire_check = QtGui.QCheckBox()
        self._wire_check.setChecked(current_wire)
        self._wire_check.setToolTip("Show triangle wireframe on NURBS objects")
        layout.addRow("Show Wireframe:", self._wire_check)

        # Buttons
        btn_box = QtGui.QDialogButtonBox(
            QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

    def _on_accept(self):
        from FCDirectModeling.dm_object import set_show_wireframe
        wire = self._wire_check.isChecked()
        set_show_wireframe(wire)
        FreeCAD.Console.PrintMessage(f"DM Settings: wireframe={wire}\n")
        self.accept()


FreeCADGui.addCommand('DM_Settings', CommandDMSettings())
