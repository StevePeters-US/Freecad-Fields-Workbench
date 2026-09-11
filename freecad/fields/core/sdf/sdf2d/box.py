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

    def __init__(self, hx: float, hy: float):
        self.hx = hx
        self.hy = hy

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
