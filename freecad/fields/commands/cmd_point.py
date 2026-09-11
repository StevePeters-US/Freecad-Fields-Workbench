# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from freecad.fields.tools.point_tool import PointCreator

class CommandFldCreatePoint:
    """Command to activate the PointCreator tool."""

    def GetResources(self):
        return {
            'Pixmap': 'Draft_Point',
            'MenuText': 'Create Point',
            'ToolTip': 'Place a Fields point',
            'Accel': 'P'
        }

    def Activated(self):
        # Start the interactive tool
        PointCreator()

    def getIsChecked(self):
        from freecad.fields.core.input.fld_tool_manager import FldToolManager
        active_tool = FldToolManager.get_instance().get_active_tool()
        return isinstance(active_tool, PointCreator)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand('Fields_CreatePoint', CommandFldCreatePoint())
