"""
core/sdf/sdf/nurbs_surface.py

SDF field for a NURBS surface (solid boundary — negative inside, positive outside).
"""
import numpy as np
import FreeCAD
import Part
from core.sdf.sdf_field import SdfField, _GLSL_APPLY_INV_MAT
from core import dm_logger

_GLSL_SDF_NURBS_SURFACE = """
vec3 evaluate_bspline_surf(vec2 uv, vec3 poles[256], float u_knots[32], float v_knots[32], int u_deg, int v_deg, int nu, int nv) {
    int ku = u_deg;
    for (int i = u_deg; i < nu; i++) if (uv.x >= u_knots[i]) ku = i;
    int kv = v_deg;
    for (int i = v_deg; i < nv; i++) if (uv.y >= v_knots[i]) kv = i;

    vec3 temp_v[4];
    for (int j = 0; j <= 3; j++) {
        if (j > v_deg) break;
        int v_idx = clamp(kv - v_deg + j, 0, nv - 1);

        vec3 d[4];
        for (int i = 0; i <= 3; i++) {
            if (i > u_deg) break;
            int u_idx = clamp(ku - u_deg + i, 0, nu - 1);
            d[i] = poles[u_idx * 16 + v_idx];
        }

        for (int r = 1; r <= 3; r++) {
            if (r > u_deg) break;
            for (int i = 3; i >= 1; i--) {
                if (i < r || i > u_deg) continue;
                float den = u_knots[ku + 1 + i - r] - u_knots[ku - u_deg + i];
                float alpha = (den > 1e-8) ? (uv.x - u_knots[ku - u_deg + i]) / den : 0.0;
                d[i] = mix(d[i-1], d[i], alpha);
            }
        }
        temp_v[j] = d[clamp(u_deg, 0, 3)];
    }

    for (int r = 1; r <= 3; r++) {
        if (r > v_deg) break;
        for (int j = 3; j >= 1; j--) {
            if (j < r || j > v_deg) continue;
            float den = v_knots[kv + 1 + j - r] - v_knots[kv - v_deg + j];
            float alpha = (den > 1e-8) ? (uv.y - v_knots[kv - v_deg + j]) / den : 0.0;
            temp_v[j] = mix(temp_v[j-1], temp_v[j], alpha);
        }
    }
    return temp_v[clamp(v_deg, 0, 3)];
}

float sdf_nurbs_surface(vec3 p, vec3 poles[256], float u_knots[32], float v_knots[32], int u_deg, int v_deg, int nu, int nv, vec2 uv0, vec2 uv1) {
    float min_d2 = 1e18;
    vec2 best_uv = uv0;
    for (int i = 0; i <= 8; i++) {
        for (int j = 0; j <= 8; j++) {
            vec2 uv = uv0 + (uv1 - uv0) * vec2(float(i)/8.0, float(j)/8.0);
            vec3 q = evaluate_bspline_surf(uv, poles, u_knots, v_knots, u_deg, v_deg, nu, nv);
            float d2 = dot(p - q, p - q);
            if (d2 < min_d2) { min_d2 = d2; best_uv = uv; }
        }
    }

    vec2 uv = best_uv;
    for (int i = 0; i < 4; i++) {
        vec3 q = evaluate_bspline_surf(uv, poles, u_knots, v_knots, u_deg, v_deg, nu, nv);
        float eps = 1e-4;
        vec3 qu = (evaluate_bspline_surf(uv + vec2(eps, 0), poles, u_knots, v_knots, u_deg, v_deg, nu, nv) - q) / eps;
        vec3 qv = (evaluate_bspline_surf(uv + vec2(0, eps), poles, u_knots, v_knots, u_deg, v_deg, nu, nv) - q) / eps;

        vec2 b = vec2(dot(p - q, qu), dot(p - q, qv));
        mat2 A = mat2(dot(qu, qu), dot(qv, qu), dot(qu, qv), dot(qv, qv));
        float det = A[0][0]*A[1][1] - A[0][1]*A[1][0];
        if (abs(det) > 1e-10) {
            vec2 duv = vec2(A[1][1]*b.x - A[0][1]*b.y, -A[1][0]*b.x + A[0][0]*b.y) / det;
            uv = clamp(uv + duv, uv0, uv1);
        }
    }

    vec3 final_q = evaluate_bspline_surf(uv, poles, u_knots, v_knots, u_deg, v_deg, nu, nv);
    vec3 final_normal = normalize(cross(
        (evaluate_bspline_surf(uv + vec2(1e-4, 0), poles, u_knots, v_knots, u_deg, v_deg, nu, nv) - final_q),
        (evaluate_bspline_surf(uv + vec2(0, 1e-4), poles, u_knots, v_knots, u_deg, v_deg, nu, nv) - final_q)
    ));
    float dist = length(p - final_q);
    return (dot(p - final_q, final_normal) >= 0.0 ? 1.0 : -1.0) * dist;
}
"""


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
        ctx.add_custom_helper("apply_inv_mat", _GLSL_APPLY_INV_MAT)
        ctx.add_custom_helper("sdf_nurbs_surface", _GLSL_SDF_NURBS_SURFACE)

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
