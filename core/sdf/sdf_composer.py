import FreeCAD
import numpy as np
from core.sdf.sdf_field import SdfField


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
        return (FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,0))
        
    return (overlap_min, overlap_max)

class ComposerField(SdfField):
    """
    Abstract base class for boolean composition fields.
    """
    def __init__(self, field_a: SdfField, field_b: SdfField):
        self.a = field_a
        self.b = field_b

class UnionField(ComposerField):
    """Min(a, b)"""
    def to_glsl(self, ctx, point_var="p"):
        a = self.a.to_glsl(ctx, point_var)
        b = self.b.to_glsl(ctx, point_var)
        return f"min({a}, {b})"

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return min(self.a.evaluate(point), self.b.evaluate(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return np.minimum(self.a.evaluate_grid(points), self.b.evaluate_grid(points))

    def bounding_box(self):
        # Bounding box of a union is the spatial union of both boxes
        return _bbox_union(self.a.bounding_box(), self.b.bounding_box())



class IntersectionField(ComposerField):
    """Max(a, b)"""
    def to_glsl(self, ctx, point_var="p"):
        a = self.a.to_glsl(ctx, point_var)
        b = self.b.to_glsl(ctx, point_var)
        return f"max({a}, {b})"

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return max(self.a.evaluate(point), self.b.evaluate(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return np.maximum(self.a.evaluate_grid(points), self.b.evaluate_grid(points))

    def bounding_box(self):
        # Bounding box of intersection is the spatial intersection of both boxes
        return _bbox_intersection(self.a.bounding_box(), self.b.bounding_box())



class SubtractionField(ComposerField):
    """Max(a, -b). A - B"""
    def to_glsl(self, ctx, point_var="p"):
        a = self.a.to_glsl(ctx, point_var)
        b = self.b.to_glsl(ctx, point_var)
        return f"max({a}, -({b}))"

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return max(self.a.evaluate(point), -self.b.evaluate(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return np.maximum(self.a.evaluate_grid(points), -self.b.evaluate_grid(points))

    def bounding_box(self):
        # Subtracting B doesn't extend A's bounding box. We just keep A's bounds.
        return self.a.bounding_box()




class SmoothUnionField(ComposerField):
    """Smooth min(a, b) with blend radius k (mm)."""
    def __init__(self, field_a: SdfField, field_b: SdfField, k: float):
        super().__init__(field_a, field_b)
        self.k = k

    def to_glsl(self, ctx, point_var="p"):
        a = self.a.to_glsl(ctx, point_var)
        b = self.b.to_glsl(ctx, point_var)
        k_u = ctx.uniform("float", self.k)
        ctx.need_helper("smooth_union")
        return f"smooth_union({a}, {b}, {k_u})"

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return _smooth_union_scalar(self.a.evaluate(point), self.b.evaluate(point), self.k)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return _smooth_union_np(self.a.evaluate_grid(points), self.b.evaluate_grid(points), self.k)

    def bounding_box(self):
        return _bbox_union(self.a.bounding_box(), self.b.bounding_box())

    # to_vdb(): inherits base SdfField.to_vdb() which samples evaluate_grid().
    # This preserves the smooth blend — no native VDB smooth CSG exists.


class SmoothSubtractionField(ComposerField):
    """Smooth A - B with blend radius k (mm). = -smooth_union(a, -b, k)"""
    def __init__(self, field_a: SdfField, field_b: SdfField, k: float):
        super().__init__(field_a, field_b)
        self.k = k

    def to_glsl(self, ctx, point_var="p"):
        a = self.a.to_glsl(ctx, point_var)
        b = self.b.to_glsl(ctx, point_var)
        k_u = ctx.uniform("float", self.k)
        ctx.need_helper("smooth_subtraction")
        return f"smooth_subtraction({a}, {b}, {k_u})"

    def evaluate(self, point: FreeCAD.Vector) -> float:
        a = self.a.evaluate(point)
        b = self.b.evaluate(point)
        h = max(0.0, min(1.0, 0.5 - 0.5 * (a + b) / self.k))
        return a * (1.0 - h) + (-b) * h + self.k * h * (1.0 - h)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        a = self.a.evaluate_grid(points)
        b = self.b.evaluate_grid(points)
        h = np.clip(0.5 - 0.5 * (a + b) / self.k, 0.0, 1.0)
        return a * (1.0 - h) + (-b) * h + self.k * h * (1.0 - h)

    def bounding_box(self):
        return self.a.bounding_box()

    # to_vdb(): inherits base SdfField.to_vdb() which samples evaluate_grid().


class SmoothIntersectionField(ComposerField):
    """Smooth intersection of A and B with blend radius k (mm). = -smooth_union(-a, -b, k)"""
    def __init__(self, field_a: SdfField, field_b: SdfField, k: float):
        super().__init__(field_a, field_b)
        self.k = k

    def to_glsl(self, ctx, point_var="p"):
        a = self.a.to_glsl(ctx, point_var)
        b = self.b.to_glsl(ctx, point_var)
        k_u = ctx.uniform("float", self.k)
        ctx.need_helper("smooth_intersection")
        return f"smooth_intersection({a}, {b}, {k_u})"

    def evaluate(self, point: FreeCAD.Vector) -> float:
        a = self.a.evaluate(point)
        b = self.b.evaluate(point)
        h = max(0.0, min(1.0, 0.5 - 0.5 * (b - a) / self.k))
        return b * (1.0 - h) + a * h + self.k * h * (1.0 - h)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        a = self.a.evaluate_grid(points)
        b = self.b.evaluate_grid(points)
        h = np.clip(0.5 - 0.5 * (b - a) / self.k, 0.0, 1.0)
        return b * (1.0 - h) + a * h + self.k * h * (1.0 - h)

    def bounding_box(self):
        return _bbox_intersection(self.a.bounding_box(), self.b.bounding_box())

    # to_vdb(): inherits base SdfField.to_vdb() which samples evaluate_grid().
