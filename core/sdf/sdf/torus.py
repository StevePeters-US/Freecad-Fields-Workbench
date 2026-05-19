import math
import numpy as np
import FreeCAD
from core.sdf.sdf_field import SdfField, _GLSL_APPLY_INV_MAT

_GLSL_SDF_TORUS = """
float sdf_torus(vec3 p, vec3 center, float major_r, float tube_r) {
    vec3 lp = p - center;
    vec2 q = vec2(length(lp.xy) - major_r, lp.z);
    return length(q) - tube_r;
}
"""


class SdfTorusField(SdfField):
    """Exact analytical torus SDF, ring in XY plane, Z up.
    Reference: sdTorus() in iquilezles.org/articles/distfunctions/
    """

    def __init__(self, center: FreeCAD.Vector, major_radius: float, tube_radius: float,
                 placement: FreeCAD.Placement = None):
        self.center = center
        self.major_radius = major_radius
        self.tube_radius = tube_radius
        self.placement = placement

        self.inv_matrix = None
        if self.placement is not None:
            m = self.placement.toMatrix()
            m.invert()
            self.inv_matrix = np.array([
                [m.A11, m.A12, m.A13, m.A14],
                [m.A21, m.A22, m.A23, m.A24],
                [m.A31, m.A32, m.A33, m.A34],
                [m.A41, m.A42, m.A43, m.A44],
            ], dtype=np.float32)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        """Scalar SDF at a single point. Ring in XY plane, Z up (matches GLSL)."""
        local = point
        if self.placement is not None:
            local = self.placement.inverse().multVec(point)

        lp = local - self.center
        q_x = math.sqrt(lp.x ** 2 + lp.y ** 2) - self.major_radius
        q_y = lp.z
        return math.sqrt(q_x ** 2 + q_y ** 2) - self.tube_radius

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        """Vectorized SDF over (N,3) float32 array. Returns (N,) float32."""
        if self.inv_matrix is not None:
            N = points.shape[0]
            pts_hom = np.hstack((points, np.ones((N, 1), dtype=np.float32)))
            local_pts = (pts_hom @ self.inv_matrix.T)[:, :3]
        else:
            local_pts = points

        c = np.array([self.center.x, self.center.y, self.center.z], dtype=np.float32)
        lp = local_pts - c

        # Ring in XY plane, Z up — matches GLSL sdf_torus
        q_x = np.sqrt(lp[:, 0] ** 2 + lp[:, 1] ** 2) - self.major_radius
        q_y = lp[:, 2]
        return (np.sqrt(q_x ** 2 + q_y ** 2) - self.tube_radius).astype(np.float32)

    def to_glsl(self, ctx, point_var="p"):
        """Returns GLSL snippet for the torus."""
        ctx.add_custom_helper("sdf_torus", _GLSL_SDF_TORUS)
        c = ctx.uniform("vec3", (self.center.x, self.center.y, self.center.z))
        R = ctx.uniform("float", self.major_radius)
        r = ctx.uniform("float", self.tube_radius)

        p = point_var
        if self.inv_matrix is not None:
            ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
            m = ctx.uniform("mat4", self.inv_matrix.tolist())
            p = f"apply_inv_mat({m}, {point_var})"

        return f"sdf_torus({p}, {c}, {R}, {r})"

    def bounding_box(self):
        """Conservative bounding box: (center-ext, center+ext)."""
        R, r = self.major_radius, self.tube_radius
        # Ring in XY plane, Z up — matches GLSL and evaluate
        extent = FreeCAD.Vector(R + r, R + r, r)
        
        corners_local = []
        for sx in (-1, 1):
            for sy in (-1, 1):
                for sz in (-1, 1):
                    corners_local.append(self.center + FreeCAD.Vector(sx * extent.x, sy * extent.y, sz * extent.z))
                    
        if self.placement is not None:
            corners = [self.placement.multVec(pt) for pt in corners_local]
        else:
            corners = corners_local
            
        min_x = min(pt.x for pt in corners); max_x = max(pt.x for pt in corners)
        min_y = min(pt.y for pt in corners); max_y = max(pt.y for pt in corners)
        min_z = min(pt.z for pt in corners); max_z = max(pt.z for pt in corners)
        return (FreeCAD.Vector(min_x, min_y, min_z), FreeCAD.Vector(max_x, max_y, max_z))

