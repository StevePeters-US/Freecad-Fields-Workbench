import FreeCAD
import numpy as np
from core.frep.frep_field import FRepField

class ComposerField(FRepField):
    """
    Abstract base class for boolean composition fields.
    """
    def __init__(self, field_a: FRepField, field_b: FRepField):
        self.a = field_a
        self.b = field_b

class UnionField(ComposerField):
    """Min(a, b)"""
    def evaluate(self, point: FreeCAD.Vector) -> float:
        return min(self.a.evaluate(point), self.b.evaluate(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return np.minimum(self.a.evaluate_grid(points), self.b.evaluate_grid(points))

    def bounding_box(self):
        min_a, max_a = self.a.bounding_box()
        min_b, max_b = self.b.bounding_box()
        # Bounding box of a union is the spatial union of both boxes
        return (
            FreeCAD.Vector(min(min_a.x, min_b.x), min(min_a.y, min_b.y), min(min_a.z, min_b.z)),
            FreeCAD.Vector(max(max_a.x, max_b.x), max(max_a.y, max_b.y), max(max_a.z, max_b.z))
        )

class IntersectionField(ComposerField):
    """Max(a, b)"""
    def evaluate(self, point: FreeCAD.Vector) -> float:
        return max(self.a.evaluate(point), self.b.evaluate(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return np.maximum(self.a.evaluate_grid(points), self.b.evaluate_grid(points))

    def bounding_box(self):
        min_a, max_a = self.a.bounding_box()
        min_b, max_b = self.b.bounding_box()
        # Bounding box of intersection is the spatial intersection of both boxes
        overlap_min = FreeCAD.Vector(max(min_a.x, min_b.x), max(min_a.y, min_b.y), max(min_a.z, min_b.z))
        overlap_max = FreeCAD.Vector(min(max_a.x, max_b.x), min(max_a.y, max_b.y), min(max_a.z, max_b.z))
        
        # Check if they completely disjoint
        if overlap_min.x > overlap_max.x or overlap_min.y > overlap_max.y or overlap_min.z > overlap_max.z:
            # Return an invalid/empty box, e.g., the origin with 0 size
            return (FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,0))
            
        return (overlap_min, overlap_max)

class SubtractionField(ComposerField):
    """Max(a, -b). A - B"""
    def evaluate(self, point: FreeCAD.Vector) -> float:
        return max(self.a.evaluate(point), -self.b.evaluate(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return np.maximum(self.a.evaluate_grid(points), -self.b.evaluate_grid(points))

    def bounding_box(self):
        # Subtracting B doesn't extend A's bounding box. We just keep A's bounds.
        return self.a.bounding_box()
