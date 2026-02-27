import FreeCAD
import FreeCADGui
from FCDirectModeling.primitives.point_creator import PointCreator

class DM_CreatePoint:
    """Command to activate the PointCreator tool."""

    def GetResources(self):
        return {
            'Pixmap': 'Draft_Point',
            'MenuText': 'Create Point',
            'ToolTip': 'Place a NURBS point',
            'Accel': 'P'
        }

    def Activated(self):
        # Start the interactive tool
        PointCreator()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand('DM_CreatePoint', DM_CreatePoint())
