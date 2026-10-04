# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/voxel_render_passes.py

The four GPU passes of one render callback, each as a function over a
`FrameContext`:

    Pass 1  bake the scene volume (only when dirty), then ray march it into the
            G-buffer -- colour, view-space position, view-space normal, depth
    Pass 2  SSAO from the G-buffer's position + normal
    Pass 3  a 4x4 box blur of the raw occlusion
    Pass 4  composite colour x occlusion, plus the selection outline

Passes 1-3 are throttled (30 fps at rest, every frame while navigating); Pass 4
runs on every Coin3D frame from whatever those last left in the FBOs, which is
what keeps the SDF overlay on screen between expensive frames.

The functions take the renderer explicitly as `r` rather than being methods,
because they are the frame, not the object: everything they need that survives
a frame lives on the renderer, and everything that does not lives on the
context. The orchestration -- state save/restore, the FBO resize and its
rollback, the throttle decision -- stays in
`FldSceneVoxelRenderer._render_gl_callback_inner`.

Must be called inside an active GL context (i.e. from a SoCallback).
"""
import ctypes
import time
from types import SimpleNamespace

import numpy as np
import FreeCAD
import pivy.coin as coin

from freecad.fields.core import fld_logger
from freecad.fields.core.render.render_state import _RS_QUALITY, _RS_INTERACTIVE
from freecad.fields.core.render.scene_volume import VOLUME_SLACK_FRAC

_GL_DEPTH_TEST      = 0x0B71
_GL_DEPTH_WRITEMASK = 0x0B72

GL_COLOR_BUFFER_BIT = 0x4000
GL_DEPTH_BUFFER_BIT = 0x0100
GL_FRAMEBUFFER      = 0x8D40
GL_TEXTURE_2D       = 0x0DE1
GL_TEXTURE_3D       = 0x806F
GL_TEXTURE0         = 0x84C0

GL_WRITE_ONLY = 0x88B9
GL_RGBA16F    = 0x881A
GL_RGBA32F    = 0x8814
GL_R32F       = 0x822E

GL_SRC_ALPHA           = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_BLEND               = 0x0BE2

#: IMAGE_ACCESS | TEXTURE_FETCH -- orders the compute writes against the reads
#: that follow them without stalling the CPU.
BARRIER_IMAGE_AND_TEXTURE = 0x00000020 | 0x00000008

#: Hard ceiling on scene volume resolution per axis. Above this the memory and
#: bake cost stop being worth the detail; the user is warned once per bake.
MAX_VOXEL_RES = 384

#: Texture units the three scene volumes are bound to for the march. High
#: enough to stay clear of the per-field sampler units the compiler hands out.
UNIT_VOLUME      = 10
UNIT_ID_VOLUME   = 11
UNIT_NORM_VOLUME = 12


def load_frame_entrypoints():
    """The GL functions every pass needs, plus a `check_error` helper.

    Resolved once per callback rather than once per pass so that the passes
    share one namespace and no pass has to know which loader it came from.
    """
    from freecad.fields.core.gl.gl_texture3d import _loader

    gl = SimpleNamespace(
        glGetIntegerv     = _loader.get("glGetIntegerv",     [ctypes.c_uint, ctypes.POINTER(ctypes.c_int)], None),
        glGetFloatv       = _loader.get("glGetFloatv",       [ctypes.c_uint, ctypes.POINTER(ctypes.c_float)], None),
        glBindFramebuffer = _loader.get("glBindFramebuffer", [ctypes.c_uint, ctypes.c_uint], None),
        glActiveTexture   = _loader.get("glActiveTexture",   [ctypes.c_uint], None),
        glBindTexture     = _loader.get("glBindTexture",     [ctypes.c_uint, ctypes.c_uint], None),
        glClear           = _loader.get("glClear",           [ctypes.c_uint], None),
        glUseProgram      = _loader.get("glUseProgram",      [ctypes.c_uint], None),
        glViewport        = _loader.get("glViewport",        [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int], None),
        glEnable          = _loader.get("glEnable",          [ctypes.c_uint], None),
        glDisable         = _loader.get("glDisable",         [ctypes.c_uint], None),
        glDepthMask       = _loader.get("glDepthMask",       [ctypes.c_uint], None),
        glBlendFunc       = _loader.get("glBlendFunc",       [ctypes.c_uint, ctypes.c_uint], None),
        glFinish          = _loader.get("glFinish",          [], None),
        glGetError        = _loader.get("glGetError",        [], ctypes.c_uint),
    )

    def check_error(checkpoint):
        # glGetError() returns (and clears) only the first error since the last
        # call, so checking at each named checkpoint localizes which GL call
        # actually failed -- Python-level try/except never sees these, since
        # ctypes does not raise on GL errors.
        if not gl.glGetError:
            return
        err = gl.glGetError()
        if err != 0:
            fld_logger.render_debug_throttled(
                f"gl_error_{checkpoint}",
                f"SceneVoxel: GL error 0x{err:04x} at '{checkpoint}'")

    gl.check_error = check_error
    return gl


class FrameContext:
    """What one render callback worked out before any pass ran.

    Everything here is per-frame and dies with the callback; anything that has
    to survive to the next frame belongs on the renderer instead.
    """
    __slots__ = ("gl", "w", "h", "target_w", "target_h", "res_scale",
                 "interactive", "profiling", "proj_data", "mv_data",
                 "prev_fbo", "has_analytical")

    def __init__(self, gl, w, h, target_w, target_h, res_scale,
                 interactive, profiling, prev_fbo, has_analytical):
        self.gl             = gl
        self.w              = w
        self.h              = h
        self.target_w       = target_w
        self.target_h       = target_h
        #: Fraction of the native-sized G-buffer/SSAO/blur textures actually
        #: populated this frame. Those shaders use it to map back into the right
        #: sub-rect regardless of which resolution was last rendered.
        self.res_scale      = res_scale
        self.interactive    = interactive
        self.profiling      = profiling
        self.prev_fbo       = prev_fbo
        self.has_analytical = has_analytical
        self.proj_data      = None
        self.mv_data        = None

    def read_camera_matrices(self):
        """GL_PROJECTION_MATRIX and GL_MODELVIEW_MATRIX, column-major.

        Read once per frame: Pass 1 needs both (the inverse MVP for ray
        generation, the modelview for view-space output) and Pass 2 needs the
        projection to re-project its hemisphere samples.
        """
        self.proj_data = (ctypes.c_float * 16)()
        self.gl.glGetFloatv(0x0BA7, self.proj_data)   # GL_PROJECTION_MATRIX
        self.mv_data = (ctypes.c_float * 16)()
        self.gl.glGetFloatv(0x0BA6, self.mv_data)     # GL_MODELVIEW_MATRIX

    def finish_if_profiling(self):
        """Drain the GPU so the pass that just ran owns its own time.

        Every pass is timed by a glFinish at its END; a glFinish drains
        everything queued since the last one, so without a sync per pass the
        first pass to sync inherits all of its predecessors' work. Only paid
        while profiling (VX-039, LE-001).
        """
        if self.profiling and self.gl.glFinish:
            self.gl.glFinish()


def _scene_bounds(analytical_data):
    """The box enclosing every field the baker is about to see."""
    mn = FreeCAD.Vector(min(d["bbox_min"].x for d in analytical_data),
                        min(d["bbox_min"].y for d in analytical_data),
                        min(d["bbox_min"].z for d in analytical_data))
    mx = FreeCAD.Vector(max(d["bbox_max"].x for d in analytical_data),
                        max(d["bbox_max"].y for d in analytical_data),
                        max(d["bbox_max"].z for d in analytical_data))
    return mn, mx


def _bake_resolution(analytical_data, scene_extent, interactive):
    """Voxels per axis for this bake, and a warning when the scene is clamped.

    Bake coarser while dragging: the volume is the whole cost of an edit -- a
    growing scene bbox remaps it, so every tick is a full bake -- and the saving
    is cubic in the divisor, unlike the screen downscale. This is only read on a
    frame that was going to bake anyway, so orbiting an unchanged scene never
    triggers the reallocation, and the first frame after a drag ends is a
    quality frame that bakes at full resolution.
    """
    from freecad.fields.core.fld_settings import (get_voxel_grid_resolution,
                                                  get_interactive_voxel_scaling)

    base_res = get_voxel_grid_resolution()
    for d in analytical_data:
        fld = d.get("field")
        if fld is not None and hasattr(fld, "preferred_scene_resolution"):
            pref = fld.preferred_scene_resolution(scene_extent)
            if pref is not None:
                base_res = max(base_res, pref)

    if base_res > MAX_VOXEL_RES and not interactive:
        longest = max(scene_extent)
        scene_vox = longest / float(MAX_VOXEL_RES)
        fld_logger.warn(f"scene_volume: resolution clamped to {MAX_VOXEL_RES}; "
                        f"scene voxel {scene_vox:.3f} mm")

    div = get_interactive_voxel_scaling() if interactive else 1
    return max(32, min(base_res, MAX_VOXEL_RES) // max(1, div))


def bake_scene_volume(r, ctx):
    """Re-bake the scene volume if anything marked it dirty. No-op otherwise.

    Bills itself to `voxel_bake`, and separately to `voxel_bake_full` or
    `voxel_bake_region` -- a full bake and a region rebake are the same call
    costing wildly different amounts, and which one a drag gets is the question
    those two counters exist to answer.
    """
    from freecad.fields.core.fld_settings import get_voxel_band_voxels
    from freecad.fields.core.gl.gl_program import memory_barrier

    analytical_data = r._last_analytical_data or []
    if not analytical_data or not r._volume_dirty:
        return

    sv = r._scene_volume
    mn, mx = _scene_bounds(analytical_data)
    t_bake = time.perf_counter()

    scene_extent = (mx.x - mn.x, mx.y - mn.y, mx.z - mn.z)
    vox_res = _bake_resolution(analytical_data, scene_extent, ctx.interactive)
    slack = VOLUME_SLACK_FRAC if ctx.interactive else 0.0
    sv.ensure(mn, mx, vox_res, get_voxel_band_voxels(), slack_frac=slack)

    region = None if r._dirty_region_full else r._dirty_region
    # ensure() may have moved the mapping and set sv.dirty, in which case bake()
    # drops the region -- so ask AFTER ensure() and BEFORE bake() clears it.
    was_full = region is None or sv.dirty
    sv.bake(analytical_data, r, region=region)
    r._dirty_region = None
    r._dirty_region_full = False
    r._volume_dirty = False
    memory_barrier(BARRIER_IMAGE_AND_TEXTURE)

    # `sv.bake` only DISPATCHES -- the compute work is async and the barrier
    # orders it against the march without blocking the CPU -- so timing it alone
    # would measure the dispatch and nothing else, and its real cost would land
    # in whichever counter next hit a glFinish. Finish inside the bracket so
    # each counter owns its own GPU time (VX-039).
    ctx.finish_if_profiling()
    dt_bake = time.perf_counter() - t_bake
    r._perf.add("voxel_bake", dt_bake)
    r._perf.add("voxel_bake_full" if was_full else "voxel_bake_region", dt_bake)


def _set_march_uniforms(prog, ctx, sv):
    """Camera, volume mapping, material and hatch uniforms for the march."""
    proj_np = np.array(list(ctx.proj_data), dtype=np.float64).reshape(4, 4, order='F')
    mv_np   = np.array(list(ctx.mv_data),   dtype=np.float64).reshape(4, 4, order='F')
    inv_mvp = np.linalg.inv(proj_np @ mv_np)

    prog.set_2i("u_resolution", ctx.target_w, ctx.target_h)
    prog.set_mat4("u_inv_mvp", inv_mvp.flatten(order='F').astype(np.float32).tolist())
    prog.set_mat4("u_mv",      list(ctx.mv_data))
    prog.set_mat4("u_proj",    list(ctx.proj_data))
    prog.set_3f("u_light_dir", 0.4472, 0.7454, 0.4943)

    diag_voxels = 1.75 * max(sv.nx, sv.ny, sv.nz)
    prog.set_1i("u_max_steps", min(max(512, int(2.0 * diag_voxels)), 1024))
    prog.set_3f("u_vol_min", float(sv.vol_min.x), float(sv.vol_min.y), float(sv.vol_min.z))
    prog.set_3f("u_vol_max", float(sv.vol_max.x), float(sv.vol_max.y), float(sv.vol_max.z))
    prog.set_3f("u_voxel_step", float(sv.step.x), float(sv.step.y), float(sv.step.z))
    prog.set_1f("u_band", float(sv.band))
    prog.set_1f("u_step_div", float(getattr(sv, "lip", 1.0)))
    prog.set_3f("u_base_color", 0.8, 0.8, 0.8)
    prog.set_3f("u_spec_color", 1.0, 1.0, 1.0)
    prog.set_1f("u_shininess", 32.0)

    # ParamGet at bind time matches the precedent for u_outline_size in Pass 4.
    # This runs once per frame, not per voxel; if it ever shows up in a drag
    # profile, cache it on the renderer and invalidate from on_prefs_changed --
    # do not guess.
    from freecad.fields.core.fld_settings import (
        get_hatch_size, get_hatch_color, get_hatch_strength)
    hc = get_hatch_color()
    prog.set_3f("u_hatch_color", float(hc[0]), float(hc[1]), float(hc[2]))
    # The preference is in SCREEN pixels. During navigation the march runs at
    # target_w x target_h and the composite upscales by u_res_scale, so one
    # march pixel covers 1/res_scale screen pixels. Without this factor the
    # hatch period doubles on mouse-down at scale 0.5 and snaps back on release.
    prog.set_1f("u_hatch_period", float(get_hatch_size()) * ctx.res_scale)
    prog.set_1f("u_hatch_strength", float(get_hatch_strength()))


def _bind_volumes(gl, prog, sv):
    """Bind the distance, id and normal volumes and name their units."""
    for unit, tex_id, uniform in (
            (UNIT_VOLUME,      sv.tex_id,                        "u_volume"),
            (UNIT_ID_VOLUME,   sv.id_tex_id,                     "u_id_volume"),
            (UNIT_NORM_VOLUME, getattr(sv, "norm_tex_id", 0),    "u_norm_volume")):
        gl.glActiveTexture(GL_TEXTURE0 + unit)
        gl.glBindTexture(GL_TEXTURE_3D, tex_id)
        prog.set_1i(uniform, unit)


def _unbind_volumes(gl):
    for unit in (UNIT_VOLUME, UNIT_ID_VOLUME, UNIT_NORM_VOLUME):
        gl.glActiveTexture(GL_TEXTURE0 + unit)
        gl.glBindTexture(GL_TEXTURE_3D, 0)
    gl.glActiveTexture(GL_TEXTURE0)


#: (image unit, g-buffer attachment or None for the depth image, format).
#: None means the r32f depth image, which is not an FBO attachment.
_MARCH_IMAGES = ((0, 0, GL_RGBA16F), (1, 1, GL_RGBA16F),
                 (2, 2, GL_RGBA32F), (3, None, GL_R32F))


def _bind_march_images(r, bind_image_texture):
    for unit, attachment, fmt in _MARCH_IMAGES:
        tex = (r._depth_img_tex if attachment is None
               else r._gbuf_fbo.color_texture(attachment))
        bind_image_texture(unit, tex, GL_WRITE_ONLY, fmt)


def _unbind_march_images(bind_image_texture):
    for unit, _attachment, fmt in _MARCH_IMAGES:
        bind_image_texture(unit, 0, GL_WRITE_ONLY, fmt)


def pass1_gbuffer(r, ctx):
    """Bake if dirty, then march the volume into the G-buffer.

    With no compute support and no active fields there is nothing to march, so
    the G-buffer is simply cleared and Pass 4 composites an empty frame.
    """
    from freecad.fields.core.render.scene_volume import SceneVolume
    from freecad.fields.core.render.voxel_march_shader import VOXEL_MARCH_SRC
    from freecad.fields.core.gl.gl_program import bind_image_texture, memory_barrier

    gl = ctx.gl

    if not ctx.has_analytical:
        t_start = time.perf_counter()
        r._gbuf_fbo.bind()
        gl.glViewport(0, 0, ctx.target_w, ctx.target_h)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        gl.glBindFramebuffer(GL_FRAMEBUFFER, 0)
        ctx.finish_if_profiling()
        r._perf.add("pass1_dispatch", time.perf_counter() - t_start)
        return

    if r._scene_volume is None:
        r._scene_volume = SceneVolume()
    if r._voxel_prog is None:
        r._voxel_prog = r._compute_program_for(VOXEL_MARCH_SRC, key="voxel_march_v1")

    bake_scene_volume(r, ctx)

    t_start = time.perf_counter()
    sv = r._scene_volume
    _bind_march_images(r, bind_image_texture)

    prog = r._voxel_prog
    prog.use()
    _set_march_uniforms(prog, ctx, sv)

    if r._scene and r._scene.fields and r._gl_buf.glGenBuffers is not None:
        snapshot = r._field_meta.upload_and_bind(
            r._gl_buf, r._scene.fields, r._selected_labels)
        if snapshot is not None:
            r._selected_labels_last_march = snapshot

    _bind_volumes(gl, prog, sv)
    prog.dispatch_compute((ctx.target_w + 7) // 8, (ctx.target_h + 7) // 8)
    memory_barrier(BARRIER_IMAGE_AND_TEXTURE)

    _unbind_volumes(gl)
    _unbind_march_images(bind_image_texture)

    ctx.finish_if_profiling()
    r._perf.add("pass1_dispatch", time.perf_counter() - t_start)


def pass2_ssao(r, ctx):
    """Screen-space ambient occlusion from the G-buffer's position + normal."""
    gl = ctx.gl
    t_start = time.perf_counter()

    r._ssao_fbo.bind()
    gl.glViewport(0, 0, ctx.target_w, ctx.target_h)
    gl.glClear(GL_COLOR_BUFFER_BIT)

    prog = r._prog_ssao
    prog.use()
    gl.glActiveTexture(GL_TEXTURE0 + 0)
    gl.glBindTexture(GL_TEXTURE_2D, r._gbuf_fbo.color_texture(1))   # vs_pos
    prog.set_1i("u_pos", 0)
    gl.glActiveTexture(GL_TEXTURE0 + 1)
    gl.glBindTexture(GL_TEXTURE_2D, r._gbuf_fbo.color_texture(2))   # vs_normal
    prog.set_1i("u_normal", 1)
    gl.glActiveTexture(GL_TEXTURE0 + 2)
    gl.glBindTexture(GL_TEXTURE_2D, r._noise_tex_id)
    prog.set_1i("u_noise", 2)
    prog.set_2f("u_noise_scale", ctx.target_w / 4.0, ctx.target_h / 4.0)
    prog.set_3fv("u_samples", 64, r._ssao_kernel_flat)
    prog.set_mat4("u_proj", list(ctx.proj_data))
    prog.set_1f("u_res_scale", ctx.res_scale)
    prog.draw_fullscreen_quad()
    gl.check_error("pass2_ssao")

    # The sync here only exists to attribute SSAO and blur separately; Pass 3's
    # own finish already covers both when profiling is off.
    ctx.finish_if_profiling()
    r._perf.add("pass2_ssao", time.perf_counter() - t_start)


def pass3_blur(r, ctx):
    """4x4 box blur of the raw occlusion buffer."""
    gl = ctx.gl
    t_start = time.perf_counter()

    r._blur_fbo.bind()
    gl.glViewport(0, 0, ctx.target_w, ctx.target_h)
    gl.glClear(GL_COLOR_BUFFER_BIT)

    prog = r._prog_blur
    prog.use()
    gl.glActiveTexture(GL_TEXTURE0 + 0)
    gl.glBindTexture(GL_TEXTURE_2D, r._ssao_fbo.color_texture(0))
    prog.set_1i("u_ssao", 0)
    # Texel size is relative to the native-sized backing texture, not the
    # (possibly smaller) populated sub-rect -- see u_res_scale.
    prog.set_2f("u_texel_size", 1.0 / ctx.w, 1.0 / ctx.h)
    prog.set_1f("u_res_scale", ctx.res_scale)
    prog.draw_fullscreen_quad()
    gl.check_error("pass3_blur")

    ctx.finish_if_profiling()
    r._perf.add("pass3_blur", time.perf_counter() - t_start)


def pass4_composite(r, ctx):
    """Composite colour x occlusion into Coin3D's framebuffer, plus outlines.

    Runs every Coin3D frame (it is cheap) from whatever Passes 1-3 last left in
    the FBOs, so the SDF overlay never disappears between expensive frames.
    """
    gl = ctx.gl
    t_start = time.perf_counter()

    gl.glBindFramebuffer(GL_FRAMEBUFFER, ctx.prev_fbo)
    gl.glViewport(0, 0, ctx.w, ctx.h)

    prog = r._prog_comp
    prog.use()
    gl.glActiveTexture(GL_TEXTURE0 + 0)
    gl.glBindTexture(GL_TEXTURE_2D, r._gbuf_fbo.color_texture(0))   # Phong colour
    prog.set_1i("u_color", 0)
    gl.glActiveTexture(GL_TEXTURE0 + 1)
    gl.glBindTexture(GL_TEXTURE_2D, r._blur_fbo.color_texture(0))   # blurred AO
    prog.set_1i("u_occlusion", 1)
    gl.glActiveTexture(GL_TEXTURE0 + 2)
    # The compute path writes an r32f depth image; without it, the depth24 FBO
    # attachment is what Coin3D's own drawing left behind.
    depth_tex = (r._depth_img_tex if ctx.has_analytical and r._depth_img_tex
                 else r._gbuf_fbo.depth_texture())
    gl.glBindTexture(GL_TEXTURE_2D, depth_tex)
    prog.set_1i("u_depth", 2)
    gl.glActiveTexture(GL_TEXTURE0 + 3)
    gl.glBindTexture(GL_TEXTURE_2D, r._gbuf_fbo.color_texture(1))   # vs pos, vis_alpha in .w
    prog.set_1i("u_pos", 3)
    gl.glActiveTexture(GL_TEXTURE0 + 4)
    gl.glBindTexture(GL_TEXTURE_2D, r._gbuf_fbo.color_texture(2))   # vs normal, surface id in .w
    prog.set_1i("u_norm", 4)

    # Bounds for the SSBO lookup. The FieldMeta buffer is still bound at binding
    # point 3 from the compute pass -- SSBO binding points are context state,
    # not program state -- so this pass reads the very rows the march read, with
    # no second upload.
    prog.set_1i("u_field_count", int(r._field_meta.count))
    from freecad.fields.core.fld_settings import get_sdf_selection_outline_size
    prog.set_1i("u_outline_size", get_sdf_selection_outline_size())
    # Gate the outline sweep on the selection state as of the last march, not
    # the current frame -- they differ by one event whenever Pass 4 runs ahead
    # of Pass 1 (VR-030).
    prog.set_1i("u_any_selected", 1 if r._selected_labels_last_march else 0)
    prog.set_1f("u_res_scale", ctx.res_scale)

    if gl.glBlendFunc:
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    gl.glEnable(GL_BLEND)
    prog.draw_fullscreen_quad()
    gl.check_error("pass4_composite")
    gl.glDisable(GL_BLEND)

    ctx.finish_if_profiling()
    r._perf.add("pass4_composite", time.perf_counter() - t_start)


def unbind_texture_units(gl):
    """Release the five 2D units the passes bound, highest first."""
    for unit in (4, 3, 2, 1, 0):
        gl.glActiveTexture(GL_TEXTURE0 + unit)
        gl.glBindTexture(GL_TEXTURE_2D, 0)


def save_gl_state(gl):
    """Depth test, blend and depth-write, as Coin3D must find them again."""
    saved = []
    for cap in (_GL_DEPTH_TEST, GL_BLEND, _GL_DEPTH_WRITEMASK):
        out = (ctypes.c_uint * 1)(0)
        gl.glGetIntegerv(cap, ctypes.cast(out, ctypes.POINTER(ctypes.c_int)))
        saved.append(out[0])
    return tuple(saved)


def restore_gl_state(gl, saved):
    depth_enabled, blend_enabled, depth_write = saved
    (gl.glEnable if depth_enabled else gl.glDisable)(_GL_DEPTH_TEST)
    (gl.glEnable if blend_enabled else gl.glDisable)(GL_BLEND)
    gl.glDepthMask(depth_write)


def resize_fbos_if_needed(r, gl, w, h):
    """Swap in a new FBO set when the NATIVE window size changes.

    Returns (w, h, pending_fbo_set, old_fbo_set, force_full), or a leading
    None when this frame cannot render at all. The new set is assigned
    straight away but the old one is kept and only destroyed once Pass 1-3
    have rendered into the new one -- see `roll_back_fbo_swap`.

    Deliberately NOT triggered by the interactive/quality resolution toggle.
    FBOs are always allocated at native (w, h); the interactive path renders
    into a (target_w x target_h) sub-rect of them via glViewport, and the
    SSAO/blur/composite shaders read that sub-rect back via u_res_scale. So
    toggling downscaling never touches a GL object -- previously every single
    interactive<->quality transition destroyed and recreated all the
    SSAO/blur FBOs and the depth texture, which (per captured logs) happened
    several times a second during ordinary navigation and was the root cause
    of the SDF randomly vanishing.
    """
    if (w, h) == r._vp_size:
        return w, h, None, None, False

    try:
        pending_fbo_set = r._build_fbos(w, h)
    except Exception:
        fld_logger.exception(f"SceneVoxel: _build_fbos failed (native={w}x{h})")
        pending_fbo_set = None
    gl.check_error("build_fbos")

    if pending_fbo_set is None:
        # Keep rendering at the last successfully-built native size this frame.
        if r._gbuf_fbo is None:
            return None, None, None, None, False
        return r._vp_size[0], r._vp_size[1], None, None, False

    old_fbo_set = (r._gbuf_fbo, r._ssao_fbo, r._blur_fbo, r._depth_img_tex)
    (r._gbuf_fbo, r._ssao_fbo,
     r._blur_fbo, r._depth_img_tex) = pending_fbo_set
    return w, h, pending_fbo_set, old_fbo_set, True


def roll_back_fbo_swap(r, ctx, pending_fbo_set, old_fbo_set):
    """Undo a resize whose first render failed, in place on `ctx`.

    Restores the last good FBOs and discards the broken new set, so the
    composite pass still has valid content to show rather than the SDF
    overlay vanishing for a frame.
    """
    fld_logger.error(
        f"SceneVoxel: rolling back resolution swap to {r._vp_size} "
        f"after Pass 1-3 failure at target {ctx.target_w}x{ctx.target_h}")
    (r._gbuf_fbo, r._ssao_fbo,
     r._blur_fbo, r._depth_img_tex) = old_fbo_set
    r._destroy_fbo_set(*pending_fbo_set)
    ctx.w, ctx.h = r._vp_size
    ctx.target_w = min(ctx.target_w, ctx.w)
    ctx.target_h = min(ctx.target_h, ctx.h)
    ctx.res_scale = (ctx.target_w / ctx.w) if ctx.w > 0 else 1.0


def resolve_target_size(r, w, h):
    """The resolution Passes 1-3 render at, and the nav state behind it.

    _FldNavFilter drives _render_state via MouseButtonPress/Release and
    Wheel events. `just_went_quality` makes exactly one analytic frame fire
    after nav ends, even if Coin3D's render-rate cap would otherwise skip it.
    """
    from freecad.fields.core.fld_settings import get_interactive_resolution_scaling

    interactive = (r._render_state == _RS_INTERACTIVE)
    just_went_quality = r._prev_interactive_state and not interactive
    r._prev_interactive_state = interactive

    scale = get_interactive_resolution_scaling()
    if interactive and scale > 1:
        target_w, target_h = max(1, w // scale), max(1, h // scale)
    else:
        target_w, target_h = w, h
    res_scale = (target_w / w) if w > 0 else 1.0
    return target_w, target_h, res_scale, interactive, just_went_quality


def render_frame(r, userdata, action):
    """Set up the frame, run whichever passes are due, put GL back.

    The passes themselves live in voxel_render_passes; what stays here is
    everything that decides WHETHER and AT WHAT SIZE they run -- the GL
    state Coin3D must find intact, the FBO resize and its rollback, and the
    throttle that separates the interactive path from the quality one.
    """
    if not action.isOfType(coin.SoGLRenderAction.getClassTypeId()):
        return
    r._last_action = action
    if not r._active_uniforms or r._active_uniforms.get("n_fields", 0) == 0:
        return

    r._perf.note_frame()
    r._cb_counted = True

    # Lazy-initialise programs + noise texture on first call
    if r._prog_ssao is None:
        try:
            r._init_gl_programs()
        except Exception as e:
            fld_logger.render_debug(f"SceneVoxel: _init_gl_programs failed: {e}")
            return

    # Upload pending heightmap images as GL textures (must be inside GL context)
    r._upload_pending_heightmap_textures()

    gl = load_frame_entrypoints()

    # Viewport size — check before modifying any GL state
    viewport = (ctypes.c_int * 4)(0, 0, 0, 0)
    gl.glGetIntegerv(0x0BA2, viewport)   # GL_VIEWPORT
    w, h = viewport[2], viewport[3]
    if w <= 0 or h <= 0:
        return

    # Save Coin3D's current FBO
    prev_fbo = (ctypes.c_int * 1)(0)
    gl.glGetIntegerv(0x8CA6, prev_fbo)   # GL_FRAMEBUFFER_BINDING

    saved_state = save_gl_state(gl)
    # Our passes need depth write and no blending
    gl.glEnable(_GL_DEPTH_TEST)
    gl.glDepthMask(1)
    gl.glDisable(GL_BLEND)

    w, h, pending_fbo_set, old_fbo_set, force_full = resize_fbos_if_needed(r, gl, w, h)
    if w is None:
        return

    (target_w, target_h, res_scale,
     interactive, just_went_quality) = resolve_target_size(r, w, h)

    from freecad.fields.core.fld_settings import get_perf_profiler_enabled
    profiling = get_perf_profiler_enabled()
    if profiling:
        # Drain whatever the host still had queued BEFORE any of our passes
        # start, and bill it to its own stage. Every pass is timed by a
        # glFinish at its END, which drains everything queued since the last
        # one -- so without a drain here, the first pass to sync inherits all
        # of Coin3D's drawing for this frame (handles, control cages, lines).
        # Passes 1-3 are throttled and Pass 4 is not, so on most callbacks
        # Pass 4 was the first sync and absorbed the lot: that is why
        # pass4_composite read 4-8 ms/call for one fullscreen quad. Charged
        # to `inherited_queue` rather than hidden, because how much work the
        # host arrives with is worth seeing, and it keeps callback_total
        # equal to the sum of its parts.
        t_inherit = time.perf_counter()
        if gl.glFinish:
            gl.glFinish()
        r._perf.add("inherited_queue", time.perf_counter() - t_inherit)

    ctx = FrameContext(
        gl=gl, w=w, h=h, target_w=target_w, target_h=target_h,
        res_scale=res_scale,
        interactive=interactive,
        profiling=profiling,
        prev_fbo=prev_fbo[0],
        has_analytical=(r._depth_img_tex is not None
                        and getattr(r, "_compute_supported", True)),
    )

    if force_full:
        # Not throttled — a native window resize is rare, so logging every
        # occurrence is cheap and lets us correlate it with anything odd.
        fld_logger.render_debug(f"SceneVoxel: native FBO resize attempted this frame -> {w}x{h}")

    # In INTERACTIVE state always re-render (the downscaled march keeps cost
    # low). In QUALITY state cap at 30 fps to reduce idle GPU load.
    now = time.perf_counter()
    run_expensive = (force_full or ctx.interactive or just_went_quality
                     or (now - r._last_render_t) >= (1.0 / 30.0))
    if run_expensive:
        r._last_render_t = now
        r._run_expensive_last = True
        try:
            ctx.read_camera_matrices()
            pass1_gbuffer(r, ctx)
            if ctx.has_analytical:
                pass2_ssao(r, ctx)
                pass3_blur(r, ctx)
        except Exception:
            fld_logger.exception("SceneVoxel: Pass 1-3 render failed")
            if pending_fbo_set is not None:
                roll_back_fbo_swap(r, ctx, pending_fbo_set, old_fbo_set)
                pending_fbo_set = None
        else:
            r._gbuf_res_scale = ctx.res_scale
            if pending_fbo_set is not None:
                fld_logger.render_debug(f"SceneVoxel: native FBO resize committed -> {w}x{h}")
                r._destroy_fbo_set(*old_fbo_set)
                r._vp_size = (w, h)
                pending_fbo_set = None

    # Pass 4 runs on every callback, from whatever Passes 1-3 last left in
    # the FBOs. Guard everything it dereferences, not just the G-buffer.
    if (r._gbuf_fbo is not None and r._blur_fbo is not None
            and r._prog_comp is not None and ctx.w > 0 and ctx.h > 0):
        pass4_composite(r, ctx)
    else:
        fld_logger.error("SceneVoxel: no valid FBO set to composite; skipping Pass 4")

    unbind_texture_units(gl)
    gl.glUseProgram(0)
    restore_gl_state(gl, saved_state)

    if getattr(r, "_run_expensive_last", False):
        r._run_expensive_last = False

    r._perf.maybe_flush()


def pick_surface_at(r, x: int, y: int):
    """Read back the surface ID at window coordinates (x, y).

    Returns int surface_id (or None if no hit / 65535).
    """
    if not r._gbuf_fbo or not getattr(r._gbuf_fbo, "id", 0):
        return None
    w, h = r._vp_size
    if w <= 0 or h <= 0 or x < 0 or x >= w or y < 0 or y >= h:
        return None

    # Scale according to the resolution scale currently recorded in the G-buffer (LV-010)
    res_scale = getattr(r, "_gbuf_res_scale", 1.0)
    max_x = max(0, int(w * res_scale) - 1)
    max_y = max(0, int(h * res_scale) - 1)
    rx = min(max_x, max(0, int(x * res_scale)))
    # Flip y: GL origin is bottom-left, Qt is top-left
    ry = min(max_y, max(0, int((h - 1 - y) * res_scale)))

    from freecad.fields.core.gl.gl_texture3d import _loader
    GL_READ_FRAMEBUFFER = 0x8CA8
    GL_COLOR_ATTACHMENT2 = 0x8CE2
    GL_RGBA = 0x1908
    GL_FLOAT = 0x1406
    glBindFramebuffer = _loader.get("glBindFramebuffer", [ctypes.c_uint, ctypes.c_uint], None)
    glReadBuffer = _loader.get("glReadBuffer", [ctypes.c_uint], None)
    glReadPixels = _loader.get("glReadPixels", [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p], None)
    glGetIntegerv = _loader.get("glGetIntegerv", [ctypes.c_uint, ctypes.POINTER(ctypes.c_int)], None)

    if not (glBindFramebuffer and glReadBuffer and glReadPixels and glGetIntegerv):
        return None

    prev_fbo = (ctypes.c_int * 1)(0)
    glGetIntegerv(0x8CA6, prev_fbo)

    try:
        glBindFramebuffer(GL_READ_FRAMEBUFFER, r._gbuf_fbo.id)
        glReadBuffer(GL_COLOR_ATTACHMENT2)
        buf = (ctypes.c_float * 4)()
        glReadPixels(rx, ry, 1, 1, GL_RGBA, GL_FLOAT, ctypes.byref(buf))
        sid_float = buf[3]
        if sid_float >= 65534.5 or sid_float < -0.5:
            return None
        return int(round(sid_float))
    except Exception as e:
        fld_logger.debug(f"pick_surface_at error: {e}")
        return None
    finally:
        if glBindFramebuffer:
            glBindFramebuffer(GL_READ_FRAMEBUFFER, prev_fbo[0])


def get_field_texture3d(r, label, sampler_name, provider, action):
    """Return {"tex": GLTexture3D, "uniforms": {...}} for one sampler3D, or None.

    Bakes and uploads only when the provider's key changes. `texture3d_key()` is
    called on every bake and must stay cheap; `texture3d_data()` is the expensive
    half and is reached only on a miss -- a deform cage's payload costs 100.3 ms.
    Must be called inside the GL callback: GLTexture3D.upload queues, and
    _gl_callback performs, the actual GL call.
    """
    from freecad.fields.core.gl.gl_texture3d import GLTexture3D

    cache_key = (label, sampler_name)
    entry = r._tex3d_cache.get(cache_key)
    try:
        key = provider.texture3d_key()
    except Exception as e:
        fld_logger.error(f"_get_field_texture3d: {sampler_name} key failed: {e}")
        return None
    if key is None:
        return None
    if entry is not None and entry.get("key") == key:
        return entry

    _t0 = time.perf_counter()
    try:
        data = provider.texture3d_data()
    except Exception as e:
        fld_logger.error(f"_get_field_texture3d: {sampler_name} provider failed: {e}")
        return None
    if not data:
        return None
    tex = entry["tex"] if entry and entry["tex"]._fmt == data["fmt"] else GLTexture3D(fmt=data["fmt"])
    tex.upload(data["nx"], data["ny"], data["nz"], data["bytes"])
    tex._gl_callback(None, action)
    r._perf.add("tex3d_upload", time.perf_counter() - _t0)

    entry = {"tex": tex, "key": key, "uniforms": data.get("uniforms", {})}
    r._tex3d_cache[cache_key] = entry
    return entry
