"""
DM Boolean commands — Fuse / Cut / Common using native BRep operations.
"""

import FreeCAD
import FreeCADGui
import Part
from core import dm_logger


class CommandDMBoolean:
    _OP_MAP = {
        "Add":   "union",
        "Subtract":    "cut",
        "Intersection": "intersect",
    }
    _ICONS = {
        "Add":   "MakeAdd",
        "Subtract":    "MakeSubtract",
        "Intersection": "MakeIntersection",
    }

    def __init__(self, operation="Add"):
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

        all_sdf = all(
            getattr(obj, "ShapeType", None) == "frep" for obj in sel
        )

        if all_sdf:
            self._sdf_boolean(sel)
        else:
            self._brep_boolean(sel)

    def _sdf_boolean(self, sel):
        """Compose SdfField trees for SDF objects."""
        from core.frep.frep_composer import UnionField, SubtractionField, IntersectionField
        from core.dm_object import create_dm_object

        _OP_CLASS = {
            "Add":          UnionField,
            "Subtract":     SubtractionField,
            "Intersection": IntersectionField,
        }

        try:
            # Extract fields from selected objects
            fields = []
            for obj in sel:
                proxy = getattr(obj, "Proxy", None)
                field = getattr(proxy, "SdfField", None) if proxy else None
                if field is None:
                    dm_logger.error(
                        f"DM_{self.operation}: '{obj.Label}' has no SdfField."
                    )
                    return
                fields.append(field)

            # Compose fields left-to-right
            composer_cls = _OP_CLASS[self.operation]
            result_field = fields[0]
            for i in range(1, len(fields)):
                result_field = composer_cls(result_field, fields[i])

            # Create new frep object with composed field
            new_name = f"{self.operation}"
            result = create_dm_object(name=new_name, shape_type="frep")
            result.Proxy.SdfField = result_field
            result.touch()

            doc = FreeCAD.activeDocument()

            # Hide originals
            for obj in sel:
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    obj.ViewObject.Visibility = False

            doc.recompute()
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(result)
            dm_logger.info(
                f"F-Rep {self.operation}: composed {len(fields)} fields."
            )
        except Exception as e:
            dm_logger.error(f"DM_{self.operation} (F-Rep) failed: {e}")

    def _brep_boolean(self, sel):
        """Existing BRep boolean logic for non-frep objects."""
        # Verify all selected objects are DM objects
        for obj in sel:
            if not hasattr(obj, "ShapeType"):
                dm_logger.error(
                    f"DM_{self.operation}: '{obj.Label}' is not a DM object."
                )
                return

        from core.dm_object import create_dm_object

        try:
            doc = FreeCAD.activeDocument()

            # Combine shapes using native Part operations
            shape_a = sel[0].Shape
            for i in range(1, len(sel)):
                shape_b = sel[i].Shape
                if self.operation == "Add":
                    shape_a = shape_a.fuse(shape_b)
                elif self.operation == "Subtract":
                    shape_a = shape_a.cut(shape_b)
                elif self.operation == "Intersection":
                    shape_a = shape_a.common(shape_b)

            new_name = f"{self.operation}"
            result = create_dm_object(
                name=new_name,
                shape_type="boolean",
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


FreeCADGui.addCommand('DM_Add',         CommandDMBoolean("Add"))
FreeCADGui.addCommand('DM_Subtract',    CommandDMBoolean("Subtract"))
FreeCADGui.addCommand('DM_Intersection', CommandDMBoolean("Intersection"))
