"""
DM Translate Command — Move objects or control points with axis constraints.
"""

import FreeCAD
import FreeCADGui
from tools.translate_tool import TranslateTool

class CommandDMTranslate:
    def GetResources(self):
        return {
            'Pixmap': 'view-unselectable', # Placeholder icon
            'MenuText': 'Translate',
            'ToolTip': 'Translate selected objects or points (Hotkey: T)',
            'Accel': 'T'
        }

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

    def Activated(self):
        # The tool handles its own selection logic
        TranslateTool()

FreeCADGui.addCommand('DM_Translate', CommandDMTranslate())
