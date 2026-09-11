# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCADGui
from freecad.fields.commands.cmd_modifier_base import CommandFldModifierBase

FreeCADGui.addCommand('Fields_CreateHeightmapModifier', CommandFldModifierBase(
    op_name="Heightmap", name_suffix="_Heightmap",
    pixmap='SDF_Heightmap', menu_text='Add Heightmap Modifier',
    tooltip=(
        'Displace an SDF object using a grayscale heightmap image. '
        'White pixels displace the surface outward along the direction vector '
        'by up to Amplitude units. Drag the yellow sphere to adjust amplitude.'
    ),
    creator_module="freecad.fields.core.objects.fld_heightmap_object", creator_func="create_heightmap_modifier",
    tool_module="freecad.fields.tools.heightmap_tool", tool_class="HeightmapTool",
))
