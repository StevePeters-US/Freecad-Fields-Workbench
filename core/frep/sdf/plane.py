import FreeCAD
from core.frep.frep_field import SdfField

class SdfPlaneField(SdfField):
    """A half-space field divided by an infinite plane."""
    def __init__(self, normal: FreeCAD.Vector, origin: FreeCAD.Vector):
        self.normal = normal
        self.normal.normalize()
        self.origin = origin

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return (point - self.origin).dot(self.normal)

    def to_glsl(self, ctx, point_var="p"):
        ctx.need_helper("sdf_plane")
        o = ctx.uniform("vec3", (self.origin.x, self.origin.y, self.origin.z))
        n = ctx.uniform("vec3", (self.normal.x, self.normal.y, self.normal.z))
        return f"sdf_plane({point_var}, {o}, {n})"

    def gradient(self, point: FreeCAD.Vector, h: float = 1e-4) -> FreeCAD.Vector:
        return self.normal

    def bounding_box(self):
        from core.dm_object import get_max_bounds
        mb = get_max_bounds()
        return (FreeCAD.Vector(-mb, -mb, -mb), FreeCAD.Vector(mb, mb, mb))
