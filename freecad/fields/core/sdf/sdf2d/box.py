# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import numpy as np
from .sdf2d_field import Sdf2dField

_GLSL_SDF_BOX2D = """
float sdf_box2d(vec2 p, vec2 h) {
    vec2 d = abs(p) - h;
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
}
"""


class Sdf2dBox(Sdf2dField):
    """2D axis-aligned box SDF centered at origin with half-extents hx, hy."""

    def __init__(self, hx=None, hy=None, size=None):
        """hx/hy are HALF-extents; `size` is the full width/height pair.

        Keep those two the only spellings. A tuple accepted positionally as hx
        would read as half-extents at the call site and as a full size here, so
        Sdf2dBox(5.0) and Sdf2dBox((5.0, 5.0)) would differ by a factor of two.
        """
        if size is not None:
            if hx is not None or hy is not None:
                raise TypeError("Sdf2dBox takes either size=(w, h) or hx/hy half-extents, not both")
            self.hx = float(size[0]) * 0.5
            self.hy = float(size[1]) * 0.5
            return
        if hx is None:
            raise TypeError("Sdf2dBox requires size=(w, h), hx=..., or hx/hy half-extents")
        if isinstance(hx, (tuple, list)):
            raise TypeError("Sdf2dBox: pass a pair as size=(width, height); hx/hy are scalar half-extents")
        self.hx = float(hx)
        self.hy = float(hy) if hy is not None else float(hx)

    def bbox_2d(self):
        return (-self.hx, -self.hy, self.hx, self.hy)

    def evaluate_2d(self, x: float, y: float) -> float:
        dx = abs(x) - self.hx
        dy = abs(y) - self.hy
        return (max(dx, 0.0) ** 2 + max(dy, 0.0) ** 2) ** 0.5 + min(max(dx, dy), 0.0)

    def evaluate_2d_grid(self, pts: np.ndarray) -> np.ndarray:
        dx = np.abs(pts[:, 0]) - self.hx
        dy = np.abs(pts[:, 1]) - self.hy
        outer = np.sqrt(np.maximum(dx, 0.0) ** 2 + np.maximum(dy, 0.0) ** 2)
        inner = np.minimum(np.maximum(dx, dy), 0.0)
        return (outer + inner).astype(np.float32)

    def to_glsl_2d(self, ctx, pvar: str = "q") -> str:
        ctx.add_custom_helper("sdf_box2d", _GLSL_SDF_BOX2D)
        hx = ctx.uniform("float", self.hx)
        hy = ctx.uniform("float", self.hy)
        return f"sdf_box2d({pvar}, vec2({hx}, {hy}))"
