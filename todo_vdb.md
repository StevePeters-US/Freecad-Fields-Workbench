# Direct Modeling Workbench — OpenVDB Sparse Tree Migration

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

The SDF pipeline currently bakes analytical formulas to **dense** numpy grids for GPU rendering, and uses custom Python meshers (Marching Cubes, Surface Nets, Dual Contouring) for mesh export. This is memory-inefficient (a 100mm cube at 2mm cell size allocates the entire bounding box as a float32 array) and the meshers have topology bugs (cracks, degenerate triangles, winding issues).

OpenVDB replaces the dense bake path with **sparse level sets** that store only the narrow band around the surface. VDB also provides native boolean CSG operations (`csgUnion`, `csgDifference`, `csgIntersection`) and watertight meshing (`volumeToMesh`).

After all tasks are complete:
- `SdfField` subclasses gain a `to_vdb()` method that produces VDB FloatGrids
- The renderer uses VDB grids (with dense fallback when pyopenvdb is unavailable)
- Boolean operations use VDB CSG (sharp) and keep formula-based smooth booleans as an option
- `DMObjectProxy` caches VDB grids to avoid redundant baking
- A new `VDBMesher` class wraps `volumeToMesh` for the export tool
- A new "Install OpenVDB" button in DM Settings for easy setup

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `SdfField` | `core/sdf/sdf_field.py:3` | Abstract SDF base class |
| `SdfField.evaluate_grid()` | `core/sdf/sdf_field.py:34` | Batch field evaluation (used by to_vdb base) |
| `SdfField.bounding_box()` | `core/sdf/sdf_field.py:19` | AABB for grid sizing |
| `SdfBoxField` | `core/sdf/sdf/box.py:5` | Box SDF primitive |
| `SdfSphereField` | `core/sdf/sdf/sphere.py:5` | Sphere SDF primitive |
| `SdfCylinderField` | `core/sdf/sdf/cylinder.py:6` | Cylinder SDF primitive |
| `SdfExtrusionField` | `core/sdf/sdf_extrusion.py:7` | 2D profile extrusion |
| `bake_sdf_to_volume()` | `core/sdf/sdf_baker.py:13` | Current dense bake function |
| `DMObjectProxy` | `core/dm_object.py:133` | FreeCAD document object proxy |
| `DMSceneRayMarchRenderer._rebuild()` | `core/dm_scene_ray_march_renderer.py:~1250` | Calls bake, uploads to texture atlas |
| `_fold_union()` | `commands/cmd_boolean.py:233` | Union fold helper for booleans |
| `_recompose_boolean()` | `commands/cmd_boolean.py:244` | Recompose boolean tree on recompute |
| `DMMesher` | `core/dm_mesher.py:91` | Abstract mesher base class |
| `cmd_sdf_export.py` | `commands/cmd_sdf_export.py` | Export SDF to mesh/shape |

---

## Tier 0 — VDB Installation (New)

Provides a user-facing way to install the optional dependency.

### VDB-000: Add "Install OpenVDB" button to Settings

**File:** `commands/cmd_settings.py`

**What:** Add a status label and an installation button to the `_SettingsDialog`.

---

## Tier 1 — VDB Infrastructure (Do First)

Adds `to_vdb()` to the field hierarchy and provides a VDB-aware bake function. No callers change yet.

### VDB-001: Add `to_vdb()` base method to `SdfField`

**File:** `core/sdf/sdf_field.py` — append after `bounding_box()` (line 21)

**What:** Add a `to_vdb()` method that samples the field's `evaluate_grid()` over a padded bounding box and converts the dense result to a sparse VDB FloatGrid.

**Implementation:**
```python
def to_vdb(self, voxel_size=0.5, half_width=3.0):
    """Convert this SDF to an OpenVDB FloatGrid (sparse level set).

    Args:
        voxel_size:  Grid spacing in mm.
        half_width:  Narrow band width in voxels (default 3.0).

    Returns:
        openvdb.FloatGrid with gridClass=LEVEL_SET.

    Raises:
        ImportError if pyopenvdb is not installed.
    """
    import openvdb
    mn, mx = self.bounding_box()
    pad = half_width * voxel_size
    xs = np.arange(mn.x - pad, mx.x + pad + voxel_size, voxel_size)
    ys = np.arange(mn.y - pad, mx.y + pad + voxel_size, voxel_size)
    zs = np.arange(mn.z - pad, mx.z + pad + voxel_size, voxel_size)
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing='ij')
    pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]).astype(np.float32)

    vals = self.evaluate_grid(pts).astype(np.float32)
    vol = vals.reshape(len(xs), len(ys), len(zs))

    grid = openvdb.FloatGrid()
    grid.copyFromArray(vol)
    grid.transform = openvdb.createLinearTransform(voxel_size)
    grid.transform = grid.transform.deepCopy()
    grid.transform.translate((mn.x - pad, mn.y - pad, mn.z - pad))
    grid.gridClass = openvdb.GridClass.LEVEL_SET
    grid.name = type(self).__name__

    background = half_width * voxel_size
    grid.background = background
    grid.prune(tolerance=0.0)
    return grid
```

### VDB-002: Override `to_vdb()` in `SdfSphereField` with native constructor

**File:** `core/sdf/sdf/sphere.py` — append after `bounding_box()` (line 88)

**What:** Use `openvdb.tools.createLevelSetSphere()` for exact sphere level sets instead of sampling.

**Implementation:**
```python
def to_vdb(self, voxel_size=0.5, half_width=3.0):
    import openvdb
    center = self.center
    if self.placement is not None:
        center = self.placement.multVec(center)
    return openvdb.tools.createLevelSetSphere(
        radius=float(self.radius),
        center=(float(center.x), float(center.y), float(center.z)),
        voxelSize=float(voxel_size),
        halfWidth=float(half_width),
    )
```

### VDB-003: Add `vdb_grid_to_dense()` helper to `sdf_baker.py`

**File:** `core/sdf/sdf_baker.py` — append after `bake_sdf_to_volume()` (line 88)

**What:** Extracts the active region of a VDB grid to a dense numpy array matching the same output dict as `bake_sdf_to_volume()`, so the renderer can consume it without changes.

**Implementation:**
```python
def vdb_grid_to_dense(grid, voxel_size):
    """Extract VDB narrow band to dense numpy array for GL_R32F 3D texture upload.

    Returns dict with same keys as bake_sdf_to_volume():
        volume_bytes, nx, ny, nz, bbox_min, bbox_max, max_dist, cell_size
    """
    import math
    bbox = grid.evalActiveVoxelBoundingBox()
    imin, imax = bbox
    # Pad by 1 voxel on each side for interpolation safety
    imin = tuple(i - 1 for i in imin)
    imax = tuple(i + 2 for i in imax)
    shape = tuple(mx - mn for mn, mx in zip(imin, imax))

    dense = np.full(shape, grid.background, dtype=np.float32)
    grid.copyToArray(dense, imin)

    world_min = grid.transform.indexToWorld(imin)
    world_max = grid.transform.indexToWorld(imax)

    # OpenGL row-major convention: transpose (x,y,z) → (z,y,x)
    vol = dense.transpose(2, 1, 0)

    # nx, ny, nz are cell counts (one less than sample counts)
    nx = shape[0] - 1
    ny = shape[1] - 1
    nz = shape[2] - 1

    return {
        "volume_bytes": vol.tobytes(),
        "nx": nx, "ny": ny, "nz": nz,
        "bbox_min": FreeCAD.Vector(float(world_min[0]), float(world_min[1]), float(world_min[2])),
        "bbox_max": FreeCAD.Vector(float(world_max[0]), float(world_max[1]), float(world_max[2])),
        "max_dist": voxel_size * 8.0,
        "cell_size": voxel_size,
    }
```

### VDB-004: Add `bake_sdf_vdb()` wrapper to `sdf_baker.py`

**File:** `core/sdf/sdf_baker.py` — append after `vdb_grid_to_dense()` (after VDB-003)

**What:** Convenience function that calls `field.to_vdb()` then `vdb_grid_to_dense()`. Drop-in replacement for `bake_sdf_to_volume()` when VDB is available.

**Implementation:**
```python
def bake_sdf_vdb(field, cell_size, bbox_override=None):
    """Bake an SDF field via VDB sparse level set.

    Same return format as bake_sdf_to_volume(). Raises ImportError if
    pyopenvdb is not installed.

    Args:
        field:         Any SdfField subclass with to_vdb().
        cell_size:     Grid spacing in mm.
        bbox_override: Ignored for VDB path (VDB auto-clips to active region).

    Returns:
        dict with volume_bytes, nx, ny, nz, bbox_min, bbox_max, max_dist, cell_size
    """
    import time
    t0 = time.perf_counter()
    grid = field.to_vdb(voxel_size=cell_size)
    t1 = time.perf_counter()
    result = vdb_grid_to_dense(grid, cell_size)
    t2 = time.perf_counter()

    from core.dm_object import get_perf_profiler_enabled
    if get_perf_profiler_enabled():
        n_active = grid.activeVoxelCount()
        FreeCAD.Console.PrintMessage(
            f"[bake_vdb] grid={result['nx']}x{result['ny']}x{result['nz']} "
            f"active={n_active} cell={cell_size:.1f}mm | "
            f"to_vdb={1000*(t1-t0):.1f}ms  to_dense={1000*(t2-t1):.1f}ms  "
            f"total={1000*(t2-t0):.1f}ms\n"
        )
    return result
```

**Depends on:** VDB-001, VDB-003

---

## Tier 2 — Wire VDB Into Renderer

Replace the dense bake path in the renderer with VDB when available. Keep dense as fallback.

### VDB-005: Add `_has_openvdb()` helper to `sdf_baker.py`

**File:** `core/sdf/sdf_baker.py` — insert at top of file, after the existing imports (line 5)

**What:** A module-level function that checks whether `openvdb` is importable. Used by callers to decide which bake path to use.

**Implementation:**
```python
_openvdb_available = None

def has_openvdb():
    """Return True if pyopenvdb is importable. Caches the result."""
    global _openvdb_available
    if _openvdb_available is None:
        try:
            import openvdb
            _openvdb_available = True
        except ImportError:
            _openvdb_available = False
    return _openvdb_available
```

### VDB-006: Wire VDB bake into renderer `_rebuild()` CPU fallback path

**File:** `core/dm_scene_ray_march_renderer.py` — in `_rebuild()`, replace the CPU bake call (line ~1336)

**What:** In the CPU fallback bake path, try `bake_sdf_vdb()` first; fall back to `bake_sdf_to_volume()` if pyopenvdb is unavailable. The current code is:

```python
                else:
                    # CPU fallback bake
                    from core.sdf.sdf_baker import bake_sdf_to_volume
                    baked = bake_sdf_to_volume(f, cs, bbox_override=bbox_override)
                    baked["cell_size"] = cs
                    baked["gpu"] = False
                    self._baked_cache[label] = baked
                    _n_baked += 1
```

Replace with:

```python
                else:
                    # CPU bake: prefer VDB sparse path, fall back to dense numpy
                    from core.sdf.sdf_baker import has_openvdb
                    if has_openvdb():
                        from core.sdf.sdf_baker import bake_sdf_vdb
                        baked = bake_sdf_vdb(f, cs, bbox_override=bbox_override)
                    else:
                        from core.sdf.sdf_baker import bake_sdf_to_volume
                        baked = bake_sdf_to_volume(f, cs, bbox_override=bbox_override)
                    baked["cell_size"] = cs
                    baked["gpu"] = False
                    self._baked_cache[label] = baked
                    _n_baked += 1
```

**Depends on:** VDB-004, VDB-005

---

## Tier 3 — VDB Caching

Cache VDB grids on DMObjectProxy to avoid redundant baking during drag operations.

### VDB-007: Add VDB cache to `DMObjectProxy`

**File:** `core/dm_object.py` — in `DMObjectProxy.__init__()` (line 134), add cache attributes and methods

**What:** Add `_vdb_cache`, `_vdb_cache_voxel_size` attributes and a `get_vdb_grid()` method. Invalidate cache in `execute()`.

**Implementation:**

1. After line 145 (`obj.ShapeType = shape_type`), add:
```python
        self._vdb_cache = None
        self._vdb_cache_voxel_size = None
```

2. After the `_recompute_primitive_field()` method (line 391), add:
```python
    def get_vdb_grid(self, voxel_size=0.5):
        """Return a cached VDB grid for the SdfField at the given voxel_size."""
        field = getattr(self, "SdfField", None)
        if field is None:
            return None
        if self._vdb_cache is None or self._vdb_cache_voxel_size != voxel_size:
            try:
                self._vdb_cache = field.to_vdb(voxel_size)
                self._vdb_cache_voxel_size = voxel_size
            except (ImportError, NotImplementedError):
                return None
        return self._vdb_cache

    def invalidate_vdb_cache(self):
        """Clear cached VDB grid (call when SdfField parameters change)."""
        self._vdb_cache = None
        self._vdb_cache_voxel_size = None
```

3. In `execute()` (line 306), add invalidation at the top of the method body, after `try:`:
```python
            self.invalidate_vdb_cache()
```

---

## Tier 4 — VDB Booleans

Add VDB CSG operations alongside the existing formula-based composer.

### VDB-008: Add `to_vdb()` to `ComposerField` subclasses

**File:** `core/sdf/sdf_composer.py` — add `to_vdb()` to `UnionField`, `SubtractionField`, `IntersectionField`

**What:** Each composer field's `to_vdb()` calls `to_vdb()` on its children, then applies the VDB CSG operation. This makes the existing formula tree VDB-aware without changing any callers.

**Implementation:**

After `UnionField.bounding_box()` (line 61), add:
```python
    def to_vdb(self, voxel_size=0.5, half_width=3.0):
        import openvdb
        a = self.a.to_vdb(voxel_size, half_width)
        b = self.b.to_vdb(voxel_size, half_width)
        openvdb.tools.csgUnion(a, b)
        return a
```

After `IntersectionField.bounding_box()` (line 78), add:
```python
    def to_vdb(self, voxel_size=0.5, half_width=3.0):
        import openvdb
        a = self.a.to_vdb(voxel_size, half_width)
        b = self.b.to_vdb(voxel_size, half_width)
        openvdb.tools.csgIntersection(a, b)
        return a
```

After `SubtractionField.bounding_box()` (line 95), add:
```python
    def to_vdb(self, voxel_size=0.5, half_width=3.0):
        import openvdb
        a = self.a.to_vdb(voxel_size, half_width)
        b = self.b.to_vdb(voxel_size, half_width)
        openvdb.tools.csgDifference(a, b)
        return a
```

**Depends on:** VDB-001

### VDB-009: Keep smooth booleans as formula-based fallback

**File:** `core/sdf/sdf_composer.py` — add `to_vdb()` to `SmoothUnionField`, `SmoothSubtractionField`, `SmoothIntersectionField`

**What:** Smooth booleans have no native VDB equivalent. Their `to_vdb()` falls through to the base class implementation (samples `evaluate_grid()` into a VDB grid), preserving the smooth blend shape.

**Implementation:**

No code change needed — the base class `SdfField.to_vdb()` (from VDB-001) already works via `evaluate_grid()`. Smooth composer fields inherit `evaluate_grid()` which calls their smooth formula. The base `to_vdb()` samples that. Just verify this works by running the test.

Add a comment after each smooth composer class's last method to document this:

After `SmoothUnionField.bounding_box()` (line 118), add:
```python
    # to_vdb(): inherits base SdfField.to_vdb() which samples evaluate_grid().
    # This preserves the smooth blend — no native VDB smooth CSG exists.
```

After `SmoothSubtractionField.bounding_box()` (line 147), add:
```python
    # to_vdb(): inherits base SdfField.to_vdb() which samples evaluate_grid().
```

After `SmoothIntersectionField.bounding_box()` (line 176), add:
```python
    # to_vdb(): inherits base SdfField.to_vdb() which samples evaluate_grid().
```

**Depends on:** VDB-001

---

## Tier 5 — VDB Meshing (Optional Export Tool)

Adds a VDB-based mesher for the export command. Low priority — the primary output is SDF slicing for CNC curves, not mesh export.

### VDB-010: Add `VDBMesher` class to `dm_mesher.py`

**File:** `core/dm_mesher.py` — append after the last mesher class (end of file)

**What:** A mesher that calls `field.to_vdb()` then `openvdb.tools.volumeToMesh()` to produce watertight quads, triangulated for Coin3D. Falls back to `MarchingCubesMesher` if pyopenvdb is unavailable.

**Implementation:**
```python
class VDBMesher(DMMesher):
    """Mesher using OpenVDB's volumeToMesh. Requires pyopenvdb."""

    def mesh(self, field: SdfField, cell_size: float, decimate=False,
             deduplicate=True, adaptivity=0.0, **kwargs) -> tuple:
        from core.sdf.sdf_baker import has_openvdb
        if not has_openvdb():
            dm_logger.debug("VDBMesher: pyopenvdb not available, falling back to MarchingCubesMesher")
            return MarchingCubesMesher().mesh(field, cell_size, decimate=decimate,
                                              deduplicate=deduplicate, **kwargs)

        import openvdb
        mesh_timer.tick()

        mesh_timer.start("field_eval")
        grid = field.to_vdb(voxel_size=cell_size)
        mesh_timer.stop("field_eval")

        mesh_timer.start("mesh_build")
        points, quads = openvdb.tools.volumeToMesh(grid, isovalue=0.0,
                                                    adaptivity=adaptivity)

        # volumeToMesh returns quads (Mx4). Triangulate: each quad → 2 triangles.
        tris = []
        for q in quads:
            tris.append([q[0], q[1], q[2]])
            tris.append([q[0], q[2], q[3]])

        if not tris:
            mesh_timer.stop("mesh_build")
            return None

        tri_arr = np.array(tris, dtype=np.int32)
        flat_verts = np.array(points, dtype=np.float32)  # (V, 3)

        # Expand indexed triangles to flat per-triangle vertices for Coin3D
        tri_verts = flat_verts[tri_arr.ravel()].reshape(-1, 3)

        n_tris = len(tri_arr)
        base = np.arange(n_tris, dtype=np.int32) * 3
        tri_idx = np.stack([base, base + 1, base + 2], axis=1)
        sentinel = np.full((n_tris, 1), -1, dtype=np.int32)
        flat_idx = np.hstack([tri_idx, sentinel]).ravel()
        mesh_timer.stop("mesh_build")

        if deduplicate:
            mesh_timer.start("deduplicate")
            tri_verts, flat_idx = deduplicate_verts(tri_verts, flat_idx)
            mesh_timer.stop("deduplicate")

        return tri_verts, flat_idx
```

**Depends on:** VDB-001, VDB-005

---

## Tier 6 — Testing

### VDB-011: Add VDB roundtrip test

**File:** `tests/test_vdb_bake.py` — **new file**

**What:** Test that `to_vdb()` → `vdb_grid_to_dense()` produces output compatible with the renderer (same dict keys, reasonable dimensions, correct sign at known points).

**Implementation:**
```python
"""Test VDB bake roundtrip: field → VDB grid → dense → verify."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock FreeCAD
class _Vec:
    def __init__(self, x=0, y=0, z=0):
        self.x, self.y, self.z = x, y, z
    def __add__(self, o): return _Vec(self.x+o.x, self.y+o.y, self.z+o.z)
    def __sub__(self, o): return _Vec(self.x-o.x, self.y-o.y, self.z-o.z)
    def __mul__(self, s): return _Vec(self.x*s, self.y*s, self.z*s)
    @property
    def Length(self): return (self.x**2+self.y**2+self.z**2)**0.5

class _FC:
    Vector = _Vec
    class Units:
        class Quantity:
            pass
    class Console:
        @staticmethod
        def PrintMessage(s): pass
        @staticmethod
        def PrintWarning(s): pass
        @staticmethod
        def PrintError(s): pass
    class ParamGet:
        def __init__(self, *a): pass
        def GetBool(self, *a): return False
        def GetFloat(self, k, d): return d
    @staticmethod
    def ParamGet(path): return _FC.ParamGet()

sys.modules['FreeCAD'] = _FC

import numpy as np

def test_box_vdb_roundtrip():
    try:
        import openvdb
    except ImportError:
        print("SKIP: pyopenvdb not installed")
        return

    from core.sdf.sdf.box import SdfBoxField
    from core.sdf.sdf_baker import vdb_grid_to_dense

    box = SdfBoxField(_Vec(0,0,0), _Vec(20,20,20))
    grid = box.to_vdb(voxel_size=1.0)

    assert grid.activeVoxelCount() > 0, "Grid has no active voxels"

    result = vdb_grid_to_dense(grid, 1.0)
    assert "volume_bytes" in result
    assert "nx" in result and "ny" in result and "nz" in result
    assert result["nx"] > 0 and result["ny"] > 0 and result["nz"] > 0

    print(f"PASS: box VDB roundtrip — {grid.activeVoxelCount()} active voxels, "
          f"dense={result['nx']}x{result['ny']}x{result['nz']}")

def test_sphere_native_vdb():
    try:
        import openvdb
    except ImportError:
        print("SKIP: pyopenvdb not installed")
        return

    from core.sdf.sdf.sphere import SdfSphereField
    sphere = SdfSphereField(_Vec(0,0,0), 10.0)
    grid = sphere.to_vdb(voxel_size=0.5)

    assert grid.activeVoxelCount() > 0, "Grid has no active voxels"
    print(f"PASS: sphere native VDB — {grid.activeVoxelCount()} active voxels")

if __name__ == "__main__":
    test_box_vdb_roundtrip()
    test_sphere_native_vdb()
    print("All VDB tests passed.")
```

**Depends on:** VDB-001, VDB-002, VDB-003

---

## Agent Skills

See `.agents/skills/` for project-specific knowledge:

| Skill | Purpose |
|-------|---------|
| `dm_vdb_bake_backend` | VDB bake patterns: `to_vdb()` contract, `vdb_grid_to_dense()`, CSG, caching, import guard |
| `dm_openvdb_migration` | Full VDB migration architecture (broader scope, includes deleted-code list) |
| `dm_mesher_architecture` | Mesher output format `(flat_verts, flat_idx)` and timer instrumentation |

See `.agents/workflows/` for executable workflows:

| Workflow | Purpose |
|----------|---------|
| `/fix-task` | Fix a single task by ID |
