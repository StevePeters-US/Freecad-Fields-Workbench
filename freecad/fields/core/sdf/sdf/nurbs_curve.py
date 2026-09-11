# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/sdf/sdf/nurbs_curve.py

SDF field for a 3D NURBS (BSpline) curve. Evaluates the distance from
any point to the closest point on the curve.
"""
import FreeCAD
from freecad.fields.core.sdf.sdf_field import (
    SdfField, _GLSL_APPLY_INV_MAT, placement_matrix,
)
from freecad.fields.core import fld_logger
import numpy as np

_GLSL_SDF_NURBS_CURVE = """
vec3 evaluate_bspline(float t, vec3 poles[32], float knots[32], int degree, int n) {
    int k = degree;
    for (int i = degree; i < n; i++) {
        if (t >= knots[i]) k = i;
    }
    vec3 d[4];
    for (int i = 0; i <= 3; i++) {
        if (i <= degree) d[i] = poles[clamp(k - degree + i, 0, n-1)];
    }
    for (int r = 1; r <= 3; r++) {
        if (r > degree) break;
        for (int i = 3; i >= 1; i--) {
            if (i < r || i > degree) continue;
            float den = knots[k + 1 + i - r] - knots[k - degree + i];
            float alpha = (den > 1e-8) ? (t - knots[k - degree + i]) / den : 0.0;
            d[i] = mix(d[i-1], d[i], alpha);
        }
    }
    return d[clamp(degree, 0, 3)];
}

vec3 bspline_deriv(float t, vec3 poles[32], float knots[32], int degree, int n) {
    if (degree < 1) return vec3(0.0);
    int k = degree;
    for (int i = degree; i < n; i++) {
        if (t >= knots[i]) k = i;
    }
    vec3 d[4];
    for (int i = 0; i < degree; i++) {
        float den = knots[k - degree + i + degree + 1] - knots[k - degree + i + 1];
        float alpha = (den > 1e-8) ? float(degree) / den : 0.0;
        d[i] = (poles[k - degree + i + 1] - poles[k - degree + i]) * alpha;
    }
    int deg1 = degree - 1;
    for (int r = 1; r <= 2; r++) {
        if (r > deg1) break;
        for (int i = 2; i >= 1; i--) {
            if (i < r || i > deg1) continue;
            float den = knots[k + 1 + i - r] - knots[k - deg1 + i];
            float alpha = (den > 1e-8) ? (t - knots[k - deg1 + i]) / den : 0.0;
            d[i] = mix(d[i-1], d[i], alpha);
        }
    }
    return d[clamp(deg1, 0, 2)];
}

float sdf_nurbs_curve(vec3 p, vec3 poles[32], float knots[32], int degree, int n, float u0, float u1, float r) {
    float min_d2 = 1e18;
    float best_t = u0;
    for (int i = 0; i <= 16; i++) {
        float ut = u0 + (u1 - u0) * float(i) / 16.0;
        vec3 q = evaluate_bspline(ut, poles, knots, degree, n);
        float d2 = dot(p - q, p - q);
        if (d2 < min_d2) { min_d2 = d2; best_t = ut; }
    }
    float t = best_t;
    for (int i = 0; i < 4; i++) {
        vec3 q = evaluate_bspline(t, poles, knots, degree, n);
        vec3 dq = bspline_deriv(t, poles, knots, degree, n);
        float d2 = dot(dq, dq);
        if (d2 > 1e-8) t = clamp(t - dot(q - p, dq) / d2, u0, u1);
    }
    vec3 final_q = evaluate_bspline(t, poles, knots, degree, n);
    return length(p - final_q) - r;
}
"""


_FAR = 1e9  # finite "no result" distance; inf poisons max()/central differences


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
        super().__init__()
        self.curve = bspline_curve
        self.tube_radius = tube_radius
        self.placement = placement
        self._start_pt = None
        self._end_pt = None
        self._sample_pts = None
        self._sample_us = None
        self._init_samples()

    def _init_samples(self):
        """Pre-sample curve endpoints and coarse samples for global closest-point bounds (NC-009)."""
        try:
            u0 = float(getattr(self.curve, "FirstParameter", 0.0))
            u1 = float(getattr(self.curve, "LastParameter", 1.0))
            if hasattr(self.curve, "value"):
                self._start_pt = self.curve.value(u0)
                self._end_pt = self.curve.value(u1)
                M = 64
                us = np.linspace(u0, u1, M)
                pts = [self.curve.value(float(u)) for u in us]
                self._sample_pts = np.array([[p.x, p.y, p.z] for p in pts], dtype=np.float64)
                self._sample_us = us
        except Exception as e:
            fld_logger.debug(f"SdfNurbsCurveField._init_samples failed: {e}")

    def _world_to_local(self, point: FreeCAD.Vector) -> FreeCAD.Vector:
        if self.placement is None:
            return point
        return self.placement.inverse().multVec(point)

    def _evaluate_local_batch(self, local_pts: np.ndarray) -> np.ndarray:
        """Signed distance for a batch of already-LOCAL points. The single
        implementation -- `evaluate` and `evaluate_grid` both route through here,
        so the two can never disagree.

        NC-009: OCC's `parameter()` solves for an orthogonal projection on the
        interior of the curve (C'(u) . (C(u) - p) = 0). Where the true closest
        point is an endpoint -- or the seam of a closed but non-periodic curve --
        no interior orthogonal projection exists and `parameter()` settles on a
        distant local extremum instead. Measured at up to 39.8 mm on a hairpin and
        on 416 of 4000 points on an open helix, so the endpoint test below is
        unconditional. Everything except the OCC call itself is batched: doing this
        work per point cost 1.63x on `evaluate_grid` (NC-002 regression, fixed).
        """
        n = len(local_pts)
        dist = np.empty(n, dtype=np.float64)

        # The one irreducibly per-point step: ~90% of this function's cost is
        # inside these two OCC calls, and no amount of Python work removes them.
        for i in range(n):
            lp = FreeCAD.Vector(float(local_pts[i, 0]),
                                float(local_pts[i, 1]),
                                float(local_pts[i, 2]))
            try:
                closest = self.curve.value(self.curve.parameter(lp))
                dist[i] = (lp - closest).Length
            except Exception as e:
                fld_logger.warn(f"SdfNurbsCurveField.evaluate failed at {lp}: {e}")
                dist[i] = _FAR

        # Endpoints -- unconditional, and the correction that does nearly all the work.
        for endpoint in (self._start_pt, self._end_pt):
            if endpoint is not None:
                ep = np.array([endpoint.x, endpoint.y, endpoint.z], dtype=np.float64)
                np.minimum(dist, np.sqrt(((local_pts - ep) ** 2).sum(axis=1)), out=dist)

        # Coarse samples, for a point OCC trapped on a distant interior branch.
        # Skipped inside the tube, where an overstated distance cannot flip the sign.
        if self._sample_pts is not None and len(self._sample_pts) > 0:
            trapped = dist > self.tube_radius
            if trapped.any():
                sub = local_pts[trapped]
                d2 = ((sub[:, None, :] - self._sample_pts[None, :, :]) ** 2).sum(axis=2)
                idx = d2.argmin(axis=1)
                s_dist = np.sqrt(d2[np.arange(len(sub)), idx])
                cur = dist[trapped]
                better = np.nonzero(s_dist < cur)[0]
                cur = np.minimum(cur, s_dist)
                # Refine only the few points the coarse grid actually improved.
                if len(better) and self._sample_us is not None:
                    u0, u1 = self._sample_us[0], self._sample_us[-1]
                    du = (u1 - u0) / (len(self._sample_us) - 1)
                    for j in better:
                        best_u = self._sample_us[idx[j]]
                        q = FreeCAD.Vector(float(sub[j, 0]), float(sub[j, 1]), float(sub[j, 2]))
                        for fu in np.linspace(max(u0, best_u - du), min(u1, best_u + du), 9):
                            fd = (q - self.curve.value(float(fu))).Length
                            if fd < cur[j]:
                                cur[j] = fd
                dist[trapped] = cur

        out = dist - self.tube_radius
        out[dist >= _FAR] = _FAR
        return out

    def _to_local_array(self, points: np.ndarray) -> np.ndarray:
        """World (N,3) -> local (N,3). The placement inverse is computed once for
        the whole batch, not once per point (NC-002)."""
        pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        if self.placement is None:
            return pts
        # `inverse().toMatrix()`, matching what `multVec` would do -- NOT
        # `toMatrix()` then invert, which diverges for a non-rigid placement.
        m = self.placement.inverse().toMatrix()
        a = np.array([[m.A11, m.A12, m.A13], [m.A21, m.A22, m.A23], [m.A31, m.A32, m.A33]],
                     dtype=np.float64)
        t = np.array([m.A14, m.A24, m.A34], dtype=np.float64)
        return pts @ a.T + t

    def evaluate(self, point: FreeCAD.Vector) -> float:
        local = self._to_local_array(np.array([[point.x, point.y, point.z]]))
        return float(self._evaluate_local_batch(local)[0])

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        n = len(points)
        fld_logger.debug(
            f"SdfNurbsCurveField.evaluate_grid: evaluating {n} "
            "points sequentially (C++ thread safety)"
        )
        results = np.empty(n, dtype=np.float32)
        # Chunked so the (chunk x samples) distance matrix stays small on big grids.
        chunk = 4096
        for s in range(0, n, chunk):
            local = self._to_local_array(points[s:s + chunk])
            results[s:s + chunk] = self._evaluate_local_batch(local)
            fld_logger.debug(
                f"SdfNurbsCurveField.evaluate_grid: progress {min(s + chunk, n)}/{n}"
            )
        fld_logger.debug("SdfNurbsCurveField.evaluate_grid: done")
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
            from freecad.fields.core import fld_logger
            fld_logger.debug("SdfNurbsCurveField.bounding_box fallback to unit cube")
            return FreeCAD.Vector(-10, -10, -10), FreeCAD.Vector(10, 10, 10)

    def to_glsl(self, ctx, point_var="p"):
        ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
        ctx.add_custom_helper("sdf_nurbs_curve", _GLSL_SDF_NURBS_CURVE)

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
            inv_m = placement_matrix(self.placement, inverse=True, flat=True)
            u_inv_m = ctx.uniform("mat4", inv_m)
            p_expr = f"apply_inv_mat({u_inv_m}, {point_var})"

        return f"sdf_nurbs_curve({p_expr}, {u_poles}, {u_knots}, {u_deg}, {u_n}, {u_u0}, {u_u1}, {u_rad})"
