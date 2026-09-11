# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
commands/cmd_sculpt.py

Fields_SculptBrush command -- activates SculptBrushTool on the selected SDF object.
"""
import FreeCADGui
from freecad.fields.core import fld_logger
from freecad.fields.core.objects.fld_modifier_stack import is_sdf_object


class CommandFldSculptBrush:
    """Command to start the interactive sculpt brush tool on the selected SDF object."""

    def GetResources(self):
        return {
            'Pixmap': 'SDF_SculptBrush',
            'MenuText': 'Sculpt Brush',
            'ToolTip': 'Interactively sculpt the selected SDF object with a 3D volumetric brush (Hold Ctrl to Carve, [ ] to resize).',
            'Accel': '',
        }

    def IsActive(self):
        sel = FreeCADGui.Selection.getSelection()
        if len(sel) != 1:
            return False
        return is_sdf_object(sel[0])

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel or not is_sdf_object(sel[0]):
            fld_logger.warn("Fields_SculptBrush: select exactly one SDF object to sculpt")
            return
        from freecad.fields.tools.sculpt_brush_tool import SculptBrushTool
        tool = SculptBrushTool()
        tool.edit_object(sel[0])


FreeCADGui.addCommand('Fields_SculptBrush', CommandFldSculptBrush())
