# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.sdf.sdf_field import SdfField, _GLSL_APPLY_INV_MAT
import numpy as np
import math

_GLSL_SDF_CYLINDER = """
float sdf_cylinder(vec3 p, vec3 base_center, vec3 axis, float radius, float height) {
    vec3 pa = p - base_center;
    float h = dot(pa, axis);
    vec3 radial = pa - axis * h;
    float d_radial = length(radial) - radius;
    float h_center = h - height * 0.5;
    float d_axial = abs(h_center) - abs(height) * 0.5;
    float d_r_pos = max(d_radial, 0.0);
    float d_a_pos = max(d_axial, 0.0);
    return sqrt(d_r_pos * d_r_pos + d_a_pos * d_a_pos) + min(max(d_radial, d_axial), 0.0);
}
"""

class SdfCylinderField(SdfField):
    """A finite cylinder exact SDF."""
    def __init__(self, base_center: FreeCAD.Vector, axis: FreeCAD.Vector, radius: float, height: float, placement: FreeCAD.Placement = None):
        super().__init__()
        self.base_center = base_center
        self.axis = axis
        self.axis.normalize()
        self.radius = radius
        self.height = height
        self.placement = placement
        self.inv_matrix = self._compute_inv_matrix(self.placement)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        local_pt = self._to_local_point(point)

        pa = local_pt - self.base_center
        h = pa.dot(self.axis)
        radial_vec = pa - self.axis * h
        d_radial = radial_vec.Length - self.radius
        h_center = h - (self.height / 2.0)
        d_axial = abs(h_center) - (abs(self.height) / 2.0)
        out_dist = FreeCAD.Vector(max(d_radial, 0.0), max(d_axial, 0.0), 0).Length
        in_dist = min(max(d_radial, d_axial), 0.0)
        return out_dist + in_dist

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        """Vectorized cylinder SDF over (N, 3) points."""
        local_pts = self._to_local_grid(points)

        c = np.array([self.base_center.x, self.base_center.y, self.base_center.z])
        ax = np.array([self.axis.x, self.axis.y, self.axis.z])

        pa = local_pts - c                          # (N, 3) vectors from base center
        h = pa @ ax                                 # (N,) projection along axis
        radial = pa - np.outer(h, ax)              # (N, 3) radial component
        d_radial = np.linalg.norm(radial, axis=1) - self.radius  # (N,)

        h_center = h - self.height / 2.0
        d_axial = np.abs(h_center) - np.abs(self.height) / 2.0            # (N,)

        d_r_pos = np.maximum(d_radial, 0.0)
        d_a_pos = np.maximum(d_axial, 0.0)
        out_dist = np.sqrt(d_r_pos**2 + d_a_pos**2)
        in_dist = np.minimum(np.maximum(d_radial, d_axial), 0.0)
        return (out_dist + in_dist).astype(np.float32)

    def to_glsl(self, ctx, point_var="p"):
        ctx.add_custom_helper("sdf_cylinder", _GLSL_SDF_CYLINDER)
        c = ctx.uniform("vec3", (self.base_center.x, self.base_center.y, self.base_center.z))
        ax = ctx.uniform("vec3", (self.axis.x, self.axis.y, self.axis.z))
        r  = ctx.uniform("float", self.radius)
        h  = ctx.uniform("float", self.height)
        if self.inv_matrix is not None:
            ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
            m = ctx.uniform("mat4", self.inv_matrix.tolist())
            return f"sdf_cylinder(apply_inv_mat({m}, {point_var}), {c}, {ax}, {r}, {h})"
        return f"sdf_cylinder({point_var}, {c}, {ax}, {r}, {h})"

    def max_erosion(self) -> float:
        return min(self.radius, abs(self.height) * 0.5)

    def eroded(self, distance: float):
        d = float(distance)
        if d <= 0.0 or d > self.max_erosion():
            return None
        # Both end caps move inward along the axis, so the base slides by +d and
        # the height loses 2d. A negative height runs the other way down the axis.
        sign = 1.0 if self.height >= 0.0 else -1.0
        return SdfCylinderField(self.base_center + self.axis * (d * sign),
                                FreeCAD.Vector(self.axis),
                                self.radius - d,
                                self.height - 2.0 * d * sign,
                                self.placement)

    def to_patch_cage(self):
        from freecad.fields.core.sdf.sdf.cage import SdfCageField
        return SdfCageField.from_cylinder(self)

    # to_deform_cage() is inherited from SdfField (derived from to_patch_cage()).

    def bounding_box(self):
        # Calculate tight AABB by transforming local corners of the cylinder's bounding box
        # Our cylinder extends along self.axis from 0 to height (relative to base_center)
        r = self.radius
        v = self.axis
        # Construct orthogonal basis in local space for the cylinder axis
        ref = FreeCAD.Vector(1, 0, 0) if abs(v.x) < 0.9 else FreeCAD.Vector(0, 1, 0)
        u = v.cross(ref).normalize()
        w = v.cross(u).normalize()
        
        p0 = self.base_center
        p1 = self.base_center + v * self.height
        
        corners_local = []
        for p in (p0, p1):
            for su in (-1, 1):
                for sw in (-1, 1):
                    corners_local.append(p + u * (su * r) + w * (sw * r))
        
        if self.placement is not None:
            corners = [self.placement.multVec(pt) for pt in corners_local]
        else:
            corners = corners_local
            
        min_x = min(pt.x for pt in corners); max_x = max(pt.x for pt in corners)
        min_y = min(pt.y for pt in corners); max_y = max(pt.y for pt in corners)
        min_z = min(pt.z for pt in corners); max_z = max(pt.z for pt in corners)
        
        return (FreeCAD.Vector(min_x, min_y, min_z), FreeCAD.Vector(max_x, max_y, max_z))
