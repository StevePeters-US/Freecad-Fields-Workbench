# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
Fields Settings Command — configure the SDF meshing algorithm and resolution.

CR-065 split the actual dialog out to core/gui/settings_dialog.py; this file
now only holds command registration.
"""

import FreeCADGui
from freecad.fields.core.gui.settings_dialog import _SettingsDialog


class CommandFldSettings:
    def GetResources(self):
        from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip
        return {
            'Pixmap':   'preferences-system',
            'MenuText': 'Fields Settings',
            'ToolTip':  rich_tooltip("Fields", QT_TRANSLATE_NOOP("Fields", "Opens the Fields settings for display, snapping, rendering and logging.")),
        }

    def IsActive(self):
        return True

    def Activated(self):
        dlg = _SettingsDialog(FreeCADGui.getMainWindow())
        dlg.exec_()


FreeCADGui.addCommand('Fields_Settings', CommandFldSettings())
