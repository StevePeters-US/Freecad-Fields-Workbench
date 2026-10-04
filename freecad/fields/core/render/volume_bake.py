# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/volume_bake.py

The scene bake dispatch loop, extracted from `SceneVolume.bake()` because it
was ~40% of `scene_volume.py` and the only part of the file that touches
per-field SSBO/texture3d binding. `bake_scene(volume, ...)` takes a
`SceneVolume` instance and operates on it exactly as the method used to
operate on `self` -- it is a collaborator, not an independent object.
"""
import ctypes
import math
import time
import FreeCAD
from freecad.fields.core import fld_logger
from freecad.fields.core.gl.gl_texture3d import _loader
from freecad.fields.core.gl.gl_program import bind_image_texture, memory_barrier, GL_SHADER_IMAGE_ACCESS_BARRIER_BIT
from freecad.fields.core.render.tree_item_status import _mark_tree_item_failed

# Texture-format / access-mode enums for the bind_image_texture() calls below.
# Duplicated from scene_volume.py's own literals rather than imported, matching
# this codebase's existing practice of hardcoding stable GL enum values per file
# (heightmap_texture_cache.py, ssao_resources.py, and voxel_render_passes.py all
# redefine GL_R32F locally too) -- it also keeps this module free of any
# top-level dependency on scene_volume, so scene_volume can import
# SCAN_BLOCK/field_lipschitz back from here for re-export with no import cycle.
GL_R16F        = 0x822D
GL_R16UI       = 0x8234
GL_RGBA8_SNORM = 0x8F97
GL_READ_WRITE  = 0x88BA

GL_SHADER_STORAGE_BUFFER = 0x90D2
GL_DYNAMIC_DRAW          = 0x88E8

# Load buffer functions if needed for SSBOs during bake
try:
    glGenBuffers = _loader.get("glGenBuffers", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
    glBindBuffer = _loader.get("glBindBuffer", [ctypes.c_uint, ctypes.c_uint], None)
    glBufferData = _loader.get("glBufferData", [ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint], None)
    glBindBufferBase = _loader.get("glBindBufferBase", [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint], None)
    glDeleteBuffers = _loader.get("glDeleteBuffers", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
except Exception:
    glGenBuffers = None
    glBindBuffer = None
    glBufferData = None
    glBindBufferBase = None
    glDeleteBuffers = None

# Block edge, in voxels, for the per-field saturation scan (VR-020/VR-021).
# 8 to match the bake shader's local_size, so one block is exactly one
# workgroup and `lid / 8` is the workgroup index.
SCAN_BLOCK = 8

# SdfField.lipschitz() returns 1.0 on the base class, so a field that is not a
# true distance field and never overrode it reports a bound it does not meet --
# and an overstated bound deletes geometry with nothing logged. Keep this in
# step with the dev repo's test_volume_oracle.py, which proves the criterion
# using the same factor.
LIPSCHITZ_SAFETY = 2.0

GL_SHADER_STORAGE_BARRIER_BIT = 0x00002000


def field_lipschitz(fld):
    """`fld.lipschitz()` as a usable number: >= 1.0, never NaN, never raising.

    A field that has no bound, or whose bound blows up on a degenerate parameter,
    must fall back to 1.0 rather than take the bake and the march down with it.
    """
    try:
        lip = float(fld.lipschitz())
    except Exception:
        return 1.0
    if not (lip == lip) or lip in (float("inf"), float("-inf")):
        return 1.0
    return max(lip, 1.0)


def bake_scene(volume, analytical_data, renderer=None, region=None):
    """Bake visible fields into `volume` (full or incremental region).

    `volume` is a SceneVolume; this was SceneVolume.bake() until SVS-003 split
    it out. Every `self.` in the original body is `volume.` here -- same object,
    same attributes (`tex_id`, `id_tex_id`, `norm_tex_id`, `vol_min`, `vol_max`,
    `step`, `band`, `active_fields`, `active_data`, `dirty`, `lip`,
    `_bake_cache`, `_scan_cache`, `_no_surface_id_reported`,
    `_failed_fields_reported`) -- including the ad hoc `getattr`/`setattr` pattern
    used for the two `_reported` sets, which must stay ad hoc since
    SceneVolume.__init__ never declares them.
    """
    from freecad.fields.core.sdf.glsl_compiler import build_compute_shader
    from freecad.fields.core.sdf.gpu_field_eval import GpuFieldEvaluator

    if len(analytical_data) >= 65535:
        fld_logger.error(f"SceneVolume: field count ({len(analytical_data)}) exceeds r16ui max capacity of 65535")

    t0 = time.perf_counter()

    # A region bake only clears and rewrites its own sub-box; every other
    # voxel keeps whatever it already held. That is only sound while the
    # world->voxel mapping is unchanged, so ensure() vetoes it via volume.dirty
    # rather than trusting the caller to notice.
    if region is not None and volume.dirty:
        fld_logger.render_debug(
            "SceneVolume.bake: volume geometry changed -> region ignored, baking in full")
        region = None

    reg_box = None
    if region is not None:
        r_min, r_max = region
        reg_box = volume._sub_box(r_min, r_max, pad=volume.band)
        if reg_box is not None:
            ox, oy, oz, cx, cy, cz = reg_box
            volume.clear(sub_offset=(ox, oy, oz), sub_count=(cx, cy, cz))
        else:
            volume.clear()
    else:
        volume.clear()

    bind_image_texture(0, volume.tex_id, GL_READ_WRITE, GL_R16F, layered=True)
    bind_image_texture(1, volume.id_tex_id, GL_READ_WRITE, GL_R16UI, layered=True)
    bind_image_texture(2, volume.norm_tex_id, GL_READ_WRITE, GL_RGBA8_SNORM, layered=True)

    if reg_box is None:
        volume.active_fields = []
        volume.active_data = []
    failed_fields = []

    baked_count = 0
    for f_idx, d in enumerate(analytical_data):
        f_box = volume._sub_box(d["bbox_min"], d["bbox_max"], pad=volume.band)
        if f_box is None:
            continue
        fox, foy, foz, fcx, fcy, fcz = f_box

        if reg_box is not None:
            rox, roy, roz, rcx, rcy, rcz = reg_box
            # Intersection of field sub-box with dirty region sub-box
            ix0 = max(fox, rox)
            iy0 = max(foy, roy)
            iz0 = max(foz, roz)
            ix1 = min(fox + fcx, rox + rcx)
            iy1 = min(foy + fcy, roy + rcy)
            iz1 = min(foz + fcz, roz + rcz)
            if ix1 <= ix0 or iy1 <= iy0 or iz1 <= iz0:
                continue  # Field does not intersect dirty region
            ox, oy, oz = ix0, iy0, iz0
            cx, cy, cz = ix1 - ix0, iy1 - iy0, iz1 - iz0
        else:
            ox, oy, oz = fox, foy, foz
            cx, cy, cz = fcx, fcy, fcz

        t_field0 = time.perf_counter()
        expr = d["expr"]
        ctx = d["ctx"]
        label = d.get("label", getattr(ctx, "prefix", str(f_idx)))
        nbx, nby, nbz = ((cx + 7) // 8, (cy + 7) // 8, (cz + 7) // 8)
        fld = d.get("field")
        lip = field_lipschitz(fld) if fld is not None else 1.0
        half_diag = 0.5 * math.sqrt(sum((SCAN_BLOCK * s) ** 2 for s in
                                        (volume.step.x, volume.step.y, volume.step.z)))
        reach = float(volume.band) + LIPSCHITZ_SAFETY * lip * half_diag

        # Bind textures, 3D warp textures, SSBOs before scan and bake dispatches
        tex_ids = GpuFieldEvaluator._bind_textures(None, None, ctx, base_unit=1)

        # Bind 3D textures for every sampler3D the field's GLSL declared.
        # Units run above the 2D samplers, which occupy 1 .. len(sampler2d_names).
        sampler3d_names = getattr(ctx, "sampler3d_names", [])
        providers = getattr(ctx, "sampler3d_providers", {})
        tex3d_props = {}
        if sampler3d_names and renderer is not None:
            label = d.get("label", getattr(ctx, "prefix", ""))
            action = getattr(renderer, "_last_action", None)
            next_unit = 1 + len(getattr(ctx, "sampler2d_names", []))
            glActiveTexture0 = _loader.get("glActiveTexture", [ctypes.c_uint], None)
            glBindTexture0 = _loader.get("glBindTexture", [ctypes.c_uint, ctypes.c_uint], None)
            GL_TEXTURE0 = 0x84C0
            GL_TEXTURE_3D = 0x806F
            for sname in sampler3d_names:
                provider = providers.get(sname)
                if provider is None:
                    fld_logger.error(
                        f"scene_bake: sampler3D {sname} has no provider; it will "
                        f"sample texture unit 0. The field's to_glsl must call "
                        f"ctx.sampler3d(hint, provider=self).")
                    continue
                entry = renderer._get_field_texture3d(label, sname, provider, action)
                if not entry or not glActiveTexture0 or not glBindTexture0:
                    continue
                unit = next_unit
                next_unit += 1
                glActiveTexture0(GL_TEXTURE0 + unit)
                glBindTexture0(GL_TEXTURE_3D, entry["tex"].id)
                tex3d_props[sname] = ("int", unit)
                # Which uniform belongs to which sampler is recorded at compile
                # time (SB-036). Do NOT match on the name: one context compiles a
                # whole tree, so two cages both spell "wmin" and a name match
                # writes each provider's bbox over the other's.
                uni_map = getattr(ctx, "sampler3d_uniforms", {}).get(sname, {})
                for suffix, (utype, val) in entry.get("uniforms", {}).items():
                    uname = uni_map.get(suffix)
                    if uname is None:
                        fld_logger.error(
                            f"scene_bake: {sname} supplied uniform '{suffix}' that its "
                            f"to_glsl never registered with ctx.sampler3d_uniform(); it "
                            f"will keep its compile-time placeholder value.")
                        continue
                    tex3d_props[uname] = (utype, val)

        # Bind SSBOs if any
        ssbo_names = getattr(ctx, "ssbo_names", [])
        K = len(ssbo_names)
        buf_ids = None
        if K > 0 and glGenBuffers and glBindBuffer and glBufferData and glBindBufferBase:
            buf_ids = (ctypes.c_uint * K)()
            glGenBuffers(K, buf_ids)
            for idx, sname in enumerate(ssbo_names):
                field = ctx.ssbo_fields.get(sname)
                if field:
                    # A field that registers an SSBO packs it, via this one hook.
                    # The name-sniffing chain that used to sit here dispatched to
                    # seven `pack_*` methods that existed only on
                    # `SdfNurbsSurfaceField`, deleted 2026-09-01 when RA-012
                    # resolved as "retire". Nothing else ever implemented them,
                    # so a missing packer is a bug in the field, not a fallback.
                    packer = getattr(field, "ssbo_packer", None)
                    if packer is None:
                        fld_logger.warn(
                            f"scene_bake: {sname} was registered with ctx.ssbo() by "
                            f"{type(field).__name__}, which has no ssbo_packer(); "
                            f"the buffer will be left unbound.")
                        continue
                    packed_data = packer(sname)
                    buf_id = buf_ids[idx]
                    glBindBuffer(GL_SHADER_STORAGE_BUFFER, buf_id)
                    glBufferData(GL_SHADER_STORAGE_BUFFER, packed_data.nbytes, packed_data.ctypes.data, GL_DYNAMIC_DRAW)
                    glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 4 + idx, buf_id)

        # Bind block flag SSBO at binding 3 -- below the field SSBOs, which
        # start at 4 and are uncapped, and inside GL's guaranteed 0..7 range.
        flag_buf = None
        if glGenBuffers and glBindBuffer and glBufferData and glBindBufferBase:
            f_id = (ctypes.c_uint * 1)()
            glGenBuffers(1, f_id)
            flag_buf = f_id[0]
            glBindBuffer(GL_SHADER_STORAGE_BUFFER, flag_buf)
            glBufferData(GL_SHADER_STORAGE_BUFFER, nbx * nby * nbz * 4, None, GL_DYNAMIC_DRAW)
            glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 3, flag_buf)

        try:
            # 1. Dispatch block scan
            scan_src = build_compute_shader(expr, ctx, mode="block_scan")
            scan_prog = volume._scan_cache.get(scan_src)
            scan_prog.use()
            scan_prog.set_uniforms_from_ctx(ctx.uniforms)
            scan_prog.set_3f("u_vol_min", float(volume.vol_min.x), float(volume.vol_min.y), float(volume.vol_min.z))
            scan_prog.set_3f("u_voxel_step", float(volume.step.x), float(volume.step.y), float(volume.step.z))
            scan_prog.set_3iv("u_sub_offset", 1, [ox, oy, oz])
            scan_prog.set_3iv("u_block_count", 1, [nbx, nby, nbz])
            scan_prog.set_1f("u_reach", reach)
            scan_prog.set_1i("u_cage_quad_iters", 10)
            scan_prog.set_1i("u_cage_tri_iters", 10)
            scan_prog.set_1i("u_grad_taps", 4)
            for idx_s, sname in enumerate(getattr(ctx, "sampler2d_names", [])):
                scan_prog.set_1i(sname, 1 + idx_s)
            for uname, (utype, val) in tex3d_props.items():
                scan_prog.set_uniform(uname, utype, val)

            scan_prog.dispatch_compute((nbx + 3) // 4, (nby + 3) // 4, (nbz + 3) // 4)
            memory_barrier(GL_SHADER_STORAGE_BARRIER_BIT)

            # 2. Dispatch scene bake
            # Anything derived from the field that this call needs must be compiled
            # ONCE, with `expr`, and cached beside it in analytical_data -- never
            # computed here. `ctx.uniform()` appends unconditionally, so a per-bake
            # call that registers a uniform grows the cached ctx, changes the source
            # string every frame, and misses `_bake_cache` forever, recompiling the
            # shader on every drag tick. That is not hypothetical: it is what the
            # TR-021 analytic-normal call did before the audit caught it.
            src = build_compute_shader(expr, ctx, mode="scene_bake")
            prog = volume._bake_cache.get(src)
            prog.use()
            prog.set_uniforms_from_ctx(ctx.uniforms)
            # The ROOT surface id, not the field index: the wholly-inside branch of
            # scene_bake writes this into u_id_volume, and it has to agree with the
            # `s.id` the band voxels get, or the same solid keys two different rows
            # of the FieldMeta table.
            #
            # There is deliberately no index fallback. `min(f_idx, 65534)` stood
            # here and was the second key that bug is about: a field index written
            # into a table the march reads by surface id, so one field's index
            # silently addressed another field's row. A root with no id writes the
            # 65535 miss sentinel, the march falls through to u_base_color, and the
            # cause says so in the log instead of showing up as a wrong colour.
            root_sid = fld.surface_id if fld is not None else 65535
            if root_sid >= 65535:
                # Once per field, not once per bake: a drag bakes every tick.
                seen = getattr(volume, "_no_surface_id_reported", None)
                if seen is None:
                    seen = volume._no_surface_id_reported = set()
                if f_idx not in seen:
                    seen.add(f_idx)
                    fld_logger.error(
                        f"scene_bake: field {f_idx} has no surface id; its interior "
                        f"voxels will not key a FieldMeta row")
            prog.set_1ui("u_root_surface_id", root_sid)
            for idx_s, sname in enumerate(getattr(ctx, "sampler2d_names", [])):
                prog.set_1i(sname, 1 + idx_s)
            for uname, (utype, val) in tex3d_props.items():
                prog.set_uniform(uname, utype, val)

            prog.set_3f("u_vol_min", float(volume.vol_min.x), float(volume.vol_min.y), float(volume.vol_min.z))
            prog.set_3f("u_voxel_step", float(volume.step.x), float(volume.step.y), float(volume.step.z))
            prog.set_3iv("u_sub_offset", 1, [ox, oy, oz])
            prog.set_3iv("u_sub_count", 1, [cx, cy, cz])
            prog.set_1f("u_band", float(volume.band))
            prog.set_1i("u_cage_quad_iters", 10)
            prog.set_1i("u_cage_tri_iters", 10)
            prog.set_1i("u_grad_taps", 4)

            prog.dispatch_compute((cx + 7) // 8, (cy + 7) // 8, (cz + 7) // 8)
            memory_barrier(GL_SHADER_IMAGE_ACCESS_BARRIER_BIT)
            baked_count += 1
            if fld is not None and fld not in volume.active_fields:
                volume.active_fields.append(fld)
            if d not in volume.active_data:
                volume.active_data.append(d)
            if hasattr(volume, "_failed_fields_reported"):
                to_remove = [k for k in volume._failed_fields_reported if (isinstance(k, tuple) and k[0] == label) or k == label]
                for k in to_remove:
                    volume._failed_fields_reported.discard(k)
                if to_remove:
                    _mark_tree_item_failed(label, failed=False)
        except RuntimeError as e:
            failed_fields.append((d, str(e)))
            if reg_box is not None:
                if fld is not None and fld in volume.active_fields:
                    volume.active_fields.remove(fld)
                if d in volume.active_data:
                    volume.active_data.remove(d)
            seen = getattr(volume, "_failed_fields_reported", None)
            if seen is None:
                seen = volume._failed_fields_reported = set()
            fail_key = (label, expr)
            if fail_key not in seen:
                seen.add(fail_key)
                fld_logger.error(
                    f"SceneVolume: bake shader compilation failed for field '{label}': {e}")
            _mark_tree_item_failed(label, failed=True)
        finally:
            if buf_ids is not None and glDeleteBuffers:
                glDeleteBuffers(K, buf_ids)
            if flag_buf is not None and glDeleteBuffers:
                f_del = (ctypes.c_uint * 1)(flag_buf)
                glDeleteBuffers(1, f_del)
            GpuFieldEvaluator._unbind_textures(None, tex_ids, base_unit=1)

        t_field = (time.perf_counter() - t_field0) * 1000.0
        if t_field > 5.0:
            fld_logger.render_debug(f"SceneVolume: field bake took {t_field:.1f} ms")

    bind_image_texture(0, 0, GL_READ_WRITE, GL_R16F)
    bind_image_texture(1, 0, GL_READ_WRITE, GL_R16UI)
    bind_image_texture(2, 0, GL_READ_WRITE, GL_RGBA8_SNORM)
    if failed_fields and renderer is not None:
        if getattr(renderer, "_scene", None) is not None and getattr(renderer._scene, "fields", None) is not None:
            failed_labels = {d.get("label", getattr(d.get("ctx"), "prefix", "")) for d, _ in failed_fields}
            failed_objs = {d.get("field") for d, _ in failed_fields if d.get("field") is not None}
            renderer._scene.fields = [
                cf for cf in renderer._scene.fields
                if cf.label not in failed_labels and cf.field_obj not in failed_objs
            ]
            renderer._field_meta_dirty = True

    volume.lip = max([field_lipschitz(d["field"]) for d in volume.active_data
                    if d.get("field") is not None] or [1.0])
    ms = (time.perf_counter() - t0) * 1000.0
    if reg_box is not None:
        fld_logger.render_debug(f"SceneVolume.bake: region rebake, {baked_count}/{len(analytical_data)} fields in {ms:.1f} ms")
    else:
        fld_logger.render_debug(f"SceneVolume.bake: full bake, {len(analytical_data)} fields in {ms:.1f} ms")
    volume.dirty = False
