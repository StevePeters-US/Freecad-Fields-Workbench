# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.sdf.sdf_field import SdfField, _GLSL_APPLY_INV_MAT
import numpy as np

_GLSL_SDF_BOX = """
float sdf_box(vec3 p, vec3 center, vec3 half_size) {
    vec3 d = abs(p - center) - half_size;
    return length(max(d, 0.0)) + min(max(d.x, max(d.y, d.z)), 0.0);
}
"""

class SdfBoxField(SdfField):
    """An axis-aligned box SDF, optionally placed arbitrarily in space."""
    def __init__(self, center: FreeCAD.Vector, size: FreeCAD.Vector, placement: FreeCAD.Placement = None):
        self.center = center
        self.half_size = FreeCAD.Vector(abs(size.x)/2, abs(size.y)/2, abs(size.z)/2)
        self.placement = placement
        self.inv_matrix = self._compute_inv_matrix(self.placement)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        local_pt = self._to_local_point(point)

        dx = abs(local_pt.x - self.center.x) - self.half_size.x
        dy = abs(local_pt.y - self.center.y) - self.half_size.y
        dz = abs(local_pt.z - self.center.z) - self.half_size.z

        out_dist = FreeCAD.Vector(max(dx, 0), max(dy, 0), max(dz, 0)).Length
        in_dist = min(max(dx, max(dy, dz)), 0.0)
        return out_dist + in_dist

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        """Vectorized SDF evaluation over (N,3) array of points."""
        local_pts = self._to_local_grid(points)

        c = np.array([self.center.x, self.center.y, self.center.z])
        h = np.array([self.half_size.x, self.half_size.y, self.half_size.z])
        # d[i] = |pts[i] - center| - half_size
        d = np.abs(local_pts - c) - h
        # Outside distance: length of positive components
        out_dist = np.linalg.norm(np.maximum(d, 0.0), axis=1)
        # Inside distance: negative if fully inside
        in_dist = np.minimum(np.max(d, axis=1), 0.0)
        return (out_dist + in_dist).astype(np.float32)

    def to_glsl(self, ctx, point_var="p"):
        ctx.add_custom_helper("sdf_box", _GLSL_SDF_BOX)
        c = ctx.uniform("vec3", (self.center.x, self.center.y, self.center.z))
        h = ctx.uniform("vec3", (self.half_size.x, self.half_size.y, self.half_size.z))
        if self.inv_matrix is not None:
            ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
            m = ctx.uniform("mat4", self.inv_matrix.tolist())
            return f"sdf_box(apply_inv_mat({m}, {point_var}), {c}, {h})"
        return f"sdf_box({point_var}, {c}, {h})"

    def max_erosion(self) -> float:
        return min(self.half_size.x, self.half_size.y, self.half_size.z)

    def eroded(self, distance: float):
        d = float(distance)
        if d <= 0.0 or d > self.max_erosion():
            return None
        h = self.half_size
        return SdfBoxField(self.center,
                           FreeCAD.Vector((h.x - d) * 2.0, (h.y - d) * 2.0, (h.z - d) * 2.0),
                           self.placement)

    def to_patch_cage(self):
        from freecad.fields.core.sdf.sdf.cage import SdfCageField
        return SdfCageField.from_box(self)

    # to_deform_cage() is inherited from SdfField (derived from to_patch_cage()).

    def bounding_box(self):
        c = self.center
        h = self.half_size
        corners_local = [
            c + FreeCAD.Vector(-h.x, -h.y, -h.z),
            c + FreeCAD.Vector( h.x, -h.y, -h.z),
            c + FreeCAD.Vector(-h.x,  h.y, -h.z),
            c + FreeCAD.Vector( h.x,  h.y, -h.z),
            c + FreeCAD.Vector(-h.x, -h.y,  h.z),
            c + FreeCAD.Vector( h.x, -h.y,  h.z),
            c + FreeCAD.Vector(-h.x,  h.y,  h.z),
            c + FreeCAD.Vector( h.x,  h.y,  h.z)
        ]
        
        if self.placement is not None:
            corners = [self.placement.multVec(pt) for pt in corners_local]
        else:
            corners = corners_local
            
        min_x = min(pt.x for pt in corners)
        min_y = min(pt.y for pt in corners)
        min_z = min(pt.z for pt in corners)
        
        max_x = max(pt.x for pt in corners)
        max_y = max(pt.y for pt in corners)
        max_z = max(pt.z for pt in corners)
        
        return (FreeCAD.Vector(min_x, min_y, min_z), FreeCAD.Vector(max_x, max_y, max_z))
