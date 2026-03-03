"""
DM Boolean commands — Fuse / Cut / Common using native BRep operations.
"""

import FreeCAD
import FreeCADGui
import Part
from core import dm_logger


class CommandDMBoolean:
    _OP_MAP = {
        "Fuse":   "union",
        "Cut":    "cut",
        "Common": "intersect",
    }
    _ICONS = {
        "Fuse":   "Part_Fuse",
        "Cut":    "Part_Cut",
        "Common": "Part_Common",
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
            dm_logger.error(f"DM_{self.operation}: Select at least two objects.")
            return

        # Verify all selected objects are DM objects
        for obj in sel:
            if not hasattr(obj, "ShapeType"):
                dm_logger.error(f"DM_{self.operation}: '{obj.Label}' is not a DM object.")
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
            dm_logger.info(f"{self.operation} operation completed.")
        except Exception as e:
            dm_logger.error(f"DM_{self.operation} failed: {e}")


FreeCADGui.addCommand('DM_Fuse',   CommandDMBoolean("Fuse"))
FreeCADGui.addCommand('DM_Cut',    CommandDMBoolean("Cut"))
FreeCADGui.addCommand('DM_Common', CommandDMBoolean("Common"))
