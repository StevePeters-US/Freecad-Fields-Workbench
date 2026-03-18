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

---

## Group F — LOD Falloff Tuning

### RO-011: Soften adaptive cell size falloff in `_compute_cell_size()`

**File:** `core/dm_scene_ray_march_renderer.py`

**Problem:** The linear falloff in RO-003 is too aggressive. Shading visibly degrades
at moderate zoom-out because `h = cell * 2.0` in the normal kernel also scales up,
making lighting flatten quickly.

**Root cause:** Linear LOD: zoom out 2× → cell size 2×. Zoom out 4× → cell size 4×.
The surface patches get large quickly and each has nearly-constant normals → flat shading.

**Fix:** Replace the linear `adaptive_cell` return with a **sqrt power-law** plus a
**hard cap** on the maximum degradation ratio.

In `_compute_cell_size()`, find the final return line:
```python
        # cell_size so that n_texels fit across the field's screen extent
        adaptive_cell = field_size / max(field_screen_px / n_texels, 1.0)

        return max(MIN_CELL, min(MAX_CELL, adaptive_cell))
```

Replace with:
```python
        # Linear ratio: how many times coarser than base_cell would pure-linear LOD want?
        linear_cell  = field_size / max(field_screen_px / n_texels, 1.0)
        linear_ratio = max(linear_cell / base_cell, 1.0)   # >= 1.0 (never finer than base)

        # Sqrt falloff + hard cap:
        #   LOD_EXPONENT = 0.5 → zoom-out 4× only doubles cell size (was 4× with linear)
        #   MAX_RATIO    = 16  → worst-case = base * sqrt(16) = 4× base_cell
        LOD_EXPONENT = 0.5
        MAX_RATIO    = 16.0
        ratio = min(linear_ratio, MAX_RATIO) ** LOD_EXPONENT
        return base_cell * ratio
```

**Behaviour table (base_cell = 2mm):**

| Zoom-out factor | Linear (old) | Sqrt (new) |
|-----------------|-------------|------------|
| 1×              | 2mm         | 2mm        |
| 2×              | 4mm         | 2.8mm      |
| 4×              | 8mm         | 4mm        |
| 8×              | 16mm→10mm   | 5.7mm      |
| 16×+            | 20mm (cap)  | 8mm (cap)  |

The cap means even at extreme zoom-out, cells never exceed 4× base_cell (8mm at default 2mm).

**Verify:** Remove the now-redundant `MAX_CELL = 20.0` constant if it appears — the cap
is now enforced by `MAX_RATIO` instead.

---

---

## Group G — Frustum-Clipped Bake Region

The adaptive cell size (RO-003/011) still bakes the **entire field** even when only a
small corner is visible. Zooming into a 20mm region of a 500mm sphere still bakes 500mm³
of volume — a 15,625× waste. The real win is clipping the bake region to the view frustum.

### RO-012: Add `bbox_override` to `core/frep/sdf_baker.py`

**File:** `core/frep/sdf_baker.py`

Currently `bake_sdf_to_volume(field, cell_size)` always uses `field.bounding_box()`.
Add an optional `bbox_override` parameter to allow passing a pre-clipped region:

```python
def bake_sdf_to_volume(field, cell_size, bbox_override=None):
    """Bake SDF to 3D float32 volume.

    Args:
        field:        FRepField subclass.
        cell_size:    Grid spacing in mm.
        bbox_override: Optional (min_vec, max_vec) to bake a sub-region of the field.
                       If None, uses field.bounding_box(). Must be within field bounds.
    """
    if bbox_override is not None:
        mn_raw, mx_raw = bbox_override
    else:
        mn_raw, mx_raw = field.bounding_box()

    # rest of function unchanged — uses mn_raw, mx_raw as the bake region
```

The existing `_pad(lo, hi)` helper already adds a `cell_size` margin, so the bbox_override
region will still be padded correctly. No other changes needed in this file.

---

### RO-013: Add `_get_view_frustum_aabb(view)` to scene renderer

**File:** `core/dm_scene_ray_march_renderer.py`

Add this method to `DMSceneRayMarchRenderer`. It computes a world-space axis-aligned
bounding box for the camera frustum using camera parameters directly (no MVP matrix
inversion needed).

```python
def _get_view_frustum_aabb(self, view):
    """Return (min_vec, max_vec) world-space AABB of the camera view frustum.

    Returns None if computation fails (caller falls back to field.bounding_box()).
    """
    import numpy as np
    import math
    try:
        cam = view.getCameraNode()

        # Viewport aspect ratio
        vp_w, vp_h = 1.0, 1.0
        try:
            viewer = view.getViewer()
            for method in ("getGlxSize", "getSize"):
                if hasattr(viewer, method):
                    sz = getattr(viewer, method)()
                    vp_w = float(sz[0] if isinstance(sz, (list, tuple)) else sz.width())
                    vp_h = float(sz[1] if isinstance(sz, (list, tuple)) else sz.height())
                    break
        except Exception:
            pass
        aspect = vp_w / max(vp_h, 1.0)

        # Camera axes in world space via orientation quaternion
        rot = cam.orientation.getValue()
        right   = rot.multVec(coin.SbVec3f(1,  0,  0))
        up      = rot.multVec(coin.SbVec3f(0,  1,  0))
        forward = rot.multVec(coin.SbVec3f(0,  0, -1))  # -Z is forward in OpenGL

        r = np.array([right[0],   right[1],   right[2]])
        u = np.array([up[0],      up[1],      up[2]])
        f = np.array([forward[0], forward[1], forward[2]])
        p = np.array([cam.position.getValue()[0],
                      cam.position.getValue()[1],
                      cam.position.getValue()[2]])

        near = cam.nearDistance.getValue()
        far  = cam.farDistance.getValue()

        is_ortho = cam.isOfType(coin.SoOrthographicCamera.getClassTypeId())
        if is_ortho:
            hh = cam.height.getValue() / 2.0
            hw = hh * aspect
            # 8 corners of the orthographic frustum box
            corners = np.array([
                p + f*near + r*sx*hw + u*sy*hh
                for sx in (-1, 1) for sy in (-1, 1) for near in (near, far)
            ])
        else:
            # Perspective: frustum is a pyramid
            hh_near = math.tan(cam.heightAngle.getValue() / 2.0) * near
            hw_near = hh_near * aspect
            hh_far  = math.tan(cam.heightAngle.getValue() / 2.0) * far
            hw_far  = hh_far  * aspect
            corners = np.array([
                p + f*near + r*sx*hw_near + u*sy*hh_near
                for sx in (-1, 1) for sy in (-1, 1)
            ] + [
                p + f*far  + r*sx*hw_far  + u*sy*hh_far
                for sx in (-1, 1) for sy in (-1, 1)
            ])

        aabb_min = corners.min(axis=0)
        aabb_max = corners.max(axis=0)
        return (
            FreeCAD.Vector(float(aabb_min[0]), float(aabb_min[1]), float(aabb_min[2])),
            FreeCAD.Vector(float(aabb_max[0]), float(aabb_max[1]), float(aabb_max[2])),
        )
    except Exception as e:
        dm_logger.debug(f"SceneRayMarch: _get_view_frustum_aabb failed: {e}")
        return None
```

---

### RO-014: Clip bake region + adapt cell size to visible subregion in `_rebuild()`

**File:** `core/dm_scene_ray_march_renderer.py`

This is the core of the frustum-clipped baking. In `_rebuild()`, for each field:

1. Get the view frustum AABB
2. Intersect with the field's full AABB → visible subregion
3. Skip baking if intersection is empty (field off-screen)
4. Pass the subregion as `bbox_override` to `bake_sdf_to_volume`
5. Adapt cell size to the **visible subregion** size (not the full field size)

Replace the per-field bake block inside `_rebuild()`:

```python
frustum = self._get_view_frustum_aabb(view) if view else None

baked_list = []
for label, f in visible:
    if label in self._dirty_fields or label not in self._baked_cache:
        try:
            # Compute visible subregion
            bbox_override = None
            if frustum is not None:
                try:
                    f_min, f_max = f.bounding_box()
                    fr_min, fr_max = frustum
                    clip_min = FreeCAD.Vector(
                        max(f_min.x, fr_min.x),
                        max(f_min.y, fr_min.y),
                        max(f_min.z, fr_min.z),
                    )
                    clip_max = FreeCAD.Vector(
                        min(f_max.x, fr_max.x),
                        min(f_max.y, fr_max.y),
                        min(f_max.z, fr_max.z),
                    )
                    # Non-empty intersection?
                    if (clip_min.x < clip_max.x and
                        clip_min.y < clip_max.y and
                        clip_min.z < clip_max.z):
                        bbox_override = (clip_min, clip_max)
                    else:
                        dm_logger.debug(f"SceneRayMarch: '{label}' off-screen, skipping bake")
                        continue   # skip — not visible
                except Exception:
                    pass  # fall through to full-field bake

            # Cell size adapted to visible subregion (not full field)
            cs = self._compute_cell_size_for_region(bbox_override, view) if bbox_override else self._compute_cell_size(f, view)
            baked = bake_sdf_to_volume(f, cs, bbox_override=bbox_override)
            self._baked_cache[label] = baked
            dm_logger.debug(
                f"SceneRayMarch: Baked '{label}' "
                f"({'frustum-clipped' if bbox_override else 'full'}, cell={cs:.2f}mm)"
            )
        except Exception as e:
            dm_logger.debug(f"SceneRayMarch: Bake failed for '{label}': {e}")
            self._baked_cache.pop(label, None)
            continue
    cached = self._baked_cache.get(label)
    if cached is not None:
        baked_list.append((label, cached))
```

Also add the helper `_compute_cell_size_for_region(bbox_override, view)` that takes the
already-computed clip region instead of calling `field.bounding_box()` again:

```python
def _compute_cell_size_for_region(self, bbox_override, view):
    """Compute adaptive cell size for a pre-clipped (min, max) region."""
    import math
    from core.dm_object import get_ray_march_cell_size, get_rm_texels_per_field

    base_cell = get_ray_march_cell_size()
    n_texels  = get_rm_texels_per_field()
    LOD_EXPONENT = 0.5
    MAX_RATIO    = 16.0

    try:
        clip_min, clip_max = bbox_override
        region_size = max(
            abs(clip_max.x - clip_min.x),
            abs(clip_max.y - clip_min.y),
            abs(clip_max.z - clip_min.z),
            1e-3
        )
        cam = view.getCameraNode()
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
        is_ortho = cam.isOfType(coin.SoOrthographicCamera.getClassTypeId())
        if is_ortho:
            cam_world_h = cam.height.getValue()
        else:
            dist = (cam.position.getValue() - coin.SbVec3f(0, 0, 0)).length()
            cam_world_h = 2.0 * dist * math.tan(cam.heightAngle.getValue() / 2.0)

        px_per_world  = vp_h / max(cam_world_h, 1e-3)
        region_screen_px = region_size * px_per_world
        linear_cell   = region_size / max(region_screen_px / n_texels, 1.0)
        linear_ratio  = max(linear_cell / base_cell, 1.0)
        ratio = min(linear_ratio, MAX_RATIO) ** LOD_EXPONENT
        return base_cell * ratio
    except Exception:
        return get_ray_march_cell_size()
```

**Key behaviour:** If a 500mm sphere is visible only as a 20mm window in the viewport,
the bake region is 20mm³ (not 500mm³) and the cell size adapts to that 20mm region.
This is a ~15,000× reduction in bake volume for that scenario.

---

### RO-015: Extend camera sensor to cover pan and rotate

**File:** `core/dm_scene_ray_march_renderer.py`

RO-005 adds a `SoFieldSensor` on `cam.height` (zoom only). Frustum-clipped baking
(RO-012/013/014) also needs to rebake on **pan** (`cam.position` changes) and **rotate**
(`cam.orientation` changes), since the visible subregion changes whenever the camera moves.

Replace the `SoFieldSensor` approach in `_attach_camera_sensor()` with a `SoNodeSensor`
on the camera node itself. `SoNodeSensor` fires on any field change of the node.

```python
def _attach_camera_sensor(self, view):
    """Attach a SoNodeSensor to trigger debounced rebuild on any camera change."""
    try:
        from pivy.coin import SoNodeSensor
        cam = view.getCameraNode()
        if cam is None:
            return
        # SoNodeSensor fires on any field change (height, position, orientation, etc.)
        self._cam_sensor = SoNodeSensor(self._on_camera_changed, None)
        self._cam_sensor.attach(cam)
    except Exception as e:
        dm_logger.debug(f"SceneRayMarch: Failed to attach camera sensor: {e}")
```

The `_on_camera_changed` debounce and `_on_zoom_settled` methods from RO-005 are unchanged.

**Note on performance:** `SoNodeSensor` fires very frequently during orbit/pan. The 100ms
debounce in `_on_camera_changed` (QTimer.singleShot) handles this — baking only triggers
100ms after the last camera movement stops, not on every intermediate frame.

---

## Group H — Edge-Sharpening Normals

### RO-016: Adaptive normal kernel width near edges

**File:** `core/dm_scene_ray_march_renderer.py` and `core/dm_ray_march_renderer.py`
— fragment shader string in each file.

**Problem:** The current normal kernel uses a fixed `h = cell * 2.0`. This is good for
smooth surfaces (avoids C0 step artifacts), but it over-smooths normals at sharp edges —
convex/concave corners of boxes, cylinders, blended primitives. Edge highlights look
"mushy" when they should be crisp.

**Root cause:** A large `h` averages the gradient across a wide neighbourhood. On a smooth
surface this is fine. At a sharp edge (where the SDF surface curves within one `h` radius),
the averaged gradient points away from the actual edge normal — the highlight shifts and
blurs.

**Fix: Curvature-guided adaptive `h`**

The Laplacian of the SDF is a good curvature proxy — it is ~0 on flat surfaces and large
near edges (sign-changes at concave/convex corners). Use it to blend `h` between coarse
(smooth) and fine (sharp):

```glsl
vec3 sdf_normal_field(int fi, vec3 p) {
    float cell = (u_bbox_max[fi].x - u_bbox_min[fi].x) / max(float(u_nx[fi]), 1.0);

    // Sample SDF at the hit point
    float d0 = sample_sdf_field(fi, p);

    // Coarse-scale Laplacian: estimates local curvature
    // lap ≈ (d(p+h) + d(p-h) - 2*d) / h²  along each axis (summed → scalar)
    float h_lap = cell * 1.0;
    float lap = abs(
        (sample_sdf_field(fi, p + vec3(h_lap, 0, 0)) +
         sample_sdf_field(fi, p - vec3(h_lap, 0, 0)) - 2.0 * d0) +
        (sample_sdf_field(fi, p + vec3(0, h_lap, 0)) +
         sample_sdf_field(fi, p - vec3(0, h_lap, 0)) - 2.0 * d0) +
        (sample_sdf_field(fi, p + vec3(0, 0, h_lap)) +
         sample_sdf_field(fi, p - vec3(0, 0, h_lap)) - 2.0 * d0)
    ) / (h_lap * h_lap);

    // Edge strength: normalise by a threshold (~1/cell — sharp edge has lap ≈ 1/cell)
    float edge = clamp(lap * cell * 2.0, 0.0, 1.0);

    // Blend h: smooth surfaces → h_coarse; near edges → h_fine
    float h_fine   = cell * 0.5;
    float h_coarse = cell * 2.0;
    float h = mix(h_coarse, h_fine, edge);

    // Standard tetrahedral gradient (4 samples)
    vec2 k = vec2(1.0, -1.0);
    vec3 g = k.xyy * sample_sdf_field(fi, p + k.xyy*h) +
             k.yyx * sample_sdf_field(fi, p + k.yyx*h) +
             k.yxy * sample_sdf_field(fi, p + k.yxy*h) +
             k.xxx * sample_sdf_field(fi, p + k.xxx*h);

    float len2 = dot(g, g);
    return (len2 > 1e-10) ? g * inversesqrt(len2) : vec3(0.0, 1.0, 0.0);
}
```

**Cost:** 6 additional `sample_sdf_field` calls per shaded pixel (for the Laplacian).
These are texture lookups only — no branching, GPU-friendly. At typical fragment counts
this adds ~10–15% shader cost; the visual improvement on edges is significant.

**Tuning knobs (adjust in shader source if needed):**

| Constant | Value | Effect |
|----------|-------|--------|
| `h_lap = cell * 1.0` | 1× cell | Laplacian probe distance — increase for softer edge detection |
| `edge = lap * cell * 2.0` | 2× | Edge sensitivity — increase to sharpen more aggressively |
| `h_fine = cell * 0.5` | 0.5× | Normal kernel at full edge — decrease for crisper but noisier edges |
| `h_coarse = cell * 2.0` | 2× | Normal kernel on flat surfaces — keep at 2.0 (C0 artifact avoidance) |

**Why not just always use `h_fine`?** `h = cell * 0.5` samples within a single cell,
where the baked SDF has C0 discontinuities at cell boundaries. On flat surfaces this
produces faceted shading. The blend ensures flat areas stay smooth.

**Apply to both shaders:**
1. `core/dm_scene_ray_march_renderer.py` — scene renderer (multi-field)
2. `core/dm_ray_march_renderer.py` — single-field renderer

Search for `sdf_normal_field` in both files and replace the function body.

**Independent — no other RO task required.**

---

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
RO-003 → RO-011 (modifies _compute_cell_size added in RO-003)
RO-012 (standalone — sdf_baker.py only, no other deps)
RO-012, RO-013 → RO-014 (needs bbox_override in baker + frustum helper)
RO-014 → RO-015 (frustum clipping needs pan/rotate sensor to stay current)
RO-016 (standalone — shader string edit only, both renderers)
```
