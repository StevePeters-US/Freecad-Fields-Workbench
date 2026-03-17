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
            print(f"[DM_CURVE] sel={[o.Label for o in sel] if sel else []}")
            if sel:
                obj = sel[0]
                proxy = getattr(obj, "Proxy", None)
                proxy_name = proxy.__class__.__name__ if proxy is not None else "None"
                shape_type = getattr(obj, "ShapeType", None)
                print(f"[DM_CURVE] proxy={proxy_name}, ShapeType={shape_type}")
                if (proxy is not None
                        and proxy_name == "DMObjectProxy"
                        and shape_type == "curve"):
                    from tools import edit_tool
                    print("[DM_CURVE] -> edit mode")
                    edit_tool.activate()
                    return
            print("[DM_CURVE] -> create new curve")
            self.creator = primitive_creators.CurveCreator()
        except Exception as e:
            print(f"[DM_CURVE] ERROR: {e}")
            import traceback
            traceback.print_exc()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand('DM_CreateCurve', CreateCurveCommand())
