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


"""FreeCAD GUI init file of the DirectModeling module"""

import os
import sys
import FreeCAD
import FreeCADGui

path = ""
if "DirectModeling" in sys.modules:
    mod = sys.modules["DirectModeling"]
    if hasattr(mod, "__file__"):
        path = os.path.dirname(mod.__file__)

ICON_PATH = os.path.join(path, "resources", "icons")

class DirectModelingWorkbench(FreeCADGui.Workbench):
    """
    Direct Modeling workbench class
    """

    MenuText = "Direct Modeling"
    ToolTip = "Direct Modeling workbench"
    Icon = os.path.join(ICON_PATH, "DirectModeling.svg")

    def Initialize(self):
        """
        This function is called during FreeCAD initialization
        """
        from .commands import command_create_box, command_open_task_panel

        self.list = [
            "DM_CreateBox",
            "DM_OpenTaskPanel",
        ]

        self.appendToolbar("Direct Modeling", self.list)
        self.appendMenu("Direct Modeling", self.list)

    def Activated(self):
        """
        This function is executed when the workbench is activated
        """
        return

    def Deactivated(self):
        """
        This function is executed when the workbench is deactivated
        """
        return


FreeCADGui.addWorkbench(DirectModelingWorkbench())
