---
name: DM SDF-to-Mesh Tool Pattern
description: Reference for the SDF-to-Mesh conversion tool. Covers where meshing belongs (dedicated tool, NOT render pipeline), the mesher API, output format, and how to create a Part.Shape from mesh data.
---

# DM SDF-to-Mesh Tool Pattern

Meshing is an **explicit user action**, not a render-time side effect. The render pipeline
uses GPU ray marching for real-time visualization. When the user needs a triangle mesh
(for export, CNC, 3D printing, or FreeCAD Part operations), they invoke the SDF-to-Mesh tool.

---

## Architecture

```
Render pipeline (real-time):
  FRepField → GPU ray march shader → screen pixels
  NO meshing in this path.

SDF-to-Mesh tool (explicit action):
  FRepField → Mesher → (verts, indices) → Part.Shape
  User triggers this via command button.
```

---

## Mesher API

```python
from core.dm_mesher import get_active_mesher

mesher = get_active_mesher(type_override=None)
result = mesher.mesh(field, cell_size=1.0)
# Returns: (flat_verts, flat_idx) or None
#   flat_verts: (N, 3) float32 — vertex positions
#   flat_idx:   (M,) int32 — [v0, v1, v2, -1, ...] with -1 sentinels
```

### Mesher Types

| Value | Name | Class |
|-------|------|-------|
| 0 | Marching Cubes | `MarchingCubesMesher` |
| 1 | Adaptive MC | `AdaptiveMCMesher` |
| 2 | Surface Nets | `SurfaceNetsMesher` |
| 3 | Dual Contouring | `DualContouringMesher` |

---

## Converting Mesh to Part.Shape

```python
import Mesh
import Part

def triangles_to_shape(flat_verts, flat_idx):
    idx = flat_idx.reshape(-1, 4)[:, :3]  # (N_tris, 3)
    facets = []
    for tri in idx:
        v0, v1, v2 = flat_verts[tri[0]], flat_verts[tri[1]], flat_verts[tri[2]]
        facets.append([tuple(v0), tuple(v1), tuple(v2)])
    mesh = Mesh.Mesh(facets)
    shape = Part.Shape()
    shape.makeShapeFromMesh(mesh.Topology, 0.1)
    try:
        return Part.makeSolid(shape)
    except Exception:
        return shape
```

---

## Existing Command: `CommandSDFToShape`

**File:** `commands/cmd_sdf_export.py`

This command already implements the SDF-to-Mesh flow. It:
1. Gets the selected F-Rep object's `FRepField`
2. Calls `get_active_mesher().mesh(field, cell_size)`
3. Converts to `Part.Shape` via `Mesh.Mesh`
4. Creates a new `Part::Feature` in the document

This is the **correct** location for meshing. The render pipeline (`dm_object.py:execute()`)
should NOT call meshers.

---

## What to Remove from Render Pipeline

In `dm_object.py:execute()`, the mesh-mode branch (lines 378-390) calls
`get_active_mesher().mesh()` during document recompute. This must be removed so that
`execute()` only sets `fp.Shape = Part.Shape()` for frep objects, regardless of render mode.

The `DMViewProvider.updateData()` mesh handoff to `DMRenderer.update_frep_mesh()` also
becomes dead code once meshing is removed from execute().

---

## Files to Read Before Editing

1. `commands/cmd_sdf_export.py` — existing SDF-to-Shape command
2. `core/dm_object.py:369-407` — execute() with mesh-in-render-pipeline (to be cleaned)
3. `core/dm_mesher.py` — mesher implementations
4. `core/dm_renderer.py:192-240` — mesh rendering nodes (to be removed)
