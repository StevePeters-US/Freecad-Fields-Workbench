# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Scene-shader rebuild orchestration for FldSceneVoxelRenderer.

Extracted unchanged (SV-004). `rebuild(r)` is fields -> a compiled CompiledScene +
updated dirty region + refreshed Coin3D bbox proxy geometry; no GL calls, but it does
touch self._perf (profiler add calls) and self._switch.whichChild (Coin3D switch
state), so it still needs the renderer instance, not just SdfFieldRegistry.
"""
import time

import FreeCAD

from freecad.fields.core import fld_logger
from freecad.fields.core.render.field_bbox import union_dirty_region
from freecad.fields.core.render.scene_shader_builder import SceneShaderBuilder


def mark_dirty_region(r, region):
    """Accumulate dirty work until the next bake. `region=None` means 'full bake'.

    Called once per _rebuild(); several rebuilds may run between two GL frames,
    so this must union, never overwrite. Full-bake is absorbing: once set it
    survives every later narrow region until the bake clears it.
    """
    if r._dirty_region_full:
        return
    if region is None:
        r._dirty_region_full = True
        r._dirty_region = None
        return
    if r._dirty_region is None:
        r._dirty_region = region
    else:
        (amn, amx), (bmn, bmx) = r._dirty_region, region
        r._dirty_region = (
            FreeCAD.Vector(min(amn.x, bmn.x), min(amn.y, bmn.y), min(amn.z, bmn.z)),
            FreeCAD.Vector(max(amx.x, bmx.x), max(amx.y, bmx.y), max(amx.z, bmx.z))
        )


def rebuild(r):
    """Compile all registered SDF fields to an analytical GLSL fragment shader.
    All SDF fields must implement to_glsl(). Fields missing it are skipped with a warning.
    """
    if getattr(r, "_batch_update_count", 0) > 0:
        r._batch_needs_rebuild = True
        return

    t0 = time.perf_counter()

    if getattr(r, "_tex3d_cache", None):
        for k in list(r._tex3d_cache.keys()):
            if k[0] in r._registry.dirty_fields:
                entry = r._tex3d_cache.pop(k, None)
                if entry and entry.get("tex"):
                    try:
                        entry["tex"].destroy()
                    except Exception as e:
                        fld_logger.error(f"SceneVoxel: failed to destroy 3D texture for '{k}': {e}")

    _dirty_snapshot = set(r._registry.dirty_fields)
    visible_compiled, analytical_data, t_compile_total = r._registry.compile_visible(
        r._appearance,
        bbox_checker=r._check_bbox_margin_safety
    )

    if not analytical_data:
        r._switch.whichChild = -1
        return

    r._hmap_cache.plan_bindings(analytical_data)

    t_shader_start = time.perf_counter()
    compiled_scene = SceneShaderBuilder.build(analytical_data, visible_compiled)
    t_shader_total = time.perf_counter() - t_shader_start

    r._scene = compiled_scene

    dirty_labels = _dirty_snapshot
    old_data_map = {d.get("label", d["ctx"].prefix): d for d in (r._last_analytical_data or []) if "ctx" in d}
    new_data_map = {d.get("label", d["ctx"].prefix): d for d in analytical_data if "ctx" in d}
    labels_changed = set(old_data_map.keys()) != set(new_data_map.keys())

    if not labels_changed and dirty_labels:
        r._mark_dirty_region(
            union_dirty_region(dirty_labels, old_data_map, new_data_map))
    else:
        # A label appearing or disappearing moves solid that the old volume
        # still holds, anywhere in the scene -- no region covers that.
        r._mark_dirty_region(None)

    r._last_analytical_data = analytical_data
    r._volume_dirty = True
    r._field_meta_dirty = True

    r._active_uniforms = compiled_scene.uniform_dict

    t_total = time.perf_counter() - t0
    if hasattr(r, "_drag_session_rebuild_time"):
        r._drag_session_rebuild_time += t_total
    r._perf.add("rebuild_total", t_total)
    r._perf.add("rebuild_compile", t_compile_total)
    r._perf.add("rebuild_shadergen", t_shader_total)

    # A rebuild outside a drag has no profiler table to land in, and one
    # this slow is a stall the user just sat through.
    if t_total > 0.1:
        fld_logger.debug(
            f"SceneVoxel._rebuild: {t_total*1000:.1f}ms for "
            f"{len(analytical_data)} field(s) — glsl compile "
            f"{t_compile_total*1000:.1f}ms, shader assembly "
            f"{t_shader_total*1000:.1f}ms")

    # Update Coin3D proxy geometry so the scene graph has a valid bbox
    all_mn = [d["bbox_min"] for d in analytical_data]
    all_mx = [d["bbox_max"] for d in analytical_data]
    mn_all = FreeCAD.Vector(
        min(v.x for v in all_mn), min(v.y for v in all_mn), min(v.z for v in all_mn))
    mx_all = FreeCAD.Vector(
        max(v.x for v in all_mx), max(v.y for v in all_mx), max(v.z for v in all_mx))
    pts = [
        (mn_all.x, mn_all.y, mn_all.z), (mx_all.x, mn_all.y, mn_all.z),
        (mn_all.x, mx_all.y, mn_all.z), (mx_all.x, mx_all.y, mn_all.z),
        (mn_all.x, mn_all.y, mx_all.z), (mx_all.x, mn_all.y, mx_all.z),
        (mn_all.x, mx_all.y, mx_all.z), (mx_all.x, mx_all.y, mx_all.z),
    ]
    r._bbox_coords.point.setValues(0, 8, pts)
    r._coords.point.setValues(0, 8, pts)
    r._switch.whichChild = 0
