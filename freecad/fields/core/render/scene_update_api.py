# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Public field-registration/update API for FldSceneVoxelRenderer.

Extracted unchanged (SV-003). Every function takes the renderer instance as `r`.
These are the entry points object proxies and tools call on the singleton by name
(e.g. `FldSceneVoxelRenderer.get_instance().update_field(label, field)`), so the
renderer keeps a same-named thin delegate for each -- do not rename any of them.
"""
import ctypes
import FreeCADGui

from freecad.fields.core import fld_logger


def register_heightmap_texture(r, label, image_path):
    """Queue a heightmap image path for GL upload on the next render frame."""
    r._hmap_cache.queue_path(image_path)


def upload_pending_heightmap_textures(r):
    """Upload queued heightmap images/arrays as GL textures. Must be called inside the GL callback."""
    r._hmap_cache.upload_pending()
    r._hmap_cache.gc(r._fields.values())


def unregister_field(r, label):
    existed = r._registry.unregister(label)

    # MS-010 put a document observer in front of this, so it now sees every object
    # deleted anywhere in the application -- Part::Box, spreadsheets, sketches.
    # Rebuilding the scene shader and forcing a redraw for a label that was never
    # registered is pure cost, and `existed` was already computed and thrown away.
    if not (existed
            or any(k[0] == label for k in (getattr(r, "_tex3d_cache", None) or ()))
            or any(k == label or k.startswith(f"{label}:")
                   for k in (getattr(r, "_cage_ssbo_cache", None) or ()))):
        return

    # Clean up SSBO cache for this label
    if getattr(r, "_cage_ssbo_cache", None):
        to_del = [k for k in r._cage_ssbo_cache if k.startswith(f"{label}:") or k == label]
        for k in to_del:
            entry = r._cage_ssbo_cache.pop(k)
            buf_id = entry.get("buf_id")
            if buf_id and getattr(r._gl_buf, "glDeleteBuffers", None) is not None:
                try:
                    buf_id_val = ctypes.c_uint(buf_id)
                    r._gl_buf.glDeleteBuffers(1, ctypes.byref(buf_id_val))
                except Exception as e:
                    fld_logger.warn(
                        f"unregister_field: glDeleteBuffers failed for cage "
                        f"SSBO '{k}' (buf {buf_id}): {e}. The buffer is "
                        f"dropped from the cache and leaks on the GPU."
                    )

    if getattr(r, "_tex3d_cache", None):
        to_del = [k for k in r._tex3d_cache if k[0] == label]
        for k in to_del:
            entry = r._tex3d_cache.pop(k)
            if entry and entry.get("tex"):
                try:
                    entry["tex"].destroy()
                except Exception as e:
                    fld_logger.render_debug(f"SceneVoxel: failed to destroy 3D texture for '{label}' on unregister: {e}")

    if not r._registry:
        r._switch.whichChild = -1
        r._hmap_cache.clear_bindings_to_make()
        # `_rebuild` is what normally refreshes this, and it is skipped here --
        # so without the clear, the last field of a closed document stayed in
        # `_last_analytical_data` with a live bbox after its registry entry was
        # gone (measured 2026-08-26, MS-010). Nothing draws it while the switch
        # is off, but it is still a reference the baker reads at pass time.
        r._last_analytical_data = None
    else:
        r._rebuild()

    try:
        r._hmap_cache.gc(r._registry.fields.values())
    except Exception as e:
        fld_logger.render_debug(
            f"SceneVoxel: heightmap GC after unregistering '{label}' failed: {e}")

    try:
        active_view = FreeCADGui.ActiveDocument.ActiveView
        if active_view:
            active_view.redraw()
    except Exception as e:
        fld_logger.render_debug(f"FldSceneVoxelRenderer: Failed to redraw view: {e}")


def set_field_visible(r, label, visible):
    if label in r._registry:
        r._registry.set_visible(label, visible)
        r._rebuild()


def update_field(r, label, field):
    if hasattr(field, "heightmap_path") and field.heightmap_path:
        r.register_heightmap_texture(label, field.heightmap_path)
    r._registry.update_field(label, field)
    # Unconditional: _attach is idempotent per scene graph, and this is the hook
    # that re-attaches the renderer in a document opened after an earlier one was
    # closed (RA-013).
    r._attach()
    r._rebuild()
    try:
        if FreeCADGui.activeView():
            FreeCADGui.activeView().redraw()
    except Exception as e:
        fld_logger.render_debug(f"FldSceneVoxelRenderer: redraw after field update failed: {e}")


def begin_update_batch(r):
    """Begin a batch of updates, postponing rebuilds until end_update_batch is called."""
    r._batch_update_count += 1


def end_update_batch(r):
    """End a batch of updates. If the count reaches 0 and updates occurred, trigger rebuild."""
    r._batch_update_count = max(0, r._batch_update_count - 1)
    if r._batch_update_count == 0 and r._batch_needs_rebuild:
        r._batch_needs_rebuild = False
        r._rebuild()


def gc_fields(r):
    to_remove = r._registry.gc_orphans()
    if to_remove:
        r._rebuild()
        try:
            active_view = FreeCADGui.ActiveDocument.ActiveView
            if active_view:
                active_view.redraw()
        except Exception as e:
            fld_logger.render_debug(f"FldSceneVoxelRenderer: Failed to redraw view: {e}")


def refresh_appearance(r):
    """Recompute appearance properties (colors, specular) without rebuilding shaders, and request redraw."""
    if not r._scene.fields:
        return
    for f in r._scene.fields:
        f.shape_color = r._appearance.shape_color(f.label)
        f.selection_color = r._appearance.selection_color(f.label)
        f.specular = r._appearance.specular_shininess(f.label)
    # The loop above only changed CPU-side state. The FieldMeta SSBO the
    # shader actually samples is only repacked when it is marked dirty, so
    # without this flag the upload is skipped and the GPU keeps the old
    # colour. This worked before only because the one caller,
    # on_prefs_changed, followed it with _rebuild(), which sets it too.
    r._field_meta_dirty = True
    try:
        if FreeCADGui.activeView():
            FreeCADGui.activeView().redraw()
    except Exception as e:
        fld_logger.render_debug(f"FldSceneVoxelRenderer.refresh_appearance redraw failed: {e}")


def on_prefs_changed(r):
    from freecad.fields.core.fld_settings import get_render_debug_mode
    debug = get_render_debug_mode()
    r._bbox_switch.whichChild = 0 if debug else -1
    r.refresh_appearance()
    if r._registry:
        r._registry.mark_all_dirty()
        r._rebuild()
