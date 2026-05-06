---
name: OpenVDB Migration Patterns
description: "Patterns for the OpenVDB sparse level-set SDF pipeline (replaces all dense numpy/GLSL paths)."
---

# OpenVDB Migration Patterns

## Architecture: VDB-Only Pipeline

OpenVDB is the **sole** SDF evaluation and storage backend. There is no fallback to dense numpy grids, GLSL compute shaders, or hand-rolled marching cubes. `pyopenvdb` is a **required** dependency.

```
SdfField subclass (analytic evaluate() + to_vdb())
  → field.to_vdb(voxel_size) → OpenVDB FloatGrid (sparse level set)
    → [Preview]  vdb_grid_to_dense() → GL_R32F 3D texture → fragment shader ray march
    → [Export]   volumeToMesh() → Part.Shape
    → [File I/O] openvdb.write() → .vdb file
    → [Boolean]  csgUnion / csgIntersection / csgDifference
    → [Hit-test] grid.probeValue() for sphere tracing
```

**Key principle**: `SdfField.evaluate()` (single-point) is kept only for smooth-boolean sampling and edge cases. All bulk evaluation goes through VDB. `evaluate_grid()`, `to_glsl()`, and `GlslContext` are **deleted**.

## Key API Patterns

### SdfField.to_vdb() — Base Class Implementation

```python
def to_vdb(self, voxel_size=0.5, half_width=3.0):
    import openvdb
    mn, mx = self.bounding_box()
    pad = half_width * voxel_size
    # Build dense grid over padded bbox, then convert to sparse VDB
    xs = np.arange(mn.x - pad, mx.x + pad + voxel_size, voxel_size)
    ys = np.arange(mn.y - pad, mx.y + pad + voxel_size, voxel_size)
    zs = np.arange(mn.z - pad, mx.z + pad + voxel_size, voxel_size)
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing='ij')
    pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]).astype(np.float32)
    # evaluate() is called per-point via numpy broadcasting in subclasses
    vals = np.array([self.evaluate(FreeCAD.Vector(*p)) for p in pts], dtype=np.float32)
    vol = vals.reshape(len(xs), len(ys), len(zs))

    grid = openvdb.FloatGrid()
    grid.copyFromArray(vol)
    grid.transform = openvdb.createLinearTransform(voxel_size)
    grid.transform = grid.transform.deepCopy()
    grid.transform.translate((mn.x - pad, mn.y - pad, mn.z - pad))
    grid.gridClass = openvdb.GridClass.LEVEL_SET
    grid.name = type(self).__name__

    # Prune far-field voxels (keep only narrow band)
    background = half_width * voxel_size
    grid.background = background
    grid.prune(tolerance=0.0)
    return grid
```

### Native VDB Primitives (Override in Subclass)

```python
# SdfSphereField — uses OpenVDB's built-in exact level-set constructor
def to_vdb(self, voxel_size=0.5, half_width=3.0):
    import openvdb
    grid = openvdb.tools.createLevelSetSphere(
        radius=self.radius,
        center=(self.center.x, self.center.y, self.center.z),
        voxelSize=voxel_size,
        halfWidth=half_width
    )
    return grid
```

Override `to_vdb()` in any primitive that has a native VDB constructor. For others (box, cylinder), the base class implementation samples `evaluate()` and converts.

### VDB Boolean CSG

```python
import openvdb

# Union (modifies grid_a in place, grid_b becomes empty)
openvdb.tools.csgUnion(grid_a, grid_b)

# Intersection
openvdb.tools.csgIntersection(grid_a, grid_b)

# Difference (A - B)
openvdb.tools.csgDifference(grid_a, grid_b)
```

**Warning**: These operations modify `grid_a` in place and steal `grid_b`'s tree. Always call `to_vdb()` fresh on each operand — never reuse a grid that was passed to a CSG function.

### VDB Meshing

```python
import openvdb

# Basic meshing (returns vertices as Nx3, quads as Mx4 index arrays)
points, quads = openvdb.tools.volumeToMesh(grid, isovalue=0.0)

# Adaptive meshing (0.0 = no simplification, 1.0 = max)
points, quads = openvdb.tools.volumeToMesh(grid, isovalue=0.0, adaptivity=0.5)
```

**Important**: `volumeToMesh` returns **quads**, not triangles. Always triangulate for `Part.Shape`:
```python
# Each quad (a,b,c,d) → two triangles (a,b,c) + (a,c,d)
tris = []
for q in quads:
    tris.append([q[0], q[1], q[2]])
    tris.append([q[0], q[2], q[3]])
```

### VDB-to-Dense for Renderer (3D Texture)

```python
def vdb_grid_to_dense(grid, voxel_size):
    """Extract VDB narrow band to dense numpy array for GL_R32F 3D texture upload."""
    bbox = grid.evalActiveVoxelBoundingBox()
    imin, imax = bbox
    # Pad by 1 voxel
    imin = tuple(i - 1 for i in imin)
    imax = tuple(i + 2 for i in imax)
    shape = tuple(mx - mn for mn, mx in zip(imin, imax))

    dense = np.full(shape, grid.background, dtype=np.float32)
    grid.copyToArray(dense, imin)

    # Convert index-space bbox to world-space
    world_min = grid.transform.indexToWorld(imin)
    world_max = grid.transform.indexToWorld(imax)

    vol = dense.transpose(2, 1, 0)  # OpenGL row-major convention
    return {
        "volume_bytes": vol.tobytes(),
        "nx": shape[0], "ny": shape[1], "nz": shape[2],
        "bbox_min": FreeCAD.Vector(*world_min),
        "bbox_max": FreeCAD.Vector(*world_max),
        "max_dist": voxel_size * 8.0,
    }
```

The fragment shader (ray march visualization) is **unchanged** — it reads the 3D texture. Only the bake path that populates the texture changes.

### VDB-Based Hit Testing (ray_march replacement)

```python
def ray_march(self, ray_origin, ray_direction, grid=None, voxel_size=0.5, ...):
    if grid is None:
        grid = self.to_vdb(voxel_size)
    # Sphere trace using grid.probeValue() instead of self.evaluate()
    # Much faster — VDB tree traversal is C++ with O(1) average access
    for step in range(max_steps):
        idx = grid.transform.worldToIndex((pos.x, pos.y, pos.z))
        dist = grid.probeValue(idx)
        ...
```

## Accuracy Notes

- **0.05mm voxel size**: Sufficient for CNC machining tolerance (ISO 2768-m). Memory usage for a 100mm part: ~4M active voxels in narrow band (vs ~8B for dense grid). VDB stores this in ~50MB.
- **0.1mm voxel size**: Good for 3D printing. ~500K active voxels for same part.
- **half_width=3.0**: Standard for level sets. Narrower bands (2.0) save memory but risk artifacts in CSG. Wider (5.0) is safer for smooth booleans.
- **Dimensional accuracy**: VDB meshing error is bounded by `voxel_size / 2` in the worst case (staircase). With adaptivity=0.0, expect < `voxel_size` error.

## File I/O

```python
import openvdb

# Write
openvdb.write("/path/to/output.vdb", grids=[grid])

# Read
grids = openvdb.readAll("/path/to/input.vdb")
grid = grids[0][0]  # first grid (list of (grid, metadata) tuples)
```

## Caching Pattern

```python
# On DMObjectProxy — avoid re-baking on every render rebuild
class DMObjectProxy:
    _vdb_cache = None
    _vdb_cache_voxel_size = None

    def get_vdb_grid(self, voxel_size=0.5):
        if self._vdb_cache is None or self._vdb_cache_voxel_size != voxel_size:
            self._vdb_cache = self.SdfField.to_vdb(voxel_size)
            self._vdb_cache_voxel_size = voxel_size
        return self._vdb_cache

    def execute(self, fp):
        # ... update SdfField ...
        self._vdb_cache = None  # invalidate on parameter change
```

## What NOT to Do

- **Don't keep legacy dense/GLSL paths.** No fallbacks, no `try/except ImportError` guards. `pyopenvdb` is required.
- **Don't call `to_vdb()` during drag operations.** Use VDB cache for preview; only rebake when parameters actually change (dirty flag).
- **Don't store VDB grids in FreeCAD document properties.** They're not serializable. Cache on `DMObjectProxy._vdb_cache`.
- **Don't use `grid.fill()`** for large regions — defeats sparse storage.
- **Don't assume `volumeToMesh` returns triangles.** It returns quads. Always triangulate.

## Deleted Code (Post-Migration)

These files/methods no longer exist:
- `core/sdf/glsl_compiler.py` — deleted
- `core/gl_compute.py` — deleted
- `core/sdf/marching_cubes/` — deleted
- `SdfField.evaluate_grid()` — deleted
- `SdfField.gradient_grid()` — deleted
- `SdfField.curvature_grid()` — deleted
- `SdfField.to_glsl()` — deleted
- `bake_sdf_to_volume()` — replaced by `bake_sdf()` (VDB path)
- `MarchingCubesMesher` — replaced by `VDBMesher`
