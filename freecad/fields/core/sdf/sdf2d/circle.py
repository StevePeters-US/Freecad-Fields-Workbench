# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import math
import numpy as np
from .sdf2d_field import Sdf2dField


class Sdf2dCircle(Sdf2dField):
    """2D circle SDF: distance to circle of given radius centered at origin."""

    def __init__(self, radius: float):
        self.radius = radius

    def evaluate_2d(self, x: float, y: float) -> float:
        return math.sqrt(x * x + y * y) - self.radius

    def evaluate_2d_grid(self, pts: np.ndarray) -> np.ndarray:
        return (np.linalg.norm(pts, axis=1) - self.radius).astype(np.float32)

    def to_glsl_2d(self, ctx, pvar: str = "q") -> str:
        r = ctx.uniform("float", self.radius)
        return f"(length({pvar}) - {r})"
