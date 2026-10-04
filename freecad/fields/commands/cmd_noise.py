# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCADGui
from freecad.fields.commands.cmd_modifier_base import CommandFldModifierBase
from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP

FreeCADGui.addCommand('Fields_CreateNoiseModifier', CommandFldModifierBase(
    op_name="Noise", name_suffix="_Noise",
    pixmap='SDF_Noise', menu_text='Add Noise Modifier',
    tooltip=QT_TRANSLATE_NOOP("Fields", "Adds procedural noise to the surface of the selected SDF object.\nSelect one SDF object."),
    creator_module="freecad.fields.core.objects.fld_noise_object",
    creator_func="create_noise_modifier",
    tool_module="freecad.fields.tools.noise_tool", tool_class="NoiseTool",
))
