# Direct Modeling Workbench — SDF Operations Task List

> Tasks are ordered by dependency. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) to create or modify.

---

## Background

The workbench has three boolean operations (Union, Subtract, Intersect) in `sdf_composer.py` and
matching commands in `cmd_boolean.py`. This task list adds the remaining SDF operations from the
IQuilezles reference article, organized into four operation families:

1. **Smooth Booleans & XOR** — blend-blended union/subtract/intersect, exclusive-or
2. **Field Modifiers** — per-field shape transforms: round, onion-skin, elongate
3. **Spatial Operations** — symmetry mirroring and infinite/limited repetition
4. **Deformations** — domain-space twisting, bending, displacement

Each family gets its own field class file, command file, icon set, and a toolbar dropdown group.
After all tiers complete, the "DM - Operations" toolbar will show dropdown groups for boolean ops,
modifiers, and spatial/deform ops, keeping the bar uncluttered.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `ComposerField` | `core/sdf/sdf_composer.py:27` | Base for binary operations |
| `UnionField` | `core/sdf/sdf_composer.py:35` | Existing union (min) |
| `SdfField` | `core/sdf/sdf_field.py:1` | Abstract base for all fields |
| `create_dm_object` | `core/dm_object.py` | Creates a DM sdf object |
| `cmd_boolean.py` | `commands/cmd_boolean.py` | Existing boolean commands |
| `appendToolbar` | `InitGui.py:85` | Toolbar registration |

---

## Agent Skills

| Skill | Purpose |
|-------|---------|
| `dm_sdf_primitive_pattern` | Command structure and SdfField conventions |
| `dm_command_group` | Dropdown toolbar button groups |
| `dm_icons` | SVG icon style (64×64, #e0e0e0, #000 stroke) |

---

## Tier 1 — Smooth Booleans & XOR

Extend `sdf_composer.py` with smooth blend operations. The smooth operations take a `k`
smoothing factor: larger k = more blending (k=0 degrades to the sharp boolean).

### O-001: Add smooth boolean and XOR field classes

**File:** `core/sdf/sdf_composer.py` — append after `SubtractionField` class (end of file)

**What:** Four new field classes: `SmoothUnionField`, `SmoothSubtractionField`,
`SmoothIntersectionField`, `XorField`. The smooth versions use the polynomial smooth-min from IQ.

```python
class SmoothUnionField(ComposerField):
    """Smooth union: blends A and B with smoothing radius k. Ref: opSmoothUnion() iq."""
    def __init__(self, field_a: SdfField, field_b: SdfField, k: float):
        super().__init__(field_a, field_b)
        self.k = k

    def _smin(self, a, b):
        h = max(self.k - abs(a - b), 0.0) / self.k
        return min(a, b) - h * h * self.k * 0.25

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return self._smin(self.a.evaluate(point), self.b.evaluate(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        da = self.a.evaluate_grid(points).astype(np.float64)
        db = self.b.evaluate_grid(points).astype(np.float64)
        h = np.maximum(self.k - np.abs(da - db), 0.0) / self.k
        return (np.minimum(da, db) - h * h * self.k * 0.25).astype(np.float32)

    def bounding_box(self):
        return _bbox_union(self.a.bounding_box(), self.b.bounding_box())


class SmoothSubtractionField(ComposerField):
    """Smooth subtraction: A minus B with smooth blend. Ref: opSmoothSubtraction() iq."""
    def __init__(self, field_a: SdfField, field_b: SdfField, k: float):
        super().__init__(field_a, field_b)
        self.k = k

    def _smax_neg(self, a, b):
        """smax(a, -b) with smooth blend."""
        nb = -b
        h = max(self.k - abs(a - nb), 0.0) / self.k
        return max(a, nb) + h * h * self.k * 0.25

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return self._smax_neg(self.a.evaluate(point), self.b.evaluate(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        da = self.a.evaluate_grid(points).astype(np.float64)
        nb = -self.b.evaluate_grid(points).astype(np.float64)
        h = np.maximum(self.k - np.abs(da - nb), 0.0) / self.k
        return (np.maximum(da, nb) + h * h * self.k * 0.25).astype(np.float32)

    def bounding_box(self):
        return self.a.bounding_box()


class SmoothIntersectionField(ComposerField):
    """Smooth intersection: blend of max(A,B). Ref: opSmoothIntersection() iq."""
    def __init__(self, field_a: SdfField, field_b: SdfField, k: float):
        super().__init__(field_a, field_b)
        self.k = k

    def _smax(self, a, b):
        h = max(self.k - abs(a - b), 0.0) / self.k
        return max(a, b) + h * h * self.k * 0.25

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return self._smax(self.a.evaluate(point), self.b.evaluate(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        da = self.a.evaluate_grid(points).astype(np.float64)
        db = self.b.evaluate_grid(points).astype(np.float64)
        h = np.maximum(self.k - np.abs(da - db), 0.0) / self.k
        return (np.maximum(da, db) + h * h * self.k * 0.25).astype(np.float32)

    def bounding_box(self):
        return _bbox_intersection(self.a.bounding_box(), self.b.bounding_box())


class XorField(ComposerField):
    """XOR: surface where exactly one of A or B is inside. Ref: opXor() iq."""
    def evaluate(self, point: FreeCAD.Vector) -> float:
        a = self.a.evaluate(point)
        b = self.b.evaluate(point)
        return max(min(a, b), -max(a, b))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        da = self.a.evaluate_grid(points)
        db = self.b.evaluate_grid(points)
        return np.maximum(np.minimum(da, db), -np.maximum(da, db)).astype(np.float32)

    def bounding_box(self):
        return _bbox_union(self.a.bounding_box(), self.b.bounding_box())
```

---

### O-002: Icons — Smooth Booleans & XOR

**Files:** Create in `Resources/icons/`

**`Resources/icons/SmoothAdd.svg`** — two circles blending together (orange stroke = modifier op)
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <circle cx="22" cy="32" r="16" style="fill:#e0e0e0;stroke:#ffaa00;stroke-width:2"/>
  <circle cx="42" cy="32" r="16" style="fill:#e0e0e0;stroke:#ffaa00;stroke-width:2"/>
  <path d="M 30 20 Q 32 32 30 44" style="fill:none;stroke:#ffaa00;stroke-width:3"/>
</svg>
```

**`Resources/icons/SmoothSubtract.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <path d="M 22 16 A 16 16 0 1 0 22 48 Q 36 40 36 32 Q 36 24 22 16 Z" style="fill:#e0e0e0;stroke:#ffaa00;stroke-width:2"/>
  <circle cx="42" cy="32" r="16" style="fill:none;stroke:#ffaa00;stroke-width:2;stroke-dasharray:4,4"/>
</svg>
```

**`Resources/icons/SmoothIntersection.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <circle cx="22" cy="32" r="16" style="fill:none;stroke:#ffaa00;stroke-width:2;stroke-dasharray:4,4"/>
  <circle cx="42" cy="32" r="16" style="fill:none;stroke:#ffaa00;stroke-width:2;stroke-dasharray:4,4"/>
  <path d="M 32 18 Q 42 24 42 32 Q 42 40 32 46 Q 22 40 22 32 Q 22 24 32 18 Z" style="fill:#e0e0e0;stroke:#ffaa00;stroke-width:2"/>
</svg>
```

**`Resources/icons/MakeXor.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <circle cx="22" cy="32" r="16" style="fill:#e0e0e0;stroke:#ffaa00;stroke-width:2"/>
  <circle cx="42" cy="32" r="16" style="fill:#e0e0e0;stroke:#ffaa00;stroke-width:2"/>
  <path d="M 28 20 Q 36 26 36 32 Q 36 38 28 44 Q 20 38 20 32 Q 20 26 28 20 Z" style="fill:#ffffff;stroke:#ffaa00;stroke-width:2"/>
</svg>
```

---

### O-003: Commands — Smooth Booleans & XOR

**File:** `commands/cmd_smooth_boolean.py` — create new file

**What:** Four commands that apply smooth boolean operations to the two selected SDF objects.
Follows the same selection-based pattern as `cmd_boolean.py`.

```python
import FreeCAD
import FreeCADGui
from core import dm_logger


class CommandDMSmoothBoolean:
    _ICONS = {
        'SmoothUnion':        'SmoothAdd',
        'SmoothSubtraction':  'SmoothSubtract',
        'SmoothIntersection': 'SmoothIntersection',
        'Xor':                'MakeXor',
    }
    _CLASSES = {
        'SmoothUnion':        'SmoothUnionField',
        'SmoothSubtraction':  'SmoothSubtractionField',
        'SmoothIntersection': 'SmoothIntersectionField',
        'Xor':                'XorField',
    }

    def __init__(self, operation: str, k: float = 5.0):
        self.operation = operation
        self.k = k  # smoothing radius (ignored for Xor)

    def GetResources(self):
        label = self.operation.replace('Smooth', 'Smooth ')
        return {'Pixmap': self._ICONS[self.operation],
                'MenuText': label,
                'ToolTip': f'Apply {label} to two selected SDF objects (k={self.k}).'}

    def IsActive(self): return FreeCAD.activeDocument() is not None

    def Activated(self):
        from core.sdf.sdf_composer import (SmoothUnionField, SmoothSubtractionField,
                                              SmoothIntersectionField, XorField)
        from core.dm_object import create_dm_object
        _MAP = {'SmoothUnion': SmoothUnionField, 'SmoothSubtraction': SmoothSubtractionField,
                'SmoothIntersection': SmoothIntersectionField, 'Xor': XorField}
        sel = FreeCADGui.Selection.getSelection()
        if len(sel) < 2:
            dm_logger.error(f'DM_{self.operation}: select at least two SDF objects.')
            return
        fields = []
        for obj in sel:
            proxy = getattr(obj, 'Proxy', None)
            field = getattr(proxy, 'SdfField', None) if proxy else None
            if field is None:
                dm_logger.error(f'{obj.Label} has no SdfField — must be an SDF object.')
                return
            fields.append(field)
        cls = _MAP[self.operation]
        result_field = fields[0]
        for i in range(1, len(fields)):
            if self.operation == 'Xor':
                result_field = cls(result_field, fields[i])
            else:
                result_field = cls(result_field, fields[i], self.k)
        obj_out = create_dm_object(name=self.operation, shape_type='sdf')
        obj_out.Proxy.SdfField = result_field
        for obj in sel:
            if hasattr(obj, 'ViewObject') and obj.ViewObject:
                obj.ViewObject.Visibility = False
        obj_out.touch()
        FreeCAD.activeDocument().recompute()
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(obj_out)


FreeCADGui.addCommand('DM_SmoothUnion',        CommandDMSmoothBoolean('SmoothUnion'))
FreeCADGui.addCommand('DM_SmoothSubtraction',  CommandDMSmoothBoolean('SmoothSubtraction'))
FreeCADGui.addCommand('DM_SmoothIntersection', CommandDMSmoothBoolean('SmoothIntersection'))
FreeCADGui.addCommand('DM_Xor',                CommandDMSmoothBoolean('Xor'))
```

**Depends on:** O-001, O-002

---

### O-004: Command groups and toolbar update for booleans

**File:** `commands/cmd_boolean_groups.py` — create new file; also edit `InitGui.py`

**What:** Two dropdown groups — one for sharp booleans, one for smooth booleans — and update
the toolbar to use them. Also import new command files in `Initialize()`.

**`commands/cmd_boolean_groups.py`:**
```python
import FreeCAD
import FreeCADGui


class DMSharpBooleanGroup:
    def GetCommands(self):
        return ('DM_Add', 'DM_Subtract', 'DM_Intersection', 'DM_Xor')
    def GetDefaultCommand(self): return 0
    def GetResources(self):
        return {'MenuText': 'Boolean Ops', 'ToolTip': 'Sharp boolean operations on SDF objects'}
    def IsActive(self): return FreeCAD.activeDocument() is not None


class DMSmoothBooleanGroup:
    def GetCommands(self):
        return ('DM_SmoothUnion', 'DM_SmoothSubtraction', 'DM_SmoothIntersection')
    def GetDefaultCommand(self): return 0
    def GetResources(self):
        return {'MenuText': 'Smooth Booleans', 'ToolTip': 'Smooth blend boolean operations'}
    def IsActive(self): return FreeCAD.activeDocument() is not None


FreeCADGui.addCommand('DM_SharpBooleanGroup',  DMSharpBooleanGroup())
FreeCADGui.addCommand('DM_SmoothBooleanGroup', DMSmoothBooleanGroup())
```

**Edit `InitGui.py` — add imports after line 66 (`import commands.cmd_sdf_slice`):**
```python
import commands.cmd_smooth_boolean
import commands.cmd_boolean_groups
import commands.cmd_modifier
import commands.cmd_spatial
import commands.cmd_deform
```

**Edit `InitGui.py` — replace `appendToolbar("DM - Operations", [...])` (line 85) with:**
```python
self.appendToolbar("DM - Operations", [
    'DM_Translate',
    'DM_SharpBooleanGroup',
    'DM_SmoothBooleanGroup',
    'DM_ModifierGroup',
    'DM_SpatialGroup',
    'DM_DeformGroup',
    'DM_SDFSlice',
    'DM_SDFToShape',
    'DM_OpenSketcher',
])
```

**Depends on:** O-003

---

## Tier 2 — Field Modifiers

Unary operations that wrap a single field and modify its shape. Each takes one child field.

### O-005: Create `sdf_modifier.py` with Round, Onion, Elongate

**File:** `core/sdf/sdf_modifier.py` — create new file

**What:** Three modifier fields that each wrap one `SdfField` child.
- `RoundModifierField`: inflates the surface outward by `radius` (opRound)
- `OnionModifierField`: creates a shell of `thickness` around the surface (opOnion)
- `ElongateModifierField`: stretches the shape along each axis by `amounts` (opElongate)

```python
import numpy as np
import FreeCAD
from core.sdf.sdf_field import SdfField


class RoundModifierField(SdfField):
    """Inflates a field outward by radius. Ref: opRound() iq."""
    def __init__(self, child: SdfField, radius: float):
        self.child = child
        self.radius = radius

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return self.child.evaluate(point) - self.radius

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return (self.child.evaluate_grid(points) - self.radius).astype(np.float32)

    def bounding_box(self):
        mn, mx = self.child.bounding_box()
        r = FreeCAD.Vector(self.radius, self.radius, self.radius)
        return (mn - r, mx + r)


class OnionModifierField(SdfField):
    """Creates a shell of given thickness around a field surface. Ref: opOnion() iq."""
    def __init__(self, child: SdfField, thickness: float):
        self.child = child
        self.thickness = thickness

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return abs(self.child.evaluate(point)) - self.thickness

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return (np.abs(self.child.evaluate_grid(points)) - self.thickness).astype(np.float32)

    def bounding_box(self):
        mn, mx = self.child.bounding_box()
        t = FreeCAD.Vector(self.thickness, self.thickness, self.thickness)
        return (mn - t, mx + t)


class ElongateModifierField(SdfField):
    """Stretches a field along each axis. amounts = (hx, hy, hz) half-extents to add. Ref: opElongate() iq."""
    def __init__(self, child: SdfField, amounts: FreeCAD.Vector):
        self.child = child
        self.amounts = amounts  # hx, hy, hz

    def _remap(self, point: FreeCAD.Vector) -> FreeCAD.Vector:
        h = self.amounts
        return FreeCAD.Vector(
            point.x - max(-h.x, min(point.x, h.x)),
            point.y - max(-h.y, min(point.y, h.y)),
            point.z - max(-h.z, min(point.z, h.z)),
        )

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return self.child.evaluate(self._remap(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        h = np.array([self.amounts.x, self.amounts.y, self.amounts.z])
        remapped = points - np.clip(points, -h, h)
        return self.child.evaluate_grid(remapped).astype(np.float32)

    def bounding_box(self):
        mn, mx = self.child.bounding_box()
        h = self.amounts
        return (mn - h, mx + h)
```

---

### O-006: Icons — Field Modifiers

**Files:** Create in `Resources/icons/`. Orange stroke = modifier op.

**`Resources/icons/ModRound.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <rect x="14" y="14" width="36" height="36" style="fill:none;stroke:#000000;stroke-width:2;stroke-dasharray:4,4"/>
  <rect x="10" y="10" width="44" height="44" rx="10" ry="10" style="fill:#e0e0e0;stroke:#ffaa00;stroke-width:2"/>
</svg>
```

**`Resources/icons/ModOnion.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <circle cx="32" cy="32" r="22" style="fill:none;stroke:#ffaa00;stroke-width:5"/>
  <circle cx="32" cy="32" r="14" style="fill:none;stroke:#ffaa00;stroke-width:2;stroke-dasharray:4,4"/>
</svg>
```

**`Resources/icons/ModElongate.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <ellipse cx="32" cy="32" rx="26" ry="14" style="fill:#e0e0e0;stroke:#ffaa00;stroke-width:2"/>
  <line x1="6" y1="32" x2="2" y2="32" style="stroke:#ffaa00;stroke-width:2"/>
  <line x1="58" y1="32" x2="62" y2="32" style="stroke:#ffaa00;stroke-width:2"/>
</svg>
```

---

### O-007: Commands — Field Modifiers

**File:** `commands/cmd_modifier.py` — create new file

**What:** Three commands (Round, Onion, Elongate) that each wrap the selected SDF object.
Also includes the `DMModifierGroup` dropdown class.

```python
import FreeCAD
import FreeCADGui
from core import dm_logger


def _get_single_sdf_field(label):
    sel = FreeCADGui.Selection.getSelection()
    if len(sel) != 1:
        dm_logger.error(f'{label}: select exactly one SDF object.')
        return None, None
    obj = sel[0]
    proxy = getattr(obj, 'Proxy', None)
    field = getattr(proxy, 'SdfField', None) if proxy else None
    if field is None:
        dm_logger.error(f'{label}: selected object has no SdfField.')
        return None, None
    return obj, field


class CommandDMRound:
    def GetResources(self):
        return {'Pixmap': 'ModRound', 'MenuText': 'Round',
                'ToolTip': 'Inflate the selected SDF surface outward by a radius.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.sdf.sdf_modifier import RoundModifierField
        src_obj, field = _get_single_sdf_field('Round')
        if field is None: return
        new_field = RoundModifierField(field, radius=3.0)
        obj = create_dm_object(name='Round', shape_type='sdf')
        obj.Proxy.SdfField = new_field
        src_obj.ViewObject.Visibility = False
        obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMOnion:
    def GetResources(self):
        return {'Pixmap': 'ModOnion', 'MenuText': 'Onion',
                'ToolTip': 'Create a hollow shell around the selected SDF surface.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.sdf.sdf_modifier import OnionModifierField
        src_obj, field = _get_single_sdf_field('Onion')
        if field is None: return
        new_field = OnionModifierField(field, thickness=2.0)
        obj = create_dm_object(name='Onion', shape_type='sdf')
        obj.Proxy.SdfField = new_field
        src_obj.ViewObject.Visibility = False
        obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMElongate:
    def GetResources(self):
        return {'Pixmap': 'ModElongate', 'MenuText': 'Elongate',
                'ToolTip': 'Stretch the selected SDF shape along each axis.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.sdf.sdf_modifier import ElongateModifierField
        src_obj, field = _get_single_sdf_field('Elongate')
        if field is None: return
        new_field = ElongateModifierField(field, FreeCAD.Vector(10.0, 0.0, 0.0))
        obj = create_dm_object(name='Elongate', shape_type='sdf')
        obj.Proxy.SdfField = new_field
        src_obj.ViewObject.Visibility = False
        obj.touch();  FreeCAD.activeDocument().recompute()


class DMModifierGroup:
    def GetCommands(self): return ('DM_Round', 'DM_Onion', 'DM_Elongate')
    def GetDefaultCommand(self): return 0
    def GetResources(self):
        return {'MenuText': 'Modifiers', 'ToolTip': 'Shape modifier operations'}
    def IsActive(self): return FreeCAD.activeDocument() is not None


FreeCADGui.addCommand('DM_Round',         CommandDMRound())
FreeCADGui.addCommand('DM_Onion',         CommandDMOnion())
FreeCADGui.addCommand('DM_Elongate',      CommandDMElongate())
FreeCADGui.addCommand('DM_ModifierGroup', DMModifierGroup())
```

**Depends on:** O-005, O-006

---

## Tier 3 — Spatial Operations

### O-008: Create `sdf_spatial.py` with symmetry and repetition fields

**File:** `core/sdf/sdf_spatial.py` — create new file

**What:** Four spatial operation fields.
- `SymmetryXField`: mirrors the child across the YZ plane (X=0)
- `SymmetryXZField`: mirrors across both X=0 and Z=0 (four-fold symmetry in XZ)
- `RepetitionField`: tiles the child field with period `spacing` (infinite tiling)
- `LimitedRepetitionField`: tiles within ±`limit` repetitions, then clips

```python
import numpy as np
import FreeCAD
from core.sdf.sdf_field import SdfField


class SymmetryXField(SdfField):
    """Mirror the child across the X=0 plane. Ref: opSymX() iq."""
    def __init__(self, child: SdfField):
        self.child = child

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return self.child.evaluate(FreeCAD.Vector(abs(point.x), point.y, point.z))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        p = points.copy();  p[:, 0] = np.abs(p[:, 0])
        return self.child.evaluate_grid(p).astype(np.float32)

    def bounding_box(self):
        mn, mx = self.child.bounding_box()
        ext = max(abs(mn.x), abs(mx.x))
        return (FreeCAD.Vector(-ext, mn.y, mn.z), FreeCAD.Vector(ext, mx.y, mx.z))


class SymmetryXZField(SdfField):
    """Mirror across both X=0 and Z=0 (4-fold XZ symmetry). Ref: opSymXZ() iq."""
    def __init__(self, child: SdfField):
        self.child = child

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return self.child.evaluate(FreeCAD.Vector(abs(point.x), point.y, abs(point.z)))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        p = points.copy()
        p[:, 0] = np.abs(p[:, 0]);  p[:, 2] = np.abs(p[:, 2])
        return self.child.evaluate_grid(p).astype(np.float32)

    def bounding_box(self):
        mn, mx = self.child.bounding_box()
        ex = max(abs(mn.x), abs(mx.x));  ez = max(abs(mn.z), abs(mx.z))
        return (FreeCAD.Vector(-ex, mn.y, -ez), FreeCAD.Vector(ex, mx.y, ez))


class RepetitionField(SdfField):
    """Infinite tiling of child with period spacing (FreeCAD.Vector). Ref: opRepetition() iq."""
    def __init__(self, child: SdfField, spacing: FreeCAD.Vector):
        self.child = child
        self.spacing = spacing

    def _remap(self, p: np.ndarray) -> np.ndarray:
        s = np.array([self.spacing.x, self.spacing.y, self.spacing.z])
        return p - s * np.round(p / s)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        s = self.spacing
        import math
        px = point.x - s.x * round(point.x / s.x)
        py = point.y - s.y * round(point.y / s.y)
        pz = point.z - s.z * round(point.z / s.z)
        return self.child.evaluate(FreeCAD.Vector(px, py, pz))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return self.child.evaluate_grid(self._remap(points)).astype(np.float32)

    def bounding_box(self):
        # Infinite tiling — return a very large box
        INF = 1e6
        return (FreeCAD.Vector(-INF, -INF, -INF), FreeCAD.Vector(INF, INF, INF))


class LimitedRepetitionField(SdfField):
    """Tiling of child clamped to ±limit repetitions. Ref: opLimitedRepetition() iq."""
    def __init__(self, child: SdfField, spacing: float, limit: FreeCAD.Vector):
        self.child = child
        self.spacing = spacing
        self.limit = limit  # (lx, ly, lz) max repetition count per axis

    def _remap(self, points: np.ndarray) -> np.ndarray:
        s = self.spacing
        lim = np.array([self.limit.x, self.limit.y, self.limit.z])
        return points - s * np.clip(np.round(points / s), -lim, lim)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        import math
        s = self.spacing
        lim = self.limit
        px = point.x - s * max(-lim.x, min(round(point.x / s), lim.x))
        py = point.y - s * max(-lim.y, min(round(point.y / s), lim.y))
        pz = point.z - s * max(-lim.z, min(round(point.z / s), lim.z))
        return self.child.evaluate(FreeCAD.Vector(px, py, pz))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return self.child.evaluate_grid(self._remap(points)).astype(np.float32)

    def bounding_box(self):
        mn, mx = self.child.bounding_box()
        s = self.spacing
        lim = self.limit
        ext_x = (lim.x + 0.5) * s;  ext_y = (lim.y + 0.5) * s;  ext_z = (lim.z + 0.5) * s
        sz = FreeCAD.Vector((mx.x - mn.x) / 2 + ext_x,
                            (mx.y - mn.y) / 2 + ext_y,
                            (mx.z - mn.z) / 2 + ext_z)
        return (-sz, sz)
```

---

### O-009: Icons — Spatial Operations

**Files:** Create in `Resources/icons/`

**`Resources/icons/OpSymmetryX.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <line x1="32" y1="4" x2="32" y2="60" style="stroke:#ffaa00;stroke-width:2;stroke-dasharray:6,3"/>
  <polygon points="10,20 28,20 28,44 10,44" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <polygon points="54,20 36,20 36,44 54,44" style="fill:#e0e0e0;stroke:#000000;stroke-width:1;opacity:0.5"/>
</svg>
```

**`Resources/icons/OpSymmetryXZ.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <line x1="32" y1="4" x2="32" y2="60" style="stroke:#ffaa00;stroke-width:2;stroke-dasharray:6,3"/>
  <line x1="4" y1="32" x2="60" y2="32" style="stroke:#ffaa00;stroke-width:2;stroke-dasharray:6,3"/>
  <rect x="10" y="10" width="18" height="18" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <rect x="36" y="10" width="18" height="18" style="fill:#e0e0e0;stroke:#000000;stroke-width:1;opacity:0.5"/>
  <rect x="10" y="36" width="18" height="18" style="fill:#e0e0e0;stroke:#000000;stroke-width:1;opacity:0.5"/>
  <rect x="36" y="36" width="18" height="18" style="fill:#e0e0e0;stroke:#000000;stroke-width:1;opacity:0.5"/>
</svg>
```

**`Resources/icons/OpRepetition.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <circle cx="12" cy="32" r="7" style="fill:#e0e0e0;stroke:#000000;stroke-width:1;opacity:0.5"/>
  <circle cx="32" cy="32" r="9" style="fill:#e0e0e0;stroke:#ffaa00;stroke-width:2"/>
  <circle cx="52" cy="32" r="7" style="fill:#e0e0e0;stroke:#000000;stroke-width:1;opacity:0.5"/>
  <circle cx="32" cy="12" r="7" style="fill:#e0e0e0;stroke:#000000;stroke-width:1;opacity:0.5"/>
  <circle cx="32" cy="52" r="7" style="fill:#e0e0e0;stroke:#000000;stroke-width:1;opacity:0.5"/>
</svg>
```

**`Resources/icons/OpLimitedRepetition.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <circle cx="16" cy="32" r="8" style="fill:#e0e0e0;stroke:#000000;stroke-width:1"/>
  <circle cx="32" cy="32" r="8" style="fill:#e0e0e0;stroke:#ffaa00;stroke-width:2"/>
  <circle cx="48" cy="32" r="8" style="fill:#e0e0e0;stroke:#000000;stroke-width:1"/>
  <line x1="4" y1="10" x2="4" y2="54" style="stroke:#ffaa00;stroke-width:2"/>
  <line x1="60" y1="10" x2="60" y2="54" style="stroke:#ffaa00;stroke-width:2"/>
</svg>
```

---

### O-010: Commands — Spatial Operations

**File:** `commands/cmd_spatial.py` — create new file

```python
import FreeCAD
import FreeCADGui
from core import dm_logger


def _get_single_field(label):
    sel = FreeCADGui.Selection.getSelection()
    if len(sel) != 1:
        dm_logger.error(f'{label}: select exactly one SDF object.'); return None, None
    obj = sel[0]
    proxy = getattr(obj, 'Proxy', None)
    field = getattr(proxy, 'SdfField', None) if proxy else None
    if field is None:
        dm_logger.error(f'{label}: selected object has no SdfField.'); return None, None
    return obj, field


class CommandDMSymmetryX:
    def GetResources(self):
        return {'Pixmap': 'OpSymmetryX', 'MenuText': 'Symmetry X',
                'ToolTip': 'Mirror the selected SDF across the YZ plane (X=0).'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.sdf.sdf_spatial import SymmetryXField
        src, field = _get_single_field('SymmetryX')
        if field is None: return
        obj = create_dm_object(name='SymmetryX', shape_type='sdf')
        obj.Proxy.SdfField = SymmetryXField(field)
        src.ViewObject.Visibility = False
        obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMSymmetryXZ:
    def GetResources(self):
        return {'Pixmap': 'OpSymmetryXZ', 'MenuText': 'Symmetry XZ',
                'ToolTip': 'Mirror the selected SDF across both X=0 and Z=0 (4-fold).'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.sdf.sdf_spatial import SymmetryXZField
        src, field = _get_single_field('SymmetryXZ')
        if field is None: return
        obj = create_dm_object(name='SymmetryXZ', shape_type='sdf')
        obj.Proxy.SdfField = SymmetryXZField(field)
        src.ViewObject.Visibility = False
        obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMRepetition:
    def GetResources(self):
        return {'Pixmap': 'OpRepetition', 'MenuText': 'Repeat (Infinite)',
                'ToolTip': 'Tile the selected SDF infinitely with a fixed spacing.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.sdf.sdf_spatial import RepetitionField
        src, field = _get_single_field('Repetition')
        if field is None: return
        obj = create_dm_object(name='Repetition', shape_type='sdf')
        obj.Proxy.SdfField = RepetitionField(field, FreeCAD.Vector(50.0, 50.0, 50.0))
        src.ViewObject.Visibility = False
        obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMLimitedRepetition:
    def GetResources(self):
        return {'Pixmap': 'OpLimitedRepetition', 'MenuText': 'Repeat (Limited)',
                'ToolTip': 'Tile the selected SDF within a fixed count per axis.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.sdf.sdf_spatial import LimitedRepetitionField
        src, field = _get_single_field('LimitedRepetition')
        if field is None: return
        obj = create_dm_object(name='LimitedRepeat', shape_type='sdf')
        obj.Proxy.SdfField = LimitedRepetitionField(
            field, spacing=50.0, limit=FreeCAD.Vector(2, 2, 2))
        src.ViewObject.Visibility = False
        obj.touch();  FreeCAD.activeDocument().recompute()


class DMSpatialGroup:
    def GetCommands(self):
        return ('DM_SymmetryX', 'DM_SymmetryXZ', 'DM_Repetition', 'DM_LimitedRepetition')
    def GetDefaultCommand(self): return 0
    def GetResources(self):
        return {'MenuText': 'Spatial Ops', 'ToolTip': 'Symmetry and repetition operations'}
    def IsActive(self): return FreeCAD.activeDocument() is not None


FreeCADGui.addCommand('DM_SymmetryX',          CommandDMSymmetryX())
FreeCADGui.addCommand('DM_SymmetryXZ',         CommandDMSymmetryXZ())
FreeCADGui.addCommand('DM_Repetition',         CommandDMRepetition())
FreeCADGui.addCommand('DM_LimitedRepetition',  CommandDMLimitedRepetition())
FreeCADGui.addCommand('DM_SpatialGroup',       DMSpatialGroup())
```

**Depends on:** O-008, O-009

---

## Tier 4 — Deformations

Domain-space deformations. These wrap a field and remap evaluation points before passing to
the child. **Warning:** these do not preserve exact SDF distances — results are approximate.

### O-011: Create `sdf_deform.py` with Twist, Bend, Displace

**File:** `core/sdf/sdf_deform.py` — create new file

**What:** Three deformation fields.
- `TwistField`: twists the child around the Y axis by `twist_rate` (radians per unit height)
- `BendField`: bends the child around the Z axis by `bend_rate` (radians per unit width)
- `DisplaceField`: adds a displacement function to the SDF (requires a Python callable)

```python
import numpy as np
import FreeCAD
import math
from core.sdf.sdf_field import SdfField


class TwistField(SdfField):
    """Twist around Y axis. twist_rate = radians per unit of Y. Ref: opTwist() iq."""
    def __init__(self, child: SdfField, twist_rate: float):
        self.child = child
        self.twist_rate = twist_rate

    def _remap_pt(self, point: FreeCAD.Vector) -> FreeCAD.Vector:
        angle = self.twist_rate * point.y
        c, s = math.cos(angle), math.sin(angle)
        return FreeCAD.Vector(c * point.x - s * point.z,
                              point.y,
                              s * point.x + c * point.z)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return self.child.evaluate(self._remap_pt(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        angles = self.twist_rate * points[:, 1]
        c = np.cos(angles);  s = np.sin(angles)
        remapped = np.stack([c * points[:, 0] - s * points[:, 2],
                             points[:, 1],
                             s * points[:, 0] + c * points[:, 2]], axis=1)
        return self.child.evaluate_grid(remapped).astype(np.float32)

    def bounding_box(self):
        mn, mx = self.child.bounding_box()
        R = max(abs(mn.x), abs(mx.x), abs(mn.z), abs(mx.z)) * 1.5
        return (FreeCAD.Vector(-R, mn.y, -R), FreeCAD.Vector(R, mx.y, R))


class BendField(SdfField):
    """Bend around Z axis. bend_rate = radians per unit of X. Ref: opCheapBend() iq."""
    def __init__(self, child: SdfField, bend_rate: float):
        self.child = child
        self.bend_rate = bend_rate

    def _remap_pt(self, point: FreeCAD.Vector) -> FreeCAD.Vector:
        angle = self.bend_rate * point.x
        c, s = math.cos(angle), math.sin(angle)
        return FreeCAD.Vector(c * point.x - s * point.y,
                              s * point.x + c * point.y,
                              point.z)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return self.child.evaluate(self._remap_pt(point))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        angles = self.bend_rate * points[:, 0]
        c = np.cos(angles);  s = np.sin(angles)
        remapped = np.stack([c * points[:, 0] - s * points[:, 1],
                             s * points[:, 0] + c * points[:, 1],
                             points[:, 2]], axis=1)
        return self.child.evaluate_grid(remapped).astype(np.float32)

    def bounding_box(self):
        mn, mx = self.child.bounding_box()
        R = max(abs(mn.x), abs(mx.x), abs(mn.y), abs(mx.y)) * 1.5
        return (FreeCAD.Vector(-R, -R, mn.z), FreeCAD.Vector(R, R, mx.z))


class DisplaceField(SdfField):
    """Adds a displacement d(p) to the SDF. displacement_fn must be callable: (FreeCAD.Vector) -> float.
    Ref: opDisplace() iq. Example: lambda p: math.sin(5*p.x)*math.sin(5*p.y)*math.sin(5*p.z)"""
    def __init__(self, child: SdfField, displacement_fn):
        self.child = child
        self.displacement_fn = displacement_fn

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return self.child.evaluate(point) + self.displacement_fn(point)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        base = self.child.evaluate_grid(points)
        disp = np.array([self.displacement_fn(
                    FreeCAD.Vector(float(points[i, 0]), float(points[i, 1]), float(points[i, 2])))
                         for i in range(len(points))], dtype=np.float32)
        return (base + disp).astype(np.float32)

    def bounding_box(self):
        return self.child.bounding_box()
```

---

### O-012: Icons — Deformations

**Files:** Create in `Resources/icons/`

**`Resources/icons/DeformTwist.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <path d="M 20 56 Q 44 48 44 32 Q 44 16 20 8" style="fill:none;stroke:#e0e0e0;stroke-width:8"/>
  <path d="M 20 56 Q 44 48 44 32 Q 44 16 20 8" style="fill:none;stroke:#ffaa00;stroke-width:2"/>
  <path d="M 44 56 Q 20 48 20 32 Q 20 16 44 8" style="fill:none;stroke:#ffaa00;stroke-width:2;stroke-dasharray:4,4"/>
</svg>
```

**`Resources/icons/DeformBend.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <path d="M 8 32 Q 32 8 56 32" style="fill:none;stroke:#e0e0e0;stroke-width:8"/>
  <path d="M 8 32 Q 32 8 56 32" style="fill:none;stroke:#ffaa00;stroke-width:2"/>
  <line x1="8" y1="26" x2="8" y2="38" style="stroke:#ffaa00;stroke-width:2"/>
  <line x1="56" y1="26" x2="56" y2="38" style="stroke:#ffaa00;stroke-width:2"/>
</svg>
```

**`Resources/icons/DeformDisplace.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <circle cx="32" cy="32" r="20" style="fill:none;stroke:#000000;stroke-width:2;stroke-dasharray:4,4"/>
  <path d="M 12 32 Q 16 22 20 32 Q 24 42 28 32 Q 32 22 36 32 Q 40 42 44 32 Q 48 22 52 32" style="fill:none;stroke:#ffaa00;stroke-width:2"/>
</svg>
```

---

### O-013: Commands — Deformations

**File:** `commands/cmd_deform.py` — create new file

```python
import FreeCAD
import FreeCADGui
import math
from core import dm_logger


def _get_single_field(label):
    sel = FreeCADGui.Selection.getSelection()
    if len(sel) != 1:
        dm_logger.error(f'{label}: select exactly one SDF object.'); return None, None
    obj = sel[0]
    proxy = getattr(obj, 'Proxy', None)
    field = getattr(proxy, 'SdfField', None) if proxy else None
    if field is None:
        dm_logger.error(f'{label}: selected object has no SdfField.'); return None, None
    return obj, field


class CommandDMTwist:
    def GetResources(self):
        return {'Pixmap': 'DeformTwist', 'MenuText': 'Twist',
                'ToolTip': 'Twist the selected SDF around the Y axis.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.sdf.sdf_deform import TwistField
        src, field = _get_single_field('Twist')
        if field is None: return
        obj = create_dm_object(name='Twist', shape_type='sdf')
        obj.Proxy.SdfField = TwistField(field, twist_rate=math.radians(2.0))
        src.ViewObject.Visibility = False
        obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMBend:
    def GetResources(self):
        return {'Pixmap': 'DeformBend', 'MenuText': 'Bend',
                'ToolTip': 'Bend the selected SDF around the Z axis.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.sdf.sdf_deform import BendField
        src, field = _get_single_field('Bend')
        if field is None: return
        obj = create_dm_object(name='Bend', shape_type='sdf')
        obj.Proxy.SdfField = BendField(field, bend_rate=math.radians(1.5))
        src.ViewObject.Visibility = False
        obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMDisplace:
    def GetResources(self):
        return {'Pixmap': 'DeformDisplace', 'MenuText': 'Displace (Sine)',
                'ToolTip': 'Apply a sine-wave displacement to the selected SDF surface.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.sdf.sdf_deform import DisplaceField
        src, field = _get_single_field('Displace')
        if field is None: return
        disp_fn = lambda p: 2.0 * math.sin(0.3 * p.x) * math.sin(0.3 * p.y) * math.sin(0.3 * p.z)
        obj = create_dm_object(name='Displace', shape_type='sdf')
        obj.Proxy.SdfField = DisplaceField(field, disp_fn)
        src.ViewObject.Visibility = False
        obj.touch();  FreeCAD.activeDocument().recompute()


class DMDeformGroup:
    def GetCommands(self): return ('DM_Twist', 'DM_Bend', 'DM_Displace')
    def GetDefaultCommand(self): return 0
    def GetResources(self):
        return {'MenuText': 'Deformations', 'ToolTip': 'Domain-space deformation operations'}
    def IsActive(self): return FreeCAD.activeDocument() is not None


FreeCADGui.addCommand('DM_Twist',       CommandDMTwist())
FreeCADGui.addCommand('DM_Bend',        CommandDMBend())
FreeCADGui.addCommand('DM_Displace',    CommandDMDisplace())
FreeCADGui.addCommand('DM_DeformGroup', DMDeformGroup())
```

**Depends on:** O-011, O-012

---

## Tier 5 — Final Wire-up

### O-014: Register all new group commands in InitGui.py

**File:** `InitGui.py` — edit the `appendMenu` call to add all new operation commands

**What:** Expand the existing `appendMenu("Direct Modeling", [...])` section to include
all new operation entries under a logical ordering. The toolbar was already updated in O-004.

Replace the existing operations section in the menu (the `'DM_Translate' ... 'DM_OpenSketcher'` block) with:

```python
# In appendMenu("Direct Modeling", [...]):
# Operations section — replace old entries with:
'Separator',
'DM_Translate',
'Separator',
'DM_Add', 'DM_Subtract', 'DM_Intersection', 'DM_Xor',
'DM_SmoothUnion', 'DM_SmoothSubtraction', 'DM_SmoothIntersection',
'Separator',
'DM_Round', 'DM_Onion', 'DM_Elongate',
'Separator',
'DM_SymmetryX', 'DM_SymmetryXZ', 'DM_Repetition', 'DM_LimitedRepetition',
'Separator',
'DM_Twist', 'DM_Bend', 'DM_Displace',
'Separator',
'DM_SDFSlice', 'DM_SDFToShape', 'DM_OpenSketcher',
```

**Depends on:** O-004, O-007, O-010, O-013

---

## Tier 6 — Positioning & Scale

### O-015: `ScaleField` — uniform scaling wrapper

**File:** `core/sdf/sdf_modifier.py` — append after `ElongateModifierField` class (end of file)

**What:** Uniformly scales the child field by factor `s`. Input point is divided by `s`, output
distance is multiplied by `s` to preserve the SDF property. Ref: `opScale()`.

```python
class ScaleField(SdfField):
    """Uniform scale wrapper. s > 1 makes the shape larger. Ref: opScale() iq."""
    def __init__(self, child: SdfField, s: float):
        self.child = child
        self.s = s

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = FreeCAD.Vector(point.x / self.s, point.y / self.s, point.z / self.s)
        return self.child.evaluate(p) * self.s

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return (self.child.evaluate_grid(points / self.s) * self.s).astype(np.float32)

    def bounding_box(self):
        mn, mx = self.child.bounding_box()
        return (mn * self.s, mx * self.s)
```

---

### O-016: Icon and command for Scale

**File:** `Resources/icons/ModScale.svg` — create new file; also add command to `commands/cmd_modifier.py`

**`Resources/icons/ModScale.svg`:**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <circle cx="32" cy="32" r="10" style="fill:none;stroke:#000000;stroke-width:1;stroke-dasharray:4,4"/>
  <circle cx="32" cy="32" r="22" style="fill:#e0e0e0;stroke:#ffaa00;stroke-width:2"/>
  <line x1="4" y1="4" x2="14" y2="14" style="stroke:#ffaa00;stroke-width:2"/>
  <line x1="60" y1="4" x2="50" y2="14" style="stroke:#ffaa00;stroke-width:2"/>
  <line x1="4" y1="60" x2="14" y2="50" style="stroke:#ffaa00;stroke-width:2"/>
  <line x1="60" y1="60" x2="50" y2="50" style="stroke:#ffaa00;stroke-width:2"/>
</svg>
```

**Add to `commands/cmd_modifier.py`** — append before the `FreeCADGui.addCommand` block at the end of the file:

```python
class CommandDMScale:
    def GetResources(self):
        return {'Pixmap': 'ModScale', 'MenuText': 'Scale',
                'ToolTip': 'Uniformly scale the selected SDF by a factor of 2.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.sdf.sdf_modifier import ScaleField
        src_obj, field = _get_single_sdf_field('Scale')
        if field is None: return
        new_field = ScaleField(field, s=2.0)
        obj = create_dm_object(name='Scale', shape_type='sdf')
        obj.Proxy.SdfField = new_field
        src_obj.ViewObject.Visibility = False
        obj.touch();  FreeCAD.activeDocument().recompute()
```

**Also update `DMModifierGroup.GetCommands()`** to include `'DM_Scale'`:
```python
def GetCommands(self): return ('DM_Round', 'DM_Onion', 'DM_Elongate', 'DM_Scale')
```

**Also add at end of file:**
```python
FreeCADGui.addCommand('DM_Scale', CommandDMScale())
```

**Depends on:** O-015, O-007

---

## Tier 7 — Revolution & Extrusion (2D→3D)

Revolution and Extrusion create 3D shapes by applying a 2D SDF primitive. These require a
lightweight 2D SDF interface. This tier establishes that interface and two operations.

### O-017: Create 2D SDF base class and circle primitive

**File:** `core/sdf/sdf2d/sdf2d_field.py` — create new file (also create `core/sdf/sdf2d/__init__.py` as empty file)

**What:** Abstract base class for 2D SDF fields used by Revolution and Extrusion. Provides
`evaluate_2d(x, y) -> float` and `evaluate_2d_grid(points_2d) -> np.ndarray` where
`points_2d` is an (N, 2) array. Also includes `Sdf2dCircle` as the first concrete 2D primitive.

```python
# core/sdf/sdf2d/sdf2d_field.py
import numpy as np
import math


class Sdf2dField:
    """Abstract base for 2D SDF primitives used by Revolution/Extrusion."""
    def evaluate_2d(self, x: float, y: float) -> float:
        raise NotImplementedError

    def evaluate_2d_grid(self, pts: np.ndarray) -> np.ndarray:
        """Default: loop fallback. pts shape (N,2). Returns (N,) float32."""
        return np.array([self.evaluate_2d(float(pts[i, 0]), float(pts[i, 1]))
                         for i in range(len(pts))], dtype=np.float32)


class Sdf2dCircle(Sdf2dField):
    """Circle SDF in 2D. center=(cx,cy), radius=r."""
    def __init__(self, cx: float, cy: float, radius: float):
        self.cx = cx;  self.cy = cy;  self.radius = radius

    def evaluate_2d(self, x: float, y: float) -> float:
        return math.sqrt((x - self.cx)**2 + (y - self.cy)**2) - self.radius

    def evaluate_2d_grid(self, pts: np.ndarray) -> np.ndarray:
        d = np.sqrt((pts[:, 0] - self.cx)**2 + (pts[:, 1] - self.cy)**2)
        return (d - self.radius).astype(np.float32)


class Sdf2dBox(Sdf2dField):
    """Axis-aligned box SDF in 2D. center=(cx,cy), half_size=(hx,hy)."""
    def __init__(self, cx: float, cy: float, hx: float, hy: float):
        self.cx = cx;  self.cy = cy;  self.hx = hx;  self.hy = hy

    def evaluate_2d(self, x: float, y: float) -> float:
        dx = abs(x - self.cx) - self.hx
        dy = abs(y - self.cy) - self.hy
        return math.sqrt(max(dx, 0)**2 + max(dy, 0)**2) + min(max(dx, dy), 0.0)

    def evaluate_2d_grid(self, pts: np.ndarray) -> np.ndarray:
        dx = np.abs(pts[:, 0] - self.cx) - self.hx
        dy = np.abs(pts[:, 1] - self.cy) - self.hy
        out = np.sqrt(np.maximum(dx, 0)**2 + np.maximum(dy, 0)**2)
        return (out + np.minimum(np.maximum(dx, dy), 0.0)).astype(np.float32)
```

---

### O-018: `RevolutionField` and `ExtrusionField`

**File:** `core/sdf/sdf_revolution.py` — create new file

**What:** Two fields that lift 2D SDFs into 3D.
- `RevolutionField`: revolves a 2D SDF around the Y axis with offset `o`.
  The 2D SDF is evaluated at `(sqrt(x²+z²) - o, y)`. Ref: `opRevolution()`.
- `ExtrusionField`: extrudes a 2D SDF (in XZ plane) along Y with half-height `h`.
  Ref: `opExtrusion()`.

```python
import numpy as np
import FreeCAD
import math
from core.sdf.sdf_field import SdfField
from core.sdf.sdf2d.sdf2d_field import Sdf2dField


class RevolutionField(SdfField):
    """Revolves a 2D SDF around the Y axis with radial offset o. Ref: opRevolution() iq."""
    def __init__(self, sdf2d: Sdf2dField, center: FreeCAD.Vector, offset: float):
        self.sdf2d = sdf2d
        self.center = center
        self.offset = offset  # radial offset (shifts the 2D shape away from the axis)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        qx = math.sqrt(p.x**2 + p.z**2) - self.offset
        return self.sdf2d.evaluate_2d(qx, p.y)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        qx = np.sqrt(p[:, 0]**2 + p[:, 2]**2) - self.offset
        pts2d = np.stack([qx, p[:, 1]], axis=1)
        return self.sdf2d.evaluate_2d_grid(pts2d).astype(np.float32)

    def bounding_box(self):
        # Conservative: use a sphere of radius = 2D extent + offset
        R = 50.0 + self.offset  # rough estimate; override per use-case if needed
        v = FreeCAD.Vector(R, R, R)
        return (self.center - v, self.center + v)


class ExtrusionField(SdfField):
    """Extrudes a 2D SDF (XZ plane) along Y axis with half-height h. Ref: opExtrusion() iq."""
    def __init__(self, sdf2d: Sdf2dField, center: FreeCAD.Vector, half_height: float):
        self.sdf2d = sdf2d
        self.center = center
        self.half_height = half_height

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        dx = self.sdf2d.evaluate_2d(p.x, p.z)
        dy = abs(p.y) - self.half_height
        return min(max(dx, dy), 0.0) + math.sqrt(max(dx, 0)**2 + max(dy, 0)**2)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        pts2d = np.stack([p[:, 0], p[:, 2]], axis=1)
        dx = self.sdf2d.evaluate_2d_grid(pts2d).astype(np.float64)
        dy = np.abs(p[:, 1]) - self.half_height
        out = np.sqrt(np.maximum(dx, 0)**2 + np.maximum(dy, 0)**2)
        return (out + np.minimum(np.maximum(dx, dy), 0.0)).astype(np.float32)

    def bounding_box(self):
        R = 50.0  # rough; conservative
        v = FreeCAD.Vector(R, self.half_height, R)
        return (self.center - v, self.center + v)
```

**Depends on:** O-017

---

### O-019: Icons and commands for Revolution & Extrusion

**Files:** Create icons; add commands to a new `commands/cmd_revolution.py`

**`Resources/icons/OpRevolution.svg`:**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <line x1="32" y1="4" x2="32" y2="60" style="stroke:#ffaa00;stroke-width:2;stroke-dasharray:4,4"/>
  <path d="M 32 16 A 18 18 0 1 1 32 48" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <path d="M 32 16 Q 56 32 32 48" style="fill:#e0e0e0;stroke:#000000;stroke-width:1;opacity:0.4"/>
</svg>
```

**`Resources/icons/OpExtrusion.svg`:**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <ellipse cx="32" cy="52" rx="20" ry="6" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <rect x="12" y="20" width="40" height="32" style="fill:#e0e0e0;stroke:none"/>
  <line x1="12" y1="20" x2="12" y2="52" style="stroke:#000000;stroke-width:2"/>
  <line x1="52" y1="20" x2="52" y2="52" style="stroke:#000000;stroke-width:2"/>
  <ellipse cx="32" cy="20" rx="20" ry="6" style="fill:#ffffff;stroke:#000000;stroke-width:2"/>
  <line x1="32" y1="4" x2="32" y2="14" style="stroke:#ffaa00;stroke-width:2"/>
  <polygon points="32,2 29,8 35,8" style="fill:#ffaa00"/>
</svg>
```

**`commands/cmd_revolution.py`** — create new file:
```python
import FreeCAD
import FreeCADGui
from core import dm_logger


def _get_single_field(label):
    sel = FreeCADGui.Selection.getSelection()
    if len(sel) != 1:
        dm_logger.error(f'{label}: select exactly one SDF object.'); return None, None
    obj = sel[0]
    proxy = getattr(obj, 'Proxy', None)
    field = getattr(proxy, 'SdfField', None) if proxy else None
    if field is None:
        dm_logger.error(f'{label}: selected object has no SdfField.'); return None, None
    return obj, field


class CommandDMRevolution:
    def GetResources(self):
        return {'Pixmap': 'OpRevolution', 'MenuText': 'Revolve (Circle)',
                'ToolTip': 'Revolve a 2D circle profile around the Y axis to create a torus-like shape.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.sdf.sdf_revolution import RevolutionField
        from core.sdf.sdf2d.sdf2d_field import Sdf2dCircle
        sdf2d = Sdf2dCircle(0.0, 0.0, 6.0)
        field = RevolutionField(sdf2d, center=FreeCAD.Vector(0, 0, 0), offset=20.0)
        obj = create_dm_object(name='Revolution', shape_type='sdf')
        obj.Proxy.SdfField = field
        obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMExtrusion:
    def GetResources(self):
        return {'Pixmap': 'OpExtrusion', 'MenuText': 'Extrude (Circle)',
                'ToolTip': 'Extrude a 2D circle profile along the Y axis to create a cylinder.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.sdf.sdf_revolution import ExtrusionField
        from core.sdf.sdf2d.sdf2d_field import Sdf2dCircle
        sdf2d = Sdf2dCircle(0.0, 0.0, 12.0)
        field = ExtrusionField(sdf2d, center=FreeCAD.Vector(0, 0, 0), half_height=20.0)
        obj = create_dm_object(name='Extrusion', shape_type='sdf')
        obj.Proxy.SdfField = field
        obj.touch();  FreeCAD.activeDocument().recompute()


class DMRevolutionGroup:
    def GetCommands(self): return ('DM_Revolution', 'DM_Extrusion')
    def GetDefaultCommand(self): return 0
    def GetResources(self):
        return {'MenuText': '2D→3D', 'ToolTip': 'Create 3D shapes from 2D profiles'}
    def IsActive(self): return FreeCAD.activeDocument() is not None


FreeCADGui.addCommand('DM_Revolution',      CommandDMRevolution())
FreeCADGui.addCommand('DM_Extrusion',       CommandDMExtrusion())
FreeCADGui.addCommand('DM_RevolutionGroup', DMRevolutionGroup())
```

**Depends on:** O-017, O-018

---

### O-020: Final toolbar update — add 2D→3D group and Revolution imports

**File:** `InitGui.py` — two edits

**Edit 1 — add imports** after the `import commands.cmd_deform` line (added in O-004):
```python
import commands.cmd_revolution
```

**Edit 2 — add `DM_RevolutionGroup` to the "DM - Operations" toolbar** (after `DM_DeformGroup`):
```python
self.appendToolbar("DM - Operations", [
    'DM_Translate',
    'DM_SharpBooleanGroup',
    'DM_SmoothBooleanGroup',
    'DM_ModifierGroup',
    'DM_SpatialGroup',
    'DM_DeformGroup',
    'DM_RevolutionGroup',      # ← add this line
    'DM_SDFSlice',
    'DM_SDFToShape',
    'DM_OpenSketcher',
])
```

**Edit 3 — add to menu** (append after the `'DM_Displace'` line in the menu operations section added in O-014):
```python
'Separator',
'DM_Revolution', 'DM_Extrusion',
```

**Depends on:** O-019
