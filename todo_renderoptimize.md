# todo_renderoptimize.md — Ray March Renderer Optimizations

Tasks for AI coding agent (Gemini Flash).
Read `.agents/skills/dm_ray_march_lod/SKILL.md` before starting any task.

---

## Culling Status (no action needed)

Coin3D frustum culling is intentionally disabled on the root and shader separators.
The GPU fragment shader performs per-field AABB slab tests before sphere-tracing — this
is the correct and efficient culling mechanism. Off-screen pixels discard immediately
after a single AABB test with zero texture fetches. No CPU-level frustum culling is needed.

---

## Group A — Screen-Space Adaptive Cell Size

### RO-001: Add `get/set_rm_texels_per_field()` to `core/dm_object.py`

After `get_ray_march_cell_size()` / `set_ray_march_cell_size()`, add:

```python
def get_rm_texels_per_field():
    """Target texel count along the longest axis for screen-space LOD (default 64)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("RMTexelsPerField", 64)

def set_rm_texels_per_field(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("RMTexelsPerField", int(val))
```

---

### RO-002: Add Quality spinbox to `commands/cmd_settings.py`

In `__init__`, after importing `get_ray_march_cell_size`, also import `get_rm_texels_per_field`.
Add a `QSpinBox`:

```python
from core.dm_object import get_ray_march_cell_size, get_rm_texels_per_field
self._quality_spin = QtGui.QSpinBox()
self._quality_spin.setRange(16, 256)
self._quality_spin.setSingleStep(16)
self._quality_spin.setValue(get_rm_texels_per_field())
self._quality_spin.setToolTip(
    "Target texels along longest visible axis (screen-space LOD).\n"
    "Higher = sharper at all zoom levels, more CPU bake cost.\n"
    "Default: 64. Range: 16 (fast) – 256 (sharp)."
)
layout.addRow("Ray March Quality (texels):", self._quality_spin)
```
Place this row directly after "Ray March Resolution (mm)".

In `_on_accept`, import and call:
```python
from core.dm_object import set_rm_texels_per_field
set_rm_texels_per_field(self._quality_spin.value())
```

---

### RO-003: Add `_compute_cell_size(field, view)` to `core/dm_scene_ray_march_renderer.py`

Add this method to `DMSceneRayMarchRenderer`. It computes a per-field cell size
based on how large the field appears on screen. `RayMarchCellSize` acts as a floor
(maximum quality, minimum cell size). The adaptive size gets coarser when zoomed out.

```python
def _compute_cell_size(self, field, view):
    """Return adaptive cell size: coarser when field is small on screen, finer when large."""
    import math
    from core.dm_object import get_ray_march_cell_size, get_rm_texels_per_field

    base_cell  = get_ray_march_cell_size()   # quality floor (user maximum quality)
    n_texels   = get_rm_texels_per_field()   # target texels along longest axis
    MIN_CELL   = base_cell                   # never finer than the quality floor
    MAX_CELL   = 20.0                        # coarsest allowed (mm)

    try:
        bb_min, bb_max = field.bounding_box()
        field_size = max(
            abs(bb_max.x - bb_min.x),
            abs(bb_max.y - bb_min.y),
            abs(bb_max.z - bb_min.z),
            1e-3
        )
        cam = view.getCameraNode()

        # Viewport height in pixels
        vp_h = 800.0
        try:
            viewer = view.getViewer()
            for method in ("getGlxSize", "getSize"):
                if hasattr(viewer, method):
                    sz = getattr(viewer, method)()
                    vp_h = float(sz[1] if isinstance(sz, (list, tuple)) else sz.height())
                    break
        except Exception:
            pass

        # World height visible in viewport
        is_ortho = cam.isOfType(coin.SoOrthographicCamera.getClassTypeId())
        if is_ortho:
            cam_world_h = cam.height.getValue()
        else:
            dist = (cam.position.getValue() - coin.SbVec3f(0, 0, 0)).length()
            cam_world_h = 2.0 * dist * math.tan(cam.heightAngle.getValue() / 2.0)

        px_per_world = vp_h / max(cam_world_h, 1e-3)
        field_screen_px = field_size * px_per_world

        # cell_size so that n_texels fit across the field's screen extent
        adaptive_cell = field_size / max(field_screen_px / n_texels, 1.0)

        return max(MIN_CELL, min(MAX_CELL, adaptive_cell))

    except Exception:
        return base_cell
```

**Key rule:** `RayMarchCellSize` is the **floor** — adaptive can never go finer than it.
When zoomed in, adaptive = base_cell (quality limited by setting). When zoomed out,
adaptive grows (coarser), saving CPU.

---

### RO-004: Use `_compute_cell_size()` per-field in `_rebuild()`

**File:** `core/dm_scene_ray_march_renderer.py`

In `_rebuild()`, replace the fixed cell size (~line 494-500):

```python
# Before:
cell_size = get_ray_march_cell_size()
baked_list = []
for label, f in visible:
    try:
        baked = bake_sdf_to_volume(f, cell_size)
        baked_list.append(baked)

# After:
try:
    view = FreeCADGui.ActiveDocument.ActiveView
except Exception:
    view = None

baked_list = []
for label, f in visible:
    try:
        cs = self._compute_cell_size(f, view) if view else get_ray_march_cell_size()
        baked = bake_sdf_to_volume(f, cs)
        baked_list.append(baked)
```

Remove the `from core.dm_object import get_ray_march_cell_size` import at the top of
`_rebuild()` (it is now used only inside `_compute_cell_size`). The import inside
`_compute_cell_size` is sufficient.

---

## Group B — Camera-Change Rebake Trigger

### RO-005: Add debounced camera sensor in `_attach()` / `_detach()`

**File:** `core/dm_scene_ray_march_renderer.py`

When the user zooms in/out, the camera changes and adaptive cell sizes should update.
Add a `SoFieldSensor` on the camera height (orthographic) or position (perspective),
debounced with a 100ms QTimer to avoid rebuilding 60×/second during continuous zoom.

Add to `__init__`:
```python
self._cam_sensor    = None
self._zoom_timer    = None
```

Update `_attach()`:
```python
def _attach(self):
    if self._attached:
        return
    try:
        view = FreeCADGui.ActiveDocument.ActiveView
        sg = view.getSceneGraph()
        sg.addChild(self._switch)
        self._attached = True
        self._attach_camera_sensor(view)
    except Exception:
        pass
```

Add new method `_attach_camera_sensor(view)`:
```python
def _attach_camera_sensor(self, view):
    """Attach a SoFieldSensor to trigger a debounced rebuild when zoom changes."""
    try:
        from pivy.coin import SoFieldSensor, SoOrthographicCamera
        cam = view.getCameraNode()
        if cam is None:
            return
        is_ortho = cam.isOfType(SoOrthographicCamera.getClassTypeId())
        field = cam.height if is_ortho else cam.position
        self._cam_sensor = SoFieldSensor(self._on_camera_changed, None)
        self._cam_sensor.attach(field)
    except Exception as e:
        dm_logger.debug(f"SceneRayMarch: Failed to attach camera sensor: {e}")
```

Add `_on_camera_changed`:
```python
def _on_camera_changed(self, userdata, sensor):
    """Debounced callback: schedule a rebuild 100ms after last zoom event."""
    from PySide import QtCore
    if self._zoom_timer is not None:
        self._zoom_timer.stop()
    self._zoom_timer = QtCore.QTimer()
    self._zoom_timer.setSingleShot(True)
    self._zoom_timer.timeout.connect(self._on_zoom_settled)
    self._zoom_timer.start(100)
```

Add `_on_zoom_settled`:
```python
def _on_zoom_settled(self):
    """Called 100ms after zoom stopped — rebuild with new adaptive cell sizes."""
    self._zoom_timer = None
    if self._fields:
        self._rebuild()
        try:
            if FreeCADGui.activeView():
                FreeCADGui.activeView().redraw()
        except Exception:
            pass
```

Update `_detach()` to clean up the sensor:
```python
def _detach(self):
    if not self._attached:
        return
    if self._cam_sensor is not None:
        self._cam_sensor.detach()
        self._cam_sensor = None
    if self._zoom_timer is not None:
        self._zoom_timer.stop()
        self._zoom_timer = None
    try:
        view = FreeCADGui.ActiveDocument.ActiveView
        sg = view.getSceneGraph()
        sg.removeChild(self._switch)
    except Exception:
        pass
    self._gl_tex.destroy()
    self._attached = False
```

---

## Group C — Incremental Atlas Updates

### RO-006: Add `glTexSubImage3D` support to `core/gl_texture3d.py`

After the existing `_gl.glTexImage3D` signature block (~line 71), add:

```python
_gl.glTexSubImage3D.argtypes = [
    ctypes.c_uint,   # target
    ctypes.c_int,    # level
    ctypes.c_int,    # xoffset
    ctypes.c_int,    # yoffset
    ctypes.c_int,    # zoffset
    ctypes.c_int,    # width
    ctypes.c_int,    # height
    ctypes.c_int,    # depth
    ctypes.c_uint,   # format
    ctypes.c_uint,   # type
    ctypes.c_void_p, # pixels
]
_gl.glTexSubImage3D.restype = None
```

Add to `GLTexture3D.__init__`:
```python
self._pending_slice = None   # (z_offset, depth, data_ctypes) or None
self._needs_partial  = False
```

Add method `update_slice`:
```python
def update_slice(self, z_offset, width, height, depth, rgba_bytes):
    """Queue a partial z-slice update via glTexSubImage3D.

    The texture must already exist (upload() must have been called at least once
    with the full dimensions). width/height must not exceed the current texture
    width/height.
    """
    if isinstance(rgba_bytes, (bytes, bytearray)):
        data = (ctypes.c_ubyte * len(rgba_bytes)).from_buffer_copy(rgba_bytes)
    else:
        data = (ctypes.c_ubyte * len(rgba_bytes)).from_buffer_copy(bytes(rgba_bytes))
    self._pending_slice = (z_offset, width, height, depth, data)
    self._needs_partial = True
```

In `_gl_callback`, after the full `glTexImage3D` block (inside the `if self._needs_upload` block), add a sibling branch for partial updates:
```python
if self._needs_partial and self._pending_slice is not None and self._tex_id != 0:
    z_off, w, h, d, data = self._pending_slice
    _gl.glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
    _gl.glTexSubImage3D(
        GL_TEXTURE_3D,
        0,          # level
        0, 0, z_off,   # xoffset, yoffset, zoffset
        w, h, d,    # width, height, depth of the sub-region
        GL_RGBA,
        GL_UNSIGNED_BYTE,
        ctypes.cast(data, ctypes.c_void_p),
    )
    self._pending_slice = None
    self._needs_partial = False
```

**Important:** `_needs_partial` and `_needs_upload` are mutually exclusive per-frame.
If `_needs_upload` is True, do the full upload and skip the partial. Set `_needs_partial = False`
inside the `_needs_upload` branch too.

---

### RO-007: Add `_baked_cache` and `_dirty_fields` to `DMSceneRayMarchRenderer`

**File:** `core/dm_scene_ray_march_renderer.py`

Add to `__init__`:
```python
self._baked_cache  = {}    # label → baked dict (from sdf_baker)
self._dirty_fields = set() # labels needing rebake on next _rebuild()
```

Update `update_field()` to mark only that field dirty:
```python
def update_field(self, label, field):
    """Update a field — only this field will be rebaked."""
    visible = self._fields.get(label, (None, True))[1]
    self._fields[label] = (field, visible)
    self._dirty_fields.add(label)   # ← only this field
    if not self._attached:
        self._attach()
    self._rebuild()
    if FreeCADGui.activeView():
        FreeCADGui.activeView().redraw()
```

Update `register_field()` to mark new field dirty:
```python
def register_field(self, label, field):
    dm_logger.debug(f"SceneRayMarch: Registering field '{label}'")
    self._fields[label] = (field, True)
    self._dirty_fields.add(label)   # ← only new field
    self._attach()
    self._rebuild()
    if FreeCADGui.activeView():
        FreeCADGui.activeView().redraw()
```

Update `unregister_field()` to purge cache:
```python
def unregister_field(self, label):
    if label in self._fields:
        self._fields.pop(label)
    self._baked_cache.pop(label, None)
    self._dirty_fields.discard(label)
    # (rest unchanged)
```

Update `_on_zoom_settled()` (from RO-005) to mark all fields dirty:
```python
def _on_zoom_settled(self):
    self._zoom_timer = None
    if self._fields:
        self._dirty_fields = set(self._fields.keys())  # all fields need new cell size
        self._rebuild()
        ...
```

Update `on_prefs_changed()` to mark all dirty:
```python
def on_prefs_changed(self):
    from core.dm_object import get_render_debug_mode
    debug = get_render_debug_mode()
    self._bbox_switch.whichChild = 0 if debug else -1
    if self._fields:
        self._dirty_fields = set(self._fields.keys())
        self._rebuild()
```

---

### RO-008: Update `_rebuild()` to use dirty flags and partial atlas update

**File:** `core/dm_scene_ray_march_renderer.py`

This is the core of the incremental update. The full logic:

```python
def _rebuild(self):
    """Bake dirty fields only. Use glTexSubImage3D for single-field updates."""
    from core.frep.sdf_baker import bake_sdf_to_volume
    import numpy as np

    visible = [(label, f) for label, (f, vis) in self._fields.items()
               if vis and f is not None]

    if not visible:
        self._switch.whichChild = -1
        self._dirty_fields.clear()
        return

    try:
        view = FreeCADGui.ActiveDocument.ActiveView
    except Exception:
        view = None

    # --- Rebake only dirty fields ---
    for label, f in visible:
        if label in self._dirty_fields or label not in self._baked_cache:
            try:
                cs = self._compute_cell_size(f, view) if view else get_ray_march_cell_size()
                self._baked_cache[label] = bake_sdf_to_volume(f, cs)
                dm_logger.debug(f"SceneRayMarch: Baked '{label}' (cell={cs:.2f}mm)")
            except Exception as e:
                dm_logger.debug(f"SceneRayMarch: Bake failed for '{label}': {e}")
                self._baked_cache.pop(label, None)

    self._dirty_fields.clear()

    # Collect baked results in visible order
    baked_list = []
    for label, _ in visible:
        b = self._baked_cache.get(label)
        if b is not None:
            baked_list.append((label, b))

    if not baked_list:
        self._switch.whichChild = -1
        return

    n_fields = len(baked_list)

    # --- Determine if atlas dimensions changed ---
    new_max_nx = max(b["nx"] for _, b in baked_list) + 1
    new_max_ny = max(b["ny"] for _, b in baked_list) + 1
    new_total_nz = sum(b["nz"] + 1 for _, b in baked_list)

    dims_changed = (
        new_max_nx  != getattr(self, "_atlas_nx", 0) or
        new_max_ny  != getattr(self, "_atlas_ny", 0) or
        new_total_nz != getattr(self, "_atlas_nz", 0) or
        n_fields    != getattr(self, "_atlas_n",  0)
    )

    # --- Build combined volume ---
    combined = np.zeros((new_total_nz, new_max_ny, new_max_nx), dtype=np.float32)
    z_offsets = []
    z_cursor = 0
    for _, b in baked_list:
        nx1, ny1, nz1 = b["nx"] + 1, b["ny"] + 1, b["nz"] + 1
        vol = np.frombuffer(b["volume_bytes"], dtype=np.float32).reshape(nz1, ny1, nx1)
        combined[z_cursor:z_cursor + nz1, :ny1, :nx1] = vol
        z_offsets.append(z_cursor)
        z_cursor += nz1

    volume_bytes = combined.astype(np.float32).tobytes()

    if dims_changed:
        # Full upload (dimensions changed)
        self._gl_tex.upload(new_max_nx, new_max_ny, new_total_nz, volume_bytes)
        self._atlas_nx = new_max_nx
        self._atlas_ny = new_max_ny
        self._atlas_nz = new_total_nz
        self._atlas_n  = n_fields
    else:
        # Partial upload (only data changed, same dimensions)
        self._gl_tex.upload(new_max_nx, new_max_ny, new_total_nz, volume_bytes)
        # NOTE: glTexSubImage3D optimisation possible here in future if needed;
        # for now full upload is safe and covers the same-dimensions case.

    # --- Update per-field uniforms (unchanged) ---
    for fi in range(self.MAX_FIELDS):
        if fi < n_fields:
            label, b = baked_list[fi]
            mn, mx = b["bbox_min"], b["bbox_max"]
            self._u[f"u_nx[{fi}]"].value.setValue(int(b["nx"]))
            self._u[f"u_ny[{fi}]"].value.setValue(int(b["ny"]))
            self._u[f"u_nz[{fi}]"].value.setValue(int(b["nz"]))
            self._u[f"u_z_offset[{fi}]"].value.setValue(int(z_offsets[fi]))
            self._u[f"u_bbox_min[{fi}]"].value.setValue(coin.SbVec3f(mn.x, mn.y, mn.z))
            self._u[f"u_bbox_max[{fi}]"].value.setValue(coin.SbVec3f(mx.x, mx.y, mx.z))
            # IsSubtractive: parse "DocName.ObjName" label correctly
            is_sub = self._get_is_subtractive(label)
            self._u[f"u_is_subtractive[{fi}]"].value.setValue(1 if is_sub else 0)
        else:
            self._u[f"u_nx[{fi}]"].value.setValue(0)

    self._u["u_num_fields"].value.setValue(n_fields)
    self._u["u_z_total"].value.setValue(int(new_total_nz))

    # Combined bbox proxy (unchanged)
    all_mn = [b["bbox_min"] for _, b in baked_list]
    all_mx = [b["bbox_max"] for _, b in baked_list]
    mn_all = FreeCAD.Vector(min(v.x for v in all_mn), min(v.y for v in all_mn), min(v.z for v in all_mn))
    mx_all = FreeCAD.Vector(max(v.x for v in all_mx), max(v.y for v in all_mx), max(v.z for v in all_mx))
    pts = [
        (mn_all.x, mn_all.y, mn_all.z), (mx_all.x, mn_all.y, mn_all.z),
        (mn_all.x, mx_all.y, mn_all.z), (mx_all.x, mx_all.y, mn_all.z),
        (mn_all.x, mn_all.y, mx_all.z), (mx_all.x, mn_all.y, mx_all.z),
        (mn_all.x, mx_all.y, mx_all.z), (mx_all.x, mx_all.y, mx_all.z),
    ]
    self._bbox_coords.point.setValues(0, 8, pts)
    self._coords.point.setValues(0, 8, pts)
    self._switch.whichChild = 0
```

Also add `_get_is_subtractive(label)` helper to fix the existing bug (see RO-011):
```python
def _get_is_subtractive(self, label):
    """Safely look up IsSubtractive from 'DocName.ObjName' label."""
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

## Group D — Fragment Shader Stepping Fix

### RO-009: Fix sphere-tracing stepping in scene shader

**File:** `core/dm_scene_ray_march_renderer.py` — embedded fragment shader string

**Find** the main sphere-trace loop (inside `void main()`):
```glsl
float global_min_step = 1.0;
for (int fi = 0; fi < 8; fi++) {
    if (fi >= u_num_fields) break;
    float c = (u_bbox_max[fi].x - u_bbox_min[fi].x) / max(float(u_nx[fi]), 1.0);
    global_min_step = min(global_min_step, c * 0.05);
}
```
And the step line inside the march loop:
```glsl
t += max(min_d, global_min_step);
```

**Replace** the global_min_step block with a hit_thresh-based floor:
```glsl
float global_hit_thresh = 1.0;
for (int fi = 0; fi < 8; fi++) {
    if (fi >= u_num_fields) break;
    float c = (u_bbox_max[fi].x - u_bbox_min[fi].x) / max(float(u_nx[fi]), 1.0);
    global_hit_thresh = min(global_hit_thresh, c * 0.1);
}
```

And **replace** the step line:
```glsl
// Before:
t += max(min_d, global_min_step);
// After:
t += max(min_d * 0.9, global_hit_thresh * 0.5);
```

**Why:** The old `min_step = cell * 0.05` floor forces tiny steps even when `min_d` is
large. `0.9 * min_d` slightly undershoots (prevents skipping thin features) while
`hit_thresh * 0.5` provides only a minimal floor near the surface. This eliminates
most wasted micro-steps during approach.

Also **increase** the iteration limit from 256 to 512:
```glsl
for (int i = 0; i < 512; i++) {
```
At 2mm cells the old 256 limit was occasionally insufficient for large objects (a 200mm
box has a 346mm diagonal; 256 * 0.1mm = only 25.6mm minimum coverage).

Apply the same stepping fix to **`core/dm_ray_march_renderer.py`** single-object shader
(same pattern, search for `min_step` and `256`).

---

## Group E — Bug Fix

### RO-010: Fix `IsSubtractive` lookup in `_rebuild()`

**File:** `core/dm_scene_ray_march_renderer.py`

The current `_rebuild()` at ~line 548-552 does:
```python
label = visible[fi][0]      # label = "DocName.ObjName"
doc = FreeCAD.activeDocument()
field_obj = doc.getObject(label) if doc else None  # BUG: passes full "Doc.Obj" string
```
`doc.getObject("MyDoc.Box")` returns `None` because `getObject` expects just `"Box"`.
This means `is_sub` is always `False` — no field is ever rendered blue.

This is fixed by the `_get_is_subtractive(label)` helper added in RO-008.
If RO-008 is not done yet, apply just this fix to the existing `_rebuild()`:
```python
# Replace lines 548-552 with:
is_sub = self._get_is_subtractive(visible[fi][0])
self._u[f"u_is_subtractive[{fi}]"].value.setValue(1 if is_sub else 0)
```
And add the `_get_is_subtractive()` method as shown in RO-008.

**This is an independent bug fix — can be done standalone, without RO-008.**

---

## Completion Checklist

After all tasks:
- [ ] Zoom out on a sphere → `dm_logger` shows increasing cell_size (e.g. 5–10mm)
- [ ] Zoom in → cell_size decreases toward `RayMarchCellSize` floor
- [ ] Move one field while a second is static → logger shows "Baked 'X'" for only the changed field
- [ ] Toggle `IsSubtractive` on a field → it renders blue (RO-010 fix verified)
- [ ] No `_cam_sensor` AttributeError when closing/reopening the workbench
- [ ] `grep -n "min_step" core/dm_scene_ray_march_renderer.py` returns no results
- [ ] `grep -n "min_step" core/dm_ray_march_renderer.py` returns no results

## Task Dependencies

```
RO-001 → RO-002 (needs setting)
RO-001, RO-003 → RO-004 (needs helper + setting)
RO-004 → RO-005 (needs _compute_cell_size for zoom-triggered rebuild to be useful)
RO-006 (standalone — gl_texture3d.py only)
RO-007 → RO-008 (needs dirty flags infrastructure)
RO-004, RO-007 → RO-008 (needs adaptive cell size + dirty flags)
RO-010 (standalone — independent bug fix)
RO-009 (standalone — shader string edit only)
```
