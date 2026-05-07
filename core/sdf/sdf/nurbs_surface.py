"""
core/sdf/sdf/nurbs_surface.py

SDF field for a NURBS surface (solid boundary — negative inside, positive outside).
"""
import numpy as np
import FreeCAD
import Part
from core.sdf.sdf_field import SdfField
from core import dm_logger


class SdfNurbsSurfaceField(SdfField):
    """
    Signed distance to a NURBS surface.

    Positive outside the solid bounded by the surface, negative inside.
    Uses UVProjPoint for closest-point query and surface normal for sign.
    """

    def __init__(self, bspline_surface, placement: FreeCAD.Placement = None):
        """
        bspline_surface: Part.BSplineSurface (FreeCAD)
        placement:       optional world transform
        """
        self.surface = bspline_surface
        self.placement = placement

    def _world_to_local(self, point: FreeCAD.Vector) -> FreeCAD.Vector:
        if self.placement is None:
            return point
        return self.placement.inverse().multVec(point)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        local_pt = self._world_to_local(point)
        try:
            # result = self.surface.UVProjPoint(local_pt)
            # u, v = float(result[0]), float(result[1])
            # Actually, FreeCAD's UVProjPoint behavior varies.
            # Usually it returns a tuple of (u, v).
            u, v = self.surface.UVProjPoint(local_pt)
            closest = self.surface.value(u, v)
            normal = self.surface.normal(u, v)
            diff = local_pt - closest
            dist = diff.Length
            # Sign determination: if diff vector is in the same direction as normal, we are outside (positive)
            sign = 1.0 if diff.dot(normal) >= 0.0 else -1.0
            return sign * dist
        except Exception as e:
            dm_logger.debug(f"SdfNurbsSurfaceField.evaluate failed: {e}")
            return float('inf')

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        results = np.empty(len(points), dtype=np.float32)
        for i, pt in enumerate(points):
            fpt = FreeCAD.Vector(float(pt[0]), float(pt[1]), float(pt[2]))
            results[i] = self.evaluate(fpt)
        return results

    def bounding_box(self):
        try:
            # Surface BoundBox
            bb = self.surface.toShape().BoundBox
            mn = FreeCAD.Vector(bb.XMin, bb.YMin, bb.ZMin)
            mx = FreeCAD.Vector(bb.XMax, bb.YMax, bb.ZMax)
            if self.placement:
                corners = [
                    FreeCAD.Vector(mn.x, mn.y, mn.z),
                    FreeCAD.Vector(mx.x, mn.y, mn.z),
                    FreeCAD.Vector(mn.x, mx.y, mn.z),
                    FreeCAD.Vector(mx.x, mx.y, mn.z),
                    FreeCAD.Vector(mn.x, mn.y, mx.z),
                    FreeCAD.Vector(mx.x, mn.y, mx.z),
                    FreeCAD.Vector(mn.x, mx.y, mx.z),
                    FreeCAD.Vector(mx.x, mx.y, mx.z)
                ]
                w_corners = [self.placement.multVec(c) for c in corners]
                w_mn = FreeCAD.Vector(min(c.x for c in w_corners), min(c.y for c in w_corners), min(c.z for c in w_corners))
                w_mx = FreeCAD.Vector(max(c.x for c in w_corners), max(c.y for c in w_corners), max(c.z for c in w_corners))
                return w_mn, w_mx
            return mn, mx
        except Exception:
            from core import dm_logger
            dm_logger.debug("SdfNurbsSurfaceField.bounding_box fallback to unit cube")
            return FreeCAD.Vector(-10, -10, -10), FreeCAD.Vector(10, 10, 10)

    def to_glsl(self, ctx, point_var="p"):
        ctx.need_helper("apply_inv_mat")
        ctx.need_helper("sdf_nurbs_surface")

        # Extract B-Spline surface data
        poles = self.surface.getPoles() # List of lists of FreeCAD.Vector
        u_knots = self.surface.getUKnots()
        v_knots = self.surface.getVKnots()
        u_mults = self.surface.getUMultiplicities()
        v_mults = self.surface.getVMultiplicities()
        u_deg = self.surface.UDegree
        v_deg = self.surface.VDegree
        
        # Flatten knots
        u_flat = []
        for k, m in zip(u_knots, u_mults): u_flat.extend([k] * m)
        v_flat = []
        for k, m in zip(v_knots, v_mults): v_flat.extend([k] * m)
        
        nu = len(poles)
        nv = len(poles[0])
        
        # Pad to fixed sizes (max 16x16 poles for GLSL)
        max_u = 16
        max_v = 16
        padded_poles = [0.0] * (max_u * max_v * 3)
        for i in range(min(nu, max_u)):
            for j in range(min(nv, max_v)):
                p = poles[i][j]
                idx = (i * max_v + j) * 3
                padded_poles[idx : idx+3] = [p.x, p.y, p.z]
                
        padded_u_knots = [0.0] * 32
        for i, k in enumerate(u_flat[:32]): padded_u_knots[i] = k
        padded_v_knots = [0.0] * 32
        for i, k in enumerate(v_flat[:32]): padded_v_knots[i] = k

        # Register uniforms
        u_pols = ctx.uniform(f"vec3[{max_u * max_v}]", padded_poles)
        u_uknt = ctx.uniform("float[32]", padded_u_knots)
        u_vknt = ctx.uniform("float[32]", padded_v_knots)
        u_nu   = ctx.uniform("int", nu)
        u_nv   = ctx.uniform("int", nv)
        u_udeg = ctx.uniform("int", u_deg)
        u_vdeg = ctx.uniform("int", v_deg)
        u_u0   = ctx.uniform("float", u_flat[0])
        u_u1   = ctx.uniform("float", u_flat[-1])
        u_v0   = ctx.uniform("float", v_flat[0])
        u_v1   = ctx.uniform("float", v_flat[-1])

        # Inverse matrix
        p_expr = point_var
        if self.placement is not None:
            m = self.placement.toMatrix()
            m.invert()
            inv_m = [
                m.A11, m.A12, m.A13, m.A14,
                m.A21, m.A22, m.A23, m.A24,
                m.A31, m.A32, m.A33, m.A34,
                m.A41, m.A42, m.A43, m.A44
            ]
            u_inv_m = ctx.uniform("mat4", inv_m)
            p_expr = f"apply_inv_mat({u_inv_m}, {point_var})"

        return (f"sdf_nurbs_surface({p_expr}, {u_pols}, {u_uknt}, {u_vknt}, "
                f"{u_udeg}, {u_vdeg}, {u_nu}, {u_nv}, "
                f"vec2({u_u0}, {u_v0}), vec2({u_u1}, {u_v1}))")
