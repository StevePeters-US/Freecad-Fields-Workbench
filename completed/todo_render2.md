# todo_render.md — Real-Time SDF Preview Rendering Optimization

Read `AntiGravity_Skills/sdf_render_pipeline.md` before starting any task.

---

## Task 1: Instant camera zoom — stop rebaking on every zoom `Gemini Flash`

- **Goal**: Camera zoom/pan/rotate should feel instant by using the existing baked texture, only rebaking after extended idle when quality is significantly degraded.
- **Files to read**: `core/dm_scene_ray_march_renderer.py` (lines 86-120: `_on_camera_changed`, `_on_zoom_settled`)
- **Files to modify**: `core/dm_scene_ray_march_renderer.py`
- **Steps**:
  1. In `_on_camera_changed()`, add an immediate `view.redraw()` call BEFORE the debounce timer. The GPU shader already renders the existing texture at any zoom level via trilinear interpolation — no rebake needed for the user to see a correct (if slightly degraded) image.
  2. Increase the debounce timer from `100` to `400` ms. This gives more time for the user to finish zooming before triggering a costly rebake.
  3. In `_on_zoom_settled()`, instead of blanket `self._dirty_fields = set(self._fields.keys())`, compare each field's baked cell size to the current optimal cell size. Only mark a field dirty if the ratio exceeds 2x (too coarse) or 0.5x (wastefully fine).
  4. To support step 3: in the `_rebuild()` method, after calling `bake_sdf_to_volume(f, cs, ...)`, store the cell size in the baked cache: `baked["cell_size"] = cs`.
  5. New `_on_zoom_settled()` logic:
     ```python
     def _on_zoom_settled(self):
         self._zoom_timer = None
         if not self._fields:
             return
         try:
             view = FreeCADGui.ActiveDocument.ActiveView
         except Exception:
             return
         for label, (f, vis) in self._fields.items():
             if not vis or f is None:
                 continue
             cached = self._baked_cache.get(label)
             if cached is None:
                 self._dirty_fields.add(label)
                 continue
             new_cs = self._compute_cell_size(f, view)
             old_cs = cached.get("cell_size", new_cs)
             if new_cs < old_cs * 0.5 or new_cs > old_cs * 2.0:
                 self._dirty_fields.add(label)
         if self._dirty_fields:
             self._rebuild()
             try:
                 if FreeCADGui.activeView():
                     FreeCADGui.activeView().redraw()
             except Exception:
                 pass
     ```
- **Acceptance**: Zooming in/out shows immediate viewport response (no frozen frame). Quality upgrade happens 400ms after zoom stops. Only fields with significant quality mismatch are rebaked (check `dm_logger` output).

---

## Task 2: Fix throttle to use latest field state `Gemini Flash`

- **Goal**: During rapid mouse movement, the throttled preview should always render the LATEST field state, never a stale captured lambda.
- **Files to read**: `tools/dm_base.py` (lines 250-258: `_schedule_update`), `tools/primitive_tool.py` (line 211: `update_preview` call to `_schedule_update`)
- **Files to modify**: `tools/dm_base.py`
- **Steps**:
  1. In `DMBase.__init__()`, add `self._pending_callback = None` alongside the existing `self._update_pending = False`.
  2. Replace `_schedule_update()` with:
     ```python
     def _schedule_update(self, callback, interval_ms=None):
         """Throttled single-shot update. Always uses the LATEST callback."""
         self._pending_callback = callback  # Always overwrite with latest
         if getattr(self, "_update_pending", False):
             return  # Timer already running, it will pick up _pending_callback
         if interval_ms is None:
             from core.dm_object import get_interactive_throttle_interval
             interval_ms = int(get_interactive_throttle_interval() * 1000)
         self._update_pending = True
         QtCore.QTimer.singleShot(interval_ms, self._fire_pending_update)
     ```
  3. Add new method:
     ```python
     def _fire_pending_update(self):
         """Fire the most recently scheduled callback."""
         self._update_pending = False
         cb = getattr(self, "_pending_callback", None)
         if cb:
             cb()
     ```
- **Acceptance**: Create a box, drag rapidly to resize. The final preview always matches the current mouse position — no lag or "snap" to a stale intermediate state.

---

## Task 3: Shortcut FreeCAD recompute for interactive preview `Gemini Flash`

- **Goal**: During interactive drag, update the GPU renderer directly instead of going through the expensive FreeCAD `touch()` → `recompute()` → `execute()` → `updateData()` chain.
- **Files to read**: `tools/primitive_tool.py` (lines 226-246: `_apply_preview_field`), `core/dm_renderer.py` (lines 397-420: `SdfRendererStrategy`), `core/dm_object.py` (lines 299-309: `execute()` for frep)
- **Files to modify**: `tools/primitive_tool.py`
- **Steps**:
  1. In `_apply_preview_field()`, replace the `obj.touch()` + `doc.recompute([obj])` block with a direct call to the scene ray march renderer:
     ```python
     def _apply_preview_field(self, field):
         if self._preview_obj is None or not self._preview_obj.Document:
             return
         try:
             proxy = self._preview_obj.Proxy
             if proxy is None:
                 return
             proxy.SdfField = field

             im = DMInputManager.get_instance()
             if hasattr(self._preview_obj, "IsSubtractive"):
                 self._preview_obj.IsSubtractive = im.is_ctrl_down()

             # Direct GPU update — skip FreeCAD recompute cycle
             from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
             label = f"{self._preview_obj.Document.Name}.{self._preview_obj.Name}"
             DMSceneRayMarchRenderer.get_instance().update_field(label, field)
         except Exception as e:
             dm_logger.debug(f"PrimitiveCreatorBase preview update error: {e}")
         finally:
             self._update_pending = False
     ```
  2. Also update `BoxCreator._drag_update()` (line 445) which calls `_apply_preview_field` directly — it should work with the new version without changes, but verify.
- **Acceptance**: Creating/resizing a box works correctly. The preview updates on screen. `DMObject.execute()` is NOT called during interactive drag (add a temporary `dm_logger.debug` in `execute()` to verify).

---

## Task 4: Eliminate double redraws and expensive updateGui `Gemini Flash`

- **Goal**: Reduce from 2-3 `view.redraw()` calls per update cycle to exactly 1, and remove expensive `FreeCADGui.updateGui()` during drag.
- **Files to read**: `core/dm_scene_ray_march_renderer.py` (lines 488-490: `update_field` redraw), `tools/primitive_tool.py` (lines 213-220: `_do_full_preview_update`), `core/dm_renderer.py` (lines 414-416: `SdfRendererStrategy.update` redraw)
- **Files to modify**: `core/dm_scene_ray_march_renderer.py`, `tools/primitive_tool.py`
- **Steps**:
  1. In `DMSceneRayMarchRenderer.update_field()` (line 481-490), remove the `view.redraw()` call at lines 489-490. The caller is responsible for redrawing.
  2. In `_do_full_preview_update()` (line 213-220 of `primitive_tool.py`), remove the `FreeCADGui.updateGui()` call (line 220). This processes ALL pending Qt events and is very expensive during interactive drag. Keep only the single `view.redraw()`.
  3. Ensure that callers of `update_field()` that need a redraw (like `register_field`, `_on_zoom_settled`, `on_prefs_changed`) still call `view.redraw()` themselves — they already do.
- **Acceptance**: Creating/editing a box still renders correctly. During drag, only one `view.redraw()` call per throttle interval (verify by adding temporary logging).

---

## Task 5: Eliminate redundant memory copies in volume upload `Gemini Flash`

- **Goal**: Remove 3 unnecessary memory copies in the hot bake → upload path.
- **Files to read**: `core/frep/sdf_baker.py` (line 60), `core/dm_scene_ray_march_renderer.py` (line 824), `core/gl_texture3d.py` (lines 127-137)
- **Files to modify**: `core/frep/sdf_baker.py`, `core/dm_scene_ray_march_renderer.py`, `core/gl_texture3d.py`
- **Steps**:
  1. In `sdf_baker.py` line 60, `vol` is already `np.float32`. Change:
     `volume_bytes = vol.astype(np.float32).tobytes()` → `volume_bytes = vol.tobytes()`
  2. In `dm_scene_ray_march_renderer.py` line 824, `combined` is already `dtype=np.float32` (created on line 814). Change:
     `volume_bytes = combined.astype(np.float32).tobytes()` → `volume_bytes = combined.tobytes()`
  3. In `gl_texture3d.py`, add `upload_numpy()` method to `GLTexture3D` that accepts a numpy array and uses zero-copy `from_buffer()`:
     ```python
     def upload_numpy(self, width, height, depth, np_array):
         """Zero-copy upload from a contiguous C-order numpy array."""
         import numpy as np
         self._width = width
         self._height = height
         self._depth = depth
         arr = np.ascontiguousarray(np_array)
         self._np_ref = arr  # prevent GC while ctypes pointer is alive
         self._data = (ctypes.c_ubyte * arr.nbytes).from_buffer(arr)
         self._needs_upload = True
         self._needs_partial = False
     ```
  4. In `_rebuild()` of `dm_scene_ray_march_renderer.py`, replace:
     ```python
     volume_bytes = combined.tobytes()
     self._gl_tex.upload(new_max_nx, new_max_ny, new_total_nz, volume_bytes)
     ```
     with:
     ```python
     self._gl_tex.upload_numpy(new_max_nx, new_max_ny, new_total_nz, combined)
     ```
     Do this for BOTH the `dims_changed` and `else` branches.
- **Acceptance**: SDF primitives still render correctly. Peak memory usage is lower (verify with a large 200mm box at 1mm cell size — the combined volume will be ~32MB; with the old code there were 3 copies = ~96MB transient).
