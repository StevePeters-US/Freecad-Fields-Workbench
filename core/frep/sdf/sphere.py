import numpy as np
import FreeCAD
from core.frep.frep_field import SdfField

class SdfSphereField(SdfField):
    """An exact analytical sphere SDF."""
    def __init__(self, center: FreeCAD.Vector, radius: float, placement: FreeCAD.Placement = None):
        self.center = center
        self.radius = radius
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
        return (local_pt - self.center).Length - self.radius

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        """Vectorized SDF: distance from center minus radius."""
        if self.inv_matrix is not None:
            N = points.shape[0]
            pts_hom = np.hstack((points, np.ones((N, 1), dtype=np.float32)))
            local_pts = (pts_hom @ self.inv_matrix.T)[:, :3]
        else:
            local_pts = points

        c = np.array([self.center.x, self.center.y, self.center.z])
        return (np.linalg.norm(local_pts - c, axis=1) - self.radius).astype(np.float32)

    def to_glsl(self, ctx, point_var="p"):
        ctx.need_helper("sdf_sphere")
        c = ctx.uniform("vec3", (self.center.x, self.center.y, self.center.z))
        r = ctx.uniform("float", self.radius)
        if self.inv_matrix is not None:
            ctx.need_helper("apply_inv_mat")
            m = ctx.uniform("mat4", self.inv_matrix.tolist())
            return f"sdf_sphere(apply_inv_mat({m}, {point_var}), {c}, {r})"
        return f"sdf_sphere({point_var}, {c}, {r})"

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
        r_vec = FreeCAD.Vector(self.radius, self.radius, self.radius)
        if self.placement is not None:
            corners = [
                self.placement.multVec(self.center + FreeCAD.Vector(-self.radius, -self.radius, -self.radius)),
                self.placement.multVec(self.center + FreeCAD.Vector(self.radius, self.radius, self.radius))
            ]
            # SdfBoxField logic is safer for rotated spheres
            c = self.center
            r = self.radius
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
