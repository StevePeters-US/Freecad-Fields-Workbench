# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
commands/cmd_sdf_slice.py

Slice an SDF SDF on a plane and create Fields curve objects.
"""
import FreeCAD
import FreeCADGui
from freecad.fields.core import fld_logger


class CommandFldSdfSlice:
    """Slice an SDF object to create cross-section curves."""

    def GetResources(self):
        return {
            'Pixmap': 'SDFSlice',
            'MenuText': 'SDF Slice',
            'ToolTip': 'Slice an SDF object on a plane to create cross-section curves.\n'
                       'Select an SDF object first. Slice plane starts at object placement and is steered from the panel.',
        }

    def Activated(self):
        try:
            sel = FreeCADGui.Selection.getSelection()
            if not sel:
                fld_logger.warn("SDFSlice: No object selected")
                return
            from freecad.fields.core.objects.fld_modifier_stack import stack_target
            obj = stack_target(sel[0]) or sel[0]
            proxy = getattr(obj, "Proxy", None)
            field = (proxy.get_sdf_field(obj) if (proxy and hasattr(proxy, "get_sdf_field"))
                     else getattr(proxy, "SdfField", None)) if proxy else None
            if field is None:
                fld_logger.warn("SDFSlice: Selected object has no SdfField")
                return

            # Slice plane originates at the part's own placement
            pl = getattr(obj, 'Placement', None)
            if pl:
                origin = FreeCAD.Vector(pl.Base)
                normal = pl.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
            else:
                origin = FreeCAD.Vector(0, 0, 0)
                normal = FreeCAD.Vector(0, 0, 1)

            from freecad.fields.tools.sdf_slice_tool import SdfSliceTool
            tool = SdfSliceTool()
            tool.start_slicing(obj, field, origin, normal)

        except Exception as e:
            fld_logger.exception(f"SDFSlice: Error: {e}")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


FreeCADGui.addCommand('Fields_SDFSlice', CommandFldSdfSlice())
