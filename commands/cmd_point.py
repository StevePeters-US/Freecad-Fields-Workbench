import FreeCAD
import FreeCADGui
from tools.point_tool import PointCreator

class DM_CreatePoint:
    """Command to activate the PointCreator tool."""

    def GetResources(self):
        return {
            'Pixmap': 'Draft_Point',
            'MenuText': 'Create Point',
            'ToolTip': 'Place a DM point',
            'Accel': 'P'
        }

    def Activated(self):
        # Start the interactive tool
        PointCreator()

    def getIsChecked(self):
        from core.dm_tool_manager import DMToolManager
        active_tool = DMToolManager.get_instance().get_active_tool()
        return active_tool is not None and active_tool.__class__.__name__ == "PointCreator"

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand('DM_CreatePoint', DM_CreatePoint())
