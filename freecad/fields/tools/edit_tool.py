# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/edit_tool.py

Top-level dispatcher: inspects the selected document object's ShapeType
and proxy class, then routes to the appropriate editing tool (NURBS
curve/point/surface editing, SDF primitive/modifier editing).
"""
import FreeCADGui
from freecad.fields.core import fld_logger


def activate():
    """Dispatch function for editing curves, SDF primitives, noise fields, and modifiers."""
    sel = FreeCADGui.Selection.getSelection()
    if not sel:
        fld_logger.error("edit_tool.activate: No object selected.")
        return

    from freecad.fields.core.objects.fld_object_proxy import FldObjectProxy

    obj = sel[0]
    proxy = getattr(obj, "Proxy", None)
    shape_type = getattr(obj, "ShapeType", None)

    # FldObjectProxy has no subclasses, so this is the exact-name check it replaces --
    # spelled the same way as the other dispatch sites rather than by class name.
    if isinstance(proxy, FldObjectProxy):
        if shape_type == "curve":
            from freecad.fields.tools.nurbs_edit_tool import NurbsEditTool
            tool = NurbsEditTool()
            tool.activate()
            return
        elif shape_type == "surface":
            from freecad.fields.tools.sdf_face_tool import SdfFaceEditTool
            tool = SdfFaceEditTool()
            tool.edit_object(obj)
            return
        elif shape_type == "point":
            from freecad.fields.tools.nurbs_edit_tool import NurbsEditTool
            tool = NurbsEditTool()
            tool.activate()
            return

    from freecad.fields.tools.sdf_edit_tool import SdfEditTool
    tool = SdfEditTool()
    tool.activate()
