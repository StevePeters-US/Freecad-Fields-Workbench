"""
DM Boolean commands — Fuse / Cut / Common using native BRep operations.
"""

import FreeCAD
import FreeCADGui
import Part


class CommandDMBoolean:
    _OP_MAP = {
        "Fuse":   "union",
        "Cut":    "cut",
        "Common": "intersect",
    }
    _ICONS = {
        "Fuse":   "Part_Fuse.svg",
        "Cut":    "Part_Cut.svg",
        "Common": "Part_Common.svg",
    }

    def __init__(self, operation="Fuse"):
        self.operation = operation

    def GetResources(self):
        return {
            'Pixmap':   self._ICONS.get(self.operation, 'Part_Booleans.svg'),
            'MenuText': f"{self.operation}",
            'ToolTip':  f"Select two objects and perform {self.operation}.",
        }

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if len(sel) < 2:
            FreeCAD.Console.PrintError(
                f"DM_{self.operation}: Select at least two objects.\n"
            )
            return

        # Verify all selected objects are DM objects
        for obj in sel:
            if not hasattr(obj, "ShapeType"):
                FreeCAD.Console.PrintError(
                    f"DM_{self.operation}: '{obj.Label}' is not a DM object.\n"
                )
                return

        from core.dm_object import create_dm_object

        try:
            doc = FreeCAD.activeDocument()
            
            # Combine shapes using native Part operations
            shape_a = sel[0].Shape
            for i in range(1, len(sel)):
                shape_b = sel[i].Shape
                if self.operation == "Fuse":
                    shape_a = shape_a.fuse(shape_b)
                elif self.operation == "Cut":
                    shape_a = shape_a.cut(shape_b)
                elif self.operation == "Common":
                    shape_a = shape_a.common(shape_b)
            
            new_name = f"{self.operation}"
            result = create_dm_object(
                name       = new_name,
                shape_type = "boolean",
            )
            result.Shape = shape_a

            # Hide originals
            for obj in sel:
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    obj.ViewObject.Visibility = False

            doc.recompute()
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(result)
            FreeCAD.Console.PrintMessage(
                f"{self.operation} operation completed.\n"
            )
        except Exception as e:
            FreeCAD.Console.PrintError(f"DM_{self.operation} failed: {e}\n")


FreeCADGui.addCommand('DM_Fuse',   CommandDMBoolean("Fuse"))
FreeCADGui.addCommand('DM_Cut',    CommandDMBoolean("Cut"))
FreeCADGui.addCommand('DM_Common', CommandDMBoolean("Common"))
