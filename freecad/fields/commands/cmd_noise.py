# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCADGui
from freecad.fields.commands.cmd_modifier_base import CommandFldModifierBase

FreeCADGui.addCommand('Fields_CreateNoiseModifier', CommandFldModifierBase(
    op_name="Noise3D", name_suffix="_Noise3D",
    pixmap='SDF_Noise3D', menu_text='Add 3D Noise Modifier',
    tooltip='Add a 3D procedural sine-wave noise modifier to the selected SDF object.',
    creator_module="freecad.fields.core.objects.fld_noise_object", creator_func="create_noise_modifier",
    tool_module="freecad.fields.tools.noise_3d_tool", tool_class="Noise3DTool",
))

FreeCADGui.addCommand('Fields_Create2DNoiseModifier', CommandFldModifierBase(
    op_name="Noise2D", name_suffix="_Noise2D",
    pixmap='SDF_Noise2D', menu_text='Add 2D Noise Modifier',
    tooltip=(
        'Add a 2D projected noise modifier to the selected SDF object. '
        'Noise is evaluated in the plane perpendicular to the chosen direction.'
    ),
    creator_module="freecad.fields.core.objects.fld_noise2d_object", creator_func="create_noise2d_modifier",
    tool_module="freecad.fields.tools.noise_2d_tool", tool_class="Noise2DTool",
))
