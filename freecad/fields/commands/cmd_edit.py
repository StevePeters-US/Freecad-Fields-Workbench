# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCADGui
from PySide import QtCore

class CommandFldEditObject:
    """Activates the lattice editor for modifying a lattice-driven SDF object.

    NOTE: Scope is lattice-driven SDF editing, NOT curve editing.
    Curve editing is accessed via the curve button when a curve is selected.
    """

    def GetResources(self):
        return {
            'Pixmap': 'Fields_EditTool', # custom Fields icon
            'MenuText': 'Edit Lattice',
            'ToolTip': ("Edit the lattice that drives the selected SDF shape."
                        "\n\nIn edit mode: G move, R rotate, S scale."
                        "\nX/Y/Z lock an axis (Shift for the perpendicular plane); "
                        "type a number for an exact value."
                        "\nEnter or left-click applies, Esc or right-click cancels.")
        }

    def IsActive(self):
        if not FreeCADGui.ActiveDocument:
            return False

        sel = FreeCADGui.Selection.getSelection()
        if not sel: return False

        # Active for lattice-driven SDF objects, curves, surfaces, and points.
        # Modifiers are a separate hierarchy (FldModifierProxyBase, ShapeType "sdf")
        # and SdfEditTool dispatches every one of them, so they belong here too --
        # gating on FldObjectProxy alone greyed the button out for all eight.
        from freecad.fields.core.objects.fld_object_proxy import FldObjectProxy
        from freecad.fields.core.objects.fld_modifier_proxy_base import FldModifierProxyBase
        obj = sel[0]
        if hasattr(obj, "Proxy") and isinstance(obj.Proxy, (FldObjectProxy, FldModifierProxyBase)):
            if hasattr(obj, "ShapeType") and obj.ShapeType in ["sdf", "lattice", "curve", "surface", "point"]:
                return True
        return False
        
    def Activated(self):
        # We must import inside Activated to avoid circular imports during FreeCAD init
        from freecad.fields.tools import edit_tool
        # Defer execution to allow the UI to finish its current event loop
        QtCore.QTimer.singleShot(0, edit_tool.activate)

    def getIsChecked(self):
        from freecad.fields.core.input.fld_tool_manager import FldToolManager
        active_tool = FldToolManager.get_instance().get_active_tool()
        return active_tool is not None and active_tool.get_command_id() == "Fields_EditObject"

FreeCADGui.addCommand('Fields_EditObject', CommandFldEditObject())
