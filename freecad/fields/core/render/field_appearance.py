# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Appearance of an SDF field, resolved from its FreeCAD document object.

Extracted from FldSceneVoxelRenderer unchanged (RS-008). A label is
"<DocName>.<ObjName>"; every getter returns a documented default rather than
raising, because these are read on the render path.
"""
import FreeCAD
from freecad.fields.core import fld_logger

DEFAULT_ADDITIVE_COLOR    = (1.0, 0.5, 0.0)
# Was DEFAULT_SUBTRACTIVE_COLOR. Subtractive shapes are no longer recoloured;
# this is the default crosshatch colour, and fld_settings.get_hatch_color()
# defaults to exactly this triple. Change both together.
DEFAULT_HATCH_COLOR       = (0.3, 0.5, 1.0)
# Magenta: it is not near orange (the default body), not near the hatch blue, and
# not near the grey of an unlit background, so a selection outline reads against
# whatever colour the user gives an object. This is the value written into
# ViewObject.SelectionColor when FldViewProvider.attach creates the property, and
# it is the ONLY place the default lives -- nothing re-derives it at render time.
DEFAULT_SELECTION_COLOR   = (1.0, 0.0, 1.0)
DEFAULT_SPECULAR          = (1.0, 1.0, 1.0, 32.0)


def apply_default_view_settings(view_object, shape_color):
    """Visible, `shape_color`-tinted, shaded -- the default a newly-created
    object's ViewObject gets. Shared by the object-creation factories in
    `fld_noise_object.py`, `fld_noise2d_object.py`, `fld_heightmap_object.py` and
    `fld_voxel_field.py` (CR-030), which each repeated exactly these three
    assignments; the `FreeCAD.GuiUp` guard and try/except around the call are left
    at each call site since their logging context (which factory failed) differs.
    """
    view_object.Visibility = True
    view_object.ShapeColor = shape_color
    view_object.DisplayMode = "Shaded"


class FieldAppearance:
    @staticmethod
    def _object_for(label):
        """The document object a label names, or None. Never raises."""
        try:
            parts = label.split(".", 1)
            if len(parts) != 2:
                return None
            doc = FreeCAD.getDocument(parts[0])
            return doc.getObject(parts[1]) if doc else None
        except Exception:
            return None

    def is_subtractive(self, label):
        try:
            obj = self._object_for(label)
            return getattr(obj, "Group", "Additive") == "Subtractive"
        except Exception:
            return False

    def vis_alpha(self, label):
        """Return visual transparency [0,1] for this field. Reads DebugAlpha property if present."""
        try:
            obj = self._object_for(label)
            val = getattr(obj, "DebugAlpha", None)
            return float(val) if val is not None else 1.0
        except Exception:
            return 1.0

    def shape_color(self, label):
        """Return (r, g, b) from ViewObject.ShapeColor; fall back to group orange/blue."""
        try:
            obj = self._object_for(label)
            if obj and getattr(obj, "ViewObject", None):
                c = getattr(obj.ViewObject, "ShapeColor", None)
                if c is not None:
                    return (float(c[0]), float(c[1]), float(c[2]))
        except Exception as e:
            fld_logger.render_debug(f"FieldAppearance.shape_color failed: {e}")
        return DEFAULT_ADDITIVE_COLOR

    def selection_color(self, label):
        """Return (r, g, b) from ViewObject.SelectionColor.

        The property read is unconditional -- no `getattr` default, no try that
        turns an odd value back into magenta. FldViewProvider.attach creates the
        property on every Fields object, on creation and on document restore alike,
        so a field in the scene has one; if it does not, that guarantee is broken
        and the AttributeError says so rather than a default quietly standing in
        for whatever the user actually set.

        A label that resolves to no object (or to one with no ViewObject) is a
        different thing entirely: there is no per-object colour being hidden,
        because there is no object. That is the module contract in the docstring
        above, and it is the case the mock document exercises.
        """
        obj = self._object_for(label)
        if obj is None or getattr(obj, "ViewObject", None) is None:
            return DEFAULT_SELECTION_COLOR
        return tuple(float(c) for c in obj.ViewObject.SelectionColor[:3])

    def specular_shininess(self, label):
        """Return (sr, sg, sb, exponent) from ViewObject.ShapeMaterial; fall back to defaults."""
        try:
            obj = self._object_for(label)
            if obj and getattr(obj, "ViewObject", None):
                mat = getattr(obj.ViewObject, "ShapeMaterial", None)
                if mat is not None:
                    s = getattr(mat, "SpecularColor", (1.0, 1.0, 1.0))
                    exp = max(1.0, float(getattr(mat, "Shininess", 0.25)) * 128.0)
                    return (float(s[0]), float(s[1]), float(s[2]), exp)
        except Exception as e:
            fld_logger.render_debug(f"FieldAppearance.specular_shininess failed: {e}")
        return DEFAULT_SPECULAR
