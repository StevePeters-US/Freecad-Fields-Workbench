import math
import numpy as np
import FreeCAD
from .sdf_field import SdfField


_GLSL_SD_CUBIC_BEZ_3D = """
float sd_cubic_bez_3d(vec3 p, vec3 a, vec3 b, vec3 c, vec3 d) {
    float md = 1e18;
    for (int i = 0; i <= 6; i++) {
        float t = float(i) / 6.0;
        for (int j = 0; j < 2; j++) {
            float s = 1.0 - t;
            vec3 B  = s*s*s*a + 3.0*s*s*t*b + 3.0*s*t*t*c + t*t*t*d;
            vec3 dB = 3.0*(s*s*(b-a) + 2.0*s*t*(c-b) + t*t*(d-c));
            float dBdB = dot(dB, dB);
            if (dBdB > 1e-10) t = clamp(t - dot(B-p, dB)/dBdB, 0.0, 1.0);
        }
        float s = 1.0 - t;
        vec3 B = s*s*s*a + 3.0*s*s*t*b + 3.0*s*t*t*c + t*t*t*d;
        md = min(md, dot(B-p, B-p));
    }
    return sqrt(md);
}
"""


class SdfPipeField(SdfField):
    """
    SDF for a round tube/pipe swept along a 3D cubic Bezier curve path.
    segments_3d: list of (p0, p1, p2, p3), each pi = (x, y, z) in world space.
    radius: tube cross-section radius.
    """

    def __init__(self, segments_3d: list, radius: float):
        self.segments = [
            tuple(tuple(float(c) for c in pt) for pt in seg)
            for seg in segments_3d
        ]
        self.radius = radius

    def _seg_dist(self, px, py, pz, p0, p1, p2, p3, seeds=7, iters=4):
        min_d2 = math.inf
        for k in range(seeds):
            t = k / (seeds - 1) if seeds > 1 else 0.5
            for _ in range(iters):
                s = 1.0 - t
                bx = s**3*p0[0] + 3*s**2*t*p1[0] + 3*s*t**2*p2[0] + t**3*p3[0]
                by = s**3*p0[1] + 3*s**2*t*p1[1] + 3*s*t**2*p2[1] + t**3*p3[1]
                bz = s**3*p0[2] + 3*s**2*t*p1[2] + 3*s*t**2*p2[2] + t**3*p3[2]
                dx, dy, dz = bx - px, by - py, bz - pz
                b1x = 3*(s**2*(p1[0]-p0[0]) + 2*s*t*(p2[0]-p1[0]) + t**2*(p3[0]-p2[0]))
                b1y = 3*(s**2*(p1[1]-p0[1]) + 2*s*t*(p2[1]-p1[1]) + t**2*(p3[1]-p2[1]))
                b1z = 3*(s**2*(p1[2]-p0[2]) + 2*s*t*(p2[2]-p1[2]) + t**2*(p3[2]-p2[2]))
                b2x = 6*(s*(p2[0]-2*p1[0]+p0[0]) + t*(p3[0]-2*p2[0]+p1[0]))
                b2y = 6*(s*(p2[1]-2*p1[1]+p0[1]) + t*(p3[1]-2*p2[1]+p1[1]))
                b2z = 6*(s*(p2[2]-2*p1[2]+p0[2]) + t*(p3[2]-2*p2[2]+p1[2]))
                f  = dx*b1x + dy*b1y + dz*b1z
                df = b1x**2 + b1y**2 + b1z**2 + dx*b2x + dy*b2y + dz*b2z
                if abs(df) > 1e-12:
                    t = max(0.0, min(1.0, t - f / df))
            s = 1.0 - t
            bx = s**3*p0[0] + 3*s**2*t*p1[0] + 3*s*t**2*p2[0] + t**3*p3[0]
            by = s**3*p0[1] + 3*s**2*t*p1[1] + 3*s*t**2*p2[1] + t**3*p3[1]
            bz = s**3*p0[2] + 3*s**2*t*p1[2] + 3*s*t**2*p2[2] + t**3*p3[2]
            d2 = (bx - px)**2 + (by - py)**2 + (bz - pz)**2
            if d2 < min_d2:
                min_d2 = d2
        return math.sqrt(min_d2)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        if not self.segments:
            return math.inf
        d = min(self._seg_dist(point.x, point.y, point.z, *seg) for seg in self.segments)
        return d - self.radius

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        N = points.shape[0]
        if N == 0 or not self.segments:
            return np.full(N, np.inf, dtype=np.float32)
        px, py, pz = points[:, 0], points[:, 1], points[:, 2]
        min_d2 = np.full(N, np.inf, dtype=np.float64)
        for p0, p1, p2, p3 in self.segments:
            p0a = np.array(p0); p1a = np.array(p1)
            p2a = np.array(p2); p3a = np.array(p3)
            for k in range(7):
                t = np.full(N, k / 6.0, dtype=np.float64)
                for _ in range(4):
                    s = 1.0 - t
                    bx_ = s**3*p0a[0] + 3*s**2*t*p1a[0] + 3*s*t**2*p2a[0] + t**3*p3a[0]
                    by_ = s**3*p0a[1] + 3*s**2*t*p1a[1] + 3*s*t**2*p2a[1] + t**3*p3a[1]
                    bz_ = s**3*p0a[2] + 3*s**2*t*p1a[2] + 3*s*t**2*p2a[2] + t**3*p3a[2]
                    dx_ = bx_ - px; dy_ = by_ - py; dz_ = bz_ - pz
                    b1x = 3*(s**2*(p1a[0]-p0a[0]) + 2*s*t*(p2a[0]-p1a[0]) + t**2*(p3a[0]-p2a[0]))
                    b1y = 3*(s**2*(p1a[1]-p0a[1]) + 2*s*t*(p2a[1]-p1a[1]) + t**2*(p3a[1]-p2a[1]))
                    b1z = 3*(s**2*(p1a[2]-p0a[2]) + 2*s*t*(p2a[2]-p1a[2]) + t**2*(p3a[2]-p2a[2]))
                    b2x = 6*(s*(p2a[0]-2*p1a[0]+p0a[0]) + t*(p3a[0]-2*p2a[0]+p1a[0]))
                    b2y = 6*(s*(p2a[1]-2*p1a[1]+p0a[1]) + t*(p3a[1]-2*p2a[1]+p1a[1]))
                    b2z = 6*(s*(p2a[2]-2*p1a[2]+p0a[2]) + t*(p3a[2]-2*p2a[2]+p1a[2]))
                    f  = dx_*b1x + dy_*b1y + dz_*b1z
                    df = b1x**2+b1y**2+b1z**2 + dx_*b2x + dy_*b2y + dz_*b2z
                    mask = np.abs(df) > 1e-12
                    t = np.where(mask, np.clip(t - f / np.where(mask, df, 1.0), 0.0, 1.0), t)
                s = 1.0 - t
                bx_ = s**3*p0a[0] + 3*s**2*t*p1a[0] + 3*s*t**2*p2a[0] + t**3*p3a[0]
                by_ = s**3*p0a[1] + 3*s**2*t*p1a[1] + 3*s*t**2*p2a[1] + t**3*p3a[1]
                bz_ = s**3*p0a[2] + 3*s**2*t*p1a[2] + 3*s*t**2*p2a[2] + t**3*p3a[2]
                d2 = (bx_ - px)**2 + (by_ - py)**2 + (bz_ - pz)**2
                np.minimum(min_d2, d2, out=min_d2)
        return (np.sqrt(min_d2) - self.radius).astype(np.float32)

    def to_glsl(self, ctx, point_var: str = "p") -> str:
        ctx.add_custom_helper("sd_cubic_bez_3d", _GLSL_SD_CUBIC_BEZ_3D)
        func_name = ctx.get_unique_name("sdf_pipe")
        r_u = ctx.uniform("float", self.radius)

        lines = [f"float {func_name}(vec3 p) {{"]
        lines.append("    float d = 1e18;")
        for p0, p1, p2, p3 in self.segments:
            lines.append(
                f"    d = min(d, sd_cubic_bez_3d(p,"
                f" vec3({p0[0]:.6g},{p0[1]:.6g},{p0[2]:.6g}),"
                f" vec3({p1[0]:.6g},{p1[1]:.6g},{p1[2]:.6g}),"
                f" vec3({p2[0]:.6g},{p2[1]:.6g},{p2[2]:.6g}),"
                f" vec3({p3[0]:.6g},{p3[1]:.6g},{p3[2]:.6g})));"
            )
        lines.append(f"    return d - {r_u};")
        lines.append("}")

        ctx.add_custom_helper(func_name, "\n".join(lines))
        return f"{func_name}({point_var})"

    def bounding_box(self):
        if not self.segments:
            return (FreeCAD.Vector(-10, -10, -10), FreeCAD.Vector(10, 10, 10))
        all_pts = [pt for seg in self.segments for pt in seg]
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        zs = [p[2] for p in all_pts]
        r = self.radius
        return (
            FreeCAD.Vector(min(xs) - r, min(ys) - r, min(zs) - r),
            FreeCAD.Vector(max(xs) + r, max(ys) + r, max(zs) + r),
        )
