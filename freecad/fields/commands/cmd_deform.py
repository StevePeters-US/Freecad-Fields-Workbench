# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCADGui
from freecad.fields.commands.cmd_modifier_base import CommandFldModifierBase

FreeCADGui.addCommand('Fields_Twist', CommandFldModifierBase(
    op_name="Twist", name_suffix="_Twist",
    pixmap='SDF_Twist', menu_text='Add Twist Modifier',
    tooltip='Twist the selected SDF object around an axis.',
    creator_module="freecad.fields.core.objects.fld_deform_objects", creator_func="create_twist_modifier",
    tool_module="freecad.fields.tools.modifiers.twist_tool", tool_class="TwistTool",
))

FreeCADGui.addCommand('Fields_Bend', CommandFldModifierBase(
    op_name="Bend", name_suffix="_Bend",
    pixmap='SDF_Bend', menu_text='Add Bend Modifier',
    tooltip='Bend the selected SDF object around an axis.',
    creator_module="freecad.fields.core.objects.fld_deform_objects", creator_func="create_bend_modifier",
    tool_module="freecad.fields.tools.modifiers.bend_tool", tool_class="BendTool",
))

FreeCADGui.addCommand('Fields_Lattice', CommandFldModifierBase(
    op_name="Lattice", name_suffix="_Lattice",
    pixmap='SDF_Lattice', menu_text='Add Lattice Modifier',
    tooltip='Free-form lattice deformation of the selected SDF object.',
    creator_module="freecad.fields.core.objects.fld_deform_objects", creator_func="create_lattice_modifier",
    tool_module="freecad.fields.tools.modifiers.lattice_tool", tool_class="LatticeTool",
))

FreeCADGui.addCommand('Fields_Array', CommandFldModifierBase(
    op_name="Array", name_suffix="_Array",
    pixmap='SDF_Array', menu_text='Add Array Modifier',
    tooltip='Repeat the selected SDF object in a grid or around an axis.',
    creator_module="freecad.fields.core.objects.fld_deform_objects", creator_func="create_array_modifier",
    tool_module="freecad.fields.tools.modifiers.array_tool", tool_class="ArrayTool",
))

