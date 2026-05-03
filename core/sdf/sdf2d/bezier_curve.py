import math
import numpy as np
from .sdf2d_field import Sdf2dField


# Shared GLSL helpers — registered once per shader compile via add_custom_helper (deduped by name).

_GLSL_SOLVE_CUBIC = """
int sd_solve_cubic(float a, float b, float c, float d,
                   out float r0, out float r1, out float r2) {
    r0 = 0.0; r1 = 0.0; r2 = 0.0;
    if (abs(a) < 1e-8) {
        // Degenerate to quadratic: b*t^2 + c*t + d = 0
        if (abs(b) < 1e-8) {
            if (abs(c) > 1e-8) { r0 = -d / c; return 1; }
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
    for (int i = 0; i <= 4; i++) {
        float t = float(i) * 0.25;
        for (int j = 0; j < 4; j++) {
            float s = 1.0 - t;
            vec2 B  = s*s*s*a + 3.0*s*s*t*b + 3.0*s*t*t*c + t*t*t*d;
            vec2 dB = 3.0*(s*s*(b-a) + 2.0*s*t*(c-b) + t*t*(d-c));
            float dBdB = dot(dB, dB);
            if (dBdB > 1e-10) t = clamp(t - dot(B-p, dB)/dBdB, 0.0, 1.0);
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
    for (int k = 0; k < 3; k++) {
        float tr = (k == 0) ? r0 : (k == 1) ? r1 : r2;
        if (k >= nr) break;
        if (tr < 0.0 || tr > 1.0) continue;
        float s = 1.0 - tr;
        vec2 B  = s*s*s*a + 3.0*s*s*tr*b + 3.0*s*tr*tr*c + tr*tr*tr*d;
        vec2 dB = 3.0*(s*s*(b-a) + 2.0*s*tr*(c-b) + tr*tr*(d-c));
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


def _seg_dist_sq(px, py, p0, p1, p2, p3, n_sub=64):
    best = math.inf
    for i in range(n_sub + 1):
        t = i / n_sub
        x, y = _eval_cubic(t, p0, p1, p2, p3)
        d = (x - px)**2 + (y - py)**2
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

    def evaluate_2d_grid(self, pts: np.ndarray) -> np.ndarray:
        n = len(pts)
        result = np.empty(n, dtype=np.float32)
        for i in range(n):
            result[i] = self.evaluate_2d(float(pts[i, 0]), float(pts[i, 1]))
        return result

    def bbox_2d(self):
        xs, ys = [], []
        for seg in self.segments:
            for pt in seg:
                xs.append(pt[0])
                ys.append(pt[1])
        return min(xs), min(ys), max(xs), max(ys)

    def to_glsl_2d(self, ctx, pvar: str = "q") -> str:
        # Register shared helpers (deduplicated by name in ctx._custom_helpers dict)
        ctx.add_custom_helper("sd_solve_cubic", _GLSL_SOLVE_CUBIC)
        ctx.add_custom_helper("sd_cubic_bez_2d", _GLSL_CUBIC_BEZ_2D)
        ctx.add_custom_helper("sd_cubic_winding", _GLSL_CUBIC_WINDING)

        uid = f"{id(self) & 0xFFFFFFFF:08x}"
        func_name = f"sdf_bezier_{uid}"

        lines = [f"float {func_name}(vec2 p) {{"]
        lines.append("    float d = 1e18;")
        lines.append("    int w = 0;")

        for k, (p0, p1, p2, p3) in enumerate(self.segments):
            lines.append(
                f"    {{ vec2 a{k}=vec2({p0[0]},{p0[1]}), b{k}=vec2({p1[0]},{p1[1]}),"
                f" c{k}=vec2({p2[0]},{p2[1]}), d{k}=vec2({p3[0]},{p3[1]});"
            )
            lines.append(f"      d = min(d, sd_cubic_bez_2d(p, a{k}, b{k}, c{k}, d{k}));")
            lines.append(f"      w += sd_cubic_winding(p, a{k}, b{k}, c{k}, d{k}); }}")

        lines.append("    return (w == 0 ? 1.0 : -1.0) * d;")
        lines.append("}")

        ctx.add_custom_helper(func_name, "\n".join(lines))
        return f"{func_name}({pvar})"
