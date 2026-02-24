import FreeCAD
import FreeCADGui

class CommandDMBoolean:
    """Base class for performing destructive Boolean operations (Fuse, Cut, Common) on DM_Parts."""
    def __init__(self, operation="Fuse"):
        self.operation = operation

    def GetResources(self):
        icon = 'Part_Booleans.svg'
        if self.operation == "Fuse": icon = 'Part_Fuse.svg'
        elif self.operation == "Cut": icon = 'Part_Cut.svg'
        elif self.operation == "Common": icon = 'Part_Common.svg'
            
        return {
            'Pixmap': icon,
            'MenuText': f"DM {self.operation}",
            'ToolTip': f"Select two or more DM_Parts and perform destructive {self.operation}."
        }

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if len(sel) < 2:
            FreeCAD.Console.PrintError(f"DM_{self.operation}: Please select at least two objects.\n")
            return
            
        # Perform destructive boolean using the shapes of the selected objects
        base_shape = sel[0].Shape.copy()
        
        try:
            for s in sel[1:]:
                tool_shape = s.Shape.copy()
                if self.operation == "Fuse":
                    base_shape = base_shape.fuse(tool_shape)
                elif self.operation == "Cut":
                    base_shape = base_shape.cut(tool_shape)
                elif self.operation == "Common":
                    base_shape = base_shape.common(tool_shape)
            
            # Create a new DM_Part to hold the result
            from FCDirectModeling.dm_part import create_dm_part
            res_obj = create_dm_part(f"DM_{self.operation}_Result")
            res_obj.Shape = base_shape
            
            # Hide or Delete original objects
            # For true destructive modeling, we delete them:
            doc = FreeCAD.activeDocument()
            for s in sel:
                 doc.removeObject(s.Name)
            
            doc.recompute()
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(res_obj)
            
            FreeCAD.Console.PrintMessage(f"DM_{self.operation} completed. Original objects deleted.\n")
            
        except Exception as e:
            FreeCAD.Console.PrintError(f"DM_{self.operation} Failed: {e}\n")


FreeCADGui.addCommand('DM_Fuse', CommandDMBoolean("Fuse"))
FreeCADGui.addCommand('DM_Cut', CommandDMBoolean("Cut"))
FreeCADGui.addCommand('DM_Common', CommandDMBoolean("Common"))
