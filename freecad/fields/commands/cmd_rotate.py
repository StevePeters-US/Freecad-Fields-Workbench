# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Fields Rotate Command — Rotate objects or control points with axis constraints."""
import FreeCADGui
from freecad.fields.commands.cmd_modifier_base import CommandFldTransformBase

FreeCADGui.addCommand(
    'Fields_Rotate',
    CommandFldTransformBase(
        menu_text='Rotate',
        tooltip='Rotate selected objects or points',
        tool_module='freecad.fields.tools.rotate_tool',
        tool_class='RotateTool',
    ),
)
