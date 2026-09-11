# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import numpy as np
from freecad.fields.core.sdf.sdf_field import SdfField
from freecad.fields.core.sdf.sdf_constants import SURFACE_ID_UNSET

_GLSL_SMOOTH_UNION = """
float smooth_union(float a, float b, float k) {
    float h = clamp(0.5 + 0.5*(b-a)/k, 0.0, 1.0);
    return mix(b, a, h) - k*h*(1.0-h);
}
"""

_GLSL_SMOOTH_SUBTRACTION = """
float smooth_subtraction(float a, float b, float k) {
    float h = clamp(0.5 - 0.5*(a+b)/k, 0.0, 1.0);
    return mix(a, -b, h) + k*h*(1.0-h);
}
"""

_GLSL_SMOOTH_INTERSECTION = """
float smooth_intersection(float a, float b, float k) {
    float h = clamp(0.5 - 0.5*(b-a)/k, 0.0, 1.0);
    return mix(b, a, h) + k*h*(1.0-h);
}
"""


def _smooth_union_scalar(a, b, k):
    h = max(0.0, min(1.0, 0.5 + 0.5 * (b - a) / k))
    return b * (1.0 - h) + a * h - k * h * (1.0 - h)


def _smooth_union_np(a, b, k):
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0.0, 1.0)
    return b * (1.0 - h) + a * h - k * h * (1.0 - h)


def _bbox_union(box_a, box_b):
    """Returns the spatial union of two bounding boxes (min_corner, max_corner)."""
    min_a, max_a = box_a
    min_b, max_b = box_b
    return (
        FreeCAD.Vector(min(min_a.x, min_b.x), min(min_a.y, min_b.y), min(min_a.z, min_b.z)),
        FreeCAD.Vector(max(max_a.x, max_b.x), max(max_a.y, max_b.y), max(max_a.z, max_b.z))
    )

def _bbox_intersection(box_a, box_b):
    """Returns the spatial intersection of two bounding boxes (min_corner, max_corner)."""
    min_a, max_a = box_a
    min_b, max_b = box_b
    overlap_min = FreeCAD.Vector(max(min_a.x, min_b.x), max(min_a.y, min_b.y), max(min_a.z, min_b.z))
    overlap_max = FreeCAD.Vector(min(max_a.x, max_b.x), min(max_a.y, max_b.y), min(max_a.z, max_b.z))
    
    # Check if they completely disjoint
    if overlap_min.x > overlap_max.x or overlap_min.y > overlap_max.y or overlap_min.z > overlap_max.z:
        from freecad.fields.core import fld_logger
        fld_logger.debug("Bounding boxes are disjoint in intersection; returning empty bbox.")
        return (FreeCAD.Vector(1e9, 1e9, 1e9), FreeCAD.Vector(-1e9, -1e9, -1e9))
        
    return (overlap_min, overlap_max)

class ComposerField(SdfField):
    """
    Abstract base class for boolean composition fields.
    """
    def __init__(self, field_a: SdfField, field_b: SdfField):
        super().__init__()
        self.a = field_a
        self.b = field_b

    def lipschitz(self) -> float:
        return max(self.a.lipschitz(), self.b.lipschitz())

    def preferred_scene_resolution(self, scene_extent):
        res = [f.preferred_scene_resolution(scene_extent)
               for f in (getattr(self, "a", None), getattr(self, "b", None))
               if f is not None and hasattr(f, "preferred_scene_resolution")]
        valid = [r for r in res if r is not None]
        return max(valid) if valid else None

class UnionField(ComposerField):
    """Min(a, b)"""
    def to_glsl(self, ctx, point_var="p"):
        a = self.a.to_glsl(ctx, point_var)
        b = self.b.to_glsl(ctx, point_var)
        return f"min({a}, {b})"

    def to_glsl_sample(self, ctx, point_var="p"):
        a = self.a.to_glsl_sample(ctx, point_var)
        b = self.b.to_glsl_sample(ctx, point_var)
        return f"fld_min({a}, {b})"

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return min(self.a.evaluate(point), self.b.evaluate(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return np.minimum(self.a.evaluate_grid(points), self.b.evaluate_grid(points)).astype(np.float32)

    def max_erosion(self) -> float:
        # Both members have to survive the shrink for the composed erosion to be
        # buildable at all, so the pair is capped by the smaller one.
        return min(self.a.max_erosion(), self.b.max_erosion())

    def eroded(self, distance: float):
        # Approximate. Erosion does not distribute over union -- (A u B) (-) r
        # contains (A(-)r) u (B(-)r) and the two differ across the overlap. Eroding
        # member by member is the behaviour a boolean wants anyway: each shape keeps
        # its own edge radius and the seam between two of them is left to the union.
        a = self.a.eroded(distance)
        b = self.b.eroded(distance)
        if a is None or b is None:
            return None
        return UnionField(a, b)

    def bounding_box(self):
        # Bounding box of a union is the spatial union of both boxes
        return _bbox_union(self.a.bounding_box(), self.b.bounding_box())



class IntersectionField(ComposerField):
    """Max(a, b)"""
    def to_glsl(self, ctx, point_var="p"):
        a = self.a.to_glsl(ctx, point_var)
        b = self.b.to_glsl(ctx, point_var)
        return f"max({a}, {b})"

    def to_glsl_sample(self, ctx, point_var="p"):
        a = self.a.to_glsl_sample(ctx, point_var)
        b = self.b.to_glsl_sample(ctx, point_var)
        return f"fld_max({a}, {b})"

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return max(self.a.evaluate(point), self.b.evaluate(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return np.maximum(self.a.evaluate_grid(points), self.b.evaluate_grid(points)).astype(np.float32)

    def max_erosion(self) -> float:
        return min(self.a.max_erosion(), self.b.max_erosion())

    def eroded(self, distance: float):
        # Exact: erosion distributes over intersection.
        a = self.a.eroded(distance)
        b = self.b.eroded(distance)
        if a is None or b is None:
            return None
        return IntersectionField(a, b)

    def bounding_box(self):
        # Bounding box of intersection is the spatial intersection of both boxes
        return _bbox_intersection(self.a.bounding_box(), self.b.bounding_box())



class SubtractionField(ComposerField):
    """Max(a, -b). A - B"""
    def to_glsl(self, ctx, point_var="p"):
        a = self.a.to_glsl(ctx, point_var)
        b = self.b.to_glsl(ctx, point_var)
        return f"max({a}, -({b}))"

    def to_glsl_sample(self, ctx, point_var="p"):
        a = self.a.to_glsl_sample(ctx, point_var)
        b = self.b.to_glsl_sample(ctx, point_var)
        return f"fld_sub({a}, {b})"

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return max(self.a.evaluate(point), -self.b.evaluate(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return np.maximum(self.a.evaluate_grid(points), -self.b.evaluate_grid(points)).astype(np.float32)

    def max_erosion(self) -> float:
        # B only ever removes material, so it cannot raise the cap; A sets it.
        return self.a.max_erosion()

    def eroded(self, distance: float):
        # Exact: (A n B') (-) r = (A (-) r) n (B (+) r)'. Erosion distributes over
        # intersection, and complementing turns the erosion of B' into a dilation of
        # B -- which is exact for any field, so B needs no closed form of its own.
        from freecad.fields.core.sdf.sdf.offset import SdfOffsetField
        core = self.a.eroded(distance)
        if core is None:
            return None
        return SubtractionField(core, SdfOffsetField(self.b, float(distance)))

    def bounding_box(self):
        # Subtracting B doesn't extend A's bounding box. We just keep A's bounds.
        return self.a.bounding_box()




class SmoothComposerField(ComposerField):
    """Abstract base for boolean composition fields with a blend radius k (mm)."""
    def __init__(self, field_a: SdfField, field_b: SdfField, k: float, blend_surface_id: int = SURFACE_ID_UNSET):
        super().__init__(field_a, field_b)
        self.k = k
        self.blend_surface_id = blend_surface_id


class SmoothUnionField(SmoothComposerField):
    """Smooth min(a, b) with blend radius k (mm)."""
    def to_glsl(self, ctx, point_var="p"):
        a = self.a.to_glsl(ctx, point_var)
        b = self.b.to_glsl(ctx, point_var)
        k_u = ctx.uniform("float", self.k)
        ctx.add_custom_helper("smooth_union", _GLSL_SMOOTH_UNION)
        return f"smooth_union({a}, {b}, {k_u})"

    def to_glsl_sample(self, ctx, point_var="p"):
        a = self.a.to_glsl_sample(ctx, point_var)
        b = self.b.to_glsl_sample(ctx, point_var)
        bid = int(getattr(self, "blend_surface_id", SURFACE_ID_UNSET))
        k = ctx.uniform("float", float(self.k))
        return f"fld_smin({a}, {b}, {k}, {bid}u)"

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return _smooth_union_scalar(self.a.evaluate(point), self.b.evaluate(point), self.k)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return _smooth_union_np(self.a.evaluate_grid(points), self.b.evaluate_grid(points), self.k).astype(np.float32)

    def max_erosion(self) -> float:
        return min(self.a.max_erosion(), self.b.max_erosion())

    def eroded(self, distance: float):
        a = self.a.eroded(distance)
        b = self.b.eroded(distance)
        if a is None or b is None:
            return None
        return SmoothUnionField(a, b, self.k)

    def bounding_box(self):
        return _bbox_union(self.a.bounding_box(), self.b.bounding_box())

    # to_vdb(): inherits base SdfField.to_vdb() which samples evaluate_grid().
    # This preserves the smooth blend — no native VDB smooth CSG exists.


class SmoothSubtractionField(SmoothComposerField):
    """Smooth A - B with blend radius k (mm). = -smooth_union(a, -b, k)"""
    def to_glsl(self, ctx, point_var="p"):
        a = self.a.to_glsl(ctx, point_var)
        b = self.b.to_glsl(ctx, point_var)
        k_u = ctx.uniform("float", self.k)
        ctx.add_custom_helper("smooth_subtraction", _GLSL_SMOOTH_SUBTRACTION)
        return f"smooth_subtraction({a}, {b}, {k_u})"

    def to_glsl_sample(self, ctx, point_var="p"):
        a = self.a.to_glsl_sample(ctx, point_var)
        b = self.b.to_glsl_sample(ctx, point_var)
        bid = int(getattr(self, "blend_surface_id", SURFACE_ID_UNSET))
        k = ctx.uniform("float", float(self.k))
        return f"fld_ssub({a}, {b}, {k}, {bid}u)"

    def evaluate(self, point: FreeCAD.Vector) -> float:
        a = self.a.evaluate(point)
        b = self.b.evaluate(point)
        h = max(0.0, min(1.0, 0.5 - 0.5 * (a + b) / self.k))
        return a * (1.0 - h) + (-b) * h + self.k * h * (1.0 - h)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        a = self.a.evaluate_grid(points)
        b = self.b.evaluate_grid(points)
        h = np.clip(0.5 - 0.5 * (a + b) / self.k, 0.0, 1.0)
        return (a * (1.0 - h) + (-b) * h + self.k * h * (1.0 - h)).astype(np.float32)

    def max_erosion(self) -> float:
        return self.a.max_erosion()

    def eroded(self, distance: float):
        from freecad.fields.core.sdf.sdf.offset import SdfOffsetField
        core = self.a.eroded(distance)
        if core is None:
            return None
        return SmoothSubtractionField(core, SdfOffsetField(self.b, float(distance)), self.k)

    def bounding_box(self):
        return self.a.bounding_box()

    # to_vdb(): inherits base SdfField.to_vdb() which samples evaluate_grid().


class SmoothIntersectionField(SmoothComposerField):
    """Smooth intersection of A and B with blend radius k (mm). = -smooth_union(-a, -b, k)"""
    def to_glsl(self, ctx, point_var="p"):
        a = self.a.to_glsl(ctx, point_var)
        b = self.b.to_glsl(ctx, point_var)
        k_u = ctx.uniform("float", self.k)
        ctx.add_custom_helper("smooth_intersection", _GLSL_SMOOTH_INTERSECTION)
        return f"smooth_intersection({a}, {b}, {k_u})"

    def to_glsl_sample(self, ctx, point_var="p"):
        a = self.a.to_glsl_sample(ctx, point_var)
        b = self.b.to_glsl_sample(ctx, point_var)
        bid = int(getattr(self, "blend_surface_id", SURFACE_ID_UNSET))
        k = ctx.uniform("float", float(self.k))
        return f"fld_smax({a}, {b}, {k}, {bid}u)"

    def evaluate(self, point: FreeCAD.Vector) -> float:
        a = self.a.evaluate(point)
        b = self.b.evaluate(point)
        h = max(0.0, min(1.0, 0.5 - 0.5 * (b - a) / self.k))
        return b * (1.0 - h) + a * h + self.k * h * (1.0 - h)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        a = self.a.evaluate_grid(points)
        b = self.b.evaluate_grid(points)
        h = np.clip(0.5 - 0.5 * (b - a) / self.k, 0.0, 1.0)
        return (b * (1.0 - h) + a * h + self.k * h * (1.0 - h)).astype(np.float32)

    def max_erosion(self) -> float:
        return min(self.a.max_erosion(), self.b.max_erosion())

    def eroded(self, distance: float):
        a = self.a.eroded(distance)
        b = self.b.eroded(distance)
        if a is None or b is None:
            return None
        return SmoothIntersectionField(a, b, self.k)

    def bounding_box(self):
        return _bbox_intersection(self.a.bounding_box(), self.b.bounding_box())

    # to_vdb(): inherits base SdfField.to_vdb() which samples evaluate_grid().


def round_convex_edges(field: SdfField, radius: float):
    """Return `field` with every convex edge rounded to `radius` mm, or None if the
    field has no exact erosion.

    This is a morphological opening: shrink the shape by r, then dilate it by r.
    The dilation is the half that does the work -- the r-level set of an exact SDF
    is already a fillet of radius r around every convex corner -- and the erosion
    is what puts the faces back where they started. Both halves have to be exact
    or they cancel, which is why the shrink goes through `eroded()` rather than
    adding r to the distance.

    Concave edges are untouched. A cutter rounded this way leaves a cavity whose
    internal corners carry the radius while the rim where it breaks the surface
    stays sharp -- the same edges a corner-radius end mill would leave.

    The radius is clamped to `field.max_erosion()`: asking for 3 mm inside a 4 mm
    slot gives the 2 mm stadium that a 4 mm cutter actually cuts, not an over-wide
    one.
    """
    from freecad.fields.core.sdf.sdf.offset import SdfOffsetField
    r = float(radius)
    if r <= 0.0:
        return field
    cap = field.max_erosion()
    if cap <= 0.0:
        return None
    r = min(r, cap)
    core = field.eroded(r)
    if core is None:
        return None
    return SdfOffsetField(core, r)
