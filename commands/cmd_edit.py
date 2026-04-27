import FreeCADGui
from PySide import QtCore

class EditDMObjectCommand:
    """Activates the lattice editor for modifying a lattice-driven SDF object.

    NOTE: Scope is lattice-driven SDF editing, NOT curve editing.
    Curve editing is accessed via the curve button when a curve is selected.
    """

    def GetResources(self):
        return {
            'Pixmap': 'EditTool', # custom DM icon
            'MenuText': 'Edit Lattice',
            'ToolTip': 'Edit the lattice that drives the selected SDF shape.'
        }

    def IsActive(self):
        if not FreeCADGui.ActiveDocument:
            return False

        sel = FreeCADGui.Selection.getSelection()
        if not sel: return False

        # Active for lattice-driven SDF objects (sdf/implicit shapes)
        obj = sel[0]
        if hasattr(obj, "Proxy") and obj.Proxy.__class__.__name__ == "DMObjectProxy":
            if hasattr(obj, "ShapeType") and obj.ShapeType in ["sdf", "lattice"]:
                return True
        return False
        
    def Activated(self):
        # We must import inside Activated to avoid circular imports during FreeCAD init
        from tools import edit_tool
        # Defer execution to allow the UI to finish its current event loop
        QtCore.QTimer.singleShot(0, edit_tool.activate)

    def getIsChecked(self):
        from core.dm_tool_manager import DMToolManager
        active_tool = DMToolManager.get_instance().get_active_tool()
        return active_tool is not None and active_tool.get_command_id() == "DM_EditObject"

FreeCADGui.addCommand('DM_EditObject', EditDMObjectCommand())
