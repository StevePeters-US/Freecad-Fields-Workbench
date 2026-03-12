# Direct Modeling Workbench — SDF Architecture Refactor Task List

> Tasks are ordered by dependency. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

The Direct Modeling workbench represents 3D shapes as F-Rep Signed Distance Fields (SDFs).
Currently there are three structural problems:

1. **Booleans use BRep, not F-Rep.** `cmd_boolean.py` calls `Part.Shape.fuse/cut/common`,
   which destroys the SDF tree. Results cannot be further boolean'd, ray marched, or
   exported to CNC toolpaths. Booleans should compose `FRepField` objects via
   `UnionField`/`SubtractionField`/`IntersectionField` from `frep_composer.py`.

2. **Meshing is coupled to recompute.** `DMObjectProxy.execute()` always calls
   `mesher.mesh()` on every property change, even when the user's render mode is GPU
   ray marching or formulaic SDF — modes that don't need triangles. This wastes CPU
   time and blocks interactive editing.

3. **No explicit mesh export.** Meshing should be an explicit user action ("export to
   mesh for CNC"), not a side-effect of every recompute. A dedicated command lets the
   user choose resolution (0.1mm for CNC) without affecting interactive editing.

### Goal State

After all tasks are complete:
- F-Rep booleans compose field trees (GPU-renderable, analytically precise)
- `execute()` skips meshing when GPU preview is active (instant updates)
- A new "Mesh to Shape" command generates a solid `Part.Shape` at user-specified resolution
- CNC tolerance ≤0.1mm is achieved via cell_size setting on the mesh export command

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `FRepField.evaluate_grid(pts)` | `core/frep/frep_field.py:30` | Batch SDF eval `(N,3) → (N,)` |
| `FRepField.bounding_box()` | `core/frep/frep_field.py:20` | AABB `→ (Vector, Vector)` |
| `ComposerField(a, b)` | `core/frep/frep_composer.py:31` | Binary boolean base class |
| `UnionField(a, b)` | `core/frep/frep_composer.py:35` | `min(a, b)` |
| `SubtractionField(a, b)` | `core/frep/frep_composer.py:59` | `max(a, -b)` |
| `IntersectionField(a, b)` | `core/frep/frep_composer.py:47` | `max(a, b)` |
| `DMObjectProxy.execute(fp)` | `core/dm_object.py:330` | Called on recompute |
| `DMViewProvider.attach(vobj)` | `core/dm_object.py:411` | Renderer creation |
| `DMViewProvider.updateData(fp, prop)` | `core/dm_object.py:454` | Data change handler |
| `get_render_mode()` | `core/dm_object.py:118` | Returns 0=Mesh, 1=PC, 2=RayMarch |
| `RENDER_MODE_*` constants | `core/dm_object.py:114-116` | Render mode enum |
| `DMMesher.mesh(field, cell_size)` | `core/dm_mesher.py:94` | Mesher contract `→ (verts, idx)` |
| `get_meshing_cell_size()` | `core/dm_object.py:63` | Cell size preference (mm) |
| `create_dm_object(name, shape_type, ...)` | `core/dm_object.py:545` | Factory function |
| `CommandDMBoolean.Activated()` | `commands/cmd_boolean.py:36` | Current BRep boolean logic |
| `dm_logger.*` | `core/dm_logger.py` | Logging (info/debug/warn/error) |

---

## Tier 1 — F-Rep Boolean Operations (Do First)

Rewrite the boolean commands to compose F-Rep field trees instead of
using BRep `Part.Shape` operations. This is the foundation — all other
refactoring depends on booleans preserving the SDF tree.

### X-001: Rewrite `CommandDMBoolean.Activated()` for F-Rep field composition

**File:** `commands/cmd_boolean.py` — replace `Activated()` method (lines 36–81)

**What:** When both selected objects have `ShapeType == "frep"`, compose their
`FRepField` objects using `ComposerField` subclasses instead of `Part.Shape`
boolean operations. Fall back to the existing BRep path for non-frep objects.

```python
def Activated(self):
    sel = FreeCADGui.Selection.getSelection()
    if len(sel) < 2:
        dm_logger.error(f"DM_{self.operation}: Select at least two objects.")
        return

    # Check if all objects are F-Rep
    all_frep = all(
        getattr(obj, "ShapeType", None) == "frep" for obj in sel
    )

    if all_frep:
        self._frep_boolean(sel)
    else:
        self._brep_boolean(sel)

def _frep_boolean(self, sel):
    """Compose FRepField trees for F-Rep objects."""
    from core.frep.frep_composer import UnionField, SubtractionField, IntersectionField
    from core.dm_object import create_dm_object

    _OP_CLASS = {
        "Add":          UnionField,
        "Subtract":     SubtractionField,
        "Intersection": IntersectionField,
    }

    try:
        # Extract fields from selected objects
        fields = []
        for obj in sel:
            proxy = getattr(obj, "Proxy", None)
            field = getattr(proxy, "FRepField", None) if proxy else None
            if field is None:
                dm_logger.error(
                    f"DM_{self.operation}: '{obj.Label}' has no FRepField."
                )
                return
            fields.append(field)

        # Compose fields left-to-right
        composer_cls = _OP_CLASS[self.operation]
        result_field = fields[0]
        for i in range(1, len(fields)):
            result_field = composer_cls(result_field, fields[i])

        # Create new frep object with composed field
        new_name = f"{self.operation}"
        result = create_dm_object(name=new_name, shape_type="frep")
        result.Proxy.FRepField = result_field
        result.touch()

        doc = FreeCAD.activeDocument()

        # Hide originals
        for obj in sel:
            if hasattr(obj, "ViewObject") and obj.ViewObject:
                obj.ViewObject.Visibility = False

        doc.recompute()
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(result)
        dm_logger.info(
            f"F-Rep {self.operation}: composed {len(fields)} fields."
        )
    except Exception as e:
        dm_logger.error(f"DM_{self.operation} (F-Rep) failed: {e}")

def _brep_boolean(self, sel):
    """Existing BRep boolean logic for non-frep objects."""
    # Verify all selected objects are DM objects
    for obj in sel:
        if not hasattr(obj, "ShapeType"):
            dm_logger.error(
                f"DM_{self.operation}: '{obj.Label}' is not a DM object."
            )
            return

    from core.dm_object import create_dm_object

    try:
        doc = FreeCAD.activeDocument()

        # Combine shapes using native Part operations
        shape_a = sel[0].Shape
        for i in range(1, len(sel)):
            shape_b = sel[i].Shape
            if self.operation == "Add":
                shape_a = shape_a.fuse(shape_b)
            elif self.operation == "Subtract":
                shape_a = shape_a.cut(shape_b)
            elif self.operation == "Intersection":
                shape_a = shape_a.common(shape_b)

        new_name = f"{self.operation}"
        result = create_dm_object(
            name=new_name,
            shape_type="boolean",
        )
        result.Shape = shape_a

        # Hide originals
        for obj in sel:
            if hasattr(obj, "ViewObject") and obj.ViewObject:
                obj.ViewObject.Visibility = False

        doc.recompute()
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(result)
        dm_logger.info(f"{self.operation} operation completed.")
    except Exception as e:
        dm_logger.error(f"DM_{self.operation} failed: {e}")
```

---

### X-002: Unit test for F-Rep boolean composition

**File:** `tests/test_frep_boolean.py` — new file

**What:** Python-only test (no FreeCAD runtime needed) that verifies `UnionField`,
`SubtractionField`, and `IntersectionField` work correctly with `SdfSphereField`
and `SdfBoxField` inputs. Uses the FreeCAD stub pattern from `tests/test_sdf_baker.py`.

```python
"""Test F-Rep boolean composition — runs without FreeCAD."""
import sys, os
from types import ModuleType
import numpy as np

# Minimal FreeCAD stubs
fc = ModuleType("FreeCAD")
class _Vec:
    def __init__(self, x=0, y=0, z=0): self.x=x; self.y=y; self.z=z
    def __add__(self, o): return _Vec(self.x+o.x, self.y+o.y, self.z+o.z)
    def __sub__(self, o): return _Vec(self.x-o.x, self.y-o.y, self.z-o.z)
    def __mul__(self, s): return _Vec(self.x*s, self.y*s, self.z*s)
    def __neg__(self): return _Vec(-self.x, -self.y, -self.z)
    @property
    def Length(self): return (self.x**2+self.y**2+self.z**2)**0.5
fc.Vector = _Vec
sys.modules["FreeCAD"] = fc

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.frep.sdf.sphere import SdfSphereField
from core.frep.sdf.box import SdfBoxField
from core.frep.frep_composer import UnionField, SubtractionField, IntersectionField


def test_union():
    """Union of two non-overlapping spheres returns min() of both."""
    s1 = SdfSphereField(center=fc.Vector(-5, 0, 0), radius=3.0)
    s2 = SdfSphereField(center=fc.Vector( 5, 0, 0), radius=3.0)
    u = UnionField(s1, s2)

    # Point at origin: equidistant from both spheres, inside neither
    val = u.evaluate(fc.Vector(0, 0, 0))
    assert val > 0, f"Origin should be outside union, got {val}"

    # Point inside s1
    val = u.evaluate(fc.Vector(-5, 0, 0))
    assert val < 0, f"Center of s1 should be inside union, got {val}"

    # Point inside s2
    val = u.evaluate(fc.Vector(5, 0, 0))
    assert val < 0, f"Center of s2 should be inside union, got {val}"

    # Bounding box should enclose both spheres
    mn, mx = u.bounding_box()
    assert mn.x <= -8.0, f"bbox min.x should be <= -8, got {mn.x}"
    assert mx.x >=  8.0, f"bbox max.x should be >= 8, got {mx.x}"

    print("PASS: test_union", flush=True)


def test_subtraction():
    """Subtraction: big sphere minus small sphere creates a hollow."""
    big = SdfSphereField(center=fc.Vector(0, 0, 0), radius=10.0)
    small = SdfSphereField(center=fc.Vector(0, 0, 0), radius=5.0)
    sub = SubtractionField(big, small)

    # Point at origin: inside small sphere, so outside the subtraction
    val = sub.evaluate(fc.Vector(0, 0, 0))
    assert val > 0, f"Origin should be outside subtraction, got {val}"

    # Point at radius 7.5: inside big, outside small → inside result
    val = sub.evaluate(fc.Vector(7.5, 0, 0))
    assert val < 0, f"Shell point should be inside subtraction, got {val}"

    # Point at radius 15: outside both → outside result
    val = sub.evaluate(fc.Vector(15, 0, 0))
    assert val > 0, f"Far point should be outside subtraction, got {val}"

    print("PASS: test_subtraction", flush=True)


def test_intersection():
    """Intersection of two overlapping spheres."""
    s1 = SdfSphereField(center=fc.Vector(-2, 0, 0), radius=5.0)
    s2 = SdfSphereField(center=fc.Vector( 2, 0, 0), radius=5.0)
    inter = IntersectionField(s1, s2)

    # Origin: inside both → inside intersection
    val = inter.evaluate(fc.Vector(0, 0, 0))
    assert val < 0, f"Origin should be inside intersection, got {val}"

    # Point far left: inside s1 only → outside intersection
    val = inter.evaluate(fc.Vector(-6, 0, 0))
    assert val > 0, f"Far left should be outside intersection, got {val}"

    print("PASS: test_intersection", flush=True)


def test_evaluate_grid_matches():
    """Batch evaluate_grid() must match pointwise evaluate()."""
    s1 = SdfSphereField(center=fc.Vector(0, 0, 0), radius=5.0)
    s2 = SdfSphereField(center=fc.Vector(3, 0, 0), radius=4.0)
    u = UnionField(s1, s2)

    pts = np.array([
        [0, 0, 0], [3, 0, 0], [10, 0, 0], [-5, 0, 0], [1.5, 2, 1]
    ], dtype=np.float32)

    batch = u.evaluate_grid(pts)
    for i in range(len(pts)):
        single = u.evaluate(fc.Vector(*pts[i]))
        assert abs(batch[i] - single) < 1e-5, (
            f"Mismatch at point {i}: batch={batch[i]}, single={single}"
        )

    print("PASS: test_evaluate_grid_matches", flush=True)


def test_chained_booleans():
    """Verify chained booleans: (A ∪ B) - C."""
    a = SdfBoxField(center=fc.Vector(0, 0, 0), size=fc.Vector(10, 10, 10))
    b = SdfSphereField(center=fc.Vector(8, 0, 0), radius=3.0)
    c = SdfSphereField(center=fc.Vector(0, 0, 0), radius=2.0)

    union_ab = UnionField(a, b)
    result = SubtractionField(union_ab, c)

    # Origin: inside A but also inside C → should be outside result
    val = result.evaluate(fc.Vector(0, 0, 0))
    assert val > 0, f"Origin should be outside (A∪B)-C, got {val}"

    # (4,0,0): inside A, outside C → inside result
    val = result.evaluate(fc.Vector(4, 0, 0))
    assert val < 0, f"(4,0,0) should be inside (A∪B)-C, got {val}"

    # (8,0,0): inside B, outside C → inside result
    val = result.evaluate(fc.Vector(8, 0, 0))
    assert val < 0, f"(8,0,0) should be inside (A∪B)-C, got {val}"

    print("PASS: test_chained_booleans", flush=True)


if __name__ == "__main__":
    try:
        test_union()
        test_subtraction()
        test_intersection()
        test_evaluate_grid_matches()
        test_chained_booleans()
        print("\nAll F-Rep boolean tests passed.", flush=True)
    except Exception as e:
        print(f"Test FAILED: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
```

**Depends on:** X-001

---

## Tier 2 — Decouple Meshing from Recompute

Skip expensive meshing in `execute()` when the render mode doesn't need triangles.
This makes GPU preview modes (ray march, formulaic SDF) respond instantly.

### X-003: Skip meshing in `execute()` for GPU render modes

**File:** `core/dm_object.py` — modify `DMObjectProxy.execute()` frep branch (lines 336–354)

**What:** When `get_render_mode()` returns `RENDER_MODE_RAY_MARCH` (2), skip the
`mesher.mesh()` call entirely. The view provider's `updateData()` will push the field
directly to the ray march renderer. Still set `fp.Shape = Part.Shape()` to trigger
the view provider update.

**Current code (lines 336–354):**
```python
if st == "frep":
    if hasattr(self, "FRepField") and self.FRepField is not None:
        from core.dm_mesher import get_active_mesher
        m_type = getattr(fp, "MeshingType", None)
        mesher = get_active_mesher(type_override=m_type)
        res = float(getattr(fp, "MeshingCellSize", getattr(self, "_final_resolution", get_meshing_cell_size())))
        result = mesher.mesh(self.FRepField, cell_size=res)
        if result is not None:
            self._frep_verts, self._frep_idx = result
        else:
            self._frep_verts = self._frep_idx = None
    else:
        dm_logger.debug(f"DMObjectProxy: Built NULL shape for {fp.Label} (expected if object is empty)")
        self._frep_verts = self._frep_idx = None

    # Setting fp.Shape triggers ViewProvider.updateData(fp, "Shape")
    fp.Shape = Part.Shape()
    return
```

**Replacement:**
```python
if st == "frep":
    if hasattr(self, "FRepField") and self.FRepField is not None:
        render_mode = get_render_mode()
        if render_mode in (RENDER_MODE_MESH, RENDER_MODE_POINT_CLOUD):
            # Mesh mode: generate triangles for Coin3D rendering
            from core.dm_mesher import get_active_mesher
            m_type = getattr(fp, "MeshingType", None)
            mesher = get_active_mesher(type_override=m_type)
            res = float(getattr(fp, "MeshingCellSize",
                        getattr(self, "_final_resolution",
                                get_meshing_cell_size())))
            result = mesher.mesh(self.FRepField, cell_size=res)
            if result is not None:
                self._frep_verts, self._frep_idx = result
            else:
                self._frep_verts = self._frep_idx = None
        else:
            # GPU preview mode: skip meshing, renderer reads
            # field directly via proxy.FRepField
            self._frep_verts = self._frep_idx = None
    else:
        dm_logger.debug(
            f"DMObjectProxy: Built NULL shape for {fp.Label}"
            " (expected if object is empty)"
        )
        self._frep_verts = self._frep_idx = None

    # Setting fp.Shape triggers ViewProvider.updateData(fp, "Shape")
    fp.Shape = Part.Shape()
    return
```

---

## Tier 3 — Mesh Export Command

Add an explicit command for meshing an F-Rep field into a `Part.Shape`,
decoupled from the render/recompute cycle.

### X-004: Create `DM_MeshToShape` command

**File:** `commands/cmd_mesh_export.py` — new file

**What:** A FreeCAD command that takes the selected frep object's `FRepField`,
meshes it using the active mesher at the configured cell size, converts the
triangles into a `Part.Shape` solid, and creates a new `Part::Feature` object
with that shape. The original frep object is kept (not hidden or deleted).

```python
"""
commands/cmd_mesh_export.py

Export an F-Rep field to a meshed Part.Shape solid.
"""

import FreeCAD
import FreeCADGui
import Part
import numpy as np
from core import dm_logger


class CommandMeshToShape:
    """Export a DM F-Rep object to a triangulated Part.Shape."""

    def GetResources(self):
        return {
            'MenuText': 'Mesh to Shape',
            'ToolTip': (
                'Generate a triangle mesh from the selected F-Rep object\n'
                'and create a Part.Shape solid.\n\n'
                'Resolution is controlled by the MeshingCellSize property\n'
                'on the object (or the global setting if unset).\n'
                'Use 0.1 mm for CNC-quality output.'
            ),
        }

    def IsActive(self):
        sel = FreeCADGui.Selection.getSelection()
        if len(sel) != 1:
            return False
        return getattr(sel[0], "ShapeType", None) == "frep"

    def Activated(self):
        sel = FreeCADGui.Selection.getSelection()
        if len(sel) != 1:
            dm_logger.error("Mesh to Shape: Select exactly one F-Rep object.")
            return

        obj = sel[0]
        proxy = getattr(obj, "Proxy", None)
        field = getattr(proxy, "FRepField", None) if proxy else None
        if field is None:
            dm_logger.error(
                f"Mesh to Shape: '{obj.Label}' has no FRepField."
            )
            return

        try:
            from core.dm_object import get_meshing_cell_size
            from core.dm_mesher import get_active_mesher

            # Use the object's own cell size if set, else global default
            cell_size = float(
                getattr(obj, "MeshingCellSize", get_meshing_cell_size())
            )
            m_type = getattr(obj, "MeshingType", None)
            mesher = get_active_mesher(type_override=m_type)

            dm_logger.info(
                f"Mesh to Shape: meshing '{obj.Label}' at "
                f"{cell_size:.2f} mm..."
            )
            result = mesher.mesh(field, cell_size=cell_size)
            if result is None:
                dm_logger.error("Mesh to Shape: mesher returned None.")
                return

            flat_verts, flat_idx = result

            # Convert flat triangle arrays to Part.Shape via Mesh
            shape = _triangles_to_shape(flat_verts, flat_idx)
            if shape is None or shape.isNull():
                dm_logger.error(
                    "Mesh to Shape: failed to build Part.Shape from mesh."
                )
                return

            # Create a new Part::Feature with the solid shape
            doc = FreeCAD.activeDocument()
            new_obj = doc.addObject("Part::Feature", f"{obj.Label}_Mesh")
            new_obj.Shape = shape

            # Style: match the orange DM look
            if hasattr(new_obj, "ViewObject") and new_obj.ViewObject:
                new_obj.ViewObject.ShapeColor = (1.0, 0.5, 0.0)
                try:
                    new_obj.ViewObject.DisplayMode = "Shaded"
                except Exception:
                    pass

            doc.recompute()
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(new_obj)
            n_tris = len(flat_idx) // 4
            dm_logger.info(
                f"Mesh to Shape: created '{new_obj.Label}' "
                f"({n_tris} triangles, cell_size={cell_size:.2f} mm)"
            )

        except Exception as e:
            dm_logger.error(f"Mesh to Shape failed: {e}")
            import traceback
            traceback.print_exc()


def _triangles_to_shape(flat_verts, flat_idx):
    """Convert Coin3D-format triangle arrays to a Part.Shape.

    Args:
        flat_verts: (N*3, 3) float32 — one row per triangle vertex
        flat_idx:   (N*4,) int32 — [v0, v1, v2, -1, ...] sentinels

    Returns:
        Part.Shape (solid if possible, shell otherwise), or None on failure.
    """
    import Mesh

    # Extract triangle vertex indices (skip -1 sentinels)
    idx = flat_idx.reshape(-1, 4)[:, :3]  # (N_tris, 3)

    # Build Mesh.Mesh from facets
    facets = []
    for tri in idx:
        v0 = flat_verts[tri[0]]
        v1 = flat_verts[tri[1]]
        v2 = flat_verts[tri[2]]
        facets.append([
            (float(v0[0]), float(v0[1]), float(v0[2])),
            (float(v1[0]), float(v1[1]), float(v1[2])),
            (float(v2[0]), float(v2[1]), float(v2[2])),
        ])

    mesh = Mesh.Mesh(facets)

    # Convert to Part.Shape via sewing
    shape = Part.Shape()
    shape.makeShapeFromMesh(mesh.Topology, 0.1)
    try:
        solid = Part.makeSolid(shape)
        return solid
    except Exception:
        dm_logger.debug("Mesh to Shape: could not make solid, returning shell")
        return shape


FreeCADGui.addCommand('DM_MeshToShape', CommandMeshToShape())
```

---

### X-005: Register `DM_MeshToShape` in toolbar and menu

**File:** `InitGui.py` — modify `Initialize()` method

**What:** Import the new command module and add `DM_MeshToShape` to the
"DM - Operations" toolbar and the "Direct Modeling" menu.

**Edit 1 — Add import (after line 64):**
```python
            import commands.cmd_mesh_export
```

**Edit 2 — Add to toolbar (line 88, before `'DM_OpenSketcher'`):**
```python
                'DM_MeshToShape',
```

**Edit 3 — Add to menu (line 106, before `'DM_OpenSketcher'`):**
```python
                'DM_MeshToShape',
```

**Depends on:** X-004

---

### X-006: Unit test for `_triangles_to_shape` helper

**File:** `tests/test_mesh_export.py` — new file

**What:** Test the triangle-to-shape conversion with a minimal tetrahedron mesh.
Uses the FreeCAD AppImage environment (see `freecad_env` skill) since it requires
`Part` and `Mesh` modules.

```python
"""Test mesh export helper — requires FreeCAD runtime."""
import sys, os

# Setup FreeCAD environment (see freecad_env skill)
FREECAD_LIB = os.environ.get("FREECAD_LIB", "/usr/lib/freecad-python3/lib")
if os.path.isdir(FREECAD_LIB):
    sys.path.insert(0, FREECAD_LIB)

try:
    import FreeCAD
    import Part
    import Mesh
except ImportError:
    print("SKIP: FreeCAD not available in this environment", flush=True)
    sys.exit(0)

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import numpy as np
from commands.cmd_mesh_export import _triangles_to_shape


def test_tetrahedron():
    """A 4-triangle tetrahedron should produce a valid solid."""
    verts = np.array([
        [0, 0, 0], [10, 0, 0], [5, 10, 0],  # tri 0
        [0, 0, 0], [5, 10, 0], [5, 5, 10],   # tri 1
        [10, 0, 0], [5, 5, 10], [5, 10, 0],  # tri 2
        [0, 0, 0], [5, 5, 10], [10, 0, 0],   # tri 3
    ], dtype=np.float32)
    idx = np.array([
        0, 1, 2, -1,
        3, 4, 5, -1,
        6, 7, 8, -1,
        9, 10, 11, -1,
    ], dtype=np.int32)

    shape = _triangles_to_shape(verts, idx)
    assert shape is not None, "Shape should not be None"
    assert not shape.isNull(), "Shape should not be null"
    print(f"Shape type: {shape.ShapeType}", flush=True)
    print("PASS: test_tetrahedron", flush=True)


if __name__ == "__main__":
    try:
        test_tetrahedron()
        print("\nAll mesh export tests passed.", flush=True)
    except Exception as e:
        print(f"Test FAILED: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
```

**Depends on:** X-004

---

## Agent Skills

See `.agents/skills/` for project-specific knowledge:

| Skill | Purpose |
|-------|---------|
| `dm_frep_boolean` | F-Rep field tree composition, how primitives create fields, data flow from proxy to renderer |
| `dm_renderer_architecture` | Coin3D rendering strategies, ShapeType branching, DMViewProvider flow |
| `dm_mesher_architecture` | Mesher class hierarchy, output format `(flat_verts, flat_idx)`, timer instrumentation |
| `dm_logging` | Logging conventions for the DM workbench |
| `dm_todo_format` | Task format and conventions for this project |

See `.agents/workflows/` for executable workflows:

| Workflow | Purpose |
|----------|---------|
| `/fix-task` | Fix a single task from this list by ID (e.g. `/fix-task X-003`) |
