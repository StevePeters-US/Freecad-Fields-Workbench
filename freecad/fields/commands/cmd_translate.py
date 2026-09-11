# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Fields Translate Command — Move objects or control points with axis constraints."""
import FreeCADGui
from freecad.fields.commands.cmd_modifier_base import CommandFldTransformBase

FreeCADGui.addCommand(
    'Fields_Translate',
    CommandFldTransformBase(
        menu_text='Translate',
        tooltip='Translate selected objects or points (Hotkey: T)',
        tool_module='freecad.fields.tools.translate_tool',
        tool_class='TranslateTool',
        accel='T',
    ),
)
