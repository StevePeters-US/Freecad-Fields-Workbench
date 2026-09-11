# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
Fields Boolean commands — Fuse / Cut / Common using native BRep operations.

CR-064 split the SDF field-composition math out to
core/sdf/sdf_boolean_compose.py and the live-preview task panel out to
tools/boolean_task_panel.py (matching this codebase's own convention: every
other command's task panel lives in tools/, not commands/) -- this file now
only holds command dispatch and selection-gathering.
"""

import FreeCAD
import FreeCADGui
from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf_boolean_compose import build_boolean_field
from freecad.fields.tools.boolean_task_panel import BooleanTaskPanel


class CommandFldBoolean:
    _ICONS = {
        "Add":          "Fields_MakeAdd",
        "Subtract":     "Fields_MakeSubtract",
        "Intersection": "Fields_MakeIntersection",
    }
    _TOOLTIPS = {
        "Add":          "Union of all selected SDF objects. Select 2+ SDF objects, or re-select a boolean result to edit.",
        "Subtract":     "Subtract blue objects from orange. Select 2+ SDF objects, or re-select a boolean result to edit.",
        "Intersection": "Intersection of orange and blue groups. Select 2+ SDF objects, or re-select a boolean result to edit.",
    }

    def __init__(self, operation="Add"):
        self.operation = operation

    def GetResources(self):
        return {
            'Pixmap':  self._ICONS.get(self.operation, 'Part_Booleans.svg'),
            'MenuText': self.operation,
            'ToolTip':  self._TOOLTIPS.get(self.operation, f"Boolean {self.operation}."),
        }

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()

        # Edit mode: single boolean result of matching operation
        if (len(sel) == 1
                and getattr(sel[0], "BooleanOp",  None) == self.operation
                and getattr(sel[0], "ShapeType",   None) == "sdf"):
            self._edit_boolean(sel[0])
            return

        if len(sel) < 2:
            fld_logger.error(f"Fld_{self.operation}: select at least two SDF objects, "
                            "or re-select an existing boolean result to edit it.")
            return

        all_sdf = all(getattr(o, "ShapeType", None) == "sdf" for o in sel)
        if all_sdf:
            self._sdf_boolean_interactive(sel)
        else:
            fld_logger.error(
                f"Fld_{self.operation}: all selected objects must be Fields SDF objects "
                f"(ShapeType == 'sdf'). BRep boolean is not supported."
            )
            return

    # ------------------------------------------------------------------ helpers

    def _gather_fields(self, objects):
        """Return (orange_fields, blue_fields) from a list of SDF objects, or (None, None) on error."""
        orange, blue = [], []
        for obj in objects:
            proxy = getattr(obj, "Proxy", None)
            field = (proxy.get_sdf_field(obj) if proxy and hasattr(proxy, "get_sdf_field")
                     else getattr(proxy, "SdfField", None))
            if field is None:
                fld_logger.error(f"Fld_{self.operation}: '{obj.Label}' has no SdfField.")
                return None, None
            if getattr(obj, "Group", "Additive") == "Subtractive":
                blue.append(field)
            else:
                orange.append(field)
        return orange, blue

    # ------------------------------------------------------------------ creation

    def _sdf_boolean_interactive(self, sel):
        """Create result object (k=0), then show live-preview dialog."""
        from freecad.fields.core.objects.fld_object import (
            create_fld_object, finalize_new_object)
        doc = FreeCAD.activeDocument()

        try:
            orange_fields, blue_fields = self._gather_fields(sel)
            if orange_fields is None:
                return

            initial_field = build_boolean_field(self.operation, orange_fields, blue_fields, 0.0)
            if initial_field is None:
                return

            # Create result object with sharp (k=0) initial field
            result = create_fld_object(name=self.operation, shape_type="sdf")
            result.Proxy.SdfField = initial_field
            result.Group = "Additive"

            if not hasattr(result, "SdfType"):
                result.addProperty("App::PropertyString", "SdfType", "Sdf", "SDF primitive type")
            result.SdfType = "boolean"

            for prop, cat, desc, val in [
                ("BooleanOp",     "Boolean", "Operation type",                     self.operation),
                ("BooleanInputs", "Boolean", "Input SDF objects",                  list(sel)),
                ("SmoothK",       "Boolean", "Internal edge radius mm (0=sharp)",  0.0),
                ("BevelIdA",      "Boolean", "Bevel first surface IDs",            []),
                ("BevelIdB",      "Boolean", "Bevel second surface IDs",           []),
                ("BevelRadius",   "Boolean", "Bevel radius per edge (mm)",         []),
                ("BevelChamfer",  "Boolean", "Bevel chamfer mix (0=round, 1=flat)", []),
            ]:
                if not hasattr(result, prop):
                    if isinstance(val, str):
                        ptype = "App::PropertyString"
                    elif isinstance(val, list):
                        if prop in ("BevelIdA", "BevelIdB"):
                            ptype = "App::PropertyIntegerList"
                        elif prop in ("BevelRadius", "BevelChamfer"):
                            ptype = "App::PropertyFloatList"
                        else:
                            ptype = "App::PropertyLinkList"
                    else:
                        ptype = "App::PropertyFloat"
                    result.addProperty(ptype, prop, cat, desc)
                setattr(result, prop, val)

            for obj in sel:
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    obj.ViewObject.Visibility = False

            finalize_new_object(result)   # recompute registers it with the renderer

            panel = BooleanTaskPanel(self.operation, orange_fields, blue_fields,
                                     result, is_creation=True, sel_objects=list(sel))
            FreeCADGui.Control.showDialog(panel)

        except Exception as e:
            fld_logger.error(f"Fld_{self.operation} failed: {e}")

    # ------------------------------------------------------------------ edit

    def _edit_boolean(self, result):
        """Re-open the live-preview task panel for an existing boolean result."""
        try:
            inputs = getattr(result, "BooleanInputs", []) or []
            orange_fields, blue_fields = [], []
            for child in inputs:
                if child is None:
                    continue
                proxy = getattr(child, "Proxy", None)
                field = (proxy.get_sdf_field(child) if proxy and hasattr(proxy, "get_sdf_field")
                         else getattr(proxy, "SdfField", None))
                if field is None:
                    continue
                if getattr(child, "Group", "Additive") == "Subtractive":
                    blue_fields.append(field)
                else:
                    orange_fields.append(field)

            if not orange_fields and not blue_fields:
                fld_logger.error(f"Fld_{self.operation}: no input fields found for edit.")
                return

            panel = BooleanTaskPanel(self.operation, orange_fields, blue_fields,
                                     result, is_creation=False)
            FreeCADGui.Control.showDialog(panel)

        except Exception as e:
            fld_logger.error(f"Fld_{self.operation} edit failed: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Register commands
# ──────────────────────────────────────────────────────────────────────────────


FreeCADGui.addCommand('Fields_Add',          CommandFldBoolean("Add"))
FreeCADGui.addCommand('Fields_Subtract',     CommandFldBoolean("Subtract"))
FreeCADGui.addCommand('Fields_Intersection', CommandFldBoolean("Intersection"))
