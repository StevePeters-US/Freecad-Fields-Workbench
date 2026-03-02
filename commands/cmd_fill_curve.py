import FreeCAD
import FreeCADGui
import Part
from core import dm_logger, dm_object

class DM_FillCurve:
    """Command to fill a closed DM curve with a NURBS surface."""
    
    def GetResources(self):
        return {
            'Pixmap': 'Part_Compound', # Use standard icon
            'MenuText': 'Fill Curve',
            'ToolTip': 'Create a NURBS surface from a closed curve',
            'Accel': 'Ctrl+F'
        }

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            dm_logger.error("Fill Curve: Select a closed curve first.")
            return

        obj = sel[0]
        if not hasattr(obj, "ShapeType") or obj.ShapeType != "curve":
            dm_logger.error("Fill Curve: Selected object is not a DM curve.")
            return

        if not getattr(obj, "Closed", False):
            dm_logger.error("Fill Curve: The selected curve must be closed.")
            return

        try:            
            # 1. Create the surface object linked to the source curve
            dm_object.create_dm_object(
                name="DMSurface",
                shape_type="surface",
                params={
                    "UCount": 8,
                    "VCount": 8,
                    "SourceCurve": obj
                },
                placement=obj.Placement
            )
            
            dm_logger.debug(f"Fill Curve: Created LIVE NURBS surface from {obj.Name}.")
            
        except Exception as e:
            dm_logger.error(f"Fill Curve error: {e}")

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None

FreeCADGui.addCommand('DM_FillCurve', DM_FillCurve())
