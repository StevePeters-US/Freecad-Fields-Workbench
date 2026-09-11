# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from freecad.fields.tools.work_plane_tool import WorkPlaneCreator

class CommandFldWorkPlane:
    """Command to activate the WorkPlaneCreator tool."""

    def GetResources(self):
        return {
            'Pixmap': 'Fields_WorkPlane',
            'MenuText': 'Set Work Plane',
            'ToolTip': 'Place a permanent Work Plane object',
            'Accel': 'W'
        }

    def Activated(self):
        WorkPlaneCreator()

    def getIsChecked(self):
        from freecad.fields.core.input.fld_tool_manager import FldToolManager
        active_tool = FldToolManager.get_instance().get_active_tool()
        return isinstance(active_tool, WorkPlaneCreator)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand('Fields_WorkPlane', CommandFldWorkPlane())
