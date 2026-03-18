---
name: DM Ray March LOD & Optimization Patterns
description: Reference for renderer optimizations: screen-space adaptive cell size, dirty-flag incremental baking, camera sensor, and fragment shader stepping. Use when implementing tasks from todo_renderoptimize.md.
---

# DM Ray March LOD & Optimization Patterns

---

## Architecture Overview

```
_rebuild() is triggered by:
  - register_field / update_field / unregister_field  (data change)
  - set_field_visible / gc_fields / on_prefs_changed  (visibility/prefs change)
  - _on_zoom_settled()                                (camera zoom change — NEW)

Each _rebuild():
  1. For each dirty field: call bake_sdf_to_volume(field, _compute_cell_size(field, view))
  2. Cache result in _baked_cache[label]
  3. Stack cached volumes into combined atlas (np.zeros)
  4. If atlas dims changed → glTexImage3D (full upload)
     Else → glTexImage3D (still full, same dims, safe fallback)
  5. Update per-field uniforms + bbox proxy coords
```

---

## Screen-Space Adaptive Cell Size

**Goal:** When a field is small on screen (zoomed out), use coarser cells (faster bake).
When large on screen (zoomed in), use finer cells (sharper surface).

**Formula:**
```python
px_per_world = vp_height_px / cam_world_height
field_screen_px = field_world_size * px_per_world   # pixels the field spans on screen
cell_size = field_world_size / (field_screen_px / n_texels)
          = cam_world_height * n_texels / vp_height_px   # simplifies to this
cell_size = clamp(cell_size, base_cell, MAX_CELL)
```

**Key insight:** `RayMarchCellSize` is a quality **floor** (minimum cell size = maximum
quality). The adaptive size only makes things *coarser* when zoomed out. It never
goes finer than the user's quality setting.

**Orthographic camera:** `cam_world_height = cam.height.getValue()`
**Perspective camera:** `cam_world_height = 2 * dist * tan(cam.heightAngle / 2)`

Where `dist` = `cam.position.getValue().length()` (distance from origin, or better:
distance from camera to field center — but origin approximation is fine).

**Getting viewport height:**
```python
vp_h = 800.0  # fallback
try:
    viewer = view.getViewer()
    for method in ("getGlxSize", "getSize"):
        if hasattr(viewer, method):
            sz = getattr(viewer, method)()
            vp_h = float(sz[1] if isinstance(sz, (list, tuple)) else sz.height())
            break
except Exception:
    pass
```
This pattern is used throughout the codebase (input_manager.py, dm_renderer.py).

---

## Camera Sensor Pattern

**Goal:** Trigger `_rebuild()` when the user zooms (but not on every pan/rotate frame).

**Coin3D `SoFieldSensor`:**
```python
from pivy.coin import SoFieldSensor, SoOrthographicCamera

cam = view.getCameraNode()
is_ortho = cam.isOfType(SoOrthographicCamera.getClassTypeId())
field = cam.height if is_ortho else cam.position  # zoom field

sensor = SoFieldSensor(callback_fn, userdata)
sensor.attach(field)
# To detach: sensor.detach()
```

**Callback signature:**
```python
def _on_camera_changed(self, userdata, sensor):
    # Called synchronously during Coin3D traversal — do NOT call _rebuild() here
    # Instead debounce with QTimer
    ...
```

**Debounce pattern (100ms):**
```python
from PySide import QtCore

def _on_camera_changed(self, userdata, sensor):
    if self._zoom_timer is not None:
        self._zoom_timer.stop()
    self._zoom_timer = QtCore.QTimer()
    self._zoom_timer.setSingleShot(True)
    self._zoom_timer.timeout.connect(self._on_zoom_settled)
    self._zoom_timer.start(100)

def _on_zoom_settled(self):
    self._zoom_timer = None
    if self._fields:
        self._dirty_fields = set(self._fields.keys())  # all fields need new cell size
        self._rebuild()
```

**Cleanup (in `_detach()`):**
```python
if self._cam_sensor is not None:
    self._cam_sensor.detach()
    self._cam_sensor = None
if self._zoom_timer is not None:
    self._zoom_timer.stop()
    self._zoom_timer = None
```

---

## Dirty Flag Pattern

**Goal:** Only rebake the field(s) that actually changed.

```python
# State added to __init__:
self._baked_cache  = {}    # label → baked dict from sdf_baker
self._dirty_fields = set() # labels that need rebaking

# Mark only the changed field dirty:
def update_field(self, label, field):
    self._fields[label] = (field, visible)
    self._dirty_fields.add(label)   # ← single field
    self._rebuild()

# Mark all dirty (zoom change, prefs change):
self._dirty_fields = set(self._fields.keys())

# In _rebuild(): only bake if dirty or not cached
for label, f in visible:
    if label in self._dirty_fields or label not in self._baked_cache:
        self._baked_cache[label] = bake_sdf_to_volume(f, cell_size)
self._dirty_fields.clear()
```

---

## IsSubtractive Label Bug

**Bug:** `_rebuild()` does `doc.getObject("DocName.ObjName")` — this always returns
`None` because `getObject()` expects just the object name, not the full label.

**Fix:**
```python
def _get_is_subtractive(self, label):
    """Parse 'DocName.ObjName' label and return IsSubtractive."""
    try:
        parts = label.split(".", 1)
        if len(parts) != 2:
            return False
        doc = FreeCAD.getDocument(parts[0])
        obj = doc.getObject(parts[1]) if doc else None
        return bool(getattr(obj, "IsSubtractive", False))
    except Exception:
        return False
```

---

## Fragment Shader Stepping Fix

**Problem:** `min_step = cell * 0.05` forces tiny steps near the surface and for very
small SDF values. This wastes iterations.

**Fix:**
```glsl
// Old:
float min_step = cell * 0.05;
t += max(min_d, min_step);

// New (no min_step global):
float hit_thresh = cell * 0.1;  // already computed per field
t += max(min_d * 0.9, hit_thresh * 0.5);
```

`0.9 * min_d` understeeps slightly to avoid overshooting thin features.
`hit_thresh * 0.5` is the surface-relative floor — only kicks in when `min_d` is
extremely small (right at the surface), and is proportional to cell size.

**Iteration count:** Increase from 256 to 512. At 2mm cells with a 200mm box
(diagonal ~346mm), 256 * minimum step can be insufficient.

---

## glTexSubImage3D Signature (for gl_texture3d.py)

```python
_gl.glTexSubImage3D.argtypes = [
    ctypes.c_uint,   # target  (GL_TEXTURE_3D)
    ctypes.c_int,    # level   (0)
    ctypes.c_int,    # xoffset (0)
    ctypes.c_int,    # yoffset (0)
    ctypes.c_int,    # zoffset (field's Z start in atlas)
    ctypes.c_int,    # width   (atlas width)
    ctypes.c_int,    # height  (atlas height)
    ctypes.c_int,    # depth   (field's Z depth)
    ctypes.c_uint,   # format  (GL_RGBA)
    ctypes.c_uint,   # type    (GL_UNSIGNED_BYTE)
    ctypes.c_void_p, # pixels
]
_gl.glTexSubImage3D.restype = None
```

**Usage rule:** Only call `glTexSubImage3D` if the texture already exists (tex_id != 0)
AND the total atlas dimensions have not changed. If dimensions changed, call the full
`glTexImage3D` first, then the sub-update is redundant.

---

## Files to Read Before Editing

1. `core/dm_scene_ray_march_renderer.py:486-578` — `_rebuild()` full method
2. `core/dm_scene_ray_march_renderer.py:396-440` — public API (register/update/unregister)
3. `core/dm_scene_ray_march_renderer.py:52-73` — `_attach()` / `_detach()`
4. `core/gl_texture3d.py:80-157` — `GLTexture3D` class (full)
5. `core/dm_object.py:115-120` — `get_ray_march_cell_size()` pattern to follow for new setting
6. `commands/cmd_settings.py:33-65` — settings dialog init pattern

---

## Invariants — Do NOT Break

- `_rebuild()` must always update ALL `MAX_FIELDS` uniform slots (set `u_nx[fi] = 0`
  for unused slots beyond n_fields) — the shader reads up to MAX_FIELDS.
- `_baked_cache` must be purged in `unregister_field()` — stale cache causes ghost objects.
- `_cam_sensor.detach()` must be called in `_detach()` — leaking a SoFieldSensor
  after the view is closed causes a crash.
- Camera sensor callback must NOT call `_rebuild()` directly — use QTimer debounce.
  Direct calls in Coin3D traversal callbacks can cause re-entrancy crashes.
