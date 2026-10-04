# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import numpy as np


class Sdf2dField:
    """Abstract base for 2D SDF primitives evaluated in the XY (or r,z) plane.

    Not a subclass of `SdfField` (freecad/fields/core/sdf/sdf_field.py) and not
    independently renderable: a `Sdf2dField` has no `bounding_box()` and is
    never a `ComposerField` operand. It becomes visible only when a 3D
    `SdfField` wraps it as a profile and folds `evaluate_2d`/`to_glsl_2d` into
    its own `evaluate`/`to_glsl` -- see `SdfExtrusionField`
    (freecad/fields/core/sdf/sdf_extrusion.py) for the reference pattern, and
    AGENTS.md's "Two SDF Systems" section for the full picture.
    """

    def evaluate_2d(self, x: float, y: float) -> float:
        raise NotImplementedError

    def evaluate_2d_grid(self, pts: np.ndarray) -> np.ndarray:
        """pts: (N, 2) float32 → (N,) float32. Override for vectorized paths."""
        return np.array([self.evaluate_2d(float(p[0]), float(p[1])) for p in pts], dtype=np.float32)

    def to_glsl_2d(self, ctx, pvar: str = "q") -> str:
        """Return a GLSL float expression evaluating this 2D SDF at vec2 pvar."""
        raise NotImplementedError
