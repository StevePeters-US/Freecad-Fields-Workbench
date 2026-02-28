import FreeCAD
import FreeCADGui
import Part
from FCDirectModeling import dm_logger, dm_object

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
            from FCDirectModeling.nurbs_geometry import DMCurve, DMPoint, DMSurface
            
            # 1. Reconstruct DMCurve from object properties
            pts = obj.Points
            h_in = getattr(obj, "HandleIn", [])
            h_out = getattr(obj, "HandleOut", [])
            dm_points = []
            for i, p in enumerate(pts):
                hi = h_in[i] if i < len(h_in) else None
                ho = h_out[i] if i < len(h_out) else None
                dm_points.append(DMPoint(p, handle_in=hi, handle_out=ho))
            
            curve = DMCurve(dm_points, is_closed=True)
            
            # 2. Helper to sample segments of the loop
            class CurveSegment:
                def __init__(self, curve, t_start, t_end):
                    self.curve = curve
                    self.t_start = t_start
                    self.t_end = t_end
                def value(self, t):
                    # Map 0-1 to segment range
                    mapped_t = self.t_start + t * (self.t_end - self.t_start)
                    return self.curve.value(mapped_t % 1.0)

            # 3. Create 4 boundaries for Coons Patch
            # Bottom (0-0.25), Right (0.25-0.5), Top (0.75-0.5), Left (1.0-0.75)
            # Reversing Top and Left ensures corners match S(u,v) orientations
            c1 = CurveSegment(curve, 0.0, 0.25)   # S(u, 0)
            c2 = CurveSegment(curve, 0.75, 0.5)  # S(u, 1)
            d1 = CurveSegment(curve, 1.0, 0.75)  # S(0, v)
            d2 = CurveSegment(curve, 0.25, 0.5)  # S(1, v)

            # 4. Generate pure NURBS surface poles grid
            surf_res = 8
            surf_logic = DMSurface.from_boundaries(c1, c2, d1, d2, res=surf_res)
            
            # 5. Create the surface object
            dm_object.create_dm_object(
                name="DMSurface",
                shape_type="surface",
                params={
                    "ControlGrid": surf_logic.control_grid,
                    "UCount": surf_res,
                    "VCount": surf_res
                },
                placement=obj.Placement
            )
            
            dm_logger.debug(f"Fill Curve: Created NURBS surface from {obj.Name} using Coons Patch.")
            
        except Exception as e:
            dm_logger.error(f"Fill Curve error: {e}")

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None

FreeCADGui.addCommand('DM_FillCurve', DM_FillCurve())
