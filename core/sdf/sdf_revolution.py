import uuid
import math
import numpy as np
import FreeCAD
from .sdf_field import SdfField, _GLSL_APPLY_INV_MAT
from .sdf2d.sdf2d_field import Sdf2dField


class SdfRevolutionField(SdfField):
    """
    Revolves a 2D profile around the local Z axis.

    The profile lives in the (r, z) half-plane where r = length(p.xy).
    An optional radial offset shifts the profile outward — use this to make
    toroid shapes (offset = major radius).

    IQ opRevolution formula:
        q = vec2(length(p.xy) - offset, p.z)
        return profile.evaluate_2d(q.x, q.y)

    Examples:
        Sphere:   Sdf2dCircle(radius=r),  offset=0
        Torus:    Sdf2dCircle(radius=tube_r), offset=major_r
        Cylinder: Sdf2dBox(hx=r, hy=h/2),  offset=0
    """

    def __init__(self, profile: Sdf2dField, offset: float = 0.0, placement: FreeCAD.Placement = None):
        self.profile = profile
        self.offset = offset
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

    def _local(self, point: FreeCAD.Vector) -> FreeCAD.Vector:
        if self.placement is not None:
            return self.placement.inverse().multVec(point)
        return point

    def evaluate(self, point: FreeCAD.Vector) -> float:
        lp = self._local(point)
        r = math.sqrt(lp.x * lp.x + lp.y * lp.y) - self.offset
        return self.profile.evaluate_2d(r, lp.z)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        if self.inv_matrix is not None:
            N = points.shape[0]
            pts_h = np.hstack((points, np.ones((N, 1), dtype=np.float32)))
            lp = (pts_h @ self.inv_matrix.T)[:, :3]
        else:
            lp = points

        r = np.linalg.norm(lp[:, :2], axis=1) - self.offset
        rz = np.stack([r, lp[:, 2]], axis=1).astype(np.float32)
        return self.profile.evaluate_2d_grid(rz)

    def to_glsl(self, ctx, point_var: str = "p") -> str:
        uid = uuid.uuid4().hex[:8]
        func_name = f"sdf_revolve_{uid}"

        offset_u = ctx.uniform("float", self.offset)
        sub_p = f"_rq_{uid}"
        expr_2d = self.profile.to_glsl_2d(ctx, sub_p)

        if self.inv_matrix is not None:
            ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
            m = ctx.uniform("mat4", self.inv_matrix.tolist())
            lp_expr = f"apply_inv_mat({m}, p)"
        else:
            lp_expr = "p"

        body = (
            f"float {func_name}(vec3 p) {{\n"
            f"    vec3 lp = {lp_expr};\n"
            f"    vec2 {sub_p} = vec2(length(lp.xy) - {offset_u}, lp.z);\n"
            f"    return {expr_2d};\n"
            f"}}"
        )
        ctx.add_custom_helper(func_name, body)
        return f"{func_name}({point_var})"

    def bounding_box(self):
        # Radial extent: max r such that profile might be inside = profile reach + offset
        try:
            from .sdf_extrusion import _profile_radial_extent
            r_profile = _profile_radial_extent(self.profile)
        except Exception:
            r_profile = 1000.0
        max_r = r_profile + abs(self.offset)

        # Z extent: sample profile along r=0 axis to find z bounds
        z_lo, z_hi = _profile_z_extent(self.profile, max_r)

        corners_local = [
            FreeCAD.Vector(-max_r, -max_r, z_lo),
            FreeCAD.Vector( max_r,  max_r, z_hi),
        ]
        if self.placement is not None:
            all_corners = [
                FreeCAD.Vector(sx * max_r, sy * max_r, sz)
                for sx in (-1, 1) for sy in (-1, 1) for sz in (z_lo, z_hi)
            ]
            world = [self.placement.multVec(c) for c in all_corners]
            return (
                FreeCAD.Vector(min(p.x for p in world), min(p.y for p in world), min(p.z for p in world)),
                FreeCAD.Vector(max(p.x for p in world), max(p.y for p in world), max(p.z for p in world)),
            )
        return (FreeCAD.Vector(-max_r, -max_r, z_lo), FreeCAD.Vector(max_r, max_r, z_hi))


def _profile_z_extent(profile: Sdf2dField, r_range: float, samples: int = 64, search_range: float = 2000.0) -> tuple:
    """Estimate z-axis bounds of the 2D profile by searching along z at r=0."""
    # Search upward
    lo, hi = 0.0, search_range
    for _ in range(32):
        mid = (lo + hi) * 0.5
        if profile.evaluate_2d(0.0, mid) < 0:
            lo = mid
        else:
            hi = mid
    z_hi = hi

    lo, hi = -search_range, 0.0
    for _ in range(32):
        mid = (lo + hi) * 0.5
        if profile.evaluate_2d(0.0, mid) < 0:
            hi = mid
        else:
            lo = mid
    z_lo = lo

    # Fallback: if profile doesn't cross z-axis at all, use radial extent as estimate
    if z_hi < 1e-3 and z_lo > -1e-3:
        z_hi = r_range
        z_lo = -r_range

    return z_lo, z_hi
