# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCADGui
from freecad.fields.commands.cmd_modifier_base import CommandFldModifierBase
from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP

FreeCADGui.addCommand('Fields_CreateHeightmapModifier', CommandFldModifierBase(
    op_name="Heightmap", name_suffix="_Heightmap",
    pixmap='SDF_Heightmap', menu_text='Add Heightmap Modifier',
    tooltip=QT_TRANSLATE_NOOP("Fields", "Displaces the surface of the selected SDF object by a grayscale image; white pushes outward.\nSelect one SDF object."),
    creator_module="freecad.fields.core.objects.fld_heightmap_object", creator_func="create_heightmap_modifier",
    tool_module="freecad.fields.tools.heightmap_tool", tool_class="HeightmapTool",
))
