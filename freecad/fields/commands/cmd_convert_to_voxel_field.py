# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
commands/cmd_convert_to_voxel_field.py

Command to discretize an analytic SDF solid into a discrete 3D voxel field.
"""
import FreeCAD
import FreeCADGui
from freecad.fields.commands.cmd_modifier_base import CommandFldModifierBase


class CommandFldConvertToVoxelField(CommandFldModifierBase):
    """Discretizes the selected SDF solid into a discrete 3D voxel field."""

    def __init__(self):
        super().__init__(
            op_name="Convert to SDF Field",
            name_suffix="_Voxel",
            pixmap="Fields_ConvertToVoxelField",
            menu_text="Convert to SDF Field",
            tooltip="Discretize the selected SDF solid into a 3D voxel field primitive.",
            creator_module="freecad.fields.core.objects.fld_voxel_field",
            creator_func="create_voxel_field_object",
            tool_module="freecad.fields.tools.tool_voxel_field",
            tool_class="VoxelFieldTool",
        )


class CommandFldCreateVoxelField:
    """Creates a new standalone 3D Voxel Field primitive and opens its editor."""

    def GetResources(self):
        return {
            "Pixmap": "Fields_CreateVoxelField",
            "MenuText": "Create SDF Field",
            "ToolTip": "Create a new discrete 3D voxel field primitive for sculpting.",
        }

    def Activated(self):
        from freecad.fields.core.objects.fld_voxel_field import create_voxel_field_object
        from freecad.fields.tools.edit_tool import activate as activate_edit_tool
        doc = FreeCAD.ActiveDocument
        if not doc:
            doc = FreeCAD.newDocument()
        obj = create_voxel_field_object(name="VoxelField")
        doc.recompute()
        if FreeCAD.GuiUp:
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(obj)
            # activate() takes no argument -- it reads FreeCADGui.Selection itself,
            # which is why the selection is set on the line above. Passing `obj`
            # raised TypeError on every click, and the test missed it because the
            # mock leaves GuiUp False and never entered this branch.
            activate_edit_tool()

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None


FreeCADGui.addCommand("Fields_ConvertToVoxelField", CommandFldConvertToVoxelField())
FreeCADGui.addCommand("Fields_CreateVoxelField", CommandFldCreateVoxelField())
