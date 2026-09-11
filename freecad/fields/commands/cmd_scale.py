# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Fields Scale Command — Scale objects or control points around a pivot."""
import FreeCADGui
from freecad.fields.commands.cmd_modifier_base import CommandFldTransformBase

FreeCADGui.addCommand(
    'Fields_Scale',
    CommandFldTransformBase(
        menu_text='Scale',
        tooltip='Scale selected objects or points',
        tool_module='freecad.fields.tools.scale_tool',
        tool_class='ScaleTool',
    ),
)
