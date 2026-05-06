"""
DM Boolean commands — Fuse / Cut / Common using native BRep operations.
"""

import FreeCAD
import FreeCADGui
from PySide import QtGui, QtCore
from core import dm_logger


class CommandDMBoolean:
    _ICONS = {
        "Add":          "MakeAdd",
        "Subtract":     "MakeSubtract",
        "Intersection": "MakeIntersection",
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
            dm_logger.error(f"DM_{self.operation}: select at least two SDF objects, "
                            "or re-select an existing boolean result to edit it.")
            return

        all_sdf = all(getattr(o, "ShapeType", None) == "sdf" for o in sel)
        if all_sdf:
            self._sdf_boolean_interactive(sel)
        else:
            self._brep_boolean(sel)

    # ------------------------------------------------------------------ helpers

    def _gather_fields(self, objects):
        """Return (orange_fields, blue_fields) from a list of SDF objects, or (None, None) on error."""
        orange, blue = [], []
        for obj in objects:
            field = getattr(getattr(obj, "Proxy", None), "SdfField", None)
            if field is None:
                dm_logger.error(f"DM_{self.operation}: '{obj.Label}' has no SdfField.")
                return None, None
            if getattr(obj, "Group", "Group 1") == "Group 2":
                blue.append(field)
            else:
                orange.append(field)
        return orange, blue

    def _build_result_field(self, orange_fields, blue_fields, k):
        """Build the composed SDF field for this operation at smoothness k."""
        from core.sdf.sdf_composer import (SubtractionField, IntersectionField,
                                           SmoothSubtractionField, SmoothIntersectionField)
        smooth = k > 0.0

        if self.operation == "Add":
            all_fields = orange_fields + blue_fields
            if not all_fields:
                dm_logger.error("DM_Add: no fields selected.")
                return None
            return _fold_union(all_fields, k)

        elif self.operation == "Subtract":
            if not orange_fields:
                dm_logger.error("DM_Subtract: no orange (additive) fields selected.")
                return None
            if not blue_fields:
                dm_logger.error("DM_Subtract: no blue (subtractive) fields selected.")
                return None
            orange = _fold_union(orange_fields, k)
            blue   = _fold_union(blue_fields,   k)
            return SmoothSubtractionField(orange, blue, k) if smooth else SubtractionField(orange, blue)

        elif self.operation == "Intersection":
            if not orange_fields:
                dm_logger.error("DM_Intersection: no orange (additive) fields selected.")
                return None
            if not blue_fields:
                dm_logger.error("DM_Intersection: no blue (subtractive) fields selected.")
                return None
            orange = _fold_union(orange_fields, k)
            blue   = _fold_union(blue_fields,   k)
            return SmoothIntersectionField(orange, blue, k) if smooth else IntersectionField(orange, blue)

        return None

    # ------------------------------------------------------------------ creation

    def _sdf_boolean_interactive(self, sel):
        """Create result object (k=0), then show live-preview dialog."""
        from core.dm_object import create_dm_object
        doc = FreeCAD.activeDocument()

        try:
            orange_fields, blue_fields = self._gather_fields(sel)
            if orange_fields is None:
                return

            initial_field = self._build_result_field(orange_fields, blue_fields, 0.0)
            if initial_field is None:
                return

            # Create result object with sharp (k=0) initial field
            result = create_dm_object(name=self.operation, shape_type="sdf")
            result.Proxy.SdfField = initial_field
            result.Group = "Group 1"

            for prop, cat, desc, val in [
                ("BooleanOp",     "Boolean", "Operation type",              self.operation),
                ("BooleanInputs", "Boolean", "Input SDF objects",           list(sel)),
                ("SmoothK",       "Boolean", "Smooth blend radius mm (0=sharp)", 0.0),
            ]:
                if not hasattr(result, prop):
                    ptype = ("App::PropertyString"   if isinstance(val, str)  else
                             "App::PropertyLinkList" if isinstance(val, list) else
                             "App::PropertyFloat")
                    result.addProperty(ptype, prop, cat, desc)
                setattr(result, prop, val)

            for obj in sel:
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    obj.ViewObject.Visibility = False

            doc.recompute()   # registers with renderer

            dlg = _BooleanDialog(self.operation, orange_fields, blue_fields,
                                 result, FreeCADGui.getMainWindow())
            if dlg.exec_() != QtGui.QDialog.Accepted:
                doc.removeObject(result.Name)
                for obj in sel:
                    if hasattr(obj, "ViewObject") and obj.ViewObject:
                        obj.ViewObject.Visibility = True
                doc.recompute()
                return

            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(result)
            dm_logger.info(f"SDF {self.operation}: {len(sel)} inputs, "
                           f"k={result.SmoothK:.1f} mm")

        except Exception as e:
            dm_logger.error(f"DM_{self.operation} failed: {e}")

    # ------------------------------------------------------------------ edit

    def _edit_boolean(self, result):
        """Re-open the live-preview dialog for an existing boolean result."""
        try:
            inputs = getattr(result, "BooleanInputs", []) or []
            orange_fields, blue_fields = [], []
            for child in inputs:
                if child is None:
                    continue
                field = getattr(getattr(child, "Proxy", None), "SdfField", None)
                if field is None:
                    continue
                if getattr(child, "Group", "Group 1") == "Group 2":
                    blue_fields.append(field)
                else:
                    orange_fields.append(field)

            if not orange_fields and not blue_fields:
                dm_logger.error(f"DM_{self.operation}: no input fields found for edit.")
                return

            dlg = _BooleanDialog(self.operation, orange_fields, blue_fields,
                                 result, FreeCADGui.getMainWindow())
            dlg.exec_()   # cancel restores original k inside the dialog

        except Exception as e:
            dm_logger.error(f"DM_{self.operation} edit failed: {e}")

    # ------------------------------------------------------------------ BRep fallback

    def _brep_boolean(self, sel):
        for obj in sel:
            if not hasattr(obj, "ShapeType"):
                dm_logger.error(f"DM_{self.operation}: '{obj.Label}' is not a DM object.")
                return

        from core.dm_object import create_dm_object
        try:
            doc   = FreeCAD.activeDocument()
            shape = sel[0].Shape
            for i in range(1, len(sel)):
                s = sel[i].Shape
                if   self.operation == "Add":          shape = shape.fuse(s)
                elif self.operation == "Subtract":     shape = shape.cut(s)
                elif self.operation == "Intersection": shape = shape.common(s)

            result = create_dm_object(name=self.operation, shape_type="boolean")
            result.Shape = shape

            for obj in sel:
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    obj.ViewObject.Visibility = False

            doc.recompute()
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(result)
            dm_logger.info(f"{self.operation} completed.")
        except Exception as e:
            dm_logger.error(f"DM_{self.operation} failed: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Module-level helpers (also called from _recompose_boolean and _BooleanDialog)
# ──────────────────────────────────────────────────────────────────────────────

def _fold_union(fields, k=0.0):
    """Left-associative union fold. Uses SmoothUnionField when k > 0."""
    from core.sdf.sdf_composer import UnionField, SmoothUnionField
    if not fields:
        return None
    result = fields[0]
    for f in fields[1:]:
        result = SmoothUnionField(result, f, k) if k > 0.0 else UnionField(result, f)
    return result


def _recompose_boolean(fp):
    """Re-compose the SDF tree from fp.BooleanInputs + fp.SmoothK.

    Called from DMObjectProxy.execute() when BooleanInputs are present.
    Returns the composed SdfField, or None on failure.
    """
    from core.sdf.sdf_composer import (SubtractionField, IntersectionField,
                                       SmoothSubtractionField, SmoothIntersectionField)
    op     = getattr(fp, "BooleanOp",    None)
    inputs = getattr(fp, "BooleanInputs", [])
    if not inputs or not op:
        return None

    k      = float(getattr(fp, "SmoothK", 0.0) or 0.0)
    smooth = k > 0.0

    orange_fields, blue_fields = [], []
    for child in inputs:
        if child is None:
            continue
        field = getattr(getattr(child, "Proxy", None), "SdfField", None)
        if field is None:
            continue
        if getattr(child, "Group", "Group 1") == "Group 2":
            blue_fields.append(field)
        else:
            orange_fields.append(field)

    if op == "Add":
        return _fold_union(orange_fields + blue_fields, k)

    elif op == "Subtract":
        orange = _fold_union(orange_fields, k)
        blue   = _fold_union(blue_fields,   k)
        if orange is None or blue is None:
            return None
        return SmoothSubtractionField(orange, blue, k) if smooth else SubtractionField(orange, blue)

    elif op == "Intersection":
        orange = _fold_union(orange_fields, k)
        blue   = _fold_union(blue_fields,   k)
        if orange is None or blue is None:
            return None
        return SmoothIntersectionField(orange, blue, k) if smooth else IntersectionField(orange, blue)

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Live-preview dialog
# ──────────────────────────────────────────────────────────────────────────────

class _BooleanDialog(QtGui.QDialog):
    """
    Modal dialog for setting the smooth blend radius with live viewport preview.
    Works for both creation (initial k=0) and editing (initial k=existing value).
    """

    def __init__(self, operation, orange_fields, blue_fields, result_obj, parent=None):
        super().__init__(parent)
        self._operation     = operation
        self._orange_fields = orange_fields
        self._blue_fields   = blue_fields
        self._result_obj    = result_obj
        self._original_k    = float(getattr(result_obj, "SmoothK", 0.0) or 0.0)

        self.setWindowTitle(f"Boolean {operation}")
        self.setMinimumWidth(300)

        layout = QtGui.QFormLayout(self)

        self._smooth_spin = QtGui.QDoubleSpinBox()
        self._smooth_spin.setRange(0.0, 500.0)
        self._smooth_spin.setSingleStep(1.0)
        self._smooth_spin.setDecimals(1)
        self._smooth_spin.setValue(self._original_k)
        self._smooth_spin.setToolTip(
            "0 = sharp boolean.\n"
            "Values > 0 blend shapes smoothly over this radius (mm).\n"
            "Editable later via the object's SmoothK property."
        )
        layout.addRow("Smooth Radius (mm):", self._smooth_spin)

        btn_box = QtGui.QDialogButtonBox(
            QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

        # 80 ms debounce so rapid spin-box changes don't each trigger a rebuild
        self._preview_timer = QtCore.QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(80)
        self._preview_timer.timeout.connect(self._apply_preview)

        self._smooth_spin.valueChanged.connect(lambda _: self._preview_timer.start())

    # ------------------------------------------------------------------ preview

    def _apply_preview(self):
        self._push_preview(self._smooth_spin.value())

    def _push_preview(self, k):
        """Build a new composed field for k and push it directly to the renderer."""
        try:
            from core.sdf.sdf_composer import (SubtractionField, IntersectionField,
                                               SmoothSubtractionField, SmoothIntersectionField)
            smooth = k > 0.0

            if self._operation == "Add":
                new_field = _fold_union(self._orange_fields + self._blue_fields, k)
            elif self._operation == "Subtract":
                orange = _fold_union(self._orange_fields, k)
                blue   = _fold_union(self._blue_fields,   k)
                if orange is None or blue is None:
                    return
                new_field = (SmoothSubtractionField(orange, blue, k) if smooth
                             else SubtractionField(orange, blue))
            elif self._operation == "Intersection":
                orange = _fold_union(self._orange_fields, k)
                blue   = _fold_union(self._blue_fields,   k)
                if orange is None or blue is None:
                    return
                new_field = (SmoothIntersectionField(orange, blue, k) if smooth
                             else IntersectionField(orange, blue))
            else:
                return

            if new_field is None:
                return

            # Write k back so recompute / serialisation is consistent
            proxy = getattr(self._result_obj, "Proxy", None)
            if proxy:
                proxy.SdfField = new_field
            self._result_obj.SmoothK = k

            # Push directly to renderer (bypasses full doc recompute)
            vp       = getattr(self._result_obj, "ViewObject", None)
            vp_proxy = getattr(vp, "Proxy", None) if vp else None
            strategy = getattr(vp_proxy, "_strategy", None) if vp_proxy else None
            if strategy and getattr(strategy, "label", None):
                from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
                DMSceneRayMarchRenderer.get_instance().update_field(strategy.label, new_field)

        except Exception as e:
            dm_logger.error(f"BooleanDialog preview failed: {e}")

    # ------------------------------------------------------------------ accept / reject

    def reject(self):
        """Cancel: restore original k before closing."""
        self._push_preview(self._original_k)
        super().reject()


# ──────────────────────────────────────────────────────────────────────────────
# Register commands
# ──────────────────────────────────────────────────────────────────────────────

FreeCADGui.addCommand('DM_Add',          CommandDMBoolean("Add"))
FreeCADGui.addCommand('DM_Subtract',     CommandDMBoolean("Subtract"))
FreeCADGui.addCommand('DM_Intersection', CommandDMBoolean("Intersection"))
