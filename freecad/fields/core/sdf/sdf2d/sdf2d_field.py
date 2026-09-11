# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import numpy as np


class Sdf2dField:
    """Abstract base for 2D SDF primitives evaluated in the XY (or r,z) plane."""

    def evaluate_2d(self, x: float, y: float) -> float:
        raise NotImplementedError

    def evaluate_2d_grid(self, pts: np.ndarray) -> np.ndarray:
        """pts: (N, 2) float32 → (N,) float32. Override for vectorized paths."""
        return np.array([self.evaluate_2d(float(p[0]), float(p[1])) for p in pts], dtype=np.float32)

    def to_glsl_2d(self, ctx, pvar: str = "q") -> str:
        """Return a GLSL float expression evaluating this 2D SDF at vec2 pvar."""
        raise NotImplementedError
