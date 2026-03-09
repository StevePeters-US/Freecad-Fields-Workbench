import FreeCAD
import FreeCADGui
import tools as primitive_creators
from core import dm_logger

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
            sel = FreeCADGui.Selection.getSelection()
            if sel:
                obj = sel[0]
                if (hasattr(obj, "Proxy") and obj.Proxy.__class__.__name__ == "DMObjectProxy"
                        and getattr(obj, "ShapeType", None) == "curve"):
                    from PySide import QtCore
                    from tools import edit_tool
                    QtCore.QTimer.singleShot(0, edit_tool.activate)
                    return
            self.creator = primitive_creators.CurveCreator()
        except Exception as e:
            dm_logger.error(f"CreateCurve: Error: {e}")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand('DM_CreateCurve', CreateCurveCommand())
