import FreeCAD
from core.frep.sdf.sdf_field import SdfField

class SdfPlaneField(SdfField):
    """A half-space field divided by an infinite plane."""
    def __init__(self, normal: FreeCAD.Vector, origin: FreeCAD.Vector):
        self.normal = normal
        self.normal.normalize()
        self.origin = origin

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return (point - self.origin).dot(self.normal)

    def gradient(self, point: FreeCAD.Vector, h: float = 1e-4) -> FreeCAD.Vector:
        return self.normal

    def bounding_box(self):
        from core.dm_object import get_max_bounds
        mb = get_max_bounds()
        return (FreeCAD.Vector(-mb, -mb, -mb), FreeCAD.Vector(mb, mb, mb))
