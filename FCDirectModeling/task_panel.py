# -*- coding: utf-8 -*-

# ***************************************************************************
# *   Copyright (c) 2024 Your Name                                          *
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENSE file.                                      *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Lesser General Public License for more details.                   *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307   *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

from PySide import QtCore, QtGui

class DirectModelingTaskPanel:
    def __init__(self):
        self.form = QtGui.QWidget()
        self.layout = QtGui.QVBoxLayout(self.form)
        
        self.label = QtGui.QLabel("Global SDF Settings")
        self.layout.addWidget(self.label)
        
        # Resolution Control
        self.res_layout = QtGui.QHBoxLayout()
        self.res_label = QtGui.QLabel("Default Resolution:")
        self.res_spin = QtGui.QSpinBox()
        self.res_spin.setRange(1, 256)
        
        # Import here to avoid circular imports if possible, or check if top-level is safe
        from FCDirectModeling import sdf_utils
        self.res_spin.setValue(sdf_utils.DEFAULT_RESOLUTION)
        
        self.res_layout.addWidget(self.res_label)
        self.res_layout.addWidget(self.res_spin)
        self.layout.addLayout(self.res_layout)
        
        # Apply to Selection Button
        self.apply_btn = QtGui.QPushButton("Apply to Selection")
        self.layout.addWidget(self.apply_btn)
        
        QtCore.QObject.connect(self.res_spin, QtCore.SIGNAL("valueChanged(int)"), self.on_resolution_changed)
        QtCore.QObject.connect(self.apply_btn, QtCore.SIGNAL("clicked()"), self.on_apply_clicked)

    def on_resolution_changed(self, val):
        from FCDirectModeling import sdf_utils
        sdf_utils.DEFAULT_RESOLUTION = val
        # FreeCAD.Console.PrintMessage(f"Default Resolution set to {val}\n")

    def on_apply_clicked(self):
        import FreeCADGui
        from FCDirectModeling import sdf_utils
        
        sel = FreeCADGui.Selection.getSelection()
        val = self.res_spin.value()
        
        count = 0
        for obj in sel:
            if hasattr(obj, "Resolution"):
                obj.Resolution = val
                count += 1
        
        # Recompute is not strictly necessary as Property change triggers it, 
        # but if we want to be sure:
        # FreeCAD.ActiveDocument.recompute()
        # FreeCAD.Console.PrintMessage(f"Applied resolution {val} to {count} objects.\n")

    def get_widget(self):
        return self.form

def create_task_panel():
    task_panel = DirectModelingTaskPanel()
    return task_panel

if __name__ == "__main__":
    # This is for testing purposes
    import sys
    app = QtGui.QApplication(sys.argv)
    panel = create_task_panel()
    widget = panel.get_widget()
    widget.show()
    sys.exit(app.exec_())
