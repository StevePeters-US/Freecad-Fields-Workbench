# Direct Modeling Workbench — SDF Primitives Task List

> Tasks are ordered by dependency. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) to create or modify.

---

## Background

The workbench currently has three SDF primitives: Sphere, Box, and Cylinder. This task list adds
all finite SDF primitives from Inigo Quilez's reference article (iquilezles.org/articles/distfunctions/),
organized into six toolbar dropdown groups. Infinite shapes (Plane, Infinite Cylinder, Infinite Cone)
are excluded.

Each primitive requires: (1) an `SdfField` subclass in `core/frep/sdf/`, (2) an SVG icon in
`Resources/icons/`, and (3) a command registered in `commands/`. Toolbar dropdowns use FreeCAD's
command-group mechanism (see `dm_command_group` skill).

Goal state: the "DM - Constructive" toolbar shows six dropdown buttons — one per shape family —
replacing the current flat Box/Sphere/Cylinder buttons. All shapes create a DM frep object at the
world origin with default dimensions; the user moves/scales via the DM Translate tool.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `SdfField` | `core/frep/sdf/sdf_field.py:1` | Base class for all SDF primitives |
| `FRepField` | `core/frep/frep_field.py:1` | Abstract base; provides gradient fallback |
| `create_dm_object` | `core/dm_object.py` | Creates a FreeCAD DM object |
| `CommandDMCreation` | `commands/cmd_primitive.py:6` | Existing flat command pattern |
| `DMObjectProxy.FRepField` | `core/dm_object.py` | Where SDF field is stored on proxy |
| `appendToolbar` | `InitGui.py:72` | Toolbar registration |

---

## Agent Skills

| Skill | Purpose |
|-------|---------|
| `dm_sdf_primitive_pattern` | Template and rules for SdfField subclasses and commands |
| `dm_command_group` | How to create FreeCAD dropdown toolbar buttons |
| `dm_icons` | SVG icon size (64×64), colors (#e0e0e0 fill, #000 stroke) |

---

## Tier 1 — Infrastructure (Do First)

Add the command-group classes and update the toolbar. All later tiers depend on P-001.

### P-001: Create command group classes

**File:** `commands/cmd_primitive_groups.py` — create new file

**What:** Six `DMCommandGroup` classes (one per shape family) that bundle related creation
commands into toolbar dropdowns. Individual commands are registered in their own files; these
groups just reference them by ID.

```python
import FreeCAD
import FreeCADGui


class DMSolidsGroup:
    def GetCommands(self):
        return ('DM_CreateBox', 'DM_CreateSphere', 'DM_CreateCylinder',
                'DM_CreateEllipsoid', 'DM_CreateRoundBox', 'DM_CreateBoxFrame')
    def GetDefaultCommand(self): return 0
    def GetResources(self):
        return {'MenuText': 'Basic Solids', 'ToolTip': 'Create a basic SDF solid'}
    def IsActive(self): return FreeCAD.activeDocument() is not None


class DMTorusGroup:
    def GetCommands(self):
        return ('DM_CreateTorus', 'DM_CreateCappedTorus', 'DM_CreateLink')
    def GetDefaultCommand(self): return 0
    def GetResources(self):
        return {'MenuText': 'Torus & Links', 'ToolTip': 'Create a torus or link primitive'}
    def IsActive(self): return FreeCAD.activeDocument() is not None


class DMCapsuleGroup:
    def GetCommands(self):
        return ('DM_CreateCapsule', 'DM_CreateVerticalCapsule', 'DM_CreateRoundedCylinder')
    def GetDefaultCommand(self): return 0
    def GetResources(self):
        return {'MenuText': 'Capsules', 'ToolTip': 'Create a capsule or rounded cylinder'}
    def IsActive(self): return FreeCAD.activeDocument() is not None


class DMConeGroup:
    def GetCommands(self):
        return ('DM_CreateCone', 'DM_CreateCappedCone', 'DM_CreateRoundCone',
                'DM_CreatePyramid', 'DM_CreateSolidAngle')
    def GetDefaultCommand(self): return 0
    def GetResources(self):
        return {'MenuText': 'Cones & Pyramids', 'ToolTip': 'Create a cone or pyramid primitive'}
    def IsActive(self): return FreeCAD.activeDocument() is not None


class DMPrismGroup:
    def GetCommands(self):
        return ('DM_CreateHexPrism', 'DM_CreateTriPrism',
                'DM_CreateRhombus', 'DM_CreateOctahedron')
    def GetDefaultCommand(self): return 0
    def GetResources(self):
        return {'MenuText': 'Prisms & Polyhedra', 'ToolTip': 'Create a prism or polyhedron'}
    def IsActive(self): return FreeCAD.activeDocument() is not None


class DMSphereVariantsGroup:
    def GetCommands(self):
        return ('DM_CreateCutSphere', 'DM_CreateCutHollowSphere', 'DM_CreateDeathStar',
                'DM_CreateVesicaSegment', 'DM_CreateTriangleSurface', 'DM_CreateQuadSurface')
    def GetDefaultCommand(self): return 0
    def GetResources(self):
        return {'MenuText': 'Sphere Variants', 'ToolTip': 'Create a sphere variant or surface primitive'}
    def IsActive(self): return FreeCAD.activeDocument() is not None


FreeCADGui.addCommand('DM_SolidsGroup',        DMSolidsGroup())
FreeCADGui.addCommand('DM_TorusGroup',         DMTorusGroup())
FreeCADGui.addCommand('DM_CapsuleGroup',       DMCapsuleGroup())
FreeCADGui.addCommand('DM_ConeGroup',          DMConeGroup())
FreeCADGui.addCommand('DM_PrismGroup',         DMPrismGroup())
FreeCADGui.addCommand('DM_SphereVariantsGroup', DMSphereVariantsGroup())
```

---

### P-002: Update InitGui.py toolbar and imports

**File:** `InitGui.py` — edit `Initialize()` starting at line 55

**What:** Import the six new command-group files and replace the flat
`DM_CreateBox/Sphere/Cylinder` toolbar entries with the six dropdown groups.

```python
# ADD these imports after line 64 (after "import commands.cmd_primitive"):
import commands.cmd_primitive_groups
import commands.cmd_torus_group
import commands.cmd_solid_group
import commands.cmd_capsule_group
import commands.cmd_cone_group
import commands.cmd_prism_group
import commands.cmd_sphere_group

# REPLACE the appendToolbar("DM - Constructive", [...]) block (lines 76-84) with:
self.appendToolbar("DM - Constructive", [
    'DM_WorkPlane',
    'DM_CreatePoint',
    'DM_CreateCurve',
    'DM_SolidsGroup',
    'DM_TorusGroup',
    'DM_CapsuleGroup',
    'DM_ConeGroup',
    'DM_PrismGroup',
    'DM_SphereVariantsGroup',
    'DM_FillCurve',
])

# REPLACE the appendMenu primitive entries (lines 98-104) with individual commands:
self.appendMenu("Direct Modeling", [
    'DM_EditObject', 'DM_Settings', 'Separator',
    'DM_WorkPlane', 'DM_CreatePoint', 'DM_CreateCurve', 'Separator',
    'DM_CreateBox', 'DM_CreateSphere', 'DM_CreateCylinder',
    'DM_CreateEllipsoid', 'DM_CreateRoundBox', 'DM_CreateBoxFrame', 'Separator',
    'DM_CreateTorus', 'DM_CreateCappedTorus', 'DM_CreateLink', 'Separator',
    'DM_CreateCapsule', 'DM_CreateVerticalCapsule', 'DM_CreateRoundedCylinder', 'Separator',
    'DM_CreateCone', 'DM_CreateCappedCone', 'DM_CreateRoundCone',
    'DM_CreatePyramid', 'DM_CreateSolidAngle', 'Separator',
    'DM_CreateHexPrism', 'DM_CreateTriPrism', 'DM_CreateRhombus', 'DM_CreateOctahedron', 'Separator',
    'DM_CreateCutSphere', 'DM_CreateCutHollowSphere', 'DM_CreateDeathStar',
    'DM_CreateVesicaSegment', 'DM_CreateTriangleSurface', 'DM_CreateQuadSurface', 'Separator',
    'DM_Translate', 'DM_Add', 'DM_Subtract', 'DM_Intersection',
    'DM_SDFSlice', 'DM_SDFToShape', 'DM_OpenSketcher',
])
```

**Depends on:** P-001

---

## Tier 2 — Torus Family

### P-003: `SdfTorusField`

**File:** `core/frep/sdf/torus.py` — create new file

**What:** Torus SDF centered at `center`, ring in the XZ plane. `major_radius` = distance from center to tube center, `minor_radius` = tube radius.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfTorusField(SdfField):
    """Torus SDF. Ring lies in XZ plane. Ref: sdTorus() iq article."""
    def __init__(self, center: FreeCAD.Vector, major_radius: float, minor_radius: float):
        self.center = center
        self.major_radius = major_radius  # R: center to tube center
        self.minor_radius = minor_radius  # r: tube radius

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        radial = math.sqrt(p.x**2 + p.z**2) - self.major_radius
        return math.sqrt(radial**2 + p.y**2) - self.minor_radius

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        radial = np.sqrt(p[:, 0]**2 + p[:, 2]**2) - self.major_radius
        return (np.sqrt(radial**2 + p[:, 1]**2) - self.minor_radius).astype(np.float32)

    def bounding_box(self):
        R = self.major_radius + self.minor_radius
        v = FreeCAD.Vector(R, self.minor_radius, R)
        return (self.center - v, self.center + v)
```

---

### P-004: `SdfCappedTorusField`

**File:** `core/frep/sdf/capped_torus.py` — create new file

**What:** Torus with a wedge cut, leaving an arc. `angle` is the half-angle of the remaining arc
(radians, 0 < angle < π). Uses IQ `sdCappedTorus` formula where `sc = (sin(angle), cos(angle))`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfCappedTorusField(SdfField):
    """Arc-shaped torus. angle = half-angle of the arc (radians). Ref: sdCappedTorus() iq."""
    def __init__(self, center: FreeCAD.Vector, major_radius: float, minor_radius: float, angle: float):
        self.center = center
        self.major_radius = major_radius
        self.minor_radius = minor_radius
        self.angle = angle  # half-angle of arc in radians

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        sx, cx = math.sin(self.angle), math.cos(self.angle)
        px = abs(p.x)
        py = p.y
        k = (px * sx + py * cx) if cx * px > sx * py else math.sqrt(px**2 + py**2)
        d2 = p.x**2 + p.y**2 + p.z**2 + self.major_radius**2 - 2.0 * self.major_radius * k
        return math.sqrt(max(d2, 0.0)) - self.minor_radius

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        sx, cx = math.sin(self.angle), math.cos(self.angle)
        px = np.abs(p[:, 0])
        py = p[:, 1]
        cond = cx * px > sx * py
        k = np.where(cond, px * sx + py * cx, np.sqrt(px**2 + py**2))
        d2 = np.sum(p**2, axis=1) + self.major_radius**2 - 2.0 * self.major_radius * k
        return (np.sqrt(np.maximum(d2, 0.0)) - self.minor_radius).astype(np.float32)

    def bounding_box(self):
        R = self.major_radius + self.minor_radius
        v = FreeCAD.Vector(R, self.minor_radius, R)
        return (self.center - v, self.center + v)
```

---

### P-005: `SdfLinkField`

**File:** `core/frep/sdf/link.py` — create new file

**What:** Chain-link shape: a rectangle with rounded ends, like a letter D extruded into a loop.
`half_length` = half the straight section, `ring_radius` = radius of the ring cross-section center,
`tube_radius` = tube radius. Ref: `sdLink()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfLinkField(SdfField):
    """Chain link SDF. Ref: sdLink() iq article."""
    def __init__(self, center: FreeCAD.Vector, half_length: float,
                 ring_radius: float, tube_radius: float):
        self.center = center
        self.le = half_length
        self.r1 = ring_radius
        self.r2 = tube_radius

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        qy = max(abs(p.y) - self.le, 0.0)
        return math.sqrt((math.sqrt(p.x**2 + qy**2) - self.r1)**2 + p.z**2) - self.r2

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        qy = np.maximum(np.abs(p[:, 1]) - self.le, 0.0)
        ring = np.sqrt(p[:, 0]**2 + qy**2) - self.r1
        return (np.sqrt(ring**2 + p[:, 2]**2) - self.r2).astype(np.float32)

    def bounding_box(self):
        R = self.r1 + self.r2
        v = FreeCAD.Vector(R, self.le + self.r2, R)
        return (self.center - v, self.center + v)
```

---

### P-006: Icons — Torus family

**Files:** Create three SVG files in `Resources/icons/`

**What:** Three icons for Torus, CappedTorus, and Link following the dm_icons style (64×64, #e0e0e0 fill, #000 stroke-width:2).

**`Resources/icons/CreateTorus.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <circle cx="32" cy="32" r="22" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <circle cx="32" cy="32" r="10" style="fill:#ffffff;stroke:#000000;stroke-width:2"/>
</svg>
```

**`Resources/icons/CreateCappedTorus.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <path d="M 32 10 A 22 22 0 1 0 54 32" style="fill:none;stroke:#000000;stroke-width:8"/>
  <path d="M 32 20 A 12 12 0 1 1 44 32" style="fill:none;stroke:#ffffff;stroke-width:6"/>
  <circle cx="32" cy="32" r="22" style="fill:none;stroke:#e0e0e0;stroke-width:0"/>
</svg>
```

**`Resources/icons/CreateLink.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <rect x="20" y="8" width="24" height="48" rx="12" ry="12" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <rect x="27" y="15" width="10" height="34" rx="5" ry="5" style="fill:#ffffff;stroke:#000000;stroke-width:2"/>
</svg>
```

---

### P-007: Commands — Torus family

**File:** `commands/cmd_torus_group.py` — create new file

**What:** Three creation commands for Torus, CappedTorus, and Link. Each creates a DM frep object at the world origin with sensible default dimensions.

```python
import FreeCAD
import FreeCADGui


class CommandDMTorus:
    def GetResources(self):
        return {'Pixmap': 'CreateTorus', 'MenuText': 'Create Torus',
                'ToolTip': 'Create an SDF torus (ring in XZ plane).'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.torus import SdfTorusField
        field = SdfTorusField(center=FreeCAD.Vector(0, 0, 0), major_radius=20.0, minor_radius=5.0)
        obj = create_dm_object(name='Torus', shape_type='frep')
        obj.Proxy.FRepField = field
        obj.touch()
        FreeCAD.activeDocument().recompute()


class CommandDMCappedTorus:
    def GetResources(self):
        return {'Pixmap': 'CreateCappedTorus', 'MenuText': 'Create Capped Torus',
                'ToolTip': 'Create an SDF capped torus (arc shape).'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        import math
        from core.dm_object import create_dm_object
        from core.frep.sdf.capped_torus import SdfCappedTorusField
        field = SdfCappedTorusField(FreeCAD.Vector(0, 0, 0), 20.0, 5.0, math.radians(60.0))
        obj = create_dm_object(name='CappedTorus', shape_type='frep')
        obj.Proxy.FRepField = field
        obj.touch()
        FreeCAD.activeDocument().recompute()


class CommandDMLink:
    def GetResources(self):
        return {'Pixmap': 'CreateLink', 'MenuText': 'Create Link',
                'ToolTip': 'Create an SDF chain-link primitive.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.link import SdfLinkField
        field = SdfLinkField(FreeCAD.Vector(0, 0, 0), half_length=10.0,
                             ring_radius=15.0, tube_radius=4.0)
        obj = create_dm_object(name='Link', shape_type='frep')
        obj.Proxy.FRepField = field
        obj.touch()
        FreeCAD.activeDocument().recompute()


FreeCADGui.addCommand('DM_CreateTorus',       CommandDMTorus())
FreeCADGui.addCommand('DM_CreateCappedTorus', CommandDMCappedTorus())
FreeCADGui.addCommand('DM_CreateLink',        CommandDMLink())
```

**Depends on:** P-003, P-004, P-005, P-006

---

## Tier 3 — Basic Solid Variants

### P-008: `SdfEllipsoidField`

**File:** `core/frep/sdf/ellipsoid.py` — create new file

**What:** Ellipsoid SDF (lower-bound approximation from IQ). `radii` = FreeCAD.Vector of the three
semi-axes. Ref: `sdEllipsoid()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfEllipsoidField(SdfField):
    """Ellipsoid SDF (lower bound). radii = (rx, ry, rz). Ref: sdEllipsoid() iq."""
    def __init__(self, center: FreeCAD.Vector, radii: FreeCAD.Vector):
        self.center = center
        self.radii = radii

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        r = self.radii
        k0 = math.sqrt((p.x/r.x)**2 + (p.y/r.y)**2 + (p.z/r.z)**2)
        k1 = math.sqrt((p.x/r.x**2)**2 + (p.y/r.y**2)**2 + (p.z/r.z**2)**2)
        if k1 < 1e-9:
            return -min(r.x, r.y, r.z)
        return k0 * (k0 - 1.0) / k1

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        r = np.array([self.radii.x, self.radii.y, self.radii.z])
        p = points - c
        pr  = p / r
        pr2 = p / (r * r)
        k0 = np.linalg.norm(pr,  axis=1)
        k1 = np.linalg.norm(pr2, axis=1)
        k1 = np.where(k1 < 1e-9, 1e-9, k1)
        return (k0 * (k0 - 1.0) / k1).astype(np.float32)

    def bounding_box(self):
        return (self.center - self.radii, self.center + self.radii)
```

---

### P-009: `SdfRoundBoxField`

**File:** `core/frep/sdf/round_box.py` — create new file

**What:** Box with uniformly rounded corners. `half_size` = half-extents (FreeCAD.Vector),
`radius` = corner rounding radius. Ref: `sdRoundBox()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfRoundBoxField(SdfField):
    """Axis-aligned box with rounded corners. Ref: sdRoundBox() iq."""
    def __init__(self, center: FreeCAD.Vector, half_size: FreeCAD.Vector, radius: float):
        self.center = center
        self.half_size = half_size
        self.radius = radius

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        qx = abs(p.x) - self.half_size.x + self.radius
        qy = abs(p.y) - self.half_size.y + self.radius
        qz = abs(p.z) - self.half_size.z + self.radius
        out = math.sqrt(max(qx, 0)**2 + max(qy, 0)**2 + max(qz, 0)**2)
        inn = min(max(qx, max(qy, qz)), 0.0)
        return out + inn - self.radius

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        h = np.array([self.half_size.x, self.half_size.y, self.half_size.z])
        p = points - c
        q = np.abs(p) - h + self.radius
        out = np.linalg.norm(np.maximum(q, 0.0), axis=1)
        inn = np.minimum(np.max(q, axis=1), 0.0)
        return (out + inn - self.radius).astype(np.float32)

    def bounding_box(self):
        v = self.half_size + FreeCAD.Vector(self.radius, self.radius, self.radius)
        return (self.center - v, self.center + v)
```

---

### P-010: `SdfBoxFrameField`

**File:** `core/frep/sdf/box_frame.py` — create new file

**What:** Hollow wireframe box (the 12 edges of a box rendered as square tubes).
`half_size` = half-extents of the frame, `edge_width` = half-thickness of each edge bar.
Ref: `sdBoxFrame()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfBoxFrameField(SdfField):
    """Wireframe box (12 edge tubes). Ref: sdBoxFrame() iq."""
    def __init__(self, center: FreeCAD.Vector, half_size: FreeCAD.Vector, edge_width: float):
        self.center = center
        self.half_size = half_size
        self.edge_width = edge_width

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        e = self.edge_width
        px = abs(p.x) - self.half_size.x
        py = abs(p.y) - self.half_size.y
        pz = abs(p.z) - self.half_size.z
        qx = abs(px + e) - e
        qy = abs(py + e) - e
        qz = abs(pz + e) - e

        def _edge(a, b, c):
            out = math.sqrt(max(a,0)**2 + max(b,0)**2 + max(c,0)**2)
            return out + min(max(a, max(b, c)), 0.0)

        return min(_edge(px, qy, qz), min(_edge(qx, py, qz), _edge(qx, qy, pz)))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        h = np.array([self.half_size.x, self.half_size.y, self.half_size.z])
        e = self.edge_width
        pp = np.abs(points - c) - h
        q  = np.abs(pp + e) - e

        def _edge(a, b, cc):
            stk = np.stack([a, b, cc], axis=1)
            return (np.linalg.norm(np.maximum(stk, 0), axis=1)
                    + np.minimum(np.max(stk, axis=1), 0.0))

        d0 = _edge(pp[:, 0], q[:, 1], q[:, 2])
        d1 = _edge(q[:, 0], pp[:, 1], q[:, 2])
        d2 = _edge(q[:, 0], q[:, 1], pp[:, 2])
        return np.minimum(d0, np.minimum(d1, d2)).astype(np.float32)

    def bounding_box(self):
        v = self.half_size + FreeCAD.Vector(self.edge_width, self.edge_width, self.edge_width)
        return (self.center - v, self.center + v)
```

---

### P-011: Icons — Basic Solid Variants

**Files:** Create in `Resources/icons/`

**`Resources/icons/CreateEllipsoid.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <ellipse cx="32" cy="32" rx="26" ry="18" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
</svg>
```

**`Resources/icons/CreateRoundBox.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <rect x="8" y="8" width="48" height="48" rx="10" ry="10" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
</svg>
```

**`Resources/icons/CreateBoxFrame.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <rect x="8" y="8" width="40" height="40" style="fill:none;stroke:#000000;stroke-width:4"/>
  <rect x="16" y="16" width="40" height="40" style="fill:none;stroke:#e0e0e0;stroke-width:4"/>
  <line x1="8" y1="8" x2="16" y2="16" style="stroke:#000000;stroke-width:4"/>
  <line x1="48" y1="8" x2="56" y2="16" style="stroke:#000000;stroke-width:4"/>
  <line x1="8" y1="48" x2="16" y2="56" style="stroke:#000000;stroke-width:4"/>
  <line x1="48" y1="48" x2="56" y2="56" style="stroke:#000000;stroke-width:4"/>
</svg>
```

---

### P-012: Commands — Basic Solid Variants

**File:** `commands/cmd_solid_group.py` — create new file

**What:** Commands for Ellipsoid, RoundBox, BoxFrame. Existing Box/Sphere/Cylinder commands remain in `cmd_primitive.py`.

```python
import FreeCAD
import FreeCADGui


class CommandDMEllipsoid:
    def GetResources(self):
        return {'Pixmap': 'CreateEllipsoid', 'MenuText': 'Create Ellipsoid',
                'ToolTip': 'Create an SDF ellipsoid (lower bound approximation).'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.ellipsoid import SdfEllipsoidField
        field = SdfEllipsoidField(FreeCAD.Vector(0, 0, 0),
                                  FreeCAD.Vector(20.0, 12.0, 10.0))
        obj = create_dm_object(name='Ellipsoid', shape_type='frep')
        obj.Proxy.FRepField = field
        obj.touch()
        FreeCAD.activeDocument().recompute()


class CommandDMRoundBox:
    def GetResources(self):
        return {'Pixmap': 'CreateRoundBox', 'MenuText': 'Create Round Box',
                'ToolTip': 'Create an SDF box with rounded corners.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.round_box import SdfRoundBoxField
        field = SdfRoundBoxField(FreeCAD.Vector(0, 0, 0),
                                 FreeCAD.Vector(15.0, 10.0, 8.0), radius=3.0)
        obj = create_dm_object(name='RoundBox', shape_type='frep')
        obj.Proxy.FRepField = field
        obj.touch()
        FreeCAD.activeDocument().recompute()


class CommandDMBoxFrame:
    def GetResources(self):
        return {'Pixmap': 'CreateBoxFrame', 'MenuText': 'Create Box Frame',
                'ToolTip': 'Create an SDF wireframe box (hollow edge tubes).'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.box_frame import SdfBoxFrameField
        field = SdfBoxFrameField(FreeCAD.Vector(0, 0, 0),
                                 FreeCAD.Vector(15.0, 10.0, 8.0), edge_width=1.5)
        obj = create_dm_object(name='BoxFrame', shape_type='frep')
        obj.Proxy.FRepField = field
        obj.touch()
        FreeCAD.activeDocument().recompute()


FreeCADGui.addCommand('DM_CreateEllipsoid', CommandDMEllipsoid())
FreeCADGui.addCommand('DM_CreateRoundBox',  CommandDMRoundBox())
FreeCADGui.addCommand('DM_CreateBoxFrame',  CommandDMBoxFrame())
```

**Depends on:** P-008, P-009, P-010, P-011

---

## Tier 4 — Cylinders & Capsules

### P-013: `SdfCapsuleField`

**File:** `core/frep/sdf/capsule.py` — create new file

**What:** Capsule between two arbitrary endpoints `a` and `b` with tube radius `radius`. Ref: `sdCapsule()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfCapsuleField(SdfField):
    """Capsule (line segment inflated by radius). Ref: sdCapsule() iq."""
    def __init__(self, a: FreeCAD.Vector, b: FreeCAD.Vector, radius: float):
        self.a = a
        self.b = b
        self.radius = radius

    def evaluate(self, point: FreeCAD.Vector) -> float:
        pa = point - self.a
        ba = self.b - self.a
        h = max(0.0, min(1.0, pa.dot(ba) / ba.dot(ba)))
        return (pa - ba * h).Length - self.radius

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        a = np.array([self.a.x, self.a.y, self.a.z])
        ba = np.array([self.b.x - self.a.x, self.b.y - self.a.y, self.b.z - self.a.z])
        pa = points - a
        h = np.clip(pa @ ba / np.dot(ba, ba), 0.0, 1.0)
        diff = pa - np.outer(h, ba)
        return (np.linalg.norm(diff, axis=1) - self.radius).astype(np.float32)

    def bounding_box(self):
        r = self.radius
        return (FreeCAD.Vector(min(self.a.x, self.b.x) - r,
                               min(self.a.y, self.b.y) - r,
                               min(self.a.z, self.b.z) - r),
                FreeCAD.Vector(max(self.a.x, self.b.x) + r,
                               max(self.a.y, self.b.y) + r,
                               max(self.a.z, self.b.z) + r))
```

---

### P-014: `SdfVerticalCapsuleField`

**File:** `core/frep/sdf/vertical_capsule.py` — create new file

**What:** Vertical capsule: cylinder with hemispherical caps, aligned on Y axis. Base at `center`,
extends upward by `height`. Ref: `sdVerticalCapsule()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfVerticalCapsuleField(SdfField):
    """Vertical capsule (Y axis). Base at center, cap at center+height*Y. Ref: sdVerticalCapsule() iq."""
    def __init__(self, center: FreeCAD.Vector, height: float, radius: float):
        self.center = center
        self.height = height
        self.radius = radius

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        py = p.y - max(0.0, min(p.y, self.height))
        return math.sqrt(p.x**2 + py**2 + p.z**2) - self.radius

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        py = p[:, 1] - np.clip(p[:, 1], 0.0, self.height)
        d = np.sqrt(p[:, 0]**2 + py**2 + p[:, 2]**2)
        return (d - self.radius).astype(np.float32)

    def bounding_box(self):
        r = self.radius
        mn = FreeCAD.Vector(self.center.x - r, self.center.y - r, self.center.z - r)
        mx = FreeCAD.Vector(self.center.x + r, self.center.y + self.height + r, self.center.z + r)
        return (mn, mx)
```

---

### P-015: `SdfRoundedCylinderField`

**File:** `core/frep/sdf/rounded_cylinder.py` — create new file

**What:** Cylinder with rounded top/bottom edges. `ra` controls body size (outer radius = 2*ra),
`rb` is the edge rounding radius, `half_height` is the half-height. Ref: `sdRoundedCylinder()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfRoundedCylinderField(SdfField):
    """Cylinder with rounded edges. Outer radius ≈ 2*ra, rounding = rb. Ref: sdRoundedCylinder() iq."""
    def __init__(self, center: FreeCAD.Vector, ra: float, rb: float, half_height: float):
        self.center = center
        self.ra = ra          # body size parameter (outer radius ≈ 2*ra)
        self.rb = rb          # edge rounding radius
        self.half_height = half_height

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        dx = math.sqrt(p.x**2 + p.z**2) - 2.0 * self.ra + self.rb
        dy = abs(p.y) - self.half_height
        out = math.sqrt(max(dx, 0)**2 + max(dy, 0)**2)
        return out + min(max(dx, dy), 0.0) - self.rb

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        dx = np.sqrt(p[:, 0]**2 + p[:, 2]**2) - 2.0 * self.ra + self.rb
        dy = np.abs(p[:, 1]) - self.half_height
        out = np.sqrt(np.maximum(dx, 0)**2 + np.maximum(dy, 0)**2)
        return (out + np.minimum(np.maximum(dx, dy), 0.0) - self.rb).astype(np.float32)

    def bounding_box(self):
        R = 2.0 * self.ra
        H = self.half_height + self.rb
        v = FreeCAD.Vector(R, H, R)
        return (self.center - v, self.center + v)
```

---

### P-016: Icons — Cylinders & Capsules

**Files:** Create in `Resources/icons/`

**`Resources/icons/CreateCapsule.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <rect x="16" y="16" width="32" height="32" style="fill:#e0e0e0;stroke:none"/>
  <circle cx="16" cy="32" r="16" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <circle cx="48" cy="32" r="16" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <rect x="16" y="16" width="32" height="32" style="fill:#e0e0e0;stroke:none"/>
  <line x1="16" y1="16" x2="48" y2="16" style="stroke:#000000;stroke-width:2"/>
  <line x1="16" y1="48" x2="48" y2="48" style="stroke:#000000;stroke-width:2"/>
</svg>
```

**`Resources/icons/CreateVerticalCapsule.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <rect x="16" y="16" width="32" height="32" style="fill:#e0e0e0;stroke:none"/>
  <circle cx="32" cy="16" r="16" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <circle cx="32" cy="48" r="16" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <rect x="16" y="16" width="32" height="32" style="fill:#e0e0e0;stroke:none"/>
  <line x1="16" y1="16" x2="16" y2="48" style="stroke:#000000;stroke-width:2"/>
  <line x1="48" y1="16" x2="48" y2="48" style="stroke:#000000;stroke-width:2"/>
</svg>
```

**`Resources/icons/CreateRoundedCylinder.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <rect x="12" y="12" width="40" height="40" rx="8" ry="8" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <ellipse cx="32" cy="12" rx="20" ry="6" style="fill:#ffffff;stroke:#000000;stroke-width:2"/>
</svg>
```

---

### P-017: Commands — Cylinders & Capsules

**File:** `commands/cmd_capsule_group.py` — create new file

```python
import FreeCAD
import FreeCADGui


class CommandDMCapsule:
    def GetResources(self):
        return {'Pixmap': 'CreateCapsule', 'MenuText': 'Create Capsule',
                'ToolTip': 'Create an SDF capsule between two points.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.capsule import SdfCapsuleField
        field = SdfCapsuleField(FreeCAD.Vector(-15, 0, 0), FreeCAD.Vector(15, 0, 0), radius=8.0)
        obj = create_dm_object(name='Capsule', shape_type='frep')
        obj.Proxy.FRepField = field
        obj.touch()
        FreeCAD.activeDocument().recompute()


class CommandDMVerticalCapsule:
    def GetResources(self):
        return {'Pixmap': 'CreateVerticalCapsule', 'MenuText': 'Create Vertical Capsule',
                'ToolTip': 'Create an SDF vertical capsule (Y axis).'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.vertical_capsule import SdfVerticalCapsuleField
        field = SdfVerticalCapsuleField(FreeCAD.Vector(0, -15, 0), height=30.0, radius=8.0)
        obj = create_dm_object(name='VerticalCapsule', shape_type='frep')
        obj.Proxy.FRepField = field
        obj.touch()
        FreeCAD.activeDocument().recompute()


class CommandDMRoundedCylinder:
    def GetResources(self):
        return {'Pixmap': 'CreateRoundedCylinder', 'MenuText': 'Create Rounded Cylinder',
                'ToolTip': 'Create an SDF cylinder with rounded top/bottom edges.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.rounded_cylinder import SdfRoundedCylinderField
        field = SdfRoundedCylinderField(FreeCAD.Vector(0, 0, 0), ra=8.0, rb=2.0, half_height=12.0)
        obj = create_dm_object(name='RoundedCylinder', shape_type='frep')
        obj.Proxy.FRepField = field
        obj.touch()
        FreeCAD.activeDocument().recompute()


FreeCADGui.addCommand('DM_CreateCapsule',         CommandDMCapsule())
FreeCADGui.addCommand('DM_CreateVerticalCapsule', CommandDMVerticalCapsule())
FreeCADGui.addCommand('DM_CreateRoundedCylinder', CommandDMRoundedCylinder())
```

**Depends on:** P-013, P-014, P-015, P-016
