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


"""Create a box"""

import os
import sys
import FreeCAD
import FreeCADGui
from PySide import QtGui

path = ""
if "DirectModeling" in sys.modules:
    mod = sys.modules["DirectModeling"]
    if hasattr(mod, "__file__"):
        path = os.path.dirname(mod.__file__)

ICON_PATH = os.path.join(path, "resources", "icons")

class CreateBoxCommand:
    """
    Create a box in the current document
    """

    def GetResources(self):
        return {
            "Pixmap": os.path.join(ICON_PATH, "CreateBox.svg"),
            "MenuText": "Create Box",
            "ToolTip": "Creates a box in the current document",
        }

    def Activated(self):
        doc = FreeCAD.activeDocument()
        if not doc:
            doc = FreeCAD.newDocument()
        
        doc.addObject("Part::Box", "Box")
        doc.recompute()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


FreeCADGui.addCommand("DM_CreateBox", CreateBoxCommand())
