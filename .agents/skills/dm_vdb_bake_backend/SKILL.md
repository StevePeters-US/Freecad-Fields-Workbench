---
name: VDB Bake Backend
description: "Reference for replacing the dense numpy SDF bake path with OpenVDB sparse level sets. Required reading before implementing VDB-* tasks in todo_vdb.md."
---

# VDB Bake Backend

## Architecture Overview

The current SDF rendering pipeline bakes analytical fields to a dense 3D numpy array, then uploads to GPU as a float32 3D texture. The VDB migration replaces the dense array with an OpenVDB sparse level set, which stores only the narrow band (~3 voxels around the surface).

```
CURRENT:  SdfField.evaluate_grid() → dense numpy (nx*ny*nz) → GL_R32F 3D texture
NEW:      SdfField.to_vdb()        → VDB FloatGrid (sparse) → dense sub-region → GL_R32F 3D texture
```

The fragment shader is **unchanged** — it reads the same 3D texture. Only the bake path changes.

## pyopenvdb Import Guard

`pyopenvdb` is an **optional** dependency. The system package is `python3-openvdb` (apt). All VDB code must use a lazy import with graceful fallback:

```python
def _has_openvdb():
    try:
        import openvdb
        return True
    except ImportError:
        return False
```

When `pyopenvdb` is not available, the existing dense bake path (`sdf_baker.bake_sdf_to_volume()`) must remain functional as the fallback.

## Key API: SdfField.to_vdb()

Base class method on `SdfField`. Samples `evaluate()` into a VDB FloatGrid:

```python
def to_vdb(self, voxel_size=0.5, half_width=3.0):
    """Convert this SDF to an OpenVDB FloatGrid (sparse level set).
    
    Args:
        voxel_size:  Grid spacing in mm.
        half_width:  Narrow band width in voxels (default 3.0).
    
    Returns:
        openvdb.FloatGrid with gridClass=LEVEL_SET
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

Subclasses with native VDB constructors override this:
```python
# SdfSphereField
def to_vdb(self, voxel_size=0.5, half_width=3.0):
    import openvdb
    center = self.center
    if self.placement:
        center = self.placement.multVec(center)
    return openvdb.tools.createLevelSetSphere(
        radius=self.radius,
        center=(center.x, center.y, center.z),
        voxelSize=voxel_size, halfWidth=half_width)
```

## Key API: vdb_grid_to_dense()

Extracts the active region of a VDB grid to a dense numpy array for 3D texture upload:

```python
def vdb_grid_to_dense(grid, voxel_size):
    """Extract VDB narrow band to dense numpy array for GL_R32F 3D texture.
    
    Returns dict with same keys as bake_sdf_to_volume():
        volume_bytes, nx, ny, nz, bbox_min, bbox_max, max_dist
    """
    bbox = grid.evalActiveVoxelBoundingBox()
    imin, imax = bbox
    # Pad by 1 voxel on each side
    imin = tuple(i - 1 for i in imin)
    imax = tuple(i + 2 for i in imax)
    shape = tuple(mx - mn for mn, mx in zip(imin, imax))
    
    dense = np.full(shape, grid.background, dtype=np.float32)
    grid.copyToArray(dense, imin)
    
    world_min = grid.transform.indexToWorld(imin)
    world_max = grid.transform.indexToWorld(imax)
    
    # OpenGL row-major convention
    vol = dense.transpose(2, 1, 0)
    
    return {
        "volume_bytes": vol.tobytes(),
        "nx": shape[0] - 1, "ny": shape[1] - 1, "nz": shape[2] - 1,
        "bbox_min": FreeCAD.Vector(*world_min),
        "bbox_max": FreeCAD.Vector(*world_max),
        "max_dist": voxel_size * 8.0,
    }
```

## VDB Boolean CSG

```python
import openvdb

# IMPORTANT: CSG modifies grid_a in-place and steals grid_b's tree.
# Always deepCopy() before passing to CSG.
a = field_a.to_vdb(voxel_size)
b = field_b.to_vdb(voxel_size)
openvdb.tools.csgUnion(a, b)         # a ← a ∪ b
openvdb.tools.csgDifference(a, b)    # a ← a \ b
openvdb.tools.csgIntersection(a, b)  # a ← a ∩ b
```

## VDB Caching on DMObjectProxy

```python
class DMObjectProxy:
    _vdb_cache = None
    _vdb_cache_voxel_size = None

    def get_vdb_grid(self, voxel_size=0.5):
        if self._vdb_cache is None or self._vdb_cache_voxel_size != voxel_size:
            self._vdb_cache = self.SdfField.to_vdb(voxel_size)
            self._vdb_cache_voxel_size = voxel_size
        return self._vdb_cache

    def invalidate_vdb_cache(self):
        self._vdb_cache = None
        self._vdb_cache_voxel_size = None
```

Invalidate in `execute()` when parameters change.

## Output Format Compatibility

`vdb_grid_to_dense()` returns the **exact same dict format** as `bake_sdf_to_volume()`. The renderer `_rebuild()` method doesn't need to know which bake path produced the data — it just reads `volume_bytes`, `nx`, `ny`, `nz`, `bbox_min`, `bbox_max`.

## Files to Read Before Editing

1. `core/sdf/sdf_field.py` — base class where `to_vdb()` is added
2. `core/sdf/sdf_baker.py` — current dense bake, to be wrapped with VDB alternative
3. `core/dm_scene_ray_march_renderer.py:_rebuild()` (line ~1250) — calls bake, uploads to texture
4. `core/dm_object.py:DMObjectProxy` (line 133) — where VDB cache lives
5. `commands/cmd_boolean.py` — where CSG operations happen
