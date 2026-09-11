# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
import freecad.fields.tools as primitive_creators
from freecad.fields.core import fld_logger

class CommandFldCreateCurve:
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
            # FldBase now handles selection detection automatically in __init__
            self.creator = primitive_creators.CurveCreator()
        except Exception as e:
            fld_logger.error(f"CreateCurve: Error: {e}")

    def getIsChecked(self):
        from freecad.fields.core.input.fld_tool_manager import FldToolManager
        active_tool = FldToolManager.get_instance().get_active_tool()
        return isinstance(active_tool, primitive_creators.CurveCreator)

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

FreeCADGui.addCommand('Fields_CreateCurve', CommandFldCreateCurve())
