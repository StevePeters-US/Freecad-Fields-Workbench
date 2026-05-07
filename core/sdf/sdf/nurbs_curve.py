"""
core/sdf/sdf/nurbs_curve.py

SDF field for a 3D NURBS (BSpline) curve. Evaluates the distance from
any point to the closest point on the curve.
"""
import numpy as np
import FreeCAD
from core.sdf.sdf_field import SdfField


class SdfNurbsCurveField(SdfField):
    """
    Signed distance to a 3D NURBS curve with a tube radius.

    Negative inside the tube, positive outside.
    f(p) = closest_distance(p, curve) - tube_radius
    """

    def __init__(self, bspline_curve, tube_radius: float = 1.0,
                 placement: FreeCAD.Placement = None):
        """
        bspline_curve: Part.BSplineCurve (FreeCAD)
        tube_radius:   radius of the implicit tube around the curve (mm)
        placement:     optional world transform for the curve
        """
        self.curve = bspline_curve
        self.tube_radius = tube_radius
        self.placement = placement

    def _world_to_local(self, point: FreeCAD.Vector) -> FreeCAD.Vector:
        if self.placement is None:
            return point
        return self.placement.inverse().multVec(point)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        local_pt = self._world_to_local(point)
        try:
            param = self.curve.parameter(local_pt)
            closest = self.curve.value(param)
            dist = (local_pt - closest).Length
        except Exception:
            dist = float('inf')
        return dist - self.tube_radius

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        results = np.empty(len(points), dtype=np.float32)
        for i, pt in enumerate(points):
            fpt = FreeCAD.Vector(float(pt[0]), float(pt[1]), float(pt[2]))
            results[i] = self.evaluate(fpt)
        return results

    def bounding_box(self):
        try:
            # Note: Part.BSplineCurve.toShape().BoundBox is the standard way
            # to get the BB of a curve. We pad by tube_radius.
            bb = self.curve.toBSpline().toShape().BoundBox
            pad = self.tube_radius
            mn = FreeCAD.Vector(bb.XMin - pad, bb.YMin - pad, bb.ZMin - pad)
            mx = FreeCAD.Vector(bb.XMax + pad, bb.YMax + pad, bb.ZMax + pad)
            if self.placement:
                # We should really transform the 8 corners and take the new BB,
                # but for simplicity we just transform the min/max if they are points.
                # Actually, placement.multVec(mn) is only correct if it's just translation.
                # A more robust way:
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
            dm_logger.debug("SdfNurbsCurveField.bounding_box fallback to unit cube")
            return FreeCAD.Vector(-10, -10, -10), FreeCAD.Vector(10, 10, 10)

    def to_glsl(self, ctx, point_var="p"):
        ctx.need_helper("apply_inv_mat")
        ctx.need_helper("sdf_nurbs_curve")

        # Extract B-Spline data
        poles = [FreeCAD.Vector(p) for p in self.curve.getPoles()]
        knots = self.curve.getKnots()
        mults = self.curve.getMultiplicities()
        degree = self.curve.Degree
        
        # Flatten knots using multiplicities
        flat_knots = []
        for k, m in zip(knots, mults):
            flat_knots.extend([k] * m)
        
        n_poles = len(poles)
        n_knots = len(flat_knots)
        
        # Pad to fixed sizes (max 32 poles/knots for GLSL)
        max_size = 32
        padded_poles = [0.0] * (max_size * 3)
        for i, p in enumerate(poles[:max_size]):
            padded_poles[i*3 : i*3+3] = [p.x, p.y, p.z]
            
        padded_knots = [0.0] * max_size
        for i, k in enumerate(flat_knots[:max_size]):
            padded_knots[i] = k

        # Register uniforms
        u_poles = ctx.uniform(f"vec3[{max_size}]", padded_poles)
        u_knots = ctx.uniform(f"float[{max_size}]", padded_knots)
        u_n     = ctx.uniform("int", n_poles)
        u_deg   = ctx.uniform("int", degree)
        u_rad   = ctx.uniform("float", self.tube_radius)
        u_u0    = ctx.uniform("float", self.curve.FirstParameter)
        u_u1    = ctx.uniform("float", self.curve.LastParameter)

        # Inverse matrix for placement
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

        return f"sdf_nurbs_curve({p_expr}, {u_poles}, {u_knots}, {u_deg}, {u_n}, {u_u0}, {u_u1}, {u_rad})"
