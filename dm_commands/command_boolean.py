"""
DM Boolean commands — Union / Cut / Intersect on SDFObjects.
Each creates a new SDFObject whose execute() composes children's SDFs.
"""

import FreeCAD
import FreeCADGui


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
            'MenuText': f"SDF {self.operation}",
            'ToolTip':  f"Select two SDFObjects and perform SDF {self.operation}.",
        }

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if len(sel) < 2:
            FreeCAD.Console.PrintError(
                f"DM_{self.operation}: Select at least two SDF objects.\n"
            )
            return

        # Verify all selected objects are SDFObjects
        for obj in sel:
            if not hasattr(obj, "SDFType"):
                FreeCAD.Console.PrintError(
                    f"DM_{self.operation}: '{obj.Label}' is not an SDFObject. "
                    f"Only SDF primitives can be combined.\n"
                )
                return

        from FCDirectModeling.sdf_object import create_sdf_object

        sdf_op   = self._OP_MAP[self.operation]
        names    = [sel[0].Label, sel[1].Label]
        new_name = f"SDF_{self.operation}"

        try:
            result = create_sdf_object(
                name       = new_name,
                sdf_type   = "boolean",
                params     = {"op": sdf_op},
                sdf_op     = sdf_op,
                child_names = names,
            )

            # Hide originals (non-destructive; user can delete manually)
            doc = FreeCAD.activeDocument()
            for obj in sel:
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    obj.ViewObject.Visibility = False

            doc.recompute()
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(result)
            FreeCAD.Console.PrintMessage(
                f"SDF {self.operation} created from {names[0]} and {names[1]}.\n"
            )
        except Exception as e:
            FreeCAD.Console.PrintError(f"DM_{self.operation} failed: {e}\n")


FreeCADGui.addCommand('DM_Fuse',   CommandDMBoolean("Fuse"))
FreeCADGui.addCommand('DM_Cut',    CommandDMBoolean("Cut"))
FreeCADGui.addCommand('DM_Common', CommandDMBoolean("Common"))
