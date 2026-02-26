"""
DM Settings Command — configure the SDF meshing algorithm and resolution.
"""

import FreeCAD
import FreeCADGui
from PySide import QtGui, QtCore


class CommandDMSettings:
    def GetResources(self):
        return {
            'Pixmap':   'Std_Options',
            'MenuText': 'DM Settings',
            'ToolTip':  'Configure Direct Modeling settings (mesh algorithm, resolution…)',
        }

    def IsActive(self):
        return True

    def Activated(self):
        dlg = _SettingsDialog(FreeCADGui.getMainWindow())
        dlg.exec_()


class _SettingsDialog(QtGui.QDialog):
    _ALGORITHMS = [
        ("Surface Nets",     "surface_nets"),
        ("Dual Contouring",  "dual_contouring"),
        ("Marching Cubes",   "marching_cubes"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Direct Modeling Settings")
        self.setMinimumWidth(320)

        from FCDirectModeling.sdf_object import get_mesh_algorithm, get_mesh_resolution, get_show_wireframe
        current_algo = get_mesh_algorithm()
        current_res  = get_mesh_resolution()
        current_wire = get_show_wireframe()

        layout = QtGui.QFormLayout(self)

        # Algorithm dropdown
        self._algo_combo = QtGui.QComboBox()
        for label, key in self._ALGORITHMS:
            self._algo_combo.addItem(label, key)
        # Select current
        for i, (_, key) in enumerate(self._ALGORITHMS):
            if key == current_algo:
                self._algo_combo.setCurrentIndex(i)
                break
        layout.addRow("Meshing Algorithm:", self._algo_combo)

        # Resolution spinner
        self._res_spin = QtGui.QSpinBox()
        self._res_spin.setRange(8, 128)
        self._res_spin.setSingleStep(4)
        self._res_spin.setValue(current_res)
        self._res_spin.setToolTip("Voxel grid resolution for meshing (higher = more detail, slower)")
        layout.addRow("Resolution:", self._res_spin)

        # Show Wireframe checkbox
        self._wire_check = QtGui.QCheckBox()
        self._wire_check.setChecked(current_wire)
        self._wire_check.setToolTip("Show triangle wireframe on SDF primitives")
        layout.addRow("Show Wireframe:", self._wire_check)

        # Buttons
        btn_box = QtGui.QDialogButtonBox(
            QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

    def _on_accept(self):
        from FCDirectModeling.sdf_object import set_mesh_algorithm, set_mesh_resolution, set_show_wireframe
        algo = self._algo_combo.currentData()
        res  = self._res_spin.value()
        wire = self._wire_check.isChecked()
        set_mesh_algorithm(algo)
        set_mesh_resolution(res)
        set_show_wireframe(wire)
        FreeCAD.Console.PrintMessage(
            f"DM Settings: algorithm={algo}, resolution={res}, wireframe={wire}\n"
        )
        self.accept()


FreeCADGui.addCommand('DM_Settings', CommandDMSettings())
