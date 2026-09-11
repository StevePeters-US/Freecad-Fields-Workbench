# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import numpy as np
from freecad.fields.core.sdf.sdf_field import SdfField

_GLSL_SDF_PLANE = """
float sdf_plane(vec3 p, vec3 origin, vec3 normal) {
    return dot(p - origin, normal);
}
"""


class SdfPlaneField(SdfField):
    """A half-space field divided by an infinite plane."""
    def __init__(self, normal: FreeCAD.Vector, origin: FreeCAD.Vector):
        self.normal = normal
        self.normal.normalize()
        self.origin = origin

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return (point - self.origin).dot(self.normal)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        o = np.array([self.origin.x, self.origin.y, self.origin.z], dtype=np.float64)
        n = np.array([self.normal.x, self.normal.y, self.normal.z], dtype=np.float64)
        return ((points - o) @ n).astype(np.float32)

    def to_glsl(self, ctx, point_var="p"):
        ctx.add_custom_helper("sdf_plane", _GLSL_SDF_PLANE)
        o = ctx.uniform("vec3", (self.origin.x, self.origin.y, self.origin.z))
        n = ctx.uniform("vec3", (self.normal.x, self.normal.y, self.normal.z))
        return f"sdf_plane({point_var}, {o}, {n})"

    def max_erosion(self) -> float:
        # A half-space has no convex edge, so it erodes without limit and rounding
        # it is the identity.
        return float("inf")

    def eroded(self, distance: float):
        d = float(distance)
        if d <= 0.0:
            return None
        return SdfPlaneField(FreeCAD.Vector(self.normal), self.origin - self.normal * d)

    def gradient(self, point: FreeCAD.Vector, h: float = 1e-4) -> FreeCAD.Vector:
        return self.normal

    def bounding_box(self):
        from freecad.fields.core.objects.fld_object import get_max_bounds
        mb = get_max_bounds()
        return (FreeCAD.Vector(-mb, -mb, -mb), FreeCAD.Vector(mb, mb, mb))
