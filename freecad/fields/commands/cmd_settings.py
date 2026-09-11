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
        return {
            'Pixmap':   'preferences-system',
            'MenuText': 'Fields Settings',
            'ToolTip':  'Configure Fields settings (mesh algorithm, resolution…)',
        }

    def IsActive(self):
        return True

    def Activated(self):
        dlg = _SettingsDialog(FreeCADGui.getMainWindow())
        dlg.exec_()


FreeCADGui.addCommand('Fields_Settings', CommandFldSettings())
