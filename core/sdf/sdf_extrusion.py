import numpy as np
import FreeCAD
from .sdf_field import SdfField
from .sdf2d.sdf2d_field import Sdf2dField


class SdfExtrusionField(SdfField):
    """
    Extrudes a 2D profile along the local Z axis by the given height.
    The profile lives in the local XY plane; the solid extends ±height/2 along Z.

    IQ opExtrusion formula:
        d2 = profile.evaluate_2d(p.x, p.y)
        dz = |p.z| - height/2
        return min(max(d2, dz), 0) + length(max(vec2(d2, dz), 0))
    """

    def __init__(self, profile: Sdf2dField, height: float, placement: FreeCAD.Placement = None):
        self.profile = profile
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
                [m.A41, m.A42, m.A43, m.A44],
            ], dtype=np.float32)

    def _local(self, point: FreeCAD.Vector) -> FreeCAD.Vector:
        if self.placement is not None:
            return self.placement.inverse().multVec(point)
        return point

    def evaluate(self, point: FreeCAD.Vector) -> float:
        lp = self._local(point)
        d2 = self.profile.evaluate_2d(lp.x, lp.y)
        dz = abs(lp.z) - self.height * 0.5
        return min(max(d2, dz), 0.0) + (max(d2, 0.0) ** 2 + max(dz, 0.0) ** 2) ** 0.5

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        if self.inv_matrix is not None:
            N = points.shape[0]
            pts_h = np.hstack((points, np.ones((N, 1), dtype=np.float32)))
            lp = (pts_h @ self.inv_matrix.T)[:, :3]
        else:
            lp = points

        d2 = self.profile.evaluate_2d_grid(lp[:, :2].astype(np.float32))
        dz = np.abs(lp[:, 2]) - self.height * 0.5
        inner = np.minimum(np.maximum(d2, dz), 0.0)
        outer = np.sqrt(np.maximum(d2, 0.0) ** 2 + np.maximum(dz, 0.0) ** 2)
        return (inner + outer).astype(np.float32)

    def to_glsl(self, ctx, point_var: str = "p") -> str:
        uid = f"{id(self) & 0xFFFFFFFF:08x}"
        func_name = f"sdf_extrude_{uid}"

        half_h = ctx.uniform("float", self.height * 0.5)

        # Build the 2D expression — use a fresh sub-pvar to avoid name collision
        sub_p = f"_ep_{uid}"
        expr_2d = self.profile.to_glsl_2d(ctx, sub_p)

        if self.inv_matrix is not None:
            ctx.need_helper("apply_inv_mat")
            m = ctx.uniform("mat4", self.inv_matrix.tolist())
            lp_expr = f"apply_inv_mat({m}, {point_var})"
        else:
            lp_expr = point_var

        body = (
            f"float {func_name}(vec3 p) {{\n"
            f"    vec3 lp = {lp_expr.replace(point_var, 'p')};\n"
            f"    vec2 {sub_p} = lp.xy;\n"
            f"    float _d2 = {expr_2d};\n"
            f"    float _dz = abs(lp.z) - {half_h};\n"
            f"    vec2 _w = vec2(_d2, _dz);\n"
            f"    return min(max(_w.x, _w.y), 0.0) + length(max(_w, 0.0));\n"
            f"}}"
        )
        ctx.add_custom_helper(func_name, body)
        return f"{func_name}({point_var})"

    def bounding_box(self):
        half_h = self.height * 0.5
        if hasattr(self.profile, 'bbox_2d'):
            minx, miny, maxx, maxy = self.profile.bbox_2d()
        else:
            try:
                r = _profile_radial_extent(self.profile)
            except Exception:
                r = 1000.0
            minx, miny, maxx, maxy = -r, -r, r, r

        all_corners = [
            FreeCAD.Vector(x, y, z)
            for x in (minx, maxx) for y in (miny, maxy) for z in (-half_h, half_h)
        ]
        if self.placement is not None:
            world = [self.placement.multVec(c) for c in all_corners]
            return (
                FreeCAD.Vector(min(p.x for p in world), min(p.y for p in world), min(p.z for p in world)),
                FreeCAD.Vector(max(p.x for p in world), max(p.y for p in world), max(p.z for p in world)),
            )
        return (FreeCAD.Vector(minx, miny, -half_h), FreeCAD.Vector(maxx, maxy, half_h))


def _profile_radial_extent(profile: Sdf2dField, samples: int = 32, search_range: float = 2000.0) -> float:
    """Estimate the radial bounding radius of a 2D profile via binary search along axes."""
    max_r = 0.0
    for angle_i in range(samples):
        import math
        a = math.pi * 2.0 * angle_i / samples
        dx, dy = math.cos(a), math.sin(a)
        lo, hi = 0.0, search_range
        for _ in range(32):
            mid = (lo + hi) * 0.5
            if profile.evaluate_2d(dx * mid, dy * mid) < 0:
                lo = mid
            else:
                hi = mid
        max_r = max(max_r, hi)
    return max_r
