# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from PySide import QtCore


class CommandFldEditCage:
    """FreeCAD command: edit the selected cage with the interactive edit tool."""

    def GetResources(self):
        from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip
        return {
            "Pixmap":   "Fields_EditTool",
            "MenuText": "Edit Cage",
            "ToolTip":  rich_tooltip("Fields", QT_TRANSLATE_NOOP("Fields", "Opens the control net of the selected cage for editing.\nSelect one cage.")),
        }

    def IsActive(self):
        if not FreeCADGui.ActiveDocument:
            return False
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            return False
        obj = sel[0]
        return (getattr(obj, "ShapeType", None) == "sdf"
                and getattr(obj, "SdfType", None) == "cage")

    def Activated(self):
        from freecad.fields.tools import edit_tool
        QtCore.QTimer.singleShot(0, edit_tool.activate)

    def getIsChecked(self):
        from freecad.fields.core.input.fld_tool_manager import FldToolManager
        active_tool = FldToolManager.get_instance().get_active_tool()
        return active_tool is not None and active_tool.get_command_id() == "Fields_EditObject"


# DM_CageFromPrimitive (patch-cage-from-primitive) retired: the patch cage's
# analytic GLSL was removed (RM-001) in favor of the FFD deform cage, so a
# patch cage created from a primitive can no longer render. Use
# Fields_DeformCageFromPrimitive instead.
FreeCADGui.addCommand("Fields_EditCage", CommandFldEditCage())


def _primitive_to_deform_cage(obj):
    """Wrap any supported SDF primitive in a FldDeformCageProxy modifier."""
    from freecad.fields.core.objects.fld_deform_objects import create_deform_cage_modifier

    mod_obj = create_deform_cage_modifier(obj.Document, obj)
    if mod_obj is None:
        return

    # Activate the edit tool on the new modifier object
    def do_edit():
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(mod_obj)
        from freecad.fields.tools import edit_tool
        edit_tool.activate()
        
    QtCore.QTimer.singleShot(0, do_edit)


class CommandFldDeformCageFromPrimitive:
    """FreeCAD command: convert selected SDF primitive to an editable deform cage."""

    def GetResources(self):
        from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip
        return {
            "Pixmap":   "Fields_DeformCageFromPrimitive",
            "MenuText": "Convert to Deform Cage",
            "ToolTip":  rich_tooltip("Fields", QT_TRANSLATE_NOOP("Fields", "Wraps the selected SDF primitive in a cage that deforms it.\nSelect one SDF primitive.")),
        }

    def IsActive(self):
        if not FreeCADGui.ActiveDocument:
            return False
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            return False
        obj = sel[0]
        proxy = getattr(obj, "Proxy", None)
        if not proxy or not hasattr(proxy, "SdfField"):
            return False
        field = getattr(proxy, "SdfField", None)
        if field is None:
            return False
        from freecad.fields.core.objects.fld_deform_objects import can_build_deform_cage
        return can_build_deform_cage(field)

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            return
        QtCore.QTimer.singleShot(0, lambda: _primitive_to_deform_cage(sel[0]))


FreeCADGui.addCommand("Fields_DeformCageFromPrimitive", CommandFldDeformCageFromPrimitive())
