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

import os
import sys
import FreeCAD
import FreeCADGui
from PySide import QtGui
from .. import task_panel

path = ""
if "DirectModeling" in sys.modules:
    mod = sys.modules["DirectModeling"]
    if hasattr(mod, "__file__"):
        path = os.path.dirname(mod.__file__)

ICON_PATH = os.path.join(path, "resources", "icons")

class OpenTaskPanelCommand:
    """
    Open the Direct Modeling task panel
    """

    def GetResources(self):
        return {
            "Pixmap": os.path.join(ICON_PATH, "OpenTaskPanel.svg"),
            "MenuText": "Open Task Panel",
            "ToolTip": "Opens the Direct Modeling task panel",
        }

    def Activated(self):
        panel = task_panel.create_task_panel()
        FreeCADGui.Control.showDialog(panel)

    def IsActive(self):
        return True


FreeCADGui.addCommand("DM_OpenTaskPanel", OpenTaskPanelCommand())
