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
        
        self.label = QtGui.QLabel("This is a Direct Modeling task panel")
        self.layout.addWidget(self.label)
        
        self.button = QtGui.QPushButton("Click Me")
        self.layout.addWidget(self.button)
        
        QtCore.QObject.connect(self.button, QtCore.SIGNAL("clicked()"), self.button_clicked)

    def button_clicked(self):
        print("Button clicked!")

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
