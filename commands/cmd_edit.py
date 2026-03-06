import FreeCADGui
from PySide import QtCore
from core import dm_logger

class EditDMObjectCommand:
    """Activates the interactive edit tool for the selected DM Object."""
    
    def GetResources(self):
        return {
            'Pixmap': 'EditTool', # custom DM icon
            'MenuText': 'Edit Tool',
            'ToolTip': 'General tool editor for the selected Direct Modeling shape.'
        }
        
    def IsActive(self):
        if not FreeCADGui.ActiveDocument:
            return False
            
        sel = FreeCADGui.Selection.getSelection()
        if not sel: return False
        
        # Only active if a DM Curve is selected
        obj = sel[0]
        if hasattr(obj, "Proxy") and obj.Proxy.__class__.__name__ == "DMObjectProxy":
            if hasattr(obj, "ShapeType") and obj.ShapeType in ["curve"]:
                return True
        return False
        
    def Activated(self):
        # We must import inside Activated to avoid circular imports during FreeCAD init
        from tools import edit_tool
        # Defer execution to allow the UI to finish its current event loop
        QtCore.QTimer.singleShot(0, edit_tool.activate)

FreeCADGui.addCommand('DM_EditObject', EditDMObjectCommand())
