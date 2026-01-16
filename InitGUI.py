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
import FreeCAD
import FreeCADGui
import DirectModeling
import FCDirectModeling

# Register the icon path at module level
wb_path = os.path.dirname(FCDirectModeling.__file__)
# DirectModeling is now just the package. But we moved Resources UP one level from the package 'DirectModeling'?
# Wait, let's verify where 'Resources' ended up. 
# I moved 'DirectModeling/resources' to 'Freecad-Direct-Modeling/Resources'.
# And 'DirectModeling/__init__.py' is in 'Freecad-Direct-Modeling/DirectModeling'.
# So 'os.path.dirname(DirectModeling.__file__)' is 'Freecad-Direct-Modeling/DirectModeling'.
# So we need to go up one level.
root_path = os.path.dirname(os.path.dirname(FCDirectModeling.__file__))
icon_path = os.path.join(root_path, "Resources", "icons")
FreeCADGui.addIconPath(icon_path)

class DirectModelingWorkbench(FreeCADGui.Workbench):
    """
    Direct Modeling workbench class
    """

    MenuText = "Direct Modeling"
    ToolTip = "Direct Modeling workbench"
    Icon = "DirectModeling.svg"

    def Initialize(self):
        """
        This function is called during FreeCAD initialization
        """
        from commands import command_create_box, command_open_task_panel, command_draw_box

        self.list = [
            "DM_DrawBox",
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
