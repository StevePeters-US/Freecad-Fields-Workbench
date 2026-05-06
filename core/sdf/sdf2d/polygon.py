import math
import numpy as np
from .sdf2d_field import Sdf2dField


class Sdf2dPolygon(Sdf2dField):
    """
    2D closed-polygon SDF (sharp corners) using the IQ winding-number approach.
    Vertices should be in order (CW or CCW); the shape is considered solid inside.
    """

    def __init__(self, vertices: list):
        """vertices: list of (x, y) tuples in order."""
        self.vertices = [(float(v[0]), float(v[1])) for v in vertices]

    def evaluate_2d(self, x: float, y: float) -> float:
        n = len(self.vertices)
        d = math.inf
        s = 1.0
        verts = self.vertices
        j = n - 1
        for i in range(n):
            ex = verts[j][0] - verts[i][0]
            ey = verts[j][1] - verts[i][1]
            wx = x - verts[i][0]
            wy = y - verts[i][1]
            e_dot_e = ex * ex + ey * ey
            h = 0.0 if e_dot_e < 1e-12 else max(0.0, min(1.0, (wx * ex + wy * ey) / e_dot_e))
            bx = wx - ex * h
            by = wy - ey * h
            d = min(d, bx * bx + by * by)
            # Winding number contribution
            c1 = y >= verts[i][1]
            c2 = y < verts[j][1]
            c3 = ex * wy > ey * wx
            if (c1 and c2 and c3) or (not c1 and not c2 and not c3):
                s = -s
            j = i
        return s * math.sqrt(d)

    def evaluate_2d_grid(self, pts: np.ndarray) -> np.ndarray:
        n = len(self.vertices)
        vx = np.array([v[0] for v in self.vertices], dtype=np.float64)
        vy = np.array([v[1] for v in self.vertices], dtype=np.float64)
        px = pts[:, 0].astype(np.float64)
        py = pts[:, 1].astype(np.float64)

        d2 = np.full(len(pts), np.inf)
        s = np.ones(len(pts), dtype=np.float64)

        j = n - 1
        for i in range(n):
            ex = vx[j] - vx[i]
            ey = vy[j] - vy[i]
            wx = px - vx[i]
            wy = py - vy[i]
            e_dot_e = ex * ex + ey * ey
            if e_dot_e < 1e-12:
                h = np.zeros(len(pts))
            else:
                h = np.clip((wx * ex + wy * ey) / e_dot_e, 0.0, 1.0)
            bx = wx - ex * h
            by = wy - ey * h
            d2 = np.minimum(d2, bx * bx + by * by)
            c1 = py >= vy[i]
            c2 = py < vy[j]
            c3 = ex * wy > ey * wx
            flip = (c1 & c2 & c3) | (~c1 & ~c2 & ~c3)
            s[flip] *= -1.0
            j = i

        return (s * np.sqrt(d2)).astype(np.float32)

    def bbox_2d(self):
        xs = [v[0] for v in self.vertices]
        ys = [v[1] for v in self.vertices]
        return min(xs), min(ys), max(xs), max(ys)

    def to_glsl_2d(self, ctx, pvar: str = "q") -> str:
        # Generate a unique inline GLSL helper with vertices baked in as constants.
        n = len(self.vertices)
        uid = f"{id(self) & 0xFFFFFFFF:08x}"
        func_name = f"sdf_poly{n}_{uid}"

        lines = [f"float {func_name}(vec2 p) {{"]
        lines.append(f"    float d = 1e18;")
        lines.append(f"    float s = 1.0;")
        # Emit vertex array as local constants
        for k, (vx, vy) in enumerate(self.vertices):
            lines.append(f"    vec2 v{k} = vec2({vx}, {vy});")
        j = n - 1
        for i in range(n):
            lines.append(f"    {{ vec2 e = v{j} - v{i}; vec2 w = p - v{i};")
            lines.append(f"      float h = clamp(dot(w,e)/dot(e,e), 0.0, 1.0);")
            lines.append(f"      vec2 b = w - e*h; d = min(d, dot(b,b));")
            lines.append(f"      bvec3 c = bvec3(p.y>=v{i}.y, p.y<v{j}.y, e.x*w.y>e.y*w.x);")
            lines.append(f"      if (all(c)||all(not(c))) s*=-1.0; }}")
            j = i
        lines.append(f"    return s*sqrt(d);")
        lines.append(f"}}")

        ctx.add_custom_helper(func_name, "\n".join(lines))
        return f"{func_name}({pvar})"
