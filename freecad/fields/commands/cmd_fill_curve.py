# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
import Part
from freecad.fields.core import fld_logger
from freecad.fields.core.objects import fld_object
from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip

class CommandFldFillCurve:
    """Command to fill a closed Fields curve with an SDF face."""
    
    def GetResources(self):
        return {
            'Pixmap': 'Fields_MakeFace', # Use custom icon
            'MenuText': 'Fill Curve',
            'ToolTip': rich_tooltip("Fields", QT_TRANSLATE_NOOP("Fields", "Creates an SDF face that fills the selected closed curve.\nSelect one closed curve.")),
            'Accel': 'Ctrl+F'
        }

    def Activated(self):
        from freecad.fields.core.input.fld_tool_manager import resolve_command_target

        obj = resolve_command_target()
        if obj is None:
            fld_logger.error("Fill Curve: Select a closed curve first.")
            return

        if not hasattr(obj, "ShapeType") or obj.ShapeType != "curve":
            fld_logger.error(
                f"Fill Curve: '{obj.Label}' is not a Fields curve "
                f"(ShapeType={getattr(obj, 'ShapeType', None)!r}).")
            return

        if not getattr(obj, "Closed", False):
            # Named, with the point count, because "must be closed" on a curve that
            # looks closed on screen says nothing about which of the two it is: a
            # curve whose ends merely coincide, or one whose Closed flag was lost.
            fld_logger.error(
                f"Fill Curve: '{obj.Label}' is not marked closed "
                f"(Closed=False, {len(getattr(obj, 'Points', []))} points). "
                "Close it with the curve tool, or toggle Closed from its right-click menu.")
            return

        surf_obj = None
        try:
            # 1. Create the surface object linked to the source curve
            surf_obj = fld_object.create_fld_object(
                name="FldSurface",
                shape_type="surface",
                params={
                    "UCount": 8,
                    "VCount": 8,
                    "SourceCurve": obj
                },
                placement=obj.Placement
            )

            fld_object.finalize_new_object(surf_obj)
            fld_logger.debug(f"Fill Curve: Created surface from {obj.Name}.")

            # 2. Select the surface object and activate the surface edit tool immediately
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(surf_obj)

            from freecad.fields.tools.sdf_face_tool import SdfFaceEditTool
            tool = SdfFaceEditTool()
            tool.edit_object(surf_obj)

        except Exception as e:
            fld_logger.error(f"Fill Curve error: {e}")
            if surf_obj is not None:
                try:
                    FreeCAD.ActiveDocument.removeObject(surf_obj.Name)
                except Exception as exc:
                    fld_logger.debug(f"[cmd_fill_curve] removeObject({surf_obj.Name}) failed: {exc}")
                    pass

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None

FreeCADGui.addCommand('Fields_FillCurve', CommandFldFillCurve())
