import FreeCAD
import FreeCADGui
from FCDirectModeling.primitives.work_plane_creator import WorkPlaneCreator

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

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand('DM_WorkPlane', DM_WorkPlane())
