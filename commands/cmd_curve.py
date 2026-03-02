import FreeCAD
import FreeCADGui
from FCDirectModeling import primitives as primitive_creators

class CreateCurveCommand:
    """
    Click-to-place NURBS (BSpline) curve points.
    """
    def GetResources(self):
        return {
            'Pixmap': 'Draft_BSpline', 
            'MenuText': 'Create Curve', 
            'ToolTip': 'Click-to-place NURBS (BSpline) curve points. Enter to finish.'
        }

    def Activated(self):
        try:
            self.creator = primitive_creators.CurveCreator()
        except Exception as e:
            FreeCAD.Console.PrintError(f"CreateCurve: Error: {e}\n")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand('DM_CreateCurve', CreateCurveCommand())
