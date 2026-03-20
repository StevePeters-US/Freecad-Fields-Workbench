---
name: DM Renderer Refactor Pattern
description: Reference for the renderer/meshing separation refactor. Covers the new RayMarchCellSize setting, normal kernel fix, and complete removal of meshing settings from all locations except cmd_sdf_export.py.
---

# DM Renderer Refactor Pattern

Use this skill when working on renderer/meshing separation or modifying `dm_scene_ray_march_renderer.py`.

---

## Core Architectural Rule

**Meshing and rendering are completely separate concerns.**

```
GPU Ray March Renderer (real-time):
  SdfField → sdf_baker.bake_sdf_to_volume(field, ray_march_cell_size)
            → 3D texture upload (gl_texture3d.py)
            → sphere-trace fragment shader → screen pixels
  Settings: RayMarchCellSize (cmd_settings.py)

SDF-to-Shape Tool (explicit user action, cmd_sdf_export.py):
  SdfField → dm_mesher.get_active_mesher().mesh(field, cell_size, decimate, deduplicate)
            → Part.Shape
  Settings: owned entirely by cmd_sdf_export.py dialog — NOT global params
```

No global meshing settings exist after this refactor. The only global setting that
affects SDF appearance is `RayMarchCellSize` (in the renderer group).

---

## New Global Setting: `RayMarchCellSize`

**Location:** `core/dm_object.py` (with all other `get_*/set_*` settings)
**Param key:** `"RayMarchCellSize"`
**Default:** `2.0` mm
**Range for UI:** 0.1 – 20.0 mm

```python
def get_ray_march_cell_size():
    """Return the SDF baking cell size for GPU ray march renderer in mm (default 2.0)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("RayMarchCellSize", 2.0)

def set_ray_march_cell_size(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("RayMarchCellSize", float(val))
```

**Why 2.0 mm default:** At 2mm, a 100mm sphere = 50 cells across. Trilinear
interpolation at this resolution produces a smooth enough zero-crossing that
faceting is not visible at typical viewing distances. The old default of 10mm
(1/5 the resolution) produced obvious faceting.

---

## Normal Kernel Fix

**Location:** Fragment shader strings in `dm_ray_march_renderer.py` and
`dm_scene_ray_march_renderer.py`.

**Find:**
```glsl
float h = cell * 0.5;
```

**Replace with:**
```glsl
float h = cell * 2.0;
```

**Why this matters:**
Trilinear interpolation is C0 continuous (value-continuous) but NOT C1 (gradient-
discontinuous at cell boundaries). When `h = cell * 0.5`, the central-difference
gradient is sampled entirely within one cell, so the normal is nearly constant across
the cell face and jumps sharply at cell edges → classic flat-shaded faceting.

When `h = cell * 2.0`, the gradient samples span 4 cells, smoothing the discontinuity.
This does NOT reduce surface accuracy; it only affects the lighting normal.

This fix is complementary to the RayMarchCellSize fix. Both are needed:
- Finer cell size → smoother zero-crossing surface position
- Larger h → smoother shading even at the remaining resolution

---

## Removed Settings (do NOT re-add to dm_object.py)

These functions are deleted from `core/dm_object.py`:

| Function | Replacement |
|----------|-------------|
| `get_meshing_type()` | Parameter to `get_active_mesher(type_override=...)` |
| `get_meshing_cell_size()` | Local in `cmd_sdf_export.py` dialog |
| `get_decimate_enabled()` | `decimate=` parameter to `mesher.mesh()` |
| `get_deduplicate_enabled()` | `deduplicate=` parameter to `mesher.mesh()` |
| `get_curvature_threshold()` | Local in `cmd_sdf_export.py` dialog (future) |
| `get_sdf_storage_type()` | Deprecated alias — deleted |

These Sdf object properties are removed from `DMObjectProxy.__init__`:
- `MeshingCellSize` (PropertyFloat)
- `MeshingType` (PropertyEnumeration)
- `DecimateEnabled` (PropertyBool)
- `DeduplicateEnabled` (PropertyBool)

Only `IsSubtractive` remains.

---

## dm_mesher.py Interface After Refactor

```python
# get_active_mesher — no global fallback; caller always passes type
def get_active_mesher(type_override=None):
    idx = type_override if type_override is not None else 0
    # ... dispatch to MarchingCubesMesher, AdaptiveMCMesher, etc. ...

# mesh() — decimate/deduplicate passed by caller, not read from globals
class MarchingCubesMesher(DMMesher):
    def mesh(self, field, cell_size, decimate=True, deduplicate=True):
        ...
        if decimate:
            flat_verts, flat_idx = decimate_flat_tris(flat_verts, flat_idx)
        if deduplicate:
            flat_verts, flat_idx = deduplicate_verts(flat_verts, flat_idx)
        return flat_verts, flat_idx
```

---

## Settings Dialog After Refactor (`cmd_settings.py`)

**Kept controls:**
- Show Wireframe (checkbox)
- Enable Crash Logs (checkbox)
- Enable Performance Profiler (checkbox)
- Line Width (spinbox)
- Picking Radius (spinbox)
- Point Size (spinbox)
- Max Bounds (spinbox)
- Interactive Throttle (spinbox)
- Near Clip Distance (spinbox)
- **Ray March Resolution (spinbox)** ← NEW

**Removed controls:**
- Meshing Type (combobox) — moved to SDF-to-Shape dialog
- Meshing Cell Size (spinbox) — moved to SDF-to-Shape dialog
- Enable Decimation (checkbox) — moved to SDF-to-Shape dialog
- Curvature Threshold (spinbox) — moved to SDF-to-Shape dialog

---

## SDF-to-Shape Tool After Refactor (`cmd_sdf_export.py`)

Shows a dialog on activation. Dialog owns all meshing settings:

```
┌─ SDF to Shape — Settings ───────────┐
│ Cell Size (mm):        [ 2.00     ] │
│ Meshing Algorithm: [Marching Cubes] │
│ Decimate Flat Triangles:  [✓]       │
│ Deduplicate Vertices:     [✓]       │
│                   [ OK ] [ Cancel ] │
└─────────────────────────────────────┘
```

These settings are NOT persisted. Each invocation starts with sensible defaults.
No FreeCAD.ParamGet calls in cmd_sdf_export.py.

---

## Files to Read Before Editing

1. `core/dm_object.py` — settings functions + DMObjectProxy sdf init + refresh_all_dm_objects
2. `core/dm_scene_ray_march_renderer.py:430-460` — _bake_and_upload() method
3. `core/dm_ray_march_renderer.py:280-310` — update() method + shader string
4. `commands/cmd_settings.py` — full file (201 lines)
5. `commands/cmd_sdf_export.py` — full file (149 lines)
6. `tools/primitive_tool.py:1-30` — imports + cell size functions
7. `core/dm_mesher.py:230-250` — post-processing section
8. `core/dm_mesher.py:1320-1343` — get_active_mesher()

---

## Grep Commands for Verification

```bash
# After all tasks — should return zero results:
grep -r "get_meshing_cell_size\|get_meshing_type\|get_decimate_enabled\|get_deduplicate_enabled\|get_curvature_threshold" core/ tools/ commands/
grep -r "MeshingCellSize\|MeshingType\|DecimateEnabled\|DeduplicateEnabled" core/ tools/ commands/
grep -r "sdf_mesher" .

# Should return results in dm_object.py and cmd_settings.py only:
grep -r "get_ray_march_cell_size" core/ commands/

# Should return h = cell * 2.0 in both shader files:
grep -r "h = cell" core/
```
