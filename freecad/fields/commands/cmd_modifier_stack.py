# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
Fields Modifier Stack Command — toggle the modifier stack dock panel.
"""

import FreeCAD
import FreeCADGui


class CommandFldModifierStack:
    def GetResources(self):
        return {
            'Pixmap':   'Fields_OpenTaskPanel',
            'MenuText': 'Modifier Stack',
            'ToolTip':  'Toggle the Non-Destructive Modifier Stack dock panel',
        }

    def IsActive(self):
        return True

    def Activated(self):
        from freecad.fields.core.gui.modifier_stack_panel import ModifierStackPanel
        ModifierStackPanel.get_instance().toggle()


FreeCADGui.addCommand('Fields_ModifierStack', CommandFldModifierStack())
