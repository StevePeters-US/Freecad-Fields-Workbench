import FreeCAD
import FreeCADGui
from tools.work_plane_tool import WorkPlaneCreator

class DM_WorkPlane:
    """Command to activate the WorkPlaneCreator tool."""

    def GetResources(self):
        return {
            'Pixmap': 'DM_WorkPlane',
            'MenuText': 'Set Work Plane',
            'ToolTip': 'Place a permanent Work Plane object',
            'Accel': 'W'
        }

    def Activated(self):
        WorkPlaneCreator()

    def getIsChecked(self):
        from core.dm_tool_manager import DMToolManager
        active_tool = DMToolManager.get_instance().get_active_tool()
        return active_tool is not None and active_tool.__class__.__name__ == "WorkPlaneCreator"

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand('DM_WorkPlane', DM_WorkPlane())
