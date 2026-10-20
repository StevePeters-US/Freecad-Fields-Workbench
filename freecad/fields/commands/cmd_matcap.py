# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
Fields Matcap Command -- opens the matcap picker for the selected Fields objects.

The dialog itself is core/gui/matcap_dialog.py; this file only registers the command.
"""

import FreeCAD
import FreeCADGui


def _selected_matcap_objects():
    """The selected objects whose view provider carries a Matcap property."""
    objs = []
    for o in FreeCADGui.Selection.getSelection():
        vo = getattr(o, "ViewObject", None)
        if vo is not None and hasattr(vo, "Matcap"):
            objs.append(o)
    return objs


class CommandFldMatcap:
    def GetResources(self):
        from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip
        return {
            'Pixmap':   'Fields_Matcap',
            'MenuText': 'Matcap',
            'ToolTip':  rich_tooltip("Fields", QT_TRANSLATE_NOOP("Fields", "Picks the matcap material of the selected Fields objects and makes material variants (colour, metallic, roughness).\nSelect one or more Fields objects to apply a matcap; with none selected you can still make variants.")),
        }

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        from freecad.fields.core.gui.matcap_dialog import MatcapDialog
        dlg = MatcapDialog(FreeCAD.ActiveDocument, _selected_matcap_objects(),
                           FreeCADGui.getMainWindow())
        dlg.exec_()


FreeCADGui.addCommand('Fields_Matcap', CommandFldMatcap())
