import numpy as np
import FreeCAD
import math
from core.sdf.sdf_field import SdfField, _GLSL_APPLY_INV_MAT

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
        self.base_center = base_center
        self.axis = axis
        self.axis.normalize()
        self.radius = radius
        self.height = height
        self.placement = placement
        
        self.inv_matrix = None
        if self.placement is not None:
             m = self.placement.toMatrix()
             m.invert()
             self.inv_matrix = np.array([
                 [m.A11, m.A12, m.A13, m.A14],
                 [m.A21, m.A22, m.A23, m.A24],
                 [m.A31, m.A32, m.A33, m.A34],
                 [m.A41, m.A42, m.A43, m.A44]
             ], dtype=np.float32)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        local_pt = point
        if self.placement is not None:
            local_pt = self.placement.inverse().multVec(point)
            
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
        if self.inv_matrix is not None:
            N = points.shape[0]
            pts_hom = np.hstack((points, np.ones((N, 1), dtype=np.float32)))
            local_pts = (pts_hom @ self.inv_matrix.T)[:, :3]
        else:
            local_pts = points

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

    def bounding_box(self):
        # Calculate tight AABB by transforming local corners of the cylinder's bounding box
        # Our cylinder extends along local Z axis from 0 to height (relative to base_center)
        r = self.radius
        z_min = min(0, self.height)
        z_max = max(0, self.height)
        
        corners_local = [
            self.base_center + FreeCAD.Vector(-r, -r, z_min),
            self.base_center + FreeCAD.Vector( r, -r, z_min),
            self.base_center + FreeCAD.Vector(-r,  r, z_min),
            self.base_center + FreeCAD.Vector( r,  r, z_min),
            self.base_center + FreeCAD.Vector(-r, -r, z_max),
            self.base_center + FreeCAD.Vector( r, -r, z_max),
            self.base_center + FreeCAD.Vector(-r,  r, z_max),
            self.base_center + FreeCAD.Vector( r,  r, z_max)
        ]
        
        if self.placement is not None:
            corners = [self.placement.multVec(pt) for pt in corners_local]
        else:
            corners = corners_local
            
        min_x = min(pt.x for pt in corners); max_x = max(pt.x for pt in corners)
        min_y = min(pt.y for pt in corners); max_y = max(pt.y for pt in corners)
        min_z = min(pt.z for pt in corners); max_z = max(pt.z for pt in corners)
        
        return (FreeCAD.Vector(min_x, min_y, min_z), FreeCAD.Vector(max_x, max_y, max_z))
