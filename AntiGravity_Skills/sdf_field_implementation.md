---
name: Sdf Field Implementation Guide
description: How to implement a new Sdf field (SDF primitive) for the Direct Modeling workbench.
---

# Sdf Field Implementation Guide

When implementing a new SDF field (SDF primitive) for the Direct Modeling workbench, follow this checklist.

## Required Methods

Every field must inherit from `SdfField` (or `SdfField` once the refactor is complete) and implement:

1. **`evaluate(self, point: FreeCAD.Vector) -> float`** — Returns the signed distance at a single point. Negative = inside, positive = outside, zero = on surface.

2. **`evaluate_grid(self, points: np.ndarray) -> np.ndarray`** — Vectorized evaluation over an `(N, 3)` numpy array. This is the hot path for the marching cubes mesher. Use numpy operations exclusively — no Python loops.

3. **`bounding_box(self) -> (Vector, Vector)`** — Returns `(min_corner, max_corner)`. Must be **tight**; the mesher only samples within this box. A loose box wastes grid resolution on empty space.

## Optional Overrides

- **`gradient(self, point, h=1e-4) -> Vector`** — Analytical gradient. The base class provides numerical central differences as a fallback, but analytical gradients are faster and more accurate.

## File Location

- Place the file in `core/sdf/sdf/` (e.g., `core/sdf/sdf/torus.py`)
- Import from the base: `from core.sdf.sdf_field import SdfField`
- Use the class name pattern: `SDF<Shape>Field` (e.g., `SdfTorusField`)

## Integration Checklist

1. Create the field class with all required methods
2. Add a creator tool in `tools/primitive_tool.py` (subclass `PrimitiveCreatorBase`)
3. Add the import to `tools/primitive_tool.py`
4. Wire into the command in `commands/cmd_primitive.py`
5. Register the command in `InitGui.py`

## Template

```python
import numpy as np
import FreeCAD
from core.sdf.sdf_field import SdfField

class SdfNewField(SdfField):
    """Exact analytical SDF for <shape>."""
    def __init__(self, center: FreeCAD.Vector, param: float):
        self.center = center
        self.param = param

    def evaluate(self, point: FreeCAD.Vector) -> float:
        # Return signed distance
        raise NotImplementedError

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        # Vectorized SDF over (N, 3) points
        c = np.array([self.center.x, self.center.y, self.center.z])
        # ... numpy operations ...
        raise NotImplementedError

    def bounding_box(self):
        # Return tight (min_corner, max_corner)
        raise NotImplementedError
```

## Key Rules

- **Never use Python loops** in `evaluate_grid()` — the mesher calls this on every grid vertex (potentially millions of points)
- **Bounding box must be tight** — the mesher uniform grid spans exactly this box. Oversized = wasted resolution.
- **SDF convention**: negative inside, positive outside. The marching cubes mesher looks for the zero-crossing.
- **Test with the box tool first** — it's the simplest 3-state tool to verify your field renders correctly.
