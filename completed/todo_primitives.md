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

### Tool Creation Pattern — SDF Self-Intersection Guard

**CRITICAL**: All primitive creator tools must skip their own preview object during SDF
hit-testing, or the tool will hit-test against itself (confirmed bug in SphereCreator —
dragging inward snapped to the sphere surface, preventing shrinking).

**How it works**: `ViewProjector.get_mouse_plane_pt()` always ray-marches against visible
SDF objects (line 273–279 of `view_projector.py`), even when `place_on_geometry=False`.
This is by design (SDF objects should occlude workplanes), but during creation the preview
must be excluded.

**Pattern for all new tools**:

1. **`on_move_state_N`** — Call `self.get_mouse_plane_pt(event_dict)`. The base class
   (`DMBase.get_mouse_plane_pt`) automatically passes `skip_objects=[self._preview_obj]`
   to the projector.

2. **`on_button1_down`** — When calling `self.projector.get_mouse_plane_pt()` directly
   (to capture `wp_hit`), always pass `skip_objects`:
   ```python
   skip = [self._preview_obj] if self._preview_obj else None
   result = self.projector.get_mouse_plane_pt(
       event_dict,
       place_on_geometry=False,
       working_plane=getattr(self, "working_plane", None),
       skip_objects=skip
   )
   ```

3. **Height/axis drags** — Use `DMInputManager.get_instance().get_axis_point()` for
   constrained axis drags (e.g., box/cylinder height). This bypasses SDF entirely.

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

---

## Tier 5 — Cones & Pyramids

### P-018: `SdfConeField`

**File:** `core/frep/sdf/cone.py` — create new file

**What:** Cone with tip at `center`, opening downward (-Y). `half_angle` = half-angle from axis (radians), `height` = height from tip to base. Ref: `sdCone()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfConeField(SdfField):
    """Cone: tip at center, base in -Y direction. Ref: sdCone() iq."""
    def __init__(self, center: FreeCAD.Vector, half_angle: float, height: float):
        self.center = center
        self.half_angle = half_angle  # radians
        self.height = height

    def _sd(self, px, py, pz):
        """2D SDF in (radial, axial) space. tip at origin, base at y=-height."""
        sx = math.sin(self.half_angle)
        cx = math.cos(self.half_angle)
        h = self.height
        qv = (h * sx / cx, -1.0)      # slope vector
        wx = math.sqrt(px**2 + pz**2)
        wy = py
        dot_wq = wx * qv[0] + wy * qv[1]
        dot_qq = qv[0]**2 + qv[1]**2
        t = max(0.0, min(1.0, dot_wq / dot_qq))
        ax = wx - qv[0] * t
        ay = wy - qv[1] * t
        t2 = max(0.0, min(1.0, wx / qv[0])) if abs(qv[0]) > 1e-9 else 0.0
        bx = wx - qv[0] * t2
        by = wy - qv[1]
        d = min(ax**2 + ay**2, bx**2 + by**2)
        s = max(-1.0 * (wx * qv[1] - wy * qv[0]),
                -1.0 * (wy - qv[1]))
        return math.sqrt(d) * math.copysign(1.0, s)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        return self._sd(p.x, p.y, p.z)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        sx = math.sin(self.half_angle)
        cx_a = math.cos(self.half_angle)
        h = self.height
        qvx = h * sx / cx_a
        qvy = -1.0
        dot_qq = qvx**2 + qvy**2
        wx = np.sqrt(p[:, 0]**2 + p[:, 2]**2)
        wy = p[:, 1]
        dot_wq = wx * qvx + wy * qvy
        t = np.clip(dot_wq / dot_qq, 0.0, 1.0)
        ax = wx - qvx * t;  ay = wy - qvy * t
        t2 = np.clip(wx / qvx, 0.0, 1.0) if abs(qvx) > 1e-9 else np.zeros_like(wx)
        bx = wx - qvx * t2; by = wy - qvy
        d = np.minimum(ax**2 + ay**2, bx**2 + by**2)
        s = np.maximum(-(wx * qvy - wy * qvx), -(wy - qvy))
        return (np.sqrt(d) * np.sign(s)).astype(np.float32)

    def bounding_box(self):
        base_r = self.height * math.tan(self.half_angle)
        mn = FreeCAD.Vector(self.center.x - base_r, self.center.y - self.height, self.center.z - base_r)
        return (mn, FreeCAD.Vector(self.center.x + base_r, self.center.y, self.center.z + base_r))
```

---

### P-019: `SdfCappedConeField`

**File:** `core/frep/sdf/capped_cone.py` — create new file

**What:** Frustum (truncated cone). `center` = geometric center, `half_height` = half the height,
`r1` = bottom cap radius, `r2` = top cap radius. Ref: `sdCappedCone()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfCappedConeField(SdfField):
    """Capped cone (frustum). r1=bottom radius, r2=top radius. Ref: sdCappedCone() iq."""
    def __init__(self, center: FreeCAD.Vector, half_height: float, r1: float, r2: float):
        self.center = center
        self.half_height = half_height
        self.r1 = r1
        self.r2 = r2

    def _sd2(self, qx, qy):
        h, r1, r2 = self.half_height, self.r1, self.r2
        k1x, k1y = r2, h
        k2x, k2y = r2 - r1, 2.0 * h
        cax = qx - min(qx, r1 if qy < 0.0 else r2)
        cay = abs(qy) - h
        dot_k2 = k2x**2 + k2y**2
        dot_k1q = (k1x - qx) * k2x + (k1y - qy) * k2y
        t = max(0.0, min(1.0, dot_k1q / dot_k2))
        cbx = qx - k1x + k2x * t
        cby = qy - k1y + k2y * t
        s = -1.0 if cbx < 0.0 and cay < 0.0 else 1.0
        return s * math.sqrt(min(cax**2 + cay**2, cbx**2 + cby**2))

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        return self._sd2(math.sqrt(p.x**2 + p.z**2), p.y)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        h, r1, r2 = self.half_height, self.r1, self.r2
        qx = np.sqrt(p[:, 0]**2 + p[:, 2]**2)
        qy = p[:, 1]
        k2x, k2y = r2 - r1, 2.0 * h
        dot_k2 = k2x**2 + k2y**2
        cax = qx - np.where(qy < 0, r1, r2)
        cax = np.maximum(cax, 0.0) * np.sign(qx - np.where(qy < 0, r1, r2))
        # Simplified conservative bound — scalar fallback for correctness
        result = np.array([self._sd2(float(qx[i]), float(qy[i])) for i in range(len(qx))],
                          dtype=np.float32)
        return result

    def bounding_box(self):
        R = max(self.r1, self.r2)
        v = FreeCAD.Vector(R, self.half_height, R)
        return (self.center - v, self.center + v)
```

---

### P-020: `SdfRoundConeField`

**File:** `core/frep/sdf/round_cone.py` — create new file

**What:** Cone between two spheres of different radii. `center` = base center, `r1` = base radius,
`r2` = tip radius, `height` = height. Ref: `sdRoundCone()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfRoundConeField(SdfField):
    """Round cone: two spheres r1 (base) and r2 (tip) joined smoothly. Ref: sdRoundCone() iq."""
    def __init__(self, center: FreeCAD.Vector, r1: float, r2: float, height: float):
        self.center = center
        self.r1 = r1
        self.r2 = r2
        self.height = height

    def _sd(self, qx, qy):
        b = (self.r1 - self.r2) / self.height
        a = math.sqrt(max(1.0 - b * b, 0.0))
        k = qx * (-b) + qy * a
        if k < 0.0:
            return math.sqrt(qx**2 + qy**2) - self.r1
        if k > a * self.height:
            return math.sqrt(qx**2 + (qy - self.height)**2) - self.r2
        return qx * a + qy * b - self.r1

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        return self._sd(math.sqrt(p.x**2 + p.z**2), p.y)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        b = (self.r1 - self.r2) / self.height
        a = math.sqrt(max(1.0 - b * b, 0.0))
        qx = np.sqrt(p[:, 0]**2 + p[:, 2]**2)
        qy = p[:, 1]
        k = qx * (-b) + qy * a
        d_base = np.sqrt(qx**2 + qy**2) - self.r1
        d_tip  = np.sqrt(qx**2 + (qy - self.height)**2) - self.r2
        d_body = qx * a + qy * b - self.r1
        result = np.where(k < 0.0, d_base, np.where(k > a * self.height, d_tip, d_body))
        return result.astype(np.float32)

    def bounding_box(self):
        R = max(self.r1, self.r2)
        mn = FreeCAD.Vector(self.center.x - R, self.center.y - self.r1, self.center.z - R)
        mx = FreeCAD.Vector(self.center.x + R, self.center.y + self.height + self.r2, self.center.z + R)
        return (mn, mx)
```

---

### P-021: `SdfPyramidField`

**File:** `core/frep/sdf/pyramid.py` — create new file

**What:** Square-base pyramid. `center` = center of the base (1×1 unit square), `height` = height.
Base spans ±0.5 in XZ. Scale with DM Translate. Ref: `sdPyramid()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfPyramidField(SdfField):
    """Square-base pyramid. Base is unit square at center in XZ. Ref: sdPyramid() iq."""
    def __init__(self, center: FreeCAD.Vector, height: float):
        self.center = center
        self.height = height

    def _sd(self, px, py, pz):
        h = self.height
        m2 = h * h + 0.25
        px, pz = abs(px), abs(pz)
        if pz > px:
            px, pz = pz, px
        px -= 0.5
        pz -= 0.5
        qx = pz
        qy = h * py - 0.5 * px
        qz = h * px + 0.5 * py
        s = max(-qx, 0.0)
        t = max(0.0, min((qy - 0.5 * pz) / (m2 + 0.25), 1.0))
        a = m2 * (qx + s)**2 + qy**2
        b = m2 * (qx + 0.5 * t)**2 + (qy - m2 * t)**2
        d2 = 0.0 if min(qx, -qy) > 0.0 else min(a, b)
        return math.sqrt((d2 + qz**2) / m2) * math.copysign(1.0, max(qz, -py))

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        return self._sd(p.x, p.y, p.z)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        return np.array([self._sd(float(p[i, 0]), float(p[i, 1]), float(p[i, 2]))
                         for i in range(len(p))], dtype=np.float32)

    def bounding_box(self):
        mn = FreeCAD.Vector(self.center.x - 0.5, self.center.y, self.center.z - 0.5)
        mx = FreeCAD.Vector(self.center.x + 0.5, self.center.y + self.height, self.center.z + 0.5)
        return (mn, mx)
```

---

### P-022: `SdfSolidAngleField`

**File:** `core/frep/sdf/solid_angle.py` — create new file

**What:** Spherical wedge / solid angle: the region of a sphere carved to a cone-shaped opening.
`angle` = half-angle of the cone (radians), `radius` = sphere radius. Ref: `sdSolidAngle()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfSolidAngleField(SdfField):
    """Spherical wedge. angle = half-angle of opening (radians). Ref: sdSolidAngle() iq."""
    def __init__(self, center: FreeCAD.Vector, angle: float, radius: float):
        self.center = center
        self.angle = angle
        self.radius = radius

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        sc = (math.sin(self.angle), math.cos(self.angle))
        qx = math.sqrt(p.x**2 + p.z**2)
        qy = p.y
        l = math.sqrt(qx**2 + qy**2) - self.radius
        dot_qc = qx * sc[0] + qy * sc[1]
        t = max(0.0, min(dot_qc, self.radius))
        mx = qx - sc[0] * t
        my = qy - sc[1] * t
        m = math.sqrt(mx**2 + my**2) * math.copysign(1.0, sc[1] * qx - sc[0] * qy)
        return max(l, m)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        sx, cx_a = math.sin(self.angle), math.cos(self.angle)
        qx = np.sqrt(p[:, 0]**2 + p[:, 2]**2)
        qy = p[:, 1]
        l = np.sqrt(qx**2 + qy**2) - self.radius
        t = np.clip(qx * sx + qy * cx_a, 0.0, self.radius)
        m = np.sqrt((qx - sx * t)**2 + (qy - cx_a * t)**2) * np.sign(cx_a * qx - sx * qy)
        return np.maximum(l, m).astype(np.float32)

    def bounding_box(self):
        r = self.radius
        v = FreeCAD.Vector(r, r, r)
        return (self.center - v, self.center + v)
```

---

### P-023: Icons — Cones & Pyramids

**Files:** Create in `Resources/icons/`

**`Resources/icons/CreateCone.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <polygon points="32,6 58,58 6,58" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
</svg>
```

**`Resources/icons/CreateCappedCone.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <polygon points="18,10 46,10 58,56 6,56" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
</svg>
```

**`Resources/icons/CreateRoundCone.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <circle cx="32" cy="12" r="8" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <path d="M 24 12 Q 6 52 20 56 L 44 56 Q 58 52 40 12 Z" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <ellipse cx="32" cy="56" rx="12" ry="4" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
</svg>
```

**`Resources/icons/CreatePyramid.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <polygon points="32,6 58,52 6,52" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <polygon points="32,6 58,52 48,60 22,60 6,52" style="fill:#c0c0c0;stroke:#000000;stroke-width:2"/>
</svg>
```

**`Resources/icons/CreateSolidAngle.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <path d="M 32 32 L 10 14 A 26 26 0 0 1 54 14 Z" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <circle cx="32" cy="32" r="26" style="fill:none;stroke:#000000;stroke-width:2;stroke-dasharray:4,4"/>
</svg>
```

---

### P-024: Commands — Cones & Pyramids

**File:** `commands/cmd_cone_group.py` — create new file

```python
import FreeCAD
import FreeCADGui
import math


class CommandDMCone:
    def GetResources(self):
        return {'Pixmap': 'CreateCone', 'MenuText': 'Create Cone',
                'ToolTip': 'Create an SDF cone (tip at center, opens downward).'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.cone import SdfConeField
        field = SdfConeField(FreeCAD.Vector(0, 0, 0), half_angle=math.radians(25.0), height=30.0)
        obj = create_dm_object(name='Cone', shape_type='frep')
        obj.Proxy.FRepField = field;  obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMCappedCone:
    def GetResources(self):
        return {'Pixmap': 'CreateCappedCone', 'MenuText': 'Create Capped Cone',
                'ToolTip': 'Create an SDF frustum (truncated cone).'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.capped_cone import SdfCappedConeField
        field = SdfCappedConeField(FreeCAD.Vector(0, 0, 0), half_height=15.0, r1=12.0, r2=6.0)
        obj = create_dm_object(name='CappedCone', shape_type='frep')
        obj.Proxy.FRepField = field;  obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMRoundCone:
    def GetResources(self):
        return {'Pixmap': 'CreateRoundCone', 'MenuText': 'Create Round Cone',
                'ToolTip': 'Create an SDF round cone (two spheres joined smoothly).'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.round_cone import SdfRoundConeField
        field = SdfRoundConeField(FreeCAD.Vector(0, 0, 0), r1=12.0, r2=3.0, height=30.0)
        obj = create_dm_object(name='RoundCone', shape_type='frep')
        obj.Proxy.FRepField = field;  obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMPyramid:
    def GetResources(self):
        return {'Pixmap': 'CreatePyramid', 'MenuText': 'Create Pyramid',
                'ToolTip': 'Create an SDF square-base pyramid (unit base, scale with Translate).'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.pyramid import SdfPyramidField
        field = SdfPyramidField(FreeCAD.Vector(0, 0, 0), height=25.0)
        obj = create_dm_object(name='Pyramid', shape_type='frep')
        obj.Proxy.FRepField = field;  obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMSolidAngle:
    def GetResources(self):
        return {'Pixmap': 'CreateSolidAngle', 'MenuText': 'Create Solid Angle',
                'ToolTip': 'Create an SDF spherical wedge (solid angle).'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.solid_angle import SdfSolidAngleField
        field = SdfSolidAngleField(FreeCAD.Vector(0, 0, 0), angle=math.radians(40.0), radius=20.0)
        obj = create_dm_object(name='SolidAngle', shape_type='frep')
        obj.Proxy.FRepField = field;  obj.touch();  FreeCAD.activeDocument().recompute()


FreeCADGui.addCommand('DM_CreateCone',       CommandDMCone())
FreeCADGui.addCommand('DM_CreateCappedCone', CommandDMCappedCone())
FreeCADGui.addCommand('DM_CreateRoundCone',  CommandDMRoundCone())
FreeCADGui.addCommand('DM_CreatePyramid',    CommandDMPyramid())
FreeCADGui.addCommand('DM_CreateSolidAngle', CommandDMSolidAngle())
```

**Depends on:** P-018, P-019, P-020, P-021, P-022, P-023

---

## Tier 6 — Prisms & Polyhedra

### P-025: `SdfHexPrismField`

**File:** `core/frep/sdf/hex_prism.py` — create new file

**What:** Hexagonal prism. `hex_radius` = inradius of hexagon cross-section, `half_height` = half-length along Z. Ref: `sdHexPrism()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfHexPrismField(SdfField):
    """Hexagonal prism along Z axis. Ref: sdHexPrism() iq."""
    def __init__(self, center: FreeCAD.Vector, hex_radius: float, half_height: float):
        self.center = center
        self.hex_radius = hex_radius
        self.half_height = half_height

    def evaluate(self, point: FreeCAD.Vector) -> float:
        kx, ky, kz = -0.8660254, 0.5, 0.57735
        p = point - self.center
        px, py, pz = abs(p.x), abs(p.y), abs(p.z)
        f = 2.0 * min(kx * px + ky * py, 0.0)
        px -= f * kx;  py -= f * ky
        cx = max(-kz * self.hex_radius, min(px, kz * self.hex_radius))
        diff = math.sqrt((px - cx)**2 + (py - self.hex_radius)**2)
        dx = diff * math.copysign(1.0, py - self.hex_radius)
        dy = pz - self.half_height
        return min(max(dx, dy), 0.0) + math.sqrt(max(dx, 0)**2 + max(dy, 0)**2)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        kx, ky, kz = -0.8660254, 0.5, 0.57735
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = np.abs(points - c)
        px, py, pz = p[:, 0].copy(), p[:, 1].copy(), p[:, 2]
        f = 2.0 * np.minimum(kx * px + ky * py, 0.0)
        px -= f * kx;  py -= f * ky
        cx = np.clip(px, -kz * self.hex_radius, kz * self.hex_radius)
        diff = np.sqrt((px - cx)**2 + (py - self.hex_radius)**2)
        dx = diff * np.sign(py - self.hex_radius)
        dy = pz - self.half_height
        return (np.minimum(np.maximum(dx, dy), 0.0)
                + np.sqrt(np.maximum(dx, 0)**2 + np.maximum(dy, 0)**2)).astype(np.float32)

    def bounding_box(self):
        v = FreeCAD.Vector(self.hex_radius, self.hex_radius, self.half_height)
        return (self.center - v, self.center + v)
```

---

### P-026: `SdfTriPrismField`

**File:** `core/frep/sdf/tri_prism.py` — create new file

**What:** Triangular prism along Z. `tri_radius` = inradius of the triangular cross-section,
`half_height` = half-length along Z. Note: result is a lower bound, not exact. Ref: `sdTriPrism()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfTriPrismField(SdfField):
    """Triangular prism along Z (lower bound). Ref: sdTriPrism() iq."""
    def __init__(self, center: FreeCAD.Vector, tri_radius: float, half_height: float):
        self.center = center
        self.tri_radius = tri_radius
        self.half_height = half_height

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        qx, qz = abs(p.x), abs(p.z)
        return max(qz - self.half_height,
                   max(qx * 0.866025 + p.y * 0.5, -p.y) - self.tri_radius * 0.5)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        qx = np.abs(p[:, 0])
        qz = np.abs(p[:, 2])
        d = np.maximum(qz - self.half_height,
                       np.maximum(qx * 0.866025 + p[:, 1] * 0.5, -p[:, 1]) - self.tri_radius * 0.5)
        return d.astype(np.float32)

    def bounding_box(self):
        r = self.tri_radius
        v = FreeCAD.Vector(r, r, self.half_height)
        return (self.center - v, self.center + v)
```

---

### P-027: `SdfRhombusField`

**File:** `core/frep/sdf/rhombus.py` — create new file

**What:** Rhombus-shaped prism (diamond cross-section). `la`, `lb` = half-extents in X and Z,
`half_height` = half-height in Y, `rounding` = edge bevel. Ref: `sdRhombus()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfRhombusField(SdfField):
    """Rhombus prism. la/lb = XZ half-extents, rounding = edge bevel. Ref: sdRhombus() iq."""
    def __init__(self, center: FreeCAD.Vector, la: float, lb: float,
                 half_height: float, rounding: float):
        self.center = center
        self.la = la;  self.lb = lb
        self.half_height = half_height
        self.rounding = rounding

    def _sd(self, px, py, pz):
        la, lb, h, ra = self.la, self.lb, self.half_height, self.rounding
        px, py, pz = abs(px), abs(py), abs(pz)
        bx, by = la, lb
        ndot = bx * (bx - 2.0 * px) - by * (by - 2.0 * pz)
        dot_bb = bx**2 + by**2
        f = max(-1.0, min(1.0, ndot / dot_bb))
        proj_x = 0.5 * bx * (1.0 - f)
        proj_z = 0.5 * by * (1.0 + f)
        inner = math.sqrt((px - proj_x)**2 + (pz - proj_z)**2)
        sign = math.copysign(1.0, px * by + pz * bx - bx * by)
        qx = inner * sign - ra
        qy = py - h
        return min(max(qx, qy), 0.0) + math.sqrt(max(qx, 0)**2 + max(qy, 0)**2)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        return self._sd(p.x, p.y, p.z)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        return np.array([self._sd(float(p[i,0]), float(p[i,1]), float(p[i,2]))
                         for i in range(len(p))], dtype=np.float32)

    def bounding_box(self):
        v = FreeCAD.Vector(self.la + self.rounding, self.half_height, self.lb + self.rounding)
        return (self.center - v, self.center + v)
```

---

### P-028: `SdfOctahedronField`

**File:** `core/frep/sdf/octahedron.py` — create new file

**What:** Regular octahedron. `size` = distance from center to each vertex. Ref: `sdOctahedron()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfOctahedronField(SdfField):
    """Regular octahedron. size = vertex distance. Ref: sdOctahedron() iq."""
    def __init__(self, center: FreeCAD.Vector, size: float):
        self.center = center
        self.size = size

    def _sd(self, px, py, pz):
        px, py, pz = abs(px), abs(py), abs(pz)
        m = px + py + pz - self.size
        if 3.0 * px < m:
            qx, qy, qz = px, py, pz
        elif 3.0 * py < m:
            qx, qy, qz = py, pz, px
        elif 3.0 * pz < m:
            qx, qy, qz = pz, px, py
        else:
            return m * 0.57735027
        k = max(0.0, min(0.5 * (qz - qy + self.size), self.size))
        return math.sqrt(qx**2 + (qy - self.size + k)**2 + (qz - k)**2)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        return self._sd(p.x, p.y, p.z)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        px = np.abs(p[:, 0]);  py = np.abs(p[:, 1]);  pz = np.abs(p[:, 2])
        m = px + py + pz - self.size
        # Select q per branch using where
        case_x = 3.0 * px < m
        case_y = ~case_x & (3.0 * py < m)
        case_z = ~case_x & ~case_y & (3.0 * pz < m)
        case_c = ~case_x & ~case_y & ~case_z
        qx = np.where(case_x, px, np.where(case_y, py, np.where(case_z, pz, px)))
        qy = np.where(case_x, py, np.where(case_y, pz, np.where(case_z, px, py)))
        qz = np.where(case_x, pz, np.where(case_y, px, np.where(case_z, py, pz)))
        k = np.clip(0.5 * (qz - qy + self.size), 0.0, self.size)
        d_branch = np.sqrt(qx**2 + (qy - self.size + k)**2 + (qz - k)**2)
        return np.where(case_c, m * 0.57735027, d_branch).astype(np.float32)

    def bounding_box(self):
        v = FreeCAD.Vector(self.size, self.size, self.size)
        return (self.center - v, self.center + v)
```

---

### P-029: Icons — Prisms & Polyhedra

**Files:** Create in `Resources/icons/`

**`Resources/icons/CreateHexPrism.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <polygon points="32,6 54,19 54,45 32,58 10,45 10,19" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
</svg>
```

**`Resources/icons/CreateTriPrism.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <polygon points="32,6 58,58 6,58" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <polygon points="32,6 58,58 50,50 24,50" style="fill:#c0c0c0;stroke:#000000;stroke-width:2"/>
</svg>
```

**`Resources/icons/CreateRhombus.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <polygon points="32,6 58,32 32,58 6,32" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
</svg>
```

**`Resources/icons/CreateOctahedron.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <polygon points="32,6 58,32 32,58 6,32" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <line x1="6" y1="32" x2="58" y2="32" style="stroke:#000000;stroke-width:1;stroke-dasharray:4,3"/>
  <line x1="32" y1="6" x2="32" y2="58" style="stroke:#000000;stroke-width:1;stroke-dasharray:4,3"/>
</svg>
```

---

### P-030: Commands — Prisms & Polyhedra

**File:** `commands/cmd_prism_group.py` — create new file

```python
import FreeCAD
import FreeCADGui


class CommandDMHexPrism:
    def GetResources(self):
        return {'Pixmap': 'CreateHexPrism', 'MenuText': 'Create Hex Prism',
                'ToolTip': 'Create an SDF hexagonal prism.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.hex_prism import SdfHexPrismField
        field = SdfHexPrismField(FreeCAD.Vector(0, 0, 0), hex_radius=15.0, half_height=10.0)
        obj = create_dm_object(name='HexPrism', shape_type='frep')
        obj.Proxy.FRepField = field;  obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMTriPrism:
    def GetResources(self):
        return {'Pixmap': 'CreateTriPrism', 'MenuText': 'Create Tri Prism',
                'ToolTip': 'Create an SDF triangular prism.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.tri_prism import SdfTriPrismField
        field = SdfTriPrismField(FreeCAD.Vector(0, 0, 0), tri_radius=15.0, half_height=10.0)
        obj = create_dm_object(name='TriPrism', shape_type='frep')
        obj.Proxy.FRepField = field;  obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMRhombus:
    def GetResources(self):
        return {'Pixmap': 'CreateRhombus', 'MenuText': 'Create Rhombus',
                'ToolTip': 'Create an SDF rhombus prism.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.rhombus import SdfRhombusField
        field = SdfRhombusField(FreeCAD.Vector(0, 0, 0), la=18.0, lb=12.0,
                                half_height=8.0, rounding=2.0)
        obj = create_dm_object(name='Rhombus', shape_type='frep')
        obj.Proxy.FRepField = field;  obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMOctahedron:
    def GetResources(self):
        return {'Pixmap': 'CreateOctahedron', 'MenuText': 'Create Octahedron',
                'ToolTip': 'Create an SDF regular octahedron.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.octahedron import SdfOctahedronField
        field = SdfOctahedronField(FreeCAD.Vector(0, 0, 0), size=18.0)
        obj = create_dm_object(name='Octahedron', shape_type='frep')
        obj.Proxy.FRepField = field;  obj.touch();  FreeCAD.activeDocument().recompute()


FreeCADGui.addCommand('DM_CreateHexPrism',  CommandDMHexPrism())
FreeCADGui.addCommand('DM_CreateTriPrism',  CommandDMTriPrism())
FreeCADGui.addCommand('DM_CreateRhombus',   CommandDMRhombus())
FreeCADGui.addCommand('DM_CreateOctahedron', CommandDMOctahedron())
```

**Depends on:** P-025, P-026, P-027, P-028, P-029

---

## Tier 7 — Sphere Variants & Surface Primitives

### P-031: `SdfCutSphereField`

**File:** `core/frep/sdf/cut_sphere.py` — create new file

**What:** Sphere with a planar cap cut off. `radius` = sphere radius, `cut_height` = Y coordinate of
the cutting plane (must satisfy -radius < cut_height < radius). Ref: `sdCutSphere()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfCutSphereField(SdfField):
    """Sphere with flat cap. cut_height in (-radius, radius). Ref: sdCutSphere() iq."""
    def __init__(self, center: FreeCAD.Vector, radius: float, cut_height: float):
        self.center = center
        self.radius = radius
        self.cut_height = cut_height

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        r, h = self.radius, self.cut_height
        w = math.sqrt(max(r * r - h * h, 0.0))
        qx = math.sqrt(p.x**2 + p.z**2)
        qy = p.y
        s = max((h - r) * qx**2 + w * w * (h + r - 2.0 * qy), h * qx - w * qy)
        if s < 0.0:
            return math.sqrt(qx**2 + qy**2) - r
        elif qx < w:
            return h - qy
        else:
            return math.sqrt((qx - w)**2 + (qy - h)**2)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        r, h = self.radius, self.cut_height
        w = math.sqrt(max(r * r - h * h, 0.0))
        qx = np.sqrt(p[:, 0]**2 + p[:, 2]**2)
        qy = p[:, 1]
        s = np.maximum((h - r) * qx**2 + w * w * (h + r - 2.0 * qy), h * qx - w * qy)
        d_sphere = np.sqrt(qx**2 + qy**2) - r
        d_cap    = h - qy
        d_rim    = np.sqrt((qx - w)**2 + (qy - h)**2)
        result   = np.where(s < 0, d_sphere, np.where(qx < w, d_cap, d_rim))
        return result.astype(np.float32)

    def bounding_box(self):
        r = self.radius
        mn = FreeCAD.Vector(self.center.x - r, self.center.y + self.cut_height, self.center.z - r)
        mx = FreeCAD.Vector(self.center.x + r, self.center.y + r, self.center.z + r)
        return (mn, mx)
```

---

### P-032: `SdfCutHollowSphereField`

**File:** `core/frep/sdf/cut_hollow_sphere.py` — create new file

**What:** Shell of a sphere with a cap removed (like a bowl). `radius` = sphere radius,
`cut_height` = Y of cut plane, `thickness` = shell wall thickness. Ref: `sdCutHollowSphere()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfCutHollowSphereField(SdfField):
    """Hollow sphere shell with flat rim opening. Ref: sdCutHollowSphere() iq."""
    def __init__(self, center: FreeCAD.Vector, radius: float, cut_height: float, thickness: float):
        self.center = center
        self.radius = radius
        self.cut_height = cut_height
        self.thickness = thickness

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        r, h, t = self.radius, self.cut_height, self.thickness
        w = math.sqrt(max(r * r - h * h, 0.0))
        qx = math.sqrt(p.x**2 + p.z**2)
        qy = p.y
        if h * qx < w * qy:
            return math.sqrt((qx - w)**2 + (qy - h)**2) - t
        else:
            return abs(math.sqrt(qx**2 + qy**2) - r) - t

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        r, h, t = self.radius, self.cut_height, self.thickness
        w = math.sqrt(max(r * r - h * h, 0.0))
        qx = np.sqrt(p[:, 0]**2 + p[:, 2]**2)
        qy = p[:, 1]
        d_rim    = np.sqrt((qx - w)**2 + (qy - h)**2) - t
        d_shell  = np.abs(np.sqrt(qx**2 + qy**2) - r) - t
        return np.where(h * qx < w * qy, d_rim, d_shell).astype(np.float32)

    def bounding_box(self):
        r = self.radius + self.thickness
        v = FreeCAD.Vector(r, r, r)
        return (self.center - v, self.center + v)
```

---

### P-033: `SdfDeathStarField`

**File:** `core/frep/sdf/death_star.py` — create new file

**What:** Sphere with a spherical dent. `radius` = main sphere radius, `bite_radius` = radius of
the spherical bite, `bite_offset` = distance from center to bite sphere center. Ref: `sdDeathStar()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfDeathStarField(SdfField):
    """Sphere with spherical indentation. Ref: sdDeathStar() iq."""
    def __init__(self, center: FreeCAD.Vector, radius: float,
                 bite_radius: float, bite_offset: float):
        self.center = center
        self.radius = radius
        self.bite_radius = bite_radius
        self.bite_offset = bite_offset

    def _sd(self, px, pyz_len):
        ra, rb, d = self.radius, self.bite_radius, self.bite_offset
        a = (ra * ra - rb * rb + d * d) / (2.0 * d)
        b = math.sqrt(max(ra * ra - a * a, 0.0))
        if px * (pyz_len - b) > b * (px - a):
            return math.sqrt((px - a)**2 + (pyz_len - b)**2) - rb
        return max(math.sqrt(px**2 + pyz_len**2) - ra,
                   -(math.sqrt((px - d)**2 + pyz_len**2) - rb))

    def evaluate(self, point: FreeCAD.Vector) -> float:
        p = point - self.center
        return self._sd(p.x, math.sqrt(p.y**2 + p.z**2))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c = np.array([self.center.x, self.center.y, self.center.z])
        p = points - c
        return np.array([self._sd(float(p[i, 0]),
                                  float(math.sqrt(p[i, 1]**2 + p[i, 2]**2)))
                         for i in range(len(p))], dtype=np.float32)

    def bounding_box(self):
        r = self.radius
        v = FreeCAD.Vector(r, r, r)
        return (self.center - v, self.center + v)
```

---

### P-034: `SdfVesicaSegmentField`

**File:** `core/frep/sdf/vesica_segment.py` — create new file

**What:** Lens-shaped volume (vesica piscis) between two points. `a`, `b` = endpoints,
`width` = lens half-width. Ref: `sdVesicaSegment()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfVesicaSegmentField(SdfField):
    """Vesica/lens shape between endpoints a and b. Ref: sdVesicaSegment() iq."""
    def __init__(self, a: FreeCAD.Vector, b: FreeCAD.Vector, width: float):
        self.a = a
        self.b = b
        self.width = width

    def evaluate(self, point: FreeCAD.Vector) -> float:
        c_pt = (self.a + self.b) * 0.5
        l = (self.b - self.a).Length
        v = (self.b - self.a) * (1.0 / l)
        y = (point - c_pt).dot(v)
        perp = point - c_pt - v * y
        qx = perp.Length
        qy = abs(y)
        r = 0.5 * l
        w = self.width
        d = 0.5 * (r * r - w * w) / w
        if r * qx < d * (qy - r):
            hx, hy, hz = 0.0, r, 0.0
        else:
            hx, hy, hz = -d, 0.0, d + w
        return math.sqrt((qx - hx)**2 + (qy - hy)**2) - hz

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        c_arr = np.array([(self.a.x + self.b.x) * 0.5,
                          (self.a.y + self.b.y) * 0.5,
                          (self.a.z + self.b.z) * 0.5])
        ba = np.array([self.b.x - self.a.x, self.b.y - self.a.y, self.b.z - self.a.z])
        l = np.linalg.norm(ba)
        v = ba / l
        pc = points - c_arr
        y = pc @ v
        perp = pc - np.outer(y, v)
        qx = np.linalg.norm(perp, axis=1)
        qy = np.abs(y)
        r = 0.5 * l;  w = self.width
        d = 0.5 * (r * r - w * w) / w
        use_rim = r * qx < d * (qy - r)
        hx = np.where(use_rim, 0.0, -d)
        hy = np.where(use_rim, r, 0.0)
        hz = np.where(use_rim, 0.0, d + w)
        return (np.sqrt((qx - hx)**2 + (qy - hy)**2) - hz).astype(np.float32)

    def bounding_box(self):
        w = self.width
        mn = FreeCAD.Vector(min(self.a.x, self.b.x) - w,
                            min(self.a.y, self.b.y) - w,
                            min(self.a.z, self.b.z) - w)
        mx = FreeCAD.Vector(max(self.a.x, self.b.x) + w,
                            max(self.a.y, self.b.y) + w,
                            max(self.a.z, self.b.z) + w)
        return (mn, mx)
```

---

### P-035: `SdfTriangleSurfaceField`

**File:** `core/frep/sdf/triangle_surface.py` — create new file

**What:** Unsigned distance to a triangle surface (thin shell, both sides have equal distance).
`a`, `b`, `c` = triangle vertices. Result is always ≥ 0. Ref: `udTriangle()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfTriangleSurfaceField(SdfField):
    """Unsigned distance to triangle surface. Ref: udTriangle() iq."""
    def __init__(self, a: FreeCAD.Vector, b: FreeCAD.Vector, c: FreeCAD.Vector):
        self.a = a;  self.b = b;  self.c = c

    def evaluate(self, point: FreeCAD.Vector) -> float:
        def dot2(v): return v.dot(v)
        a, b, c = self.a, self.b, self.c
        ba = b - a;  pa = point - a
        cb = c - b;  pb = point - b
        ac = a - c;  pc = point - c
        nor = ba.cross(ac)
        cond = (math.copysign(1, ba.cross(nor).dot(pa)) +
                math.copysign(1, cb.cross(nor).dot(pb)) +
                math.copysign(1, ac.cross(nor).dot(pc))) < 2.0
        if cond:
            t1 = max(0., min(1., ba.dot(pa) / dot2(ba)))
            t2 = max(0., min(1., cb.dot(pb) / dot2(cb)))
            t3 = max(0., min(1., ac.dot(pc) / dot2(ac)))
            d = min(dot2(ba * t1 - pa), min(dot2(cb * t2 - pb), dot2(ac * t3 - pc)))
        else:
            d = nor.dot(pa)**2 / dot2(nor)
        return math.sqrt(d)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return np.array([self.evaluate(FreeCAD.Vector(float(points[i,0]),
                                                      float(points[i,1]),
                                                      float(points[i,2])))
                         for i in range(len(points))], dtype=np.float32)

    def bounding_box(self):
        mn = FreeCAD.Vector(min(self.a.x, self.b.x, self.c.x),
                            min(self.a.y, self.b.y, self.c.y),
                            min(self.a.z, self.b.z, self.c.z))
        mx = FreeCAD.Vector(max(self.a.x, self.b.x, self.c.x),
                            max(self.a.y, self.b.y, self.c.y),
                            max(self.a.z, self.b.z, self.c.z))
        return (mn, mx)
```

---

### P-036: `SdfQuadSurfaceField`

**File:** `core/frep/sdf/quad_surface.py` — create new file

**What:** Unsigned distance to a planar quad surface (4 coplanar vertices). `a`, `b`, `c`, `d`
= quad vertices in order. Result is always ≥ 0. Ref: `udQuad()`.

```python
import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField


class SdfQuadSurfaceField(SdfField):
    """Unsigned distance to quad surface. Ref: udQuad() iq."""
    def __init__(self, a: FreeCAD.Vector, b: FreeCAD.Vector,
                 c: FreeCAD.Vector, d: FreeCAD.Vector):
        self.a = a;  self.b = b;  self.c = c;  self.d = d

    def evaluate(self, point: FreeCAD.Vector) -> float:
        def dot2(v): return v.dot(v)
        a, b, c, dd = self.a, self.b, self.c, self.d
        ba = b - a;  pa = point - a
        cb = c - b;  pb = point - b
        dc = dd - c; pc = point - c
        ad = a - dd; pd = point - dd
        nor = ba.cross(ad)
        cond = (math.copysign(1, ba.cross(nor).dot(pa)) +
                math.copysign(1, cb.cross(nor).dot(pb)) +
                math.copysign(1, dc.cross(nor).dot(pc)) +
                math.copysign(1, ad.cross(nor).dot(pd))) < 3.0
        if cond:
            t1 = max(0., min(1., ba.dot(pa) / dot2(ba)))
            t2 = max(0., min(1., cb.dot(pb) / dot2(cb)))
            t3 = max(0., min(1., dc.dot(pc) / dot2(dc)))
            t4 = max(0., min(1., ad.dot(pd) / dot2(ad)))
            d2 = min(min(dot2(ba*t1-pa), dot2(cb*t2-pb)),
                     min(dot2(dc*t3-pc), dot2(ad*t4-pd)))
        else:
            d2 = nor.dot(pa)**2 / dot2(nor)
        return math.sqrt(d2)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        return np.array([self.evaluate(FreeCAD.Vector(float(points[i,0]),
                                                      float(points[i,1]),
                                                      float(points[i,2])))
                         for i in range(len(points))], dtype=np.float32)

    def bounding_box(self):
        xs = [v.x for v in [self.a, self.b, self.c, self.d]]
        ys = [v.y for v in [self.a, self.b, self.c, self.d]]
        zs = [v.z for v in [self.a, self.b, self.c, self.d]]
        return (FreeCAD.Vector(min(xs), min(ys), min(zs)),
                FreeCAD.Vector(max(xs), max(ys), max(zs)))
```

---

### P-037: Icons — Sphere Variants & Surface Primitives

**Files:** Create in `Resources/icons/`

**`Resources/icons/CreateCutSphere.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <path d="M 8 40 A 24 24 0 1 1 56 40 Z" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <line x1="8" y1="40" x2="56" y2="40" style="stroke:#000000;stroke-width:2"/>
</svg>
```

**`Resources/icons/CreateCutHollowSphere.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <path d="M 8 36 A 24 24 0 1 1 56 36 Z" style="fill:none;stroke:#000000;stroke-width:5"/>
  <line x1="8" y1="36" x2="56" y2="36" style="stroke:#000000;stroke-width:3"/>
</svg>
```

**`Resources/icons/CreateDeathStar.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <circle cx="30" cy="32" r="22" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
  <circle cx="48" cy="20" r="12" style="fill:#ffffff;stroke:#000000;stroke-width:2;stroke-dasharray:4,4"/>
</svg>
```

**`Resources/icons/CreateVesicaSegment.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <path d="M 32 8 A 28 28 0 0 1 32 56 A 28 28 0 0 1 32 8 Z" style="fill:#e0e0e0;stroke:#000000;stroke-width:2"/>
</svg>
```

**`Resources/icons/CreateTriangleSurface.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <polygon points="32,8 58,56 6,56" style="fill:none;stroke:#000000;stroke-width:3"/>
</svg>
```

**`Resources/icons/CreateQuadSurface.svg`**
```xml
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" version="1.1">
  <rect x="8" y="8" width="48" height="48" style="fill:none;stroke:#000000;stroke-width:3"/>
</svg>
```

---

### P-038: Commands — Sphere Variants & Surface Primitives

**File:** `commands/cmd_sphere_group.py` — create new file

```python
import FreeCAD
import FreeCADGui


class CommandDMCutSphere:
    def GetResources(self):
        return {'Pixmap': 'CreateCutSphere', 'MenuText': 'Create Cut Sphere',
                'ToolTip': 'Create an SDF sphere with a flat cap cut off.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.cut_sphere import SdfCutSphereField
        field = SdfCutSphereField(FreeCAD.Vector(0, 0, 0), radius=20.0, cut_height=5.0)
        obj = create_dm_object(name='CutSphere', shape_type='frep')
        obj.Proxy.FRepField = field;  obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMCutHollowSphere:
    def GetResources(self):
        return {'Pixmap': 'CreateCutHollowSphere', 'MenuText': 'Create Cut Hollow Sphere',
                'ToolTip': 'Create an SDF hollow sphere shell with a rim opening (bowl).'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.cut_hollow_sphere import SdfCutHollowSphereField
        field = SdfCutHollowSphereField(FreeCAD.Vector(0, 0, 0),
                                        radius=20.0, cut_height=5.0, thickness=2.0)
        obj = create_dm_object(name='CutHollowSphere', shape_type='frep')
        obj.Proxy.FRepField = field;  obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMDeathStar:
    def GetResources(self):
        return {'Pixmap': 'CreateDeathStar', 'MenuText': 'Create Death Star',
                'ToolTip': 'Create an SDF sphere with a spherical dent.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.death_star import SdfDeathStarField
        field = SdfDeathStarField(FreeCAD.Vector(0, 0, 0),
                                  radius=20.0, bite_radius=10.0, bite_offset=16.0)
        obj = create_dm_object(name='DeathStar', shape_type='frep')
        obj.Proxy.FRepField = field;  obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMVesicaSegment:
    def GetResources(self):
        return {'Pixmap': 'CreateVesicaSegment', 'MenuText': 'Create Vesica Segment',
                'ToolTip': 'Create an SDF lens/vesica shape between two points.'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.vesica_segment import SdfVesicaSegmentField
        field = SdfVesicaSegmentField(FreeCAD.Vector(-12, 0, 0),
                                      FreeCAD.Vector(12, 0, 0), width=10.0)
        obj = create_dm_object(name='VesicaSegment', shape_type='frep')
        obj.Proxy.FRepField = field;  obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMTriangleSurface:
    def GetResources(self):
        return {'Pixmap': 'CreateTriangleSurface', 'MenuText': 'Create Triangle Surface',
                'ToolTip': 'Create an SDF triangle surface (unsigned distance, thin shell).'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.triangle_surface import SdfTriangleSurfaceField
        field = SdfTriangleSurfaceField(FreeCAD.Vector(0, 20, 0),
                                        FreeCAD.Vector(-18, -10, 0),
                                        FreeCAD.Vector(18, -10, 0))
        obj = create_dm_object(name='Triangle', shape_type='frep')
        obj.Proxy.FRepField = field;  obj.touch();  FreeCAD.activeDocument().recompute()


class CommandDMQuadSurface:
    def GetResources(self):
        return {'Pixmap': 'CreateQuadSurface', 'MenuText': 'Create Quad Surface',
                'ToolTip': 'Create an SDF planar quad surface (unsigned distance, thin shell).'}
    def IsActive(self): return FreeCAD.activeDocument() is not None
    def Activated(self):
        from core.dm_object import create_dm_object
        from core.frep.sdf.quad_surface import SdfQuadSurfaceField
        field = SdfQuadSurfaceField(FreeCAD.Vector(-15, -15, 0), FreeCAD.Vector(15, -15, 0),
                                    FreeCAD.Vector(15,  15, 0), FreeCAD.Vector(-15,  15, 0))
        obj = create_dm_object(name='QuadSurface', shape_type='frep')
        obj.Proxy.FRepField = field;  obj.touch();  FreeCAD.activeDocument().recompute()


FreeCADGui.addCommand('DM_CreateCutSphere',       CommandDMCutSphere())
FreeCADGui.addCommand('DM_CreateCutHollowSphere', CommandDMCutHollowSphere())
FreeCADGui.addCommand('DM_CreateDeathStar',       CommandDMDeathStar())
FreeCADGui.addCommand('DM_CreateVesicaSegment',   CommandDMVesicaSegment())
FreeCADGui.addCommand('DM_CreateTriangleSurface', CommandDMTriangleSurface())
FreeCADGui.addCommand('DM_CreateQuadSurface',     CommandDMQuadSurface())
```

**Depends on:** P-031, P-032, P-033, P-034, P-035, P-036, P-037

