# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import math
import numpy as np
from .sdf2d_field import Sdf2dField


# Shared GLSL helpers — registered once per shader compile via add_custom_helper (deduped by name).

_GLSL_SOLVE_CUBIC = """
int sd_solve_cubic(float a, float b, float c, float d,
                   out float r0, out float r1, out float r2) {
    r0 = 0.0; r1 = 0.0; r2 = 0.0;
    // Degeneracy is RELATIVE, and relative only to a, b, c. These coefficients
    // come from a Bezier's y control points in millimetres, so a segment that is
    // straight in y -- equally spaced control points, which is what a sketch line
    // converts to -- has a cubic term that is exactly zero in real arithmetic and
    // ~1e-6 in the float32 the GPU actually runs. An absolute 1e-8 test read that
    // as a genuine cubic and went on to divide c (~-44) by it; the garbage roots
    // that came back flipped the winding number along whole scan lines, which is
    // what ate the extrusion walls. Measured separation is six orders of
    // magnitude: 2e-8 for the degenerate segments, 5e-2 for the real cubics.
    // `d` is excluded on purpose -- it is the only point-dependent coefficient,
    // and letting it move the threshold would let one segment count as cubic on
    // one scan line and linear on the next.
    float scale = max(max(abs(a), abs(b)), abs(c));
    if (scale <= 0.0) return 0;
    float eps = 1e-6 * scale;
    if (abs(a) < eps) {
        // Degenerate to quadratic: b*t^2 + c*t + d = 0
        if (abs(b) < eps) {
            if (abs(c) > eps) { r0 = -d / c; return 1; }
            return 0;
        }
        float disc2 = c*c - 4.0*b*d;
        if (disc2 < 0.0) return 0;
        float sq = sqrt(disc2);
        r0 = (-c + sq) / (2.0*b);
        r1 = (-c - sq) / (2.0*b);
        return 2;
    }
    // Monic: t^3 + pb*t^2 + pc*t + pd
    float pb = b / a, pc = c / a, pd = d / a;
    // Depress: t = u - pb/3
    float p3 = pc - pb*pb/3.0;
    float q3 = 2.0*pb*pb*pb/27.0 - pb*pc/3.0 + pd;
    float disc = q3*q3*0.25 + p3*p3*p3/27.0;
    float off = -pb / 3.0;
    if (disc > 1e-12) {
        float sq = sqrt(disc);
        float u = -q3*0.5 + sq;
        float v = -q3*0.5 - sq;
        u = sign(u) * pow(abs(u), 1.0/3.0);
        v = sign(v) * pow(abs(v), 1.0/3.0);
        r0 = u + v + off;
        return 1;
    } else {
        float r = sqrt(max(-p3*p3*p3/27.0, 0.0));
        if (r < 1e-7) {
            r0 = off;
            return 1;
        }
        float theta = acos(clamp(-q3 / (2.0*r), -1.0, 1.0));
        float m = 2.0 * pow(r, 1.0/3.0);
        r0 = m * cos(theta / 3.0)             + off;
        r1 = m * cos((theta + 6.2831853) / 3.0) + off;
        r2 = m * cos((theta + 12.5663706) / 3.0) + off;
        return 3;
    }
}
"""

_GLSL_CUBIC_BEZ_2D = """
float sd_cubic_bez_2d(vec2 p, vec2 a, vec2 b, vec2 c, vec2 d) {
    float md = 1e18;
    for (int i = 0; i <= 6; i++) {
        float t = float(i) / 6.0;
        for (int j = 0; j < 2; j++) {
            float s = 1.0 - t;
            vec2 B   = s*s*s*a + 3.0*s*s*t*b + 3.0*s*t*t*c + t*t*t*d;
            vec2 dB  = 3.0*(s*s*(b-a) + 2.0*s*t*(c-b) + t*t*(d-c));
            vec2 dB2 = 6.0*(s*(c - 2.0*b + a) + t*(d - 2.0*c + b));
            float denom = dot(dB, dB) + dot(B - p, dB2);
            if (abs(denom) > 1e-10) t = clamp(t - dot(B-p, dB) / denom, 0.0, 1.0);
        }
        float s = 1.0 - t;
        vec2 B = s*s*s*a + 3.0*s*s*t*b + 3.0*s*t*t*c + t*t*t*d;
        md = min(md, dot(B-p, B-p));
    }
    return sqrt(md);
}
"""

_GLSL_CUBIC_WINDING = """
int sd_cubic_winding(vec2 p, vec2 a, vec2 b, vec2 c, vec2 d) {
    // Coefficients of B_y(t) - p.y = 0
    float ca = -a.y + 3.0*b.y - 3.0*c.y + d.y;
    float cb =  3.0*a.y - 6.0*b.y + 3.0*c.y;
    float cc = -3.0*a.y + 3.0*b.y;
    float cd =  a.y - p.y;
    float r0, r1, r2;
    int nr = sd_solve_cubic(ca, cb, cc, cd, r0, r1, r2);
    int w = 0;
    const float EPS = 1e-5;
    // Horizontal-tangent threshold, relative to the segment's own y extent.
    // At an extremum sd_solve_cubic returns the root as 1 +/- 1e-4, and dB.y
    // near tr = 1 grows like 3*(1-tr)^2*(b.y-a.y) + 6*(1-tr)*tr*(c.y-b.y),
    // which is already ~2.7e-3 there -- four orders above an absolute 1e-7.
    // The touch was therefore counted as a crossing and the whole scan line
    // through the extremum flipped sign. An absolute constant is meaningless
    // on a curve measured in millimetres; scale it.
    float y_scale = max(max(abs(a.y - d.y), abs(b.y - c.y)), 1.0);
    float tan_eps = 1e-4 * y_scale;
    for (int k = 0; k < 3; k++) {
        float tr = (k == 0) ? r0 : (k == 1) ? r1 : r2;
        if (k >= nr) break;
        float s = 1.0 - tr;
        vec2 dB = 3.0*(s*s*(b-a) + 2.0*s*tr*(c-b) + tr*tr*(d-c));
        if (abs(dB.y) < tan_eps) continue; // horizontal tangent: no crossing

        // Half-open interval convention: upward curve owns [0, 1), downward owns (0, 1]
        if (dB.y > 0.0) {
            if (tr < -EPS || tr >= 1.0 - EPS) continue;
        } else {
            if (tr <= EPS || tr > 1.0 + EPS) continue;
        }

        vec2 B = s*s*s*a + 3.0*s*s*tr*b + 3.0*s*tr*tr*c + tr*tr*tr*d;
        if (B.x > p.x) w += (dB.y > 0.0) ? 1 : -1;
    }
    return w;
}
"""


def _eval_cubic(t, p0, p1, p2, p3):
    s = 1.0 - t
    x = s**3*p0[0] + 3*s**2*t*p1[0] + 3*s*t**2*p2[0] + t**3*p3[0]
    y = s**3*p0[1] + 3*s**2*t*p1[1] + 3*s*t**2*p2[1] + t**3*p3[1]
    return x, y


def _seg_dist_sq(px, py, p0, p1, p2, p3):
    best = math.inf
    a0, a1 = p0[0], p0[1]
    b0, b1 = p1[0], p1[1]
    c0, c1 = p2[0], p2[1]
    d0, d1 = p3[0], p3[1]

    for i in range(7):
        t = i / 6.0
        for _ in range(2):
            s = 1.0 - t
            Bx = s**3 * a0 + 3.0 * s**2 * t * b0 + 3.0 * s * t**2 * c0 + t**3 * d0
            By = s**3 * a1 + 3.0 * s**2 * t * b1 + 3.0 * s * t**2 * c1 + t**3 * d1
            dBx = 3.0 * (s**2 * (b0 - a0) + 2.0 * s * t * (c0 - b0) + t**2 * (d0 - c0))
            dBy = 3.0 * (s**2 * (b1 - a1) + 2.0 * s * t * (c1 - b1) + t**2 * (d1 - c1))
            dBdB = dBx * dBx + dBy * dBy
            if dBdB > 1e-10:
                dot = (Bx - px) * dBx + (By - py) * dBy
                t = max(0.0, min(1.0, t - dot / dBdB))
        s = 1.0 - t
        Bx = s**3 * a0 + 3.0 * s**2 * t * b0 + 3.0 * s * t**2 * c0 + t**3 * d0
        By = s**3 * a1 + 3.0 * s**2 * t * b1 + 3.0 * s * t**2 * c1 + t**3 * d1
        d = (Bx - px)**2 + (By - py)**2
        if d < best:
            best = d
    return best


def _winding_number(px, py, segments, n_sub=32):
    w = 0
    for p0, p1, p2, p3 in segments:
        pts = [_eval_cubic(i / n_sub, p0, p1, p2, p3) for i in range(n_sub + 1)]
        for i in range(n_sub):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            if y0 <= py:
                if y1 > py and (x1 - x0) * (py - y0) > (y1 - y0) * (px - x0):
                    w += 1
            else:
                if y1 <= py and (x1 - x0) * (py - y0) < (y1 - y0) * (px - x0):
                    w -= 1
    return w


class Sdf2dBezierCurve(Sdf2dField):
    """
    Exact signed distance to a closed 2D cubic Bezier curve.
    segments: list of (p0, p1, p2, p3), each point (x, y), in order around the curve.
    """

    def __init__(self, segments: list):
        self.segments = [
            tuple(tuple(float(c) for c in pt) for pt in seg)
            for seg in segments
        ]

    def evaluate_2d(self, x: float, y: float) -> float:
        if not self.segments:
            return math.inf
        min_d2 = min(_seg_dist_sq(x, y, *seg) for seg in self.segments)
        w = _winding_number(x, y, self.segments)
        sign = -1.0 if w != 0 else 1.0
        return sign * math.sqrt(min_d2)

    N_SEEDS = 7          # Newton starts per segment, evenly spaced in t
    N_NEWTON = 2         # Newton steps per seed
    N_WIND = 65          # polyline samples per segment for the winding number
    _CHUNK_WORK = 2_000_000     # array elements per temporary; bounds peak memory

    @staticmethod
    def _bezier(t, a, b, c, d):
        """Cubic Bezier point for t of any shape; control points broadcast on -1."""
        s = 1.0 - t
        return ((s**3)[..., None] * a + (3.0 * s**2 * t)[..., None] * b
                + (3.0 * s * t**2)[..., None] * c + (t**3)[..., None] * d)

    def evaluate_2d_grid(self, pts: np.ndarray) -> np.ndarray:
        """Signed distance for (n, 2) points.

        Segments and Newton seeds are one batched axis rather than two Python
        loops. The loops issued ~111 numpy calls per invocation whatever the
        batch size, which is fixed overhead the callers cannot amortise: a
        stacked face extrude's height-datum scan makes ~460 of these calls on
        rings of a handful of points each, and that was 97% of its cost.
        Points are chunked so the wider temporaries stay bounded.
        """
        n = len(pts)
        if n == 0 or not self.segments:
            return np.full(n, np.inf, dtype=np.float32)

        P = np.asarray(pts, dtype=np.float32).reshape((-1, 2))
        segs = np.asarray(self.segments, dtype=np.float32)      # (K, 4, 2)
        k = len(segs)
        a, b, c, d = (segs[:, i][:, None, None, :] for i in range(4))

        # Winding polyline: (K, N_WIND, 2), flattened to one edge list.
        t_w = np.linspace(0.0, 1.0, self.N_WIND, dtype=np.float32)
        Bw = self._bezier(np.broadcast_to(t_w, (k, self.N_WIND)),
                          *(segs[:, i][:, None, :] for i in range(4)))
        x0 = Bw[:, :-1, 0].ravel();  y0 = Bw[:, :-1, 1].ravel()
        x1 = Bw[:, 1:, 0].ravel();   y1 = Bw[:, 1:, 1].ravel()
        dx = x1 - x0;                dy = y1 - y0

        seeds = np.linspace(0.0, 1.0, self.N_SEEDS, dtype=np.float32)
        width = max(k * self.N_SEEDS, k * (self.N_WIND - 1))
        chunk = max(1024, self._CHUNK_WORK // max(width, 1))

        out = np.empty(n, dtype=np.float32)
        for lo in range(0, n, chunk):
            Q = P[lo:lo + chunk]
            q = Q[None, None, :, :]                             # (1, 1, m, 2)
            t = np.broadcast_to(seeds[None, :, None],
                                (k, self.N_SEEDS, len(Q))).astype(np.float32)

            for _ in range(self.N_NEWTON):
                s = 1.0 - t
                Bv = self._bezier(t, a, b, c, d)
                dB = 3.0 * ((s**2)[..., None] * (b - a)
                            + (2.0 * s * t)[..., None] * (c - b)
                            + (t**2)[..., None] * (d - c))
                dBdB = np.sum(dB * dB, axis=-1)
                dot = np.sum((Bv - q) * dB, axis=-1)
                step = np.where(dBdB > 1e-10, dot / dBdB, 0.0)
                t = np.clip(t - step, 0.0, 1.0)

            Bv = self._bezier(t, a, b, c, d)
            diff = Bv - q
            min_dist_sq = np.sum(diff * diff, axis=-1).min(axis=(0, 1))

            px = Q[:, 0][:, None];  py = Q[:, 1][:, None]
            up = (y0 <= py) & (y1 > py)
            down = (y1 <= py) & (y0 > py)
            cross = dx * (py - y0) - dy * (px - x0)
            winding = (np.count_nonzero(up & (cross > 0), axis=1)
                       - np.count_nonzero(down & (cross < 0), axis=1))

            out[lo:lo + chunk] = np.where(winding != 0, -1.0, 1.0) * np.sqrt(min_dist_sq)

        return out

    def bbox_2d(self):
        xs, ys = [], []
        for seg in self.segments:
            for pt in seg:
                xs.append(pt[0])
                ys.append(pt[1])
        return min(xs), min(ys), max(xs), max(ys)

    def to_glsl_2d(self, ctx, pvar: str = "q") -> str:
        ctx.add_custom_helper("sd_solve_cubic", _GLSL_SOLVE_CUBIC)
        ctx.add_custom_helper("sd_cubic_bez_2d", _GLSL_CUBIC_BEZ_2D)
        ctx.add_custom_helper("sd_cubic_winding", _GLSL_CUBIC_WINDING)

        func_name = ctx.get_unique_name("sdf_bezier")

        lines = [f"float {func_name}(vec2 p) {{"]
        lines.append("    float d = 1e18;")
        lines.append("    int w = 0;")
        lines.append("    float d_aabb;")

        for k, (p0, p1, p2, p3) in enumerate(self.segments):
            xs = [p0[0], p1[0], p2[0], p3[0]]
            ys = [p0[1], p1[1], p2[1], p3[1]]
            ua = ctx.uniform("vec2", [p0[0], p0[1]])
            ub = ctx.uniform("vec2", [p1[0], p1[1]])
            uc = ctx.uniform("vec2", [p2[0], p2[1]])
            ud = ctx.uniform("vec2", [p3[0], p3[1]])
            ulo = ctx.uniform("vec2", [min(xs), min(ys)])
            uhi = ctx.uniform("vec2", [max(xs), max(ys)])
            lines.append(f"    {{ vec2 a{k}={ua}, b{k}={ub}, c{k}={uc}, d{k}={ud};")
            lines.append(f"      vec2 cl{k} = clamp(p, {ulo}, {uhi});")
            lines.append(f"      d_aabb = length(p - cl{k});")
            lines.append(f"      if (d_aabb < d) d = min(d, sd_cubic_bez_2d(p, a{k}, b{k}, c{k}, d{k}));")
            lines.append(f"      if (p.y >= {ulo}.y - 1e-6 && p.y <= {uhi}.y + 1e-6)")
            lines.append(f"          w += sd_cubic_winding(p, a{k}, b{k}, c{k}, d{k}); }}")

        lines.append("    return (w == 0 ? 1.0 : -1.0) * d;")
        lines.append("}")

        ctx.add_custom_helper(func_name, "\n".join(lines))
        return f"{func_name}({pvar})"
