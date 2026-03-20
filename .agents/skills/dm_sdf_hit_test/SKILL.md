---
name: DM SDF CPU Hit Test Pattern
description: Sphere-tracing ray march for CPU-side SDF hit detection. Required reading before implementing H-001..H-005 in todo_sdfhittest.md. Covers AABB slab test, ray_march signature, get_sdf_hit contract, and integration points.
---

# DM SDF CPU Hit Test Pattern

CPU-side sphere tracing for interactive surface snapping and selection against SDF SDF objects.
Used for workplane normal snap, point/curve placement on SDF surfaces, and LMB selection.

---

## Algorithm: Sphere Tracing

```
1. Normalize ray direction d.
2. AABB slab test against field.bounding_box() → get t_near, t_far. Return None on miss.
3. t = max(t_near, 0.0)   ← start at box entry, or at ray origin if inside box
4. Loop up to max_steps:
     if t > t_far: break (exited box, no hit)
     pos = ray_origin + d * t
     dist = field.evaluate(pos)
     if dist < surface_eps: HIT → compute normal = normalize(field.gradient(pos))
     t += max(dist, surface_eps * 0.1)   ← min step prevents stalling
5. Return None (no hit within box)
```

## AABB Slab Test

```python
t_near, t_far = -1e18, 1e18
axes = [
    (d.x, ray_origin.x, bb_min.x, bb_max.x),
    (d.y, ray_origin.y, bb_min.y, bb_max.y),
    (d.z, ray_origin.z, bb_min.z, bb_max.z),
]
for d_comp, o_comp, mn, mx in axes:
    if abs(d_comp) < 1e-10:
        if o_comp < mn or o_comp > mx:
            return None          # parallel and outside slab — miss
    else:
        t1 = (mn - o_comp) / d_comp
        t2 = (mx - o_comp) / d_comp
        if t1 > t2: t1, t2 = t2, t1
        t_near = max(t_near, t1)
        t_far  = min(t_far,  t2)
        if t_near > t_far:
            return None          # missed AABB
if t_far < 0:
    return None                  # AABB entirely behind ray
t = max(t_near, 0.0)
```

---

## `SdfField.ray_march()` Signature

```python
def ray_march(self, ray_origin, ray_direction, max_steps=128, surface_eps=0.5):
    """Returns (hit_point, hit_normal) as FreeCAD.Vector pair, or None."""
```

- `ray_direction` is normalized *inside* the method — callers don't need to pre-normalize.
- `surface_eps` default is **0.5 mm** — acceptable for interactive snapping; marching-cube cell
  size is 10–20 mm so sub-cell accuracy is not needed.
- Uses scalar `evaluate()` and `gradient()` only — NOT `evaluate_grid()`.
- Bounding box fallback (if `bounding_box()` raises `NotImplementedError`): `±50000 mm` cube.

---

## `ViewProjector.get_sdf_hit()` Contract

```python
def get_sdf_hit(self, event_dict, skip_objects=None):
    """
    Ray-march against all visible SDF objects in the document.
    Returns (hit_point, hit_normal, sdf_obj) for closest camera-depth hit, or None.
    """
```

- Iterates `FreeCAD.ActiveDocument.Objects`.
- Skips objects in `skip_objects` list (pass `[self.preview_obj]` to avoid self-hit).
- Detects SDF objects via `hasattr(obj.Proxy, 'SdfField')`.
- Checks `obj.ViewObject.Visibility` before marching.
- Selects closest hit by `(hit_pt - cam_pos).dot(ray_d_norm)`.

---

## Integration Points

| Location | What changes |
|----------|-------------|
| `core/sdf/sdf_field.py:72` | Add `ray_march()` to `SdfField` (H-001) |
| `core/view_projector.py:344` | Add `get_sdf_hit()` to `ViewProjector` (H-002) |
| `core/view_projector.py:241` | Add SDF hit to `get_mouse_plane_pt()` priority chain (H-003) |
| `tools/work_plane_tool.py:183` | Add SDF normal snap in `get_snapped_placement()` (H-004) |
| `core/input_manager.py:52` | Add LMB SDF selection when no tool active (H-005) |

### `get_mouse_plane_pt()` Priority Order (after H-003)

1. Bounded workplane hit (closest to camera, within grid bounds)
2. **SDF surface hit** ← new, always tested
3. NURBS geometry surface (only if `place_on_geometry=True`)
4. `working_plane` infinite fallback
5. Camera-facing plane

### `get_snapped_placement()` Flow (after H-004)

1. Try NURBS geometry via `get_geometry_info()` → surface normal snap
2. **Try SDF surface via `get_sdf_hit()` → surface normal snap** ← new
3. Fallback to working_plane or camera-facing placement

---

## Detecting SDF Objects

```python
# Check if a FreeCAD object is an SDF SDF object
if hasattr(obj, 'Proxy') and hasattr(obj.Proxy, 'SdfField'):
    field = obj.Proxy.SdfField   # may be None if not yet computed
```

`SdfField` is set in `tools/primitive_tool.py` (line ~70):
```python
proxy.SdfField = field   # where proxy = obj.Proxy
```

---

## Testing `ray_march()` Manually

```python
from core.sdf.sdf.sphere import SdfSphereField
import FreeCAD

s = SdfSphereField(FreeCAD.Vector(0, 0, 0), 50.0)

# Ray from above, pointing down — should hit top of sphere
hit = s.ray_march(FreeCAD.Vector(0, 0, 200), FreeCAD.Vector(0, 0, -1))
# Expected: hit_pt ≈ (0, 0, 50), hit_normal ≈ (0, 0, 1)
print(hit)

# Ray pointing away from sphere — should return None
miss = s.ray_march(FreeCAD.Vector(0, 0, 200), FreeCAD.Vector(0, 0, 1))
assert miss is None
```

---

## Coding Style (project-wide)

- `FreeCAD.Vector` for all 3D points and vectors; supports `+`, `*scalar`, `.dot()`, `.Length`, `.normalize()`
- `dm_logger.debug(f"...")` for all exception logging (import from `core`)
- No type annotations in method signatures (project convention)
- Method bodies kept short — delegate to helpers if over ~30 lines

---

## Files to Read Before Editing

1. `core/sdf/sdf_field.py` — `SdfField` base class; `evaluate()`, `gradient()`, `bounding_box()`
2. `core/sdf/sdf/sphere.py` — simplest complete SdfField; shows evaluate/gradient/bounding_box
3. `core/view_projector.py` — `get_geometry_info()` (lines 270–344) for NURBS pattern to follow
4. `tools/work_plane_tool.py:161` — `get_snapped_placement()` for H-004 insertion point
5. `core/input_manager.py:30` — `eventFilter()` for H-005 insertion point
