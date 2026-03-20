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

    _TOOLTIPS = {
        "Add":          "Union of all selected SDF objects (orange and blue combined). Result is orange.",
        "Subtract":     "Subtract blue (Ctrl-drag) objects from orange objects. Select at least one orange and one blue.",
        "Intersection": "Intersection of orange and blue groups. Select at least one orange and one blue.",
    }

    def GetResources(self):
        return {
            'Pixmap':   self._ICONS.get(self.operation, 'Part_Booleans.svg'),
            'MenuText': f"{self.operation}",
            'ToolTip':  self._TOOLTIPS.get(self.operation, f"Boolean {self.operation}."),
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

def _fold_union(fields):
    """Left-associative UnionField fold. Returns None if list is empty."""
    from core.frep.frep_composer import UnionField
    if not fields:
        return None
    result = fields[0]
    for f in fields[1:]:
        result = UnionField(result, f)
    return result

def _recompose_boolean(fp):
    """Re-compose the SDF tree for a boolean result FP object from its BooleanInputs.
    
    Returns the composed SdfField, or None on failure.
    Called from DMObjectProxy.execute() when BooleanInputs are present.
    """
    from core.frep.frep_composer import UnionField, SubtractionField, IntersectionField
    op = getattr(fp, "BooleanOp", None)
    inputs = getattr(fp, "BooleanInputs", [])
    if not inputs or not op:
        return None

    orange_fields = []
    blue_fields   = []
    for child in inputs:
        if child is None:
            continue
        proxy = getattr(child, "Proxy", None)
        field = getattr(proxy, "SdfField", None)
        if field is None:
            continue
        if getattr(child, "IsSubtractive", False):
            blue_fields.append(field)
        else:
            orange_fields.append(field)

    if op == "Add":
        return _fold_union(orange_fields + blue_fields)
    elif op == "Subtract":
        orange = _fold_union(orange_fields)
        blue   = _fold_union(blue_fields)
        if orange is None or blue is None:
            return None
        return SubtractionField(orange, blue)
    elif op == "Intersection":
        orange = _fold_union(orange_fields)
        blue   = _fold_union(blue_fields)
        if orange is None or blue is None:
            return None
        return IntersectionField(orange, blue)
    return None

    def _sdf_boolean(self, sel):
        """Compose SdfField trees for SDF objects using two-color grouping."""
        from core.frep.frep_composer import UnionField, SubtractionField, IntersectionField
        from core.dm_object import create_dm_object

        try:
            orange_fields = []
            blue_fields   = []
            for obj in sel:
                proxy = getattr(obj, "Proxy", None)
                field = getattr(proxy, "SdfField", None)
                if field is None:
                    dm_logger.error(
                        f"DM_{self.operation}: '{obj.Label}' has no SdfField."
                    )
                    return
                if getattr(obj, "IsSubtractive", False):
                    blue_fields.append(field)
                else:
                    orange_fields.append(field)

            orange = _fold_union(orange_fields)
            blue   = _fold_union(blue_fields)

            if self.operation == "Add":
                all_fields = orange_fields + blue_fields
                if not all_fields:
                    dm_logger.error(f"DM_Add: No fields selected.")
                    return
                result_field = _fold_union(all_fields)

            elif self.operation == "Subtract":
                if not orange:
                    dm_logger.error("DM_Subtract: No orange (additive) fields selected.")
                    return
                if not blue:
                    dm_logger.error("DM_Subtract: No blue (subtractive) fields selected.")
                    return
                result_field = SubtractionField(orange, blue)

            elif self.operation == "Intersection":
                if not orange:
                    dm_logger.error("DM_Intersection: No orange (additive) fields selected.")
                    return
                if not blue:
                    dm_logger.error("DM_Intersection: No blue (subtractive) fields selected.")
                    return
                result_field = IntersectionField(orange, blue)

            else:
                dm_logger.error(f"DM: Unknown operation '{self.operation}'.")
                return

            # Create result object
            new_name = f"{self.operation}"
            result = create_dm_object(name=new_name, shape_type="frep")
            result.Proxy.SdfField = result_field
            result.IsSubtractive = False  # always orange

            # B-002: Store operation and inputs for parametric updates
            if not hasattr(result, "BooleanOp"):
                result.addProperty("App::PropertyString", "BooleanOp", "Boolean",
                                   "Boolean operation: Add / Subtract / Intersection")
            result.BooleanOp = self.operation

            if not hasattr(result, "BooleanInputs"):
                result.addProperty("App::PropertyLinkList", "BooleanInputs", "Boolean",
                                   "Child SDF objects that compose this boolean result")
            result.BooleanInputs = list(sel)

            doc = FreeCAD.activeDocument()
            for obj in sel:
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    obj.ViewObject.Visibility = False

            doc.recompute()
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(result)
            dm_logger.info(
                f"F-Rep {self.operation}: composed {len(sel)} fields "
                f"({len(orange_fields)} orange, {len(blue_fields)} blue)."
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
