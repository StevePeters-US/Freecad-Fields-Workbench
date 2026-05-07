---
name: DM SDF Octree Cache
description: Interface contract, data layout, and mesher integration patterns for SdfOctreeCache — the hierarchical CPU evaluator that replaces np.meshgrid.
---

# DM SDF Octree Cache

Reference for implementing SP-001..SP-004 and MG-001..MG-005 in `todo_arch_refactor.md`.

## Why the Octree Exists

At 0.05mm over 1m³ a dense grid is 8 trillion voxels (32 TB). The octree culls subtrees where
`abs(sdf_center) > cell_half_diagonal`, guaranteeing no zero-crossing. Only cells near the
surface are evaluated. Memory scales with surface area, not volume.

## File

`core/sdf/sdf_octree.py` — created by SP-001.

## Class Interface

```python
class SdfOctreeCache:
    field:       SdfField       # the field being cached
    leaf_size:   float          # smallest cell size (mm)
    _origin:     np.ndarray(3)  # bounding-box min corner (float64)
    _leaf_step:  float          # == leaf_size
    _leaves:     dict           # (ix, iy, iz) → np.ndarray(8,) float32
```

### build(progress_callback=None)
Builds the octree from scratch. Evaluates `field.evaluate_grid()` on batches of cell centers
and corner points. Leaves are stored in `_leaves`. Must be called before any other method.

### query(point) → float
Trilinear interpolation from `_leaves`. Returns `+inf` if the point is in culled space
(not near the surface). `point` can be `FreeCAD.Vector` or `(3,)`-like.

### walk_leaves() → iterator
Yields `(origin, size, corners)` for every surface-crossing leaf:
- `origin`: `np.ndarray(3,)` — world position of the (0,0,0) corner
- `size`: `float` — leaf cell edge length (always `== leaf_size`)
- `corners`: `np.ndarray(8,)` float32 — SDF values in **Lorensen-Cline corner order**

### narrow_band_points(band_width=None) → np.ndarray(N,3)
Returns all leaf-level corner points where `abs(SDF) <= band_width`. Default band_width = 2×leaf_size.
Use this instead of `np.meshgrid` when a flat point cloud near the surface is needed.

## Lorensen-Cline Corner Order

The 8 corners of each leaf cell are always in this order (same as existing MarchingCubesMesher):

```
index  (dx, dy, dz)
  0      (0, 0, 0)   ← origin
  1      (1, 0, 0)
  2      (1, 1, 0)
  3      (0, 1, 0)
  4      (0, 0, 1)
  5      (1, 0, 0)   wait — see _OFF_X/Y/Z in dm_mesher.py for authoritative values
  6      (1, 1, 1)
  7      (0, 1, 1)
```

Authoritative corner offsets are `_OFF_X`, `_OFF_Y`, `_OFF_Z` at the top of `core/dm_mesher.py`.
The convenience constant `_CELL_OFFSETS = np.stack([_OFF_X, _OFF_Y, _OFF_Z], axis=1)` shape (8,3)
is added by MG-001.

## Mesher Integration Pattern

All rewritten meshers follow this pattern:

```python
from core.sdf.sdf_octree import SdfOctreeCache

def mesh(self, field, cell_size, ...):
    octree = SdfOctreeCache(field, leaf_size=cell_size)
    octree.build()                             # replaces np.meshgrid

    for origin, size, corners in octree.walk_leaves():
        # origin  : (3,) world position of corner 0
        # size    : float, edge length
        # corners : (8,) SDF values in Lorensen-Cline order
        ...                                    # existing cube-classification logic
```

## Cube Classification Reuse

The existing vectorized cube-classification in `MarchingCubesMesher` (lines 149–260 of
`dm_mesher.py`) can be reused without modification after MG-001, by batching all `walk_leaves()`
results into arrays:

```python
origins    = np.array([o for o,s,c in leaves])   # (M, 3)
corner_vals = np.array([c for o,s,c in leaves])  # (M, 8)
s          = leaves[0][1]                         # cell size (all equal)

neg        = corner_vals < 0                      # (M, 8)
cube_idx   = np.zeros(M, dtype=np.uint8)
for bit in range(8):
    cube_idx |= (neg[:, bit].astype(np.uint8) << bit)

active_mask = (cube_idx != 0) & (cube_idx != 255)
```

## Cull Condition

A cell is safe to cull (guaranteed no surface crossing) if:
```
abs(sdf_at_center) > size * sqrt(3) / 2
```
because `size * sqrt(3) / 2` is the half-diagonal of the cube. If the SDF at the center
is larger in magnitude than the furthest corner, no zero-crossing can exist inside.

## Key Invariant

`_leaves` only stores cells that **could** contain a surface crossing (failed the cull test).
`walk_leaves()` additionally filters to cells where the corners actually straddle the surface
(`corners.min() < 0 and corners.max() > 0`).

## Files to Read Before Editing

1. `core/sdf/sdf_field.py:35` — `evaluate_grid(points: np.ndarray(N,3)) → np.ndarray(N,)` signature
2. `core/dm_mesher.py:1-30` — `_OFF_X/Y/Z` corner order constants
3. `core/dm_mesher.py:113-250` — existing MarchingCubesMesher cube-classification logic to reuse
4. `todo_arch_refactor.md` — SP-001..SP-004, MG-001..MG-005 full task implementations
