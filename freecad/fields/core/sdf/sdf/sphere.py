# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.sdf.sdf_field import SdfField, _GLSL_APPLY_INV_MAT
import numpy as np

_GLSL_SDF_SPHERE = """
float sdf_sphere(vec3 p, vec3 center, float radius) {
    return length(p - center) - radius;
}
"""

class SdfSphereField(SdfField):
    """An exact analytical sphere SDF."""
    def __init__(self, center: FreeCAD.Vector, radius: float, placement: FreeCAD.Placement = None):
        self.center = center
        self.radius = radius
        self.placement = placement
        self.inv_matrix = self._compute_inv_matrix(self.placement)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        local_pt = self._to_local_point(point)
        return (local_pt - self.center).Length - self.radius

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        """Vectorized SDF: distance from center minus radius."""
        local_pts = self._to_local_grid(points)

        c = np.array([self.center.x, self.center.y, self.center.z])
        return (np.linalg.norm(local_pts - c, axis=1) - self.radius).astype(np.float32)

    def to_glsl(self, ctx, point_var="p"):
        ctx.add_custom_helper("sdf_sphere", _GLSL_SDF_SPHERE)
        c = ctx.uniform("vec3", (self.center.x, self.center.y, self.center.z))
        r = ctx.uniform("float", self.radius)
        if self.inv_matrix is not None:
            ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
            m = ctx.uniform("mat4", self.inv_matrix.tolist())
            return f"sdf_sphere(apply_inv_mat({m}, {point_var}), {c}, {r})"
        return f"sdf_sphere({point_var}, {c}, {r})"

    def max_erosion(self) -> float:
        return self.radius

    def eroded(self, distance: float):
        d = float(distance)
        if d <= 0.0 or d > self.radius:
            return None
        return SdfSphereField(self.center, self.radius - d, self.placement)

    def to_patch_cage(self):
        from freecad.fields.core.sdf.sdf.cage import SdfCageField
        return SdfCageField.from_sphere(self)

    # to_deform_cage() is inherited from SdfField (derived from to_patch_cage()).

    def gradient(self, point: FreeCAD.Vector, h: float = 1e-4) -> FreeCAD.Vector:
        # Note: analytical gradient for transformed field would need rotation
        # Defaulting to numerical gradient via base class for simplicity if transformed
        if self.placement is not None:
            return super().gradient(point, h)
        v = point - self.center
        L = v.Length
        if L < 1e-6:
            return FreeCAD.Vector(0, 0, 1)
        return v * (1.0 / L)

    def bounding_box(self):
        pad = max(self.radius * 0.02, 0.5)
        r_padded = self.radius + pad
        r_vec = FreeCAD.Vector(r_padded, r_padded, r_padded)
        if self.placement is not None:
            c = self.center
            r = r_padded
            corners_local = [
                c + FreeCAD.Vector(-r, -r, -r),
                c + FreeCAD.Vector( r, -r, -r),
                c + FreeCAD.Vector(-r,  r, -r),
                c + FreeCAD.Vector( r,  r, -r),
                c + FreeCAD.Vector(-r, -r,  r),
                c + FreeCAD.Vector( r, -r,  r),
                c + FreeCAD.Vector(-r,  r,  r),
                c + FreeCAD.Vector( r,  r,  r)
            ]
            corners = [self.placement.multVec(pt) for pt in corners_local]
            min_x = min(pt.x for pt in corners); max_x = max(pt.x for pt in corners)
            min_y = min(pt.y for pt in corners); max_y = max(pt.y for pt in corners)
            min_z = min(pt.z for pt in corners); max_z = max(pt.z for pt in corners)
            return (FreeCAD.Vector(min_x, min_y, min_z), FreeCAD.Vector(max_x, max_y, max_z))
            
        return (self.center - r_vec, self.center + r_vec)
        


