---
name: DM SDF Primitive Implementation Pattern
description: Template and rules for creating new SDF primitive field classes in core/sdf/sdf/. Required reading before implementing any new SdfField subclass.
---

# DM SDF Primitive Implementation Pattern

Every SDF primitive lives in its own file in `core/sdf/sdf/` and follows this exact structure.

---

## File Template

```python
import numpy as np
import FreeCAD
import math
from core.sdf.sdf_field import SdfField

class Sdf{Name}Field(SdfField):
    """One-line description. Reference: sdName() in iquilezles.org/articles/distfunctions/"""
    def __init__(self, center: FreeCAD.Vector, param1: float, ...):
        self.center = center
        self.param1 = param1
        ...

    def evaluate(self, point: FreeCAD.Vector) -> float:
        """Scalar SDF at a single point."""
        p = point - self.center
        # ... formula ...
        return result

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        """Vectorized SDF over (N,3) float32 array. Returns (N,) float32."""
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        # ... numpy formula ...
        return result.astype(np.float32)

    def bounding_box(self):
        """Returns (min_corner, max_corner) as FreeCAD.Vector pair."""
        ...
        return (self.center - v, self.center + v)
```

---

## Rules

1. **Always subtract `self.center`** from the input point before applying the SDF formula — all IQ formulas assume the shape is at the origin.
2. **`evaluate_grid` must return `np.float32`** — always `.astype(np.float32)` at the end.
3. **`bounding_box` must be conservative** — it can be larger than the shape, never smaller. When in doubt, use a sphere bound: `center ± max_radius`.
4. **No gradient override needed** — `SdfField.gradient()` provides central-difference fallback automatically. Only override if you have an analytical formula (see `sphere.py`).
5. **Formulas use Y as the "up" axis** — consistent with FreeCAD's coordinate system.

---

## Numpy Vectorization Patterns

### Clamp
```python
np.clip(x, 0.0, 1.0)  # replaces GLSL clamp(x, 0.0, 1.0)
```

### sign
```python
np.sign(x)  # replaces GLSL sign(x)
```

### dot(vec, vec) per-row
```python
np.sum(a * b, axis=1)  # replaces dot(a,b) when a,b are (N,3)
```

### length per-row
```python
np.linalg.norm(v, axis=1)  # replaces length(v)
```

### cross-product per-row
```python
np.cross(a, b)  # works element-wise on (N,3) arrays
```

### Conditional (GLSL ternary)
```python
np.where(condition, a, b)  # replaces (cond) ? a : b
```

### min/max
```python
np.minimum(a, b)   # per-element min
np.maximum(a, b)   # per-element max
np.min(a, axis=1)  # min across components of each row
np.max(a, axis=1)  # max across components of each row
```

---

## Command Pattern (simple creation at origin)

Each primitive gets a thin command in a `commands/cmd_{group}.py` file:

```python
import FreeCAD
import FreeCADGui

class CommandDM{Name}:
    def GetResources(self):
        return {
            'Pixmap': 'Create{Name}',
            'MenuText': 'Create {Display Name}',
            'ToolTip': 'Create an SDF {Display Name} primitive.'
        }

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

    def Activated(self):
        from core.dm_object import create_dm_object
        from core.sdf.sdf.{module} import Sdf{Name}Field

        field = Sdf{Name}Field(
            center=FreeCAD.Vector(0, 0, 0),
            {default_params}
        )
        obj = create_dm_object(name='{Name}', shape_type='sdf')
        obj.Proxy.SdfField = field
        obj.touch()
        FreeCAD.activeDocument().recompute()

FreeCADGui.addCommand('DM_Create{Name}', CommandDM{Name}())
```

---

## Files to Read Before Editing

1. `core/sdf/sdf/sphere.py` — simplest complete example
2. `core/sdf/sdf/box.py` — example with local-space transform
3. `core/sdf/sdf/cylinder.py` — example with axis-aligned projection
4. `core/sdf/sdf/sdf_field.py` — base class (just `pass`)
5. `core/sdf/sdf_field.py` — abstract base with `evaluate_grid` fallback
