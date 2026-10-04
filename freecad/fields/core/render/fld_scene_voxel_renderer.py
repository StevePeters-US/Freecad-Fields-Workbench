# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/fld_scene_voxel_renderer.py

Scene-level GPU voxel renderer. Evaluates analytic SDF fields via compute shaders
into distance (r16f), field-ID (r16ui) and normal (rgba8_snorm) 3D volumes, followed by G-buffer sphere-tracing
and multi-pass post-processing.
"""
import ctypes
import time

import FreeCAD
import FreeCADGui
import pivy.coin as coin

from freecad.fields.core import fld_logger

# The GLSL for the post-process passes, the quality-state constants, the
# profiler and the three observers all used to live inline here. They are
# imported rather than referenced through their modules so that the names
# other modules and tests already import FROM HERE keep resolving.
from freecad.fields.core.render.post_process_shaders import (  # noqa: F401
    _VERT_PASSTHROUGH, _FRAG_SSAO, _FRAG_BLUR, _FRAG_COMP)
from freecad.fields.core.render.render_state import _RS_QUALITY, _RS_INTERACTIVE  # noqa: F401
from freecad.fields.core.render.render_profiler import RenderProfiler
from freecad.fields.core.render.renderer_observers import (  # noqa: F401
    _FldNavFilter, _FldSelectionObserver, _FldDocumentObserver)

try:
    from PySide.QtWidgets import QApplication
except ImportError:
    from PySide.QtWidgets import QApplication

# The three pieces of GL state Coin3D must find exactly as it left them.
_GL_DEPTH_TEST      = 0x0B71
_GL_BLEND           = 0x0BE2
_GL_DEPTH_WRITEMASK = 0x0B72


class FldSceneVoxelRenderer:
    """Singleton scene-level voxel renderer."""
    _instance = None

    @property
    def _fields(self):
        return self._registry.fields

    @property
    def _compiled_fields(self):
        return self._registry.compiled_fields

    @property
    def _dirty_fields(self):
        return self._registry.dirty_fields

    @property
    def _heightmap_textures(self):
        return self._hmap_cache.textures

    @property
    def _hmap_sampler_bindings_to_make(self):
        return self._hmap_cache.bindings_to_make

    @_hmap_sampler_bindings_to_make.setter
    def _hmap_sampler_bindings_to_make(self, val):
        self._hmap_cache._bindings_to_make = val

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._install_doc_observer()
        return cls._instance

    def _install_doc_observer(self):
        from freecad.fields.core.render import scene_graph_attachment as sga
        sga.install_doc_observer(self)

    @classmethod
    def destroy(cls):
        if cls._instance is not None:
            obs = getattr(cls._instance, "_doc_observer", None)
            if obs is not None:
                try:
                    FreeCAD.removeDocumentObserver(obs)
                except Exception as e:
                    fld_logger.render_debug(f"FldSceneVoxelRenderer: document observer removal failed: {e}")
                cls._instance._doc_observer = None
            try:
                cls._instance._detach()
            finally:
                cls._instance = None

    @property
    def _field_meta_dirty(self):
        """Whether the FieldMeta SSBO must be repacked before the next march.

        A property rather than a plain attribute because the flag now lives on
        the buffer that owns the upload, while the things that set it -- the
        selection observer, an appearance refresh, a rebuild, a bake that
        renumbered surfaces -- reach the renderer, not the buffer.
        """
        return self._field_meta.dirty

    @_field_meta_dirty.setter
    def _field_meta_dirty(self, val):
        self._field_meta.dirty = bool(val)

    @property
    def _render_state(self):
        return getattr(self, "_render_state_val", _RS_QUALITY)

    @_render_state.setter
    def _render_state(self, val):
        old_val = getattr(self, "_render_state_val", None)
        if old_val != val:
            self._render_state_val = val

    def __init__(self):
        self._attached_graphs = []   # scene graphs this renderer's switch was added to
        self._doc_observer = None
        self._bbox_coords  = None
        self._batch_update_count = 0
        self._batch_needs_rebuild = False

        # Multi-pass SSAO renderer state
        self._active_uniforms  = {}   # populated by _rebuild(), consumed by render callback
        self._compute_supported = True # whether OpenGL compute shaders are supported
        self._prog_ssao  = None       # GLProgram: SSAO
        self._prog_blur  = None       # GLProgram: blur
        self._prog_comp  = None       # GLProgram: composition + gamma
        self._gbuf_fbo   = None       # GLFramebuffer: color0+vspos+vsnorm+depth
        self._ssao_fbo   = None       # GLFramebuffer: R16F occlusion
        self._blur_fbo   = None       # GLFramebuffer: R16F blurred occlusion
        self._noise_tex_id      = 0   # GL texture id: 4x4 random rotation vectors
        self._ssao_kernel_flat  = []  # 192 floats: 64 hemisphere samples (x,y,z each)
        self._vp_size    = (0, 0)     # Last known viewport (w, h) for resize detection
        self._last_render_t = 0.0     # Timestamp of last completed render pass
        self._perf = RenderProfiler() # Per-pass timing, logged under EnablePerfProfiler

        self._depth_img_tex      = 0      # r32f texture: window depth [0,1] written by compute

        # Compiled scene programs, keyed by their exact GLSL source. Without this
        # only the source currently loaded avoided a recompile, so anything that
        # returned the scene to a state it had already been in -- undo, hiding
        # and re-showing an object, leaving and re-entering a cage edit, a
        # selection change that adds or drops a field -- paid the driver compile
        # again. That is seconds once a stacked extrude is in the scene.
        # `GpuFieldEvaluator` has had the same cache (keyed by source hash) for
        # the octree/meshing path all along; the renderer simply never did.
        # Bounded because each entry holds a live GL program: evicting destroys
        # it, which is safe here because the render callback owns a GL context.
        from freecad.fields.core.render.compute_program_cache import ComputeProgramCache
        from freecad.fields.core.render.field_appearance import FieldAppearance
        from freecad.fields.core.render.heightmap_texture_cache import HeightmapTextureCache
        from freecad.fields.core.render.sdf_field_registry import SdfFieldRegistry
        from freecad.fields.core.render.scene_shader_builder import CompiledScene
        from freecad.fields.core.render.field_meta_ssbo import FieldMetaBuffer
        from freecad.fields.core.render import ssao_resources
        self._prog_cache               = ComputeProgramCache()
        self._appearance               = FieldAppearance()
        self._hmap_cache               = HeightmapTextureCache()
        self._registry                 = SdfFieldRegistry()
        self._scene                    = CompiledScene()

        self._cage_ssbo_cache          = {}    # label -> {"buf_id": buffer_id, "geometry_version": version}
        self._tex3d_cache              = {}    # (label, sampler_name) -> {"tex": GLTexture3D, "key": key, "uniforms": dict}
        self._scene_volume             = None  # SceneVolume, created lazily inside the GL context
        self._volume_dirty             = True
        self._dirty_region             = None
        self._dirty_region_full        = False
        self._field_meta               = FieldMetaBuffer()
        # Replaced with the real entry points by _init_gl_programs, on the first
        # frame. Until then every buffer call site sees None and skips.
        self._gl_buf                   = ssao_resources.no_buffer_entrypoints()
        self._voxel_prog               = None  # the fixed march program
        self._last_analytical_data     = None  # what the baker consumes
        self._selected_labels          = set() # set of "<Doc>.<Obj>" labels currently selected
        self._selected_labels_last_march = set() # snapshot as of the last SSBO upload (VR-030)
        self._sel_observer             = None

        self._render_state          = _RS_QUALITY   # _RS_QUALITY or _RS_INTERACTIVE
        self._gbuf_res_scale        = 1.0           # resolution scale of content currently in G-buffer (LV-010)
        self._prev_interactive_state = False         # for transition detection
        self._nav_filter            = None           # _FldNavFilter installed on QApplication

        self._root = coin.SoSeparator()
        for attr in ["renderCulling", "renderCaching", "boundingBoxCaching"]:
            try:
                getattr(self._root, attr).setValue(coin.SoSeparator.OFF)
            except AttributeError as e:
                fld_logger.render_debug(f"FldSceneVoxelRenderer.__init__: _root caching attr '{attr}' unset failed: {e}")
        self._switch = coin.SoSwitch()
        self._switch.addChild(self._root)
        self._switch.whichChild = -1
        self._setup_nodes()

    def _mark_dirty_region(self, region):
        from freecad.fields.core.render import scene_rebuild
        scene_rebuild.mark_dirty_region(self, region)

    @staticmethod
    def _active_scene_graph():
        from freecad.fields.core.render import scene_graph_attachment as sga
        return sga.active_scene_graph()

    @staticmethod
    def _live_scene_graphs():
        from freecad.fields.core.render import scene_graph_attachment as sga
        return sga.live_scene_graphs()

    def _prune_attached_graphs(self):
        from freecad.fields.core.render import scene_graph_attachment as sga
        sga.prune_attached_graphs(self)

    def _switch_in(self, sg):
        from freecad.fields.core.render import scene_graph_attachment as sga
        return sga.switch_in(self, sg)

    def _attach(self):
        from freecad.fields.core.render import scene_graph_attachment as sga
        sga.attach(self)

    def _detach(self):
        from freecad.fields.core.render import scene_graph_attachment as sga
        sga.detach(self)



    def register_heightmap_texture(self, label, image_path):
        from freecad.fields.core.render import scene_update_api as sua
        sua.register_heightmap_texture(self, label, image_path)

    def _upload_pending_heightmap_textures(self):
        from freecad.fields.core.render import scene_update_api as sua
        sua.upload_pending_heightmap_textures(self)

    def _get_field_texture3d(self, label, sampler_name, provider, action):
        from freecad.fields.core.render import voxel_render_passes as vp
        return vp.get_field_texture3d(self, label, sampler_name, provider, action)




    def _setup_nodes(self):
        # 1. Bounding-box debug wireframe (toggled by render debug mode)
        self._bbox_switch = coin.SoSwitch()
        self._bbox_sep = coin.SoSeparator()
        self._bbox_switch.addChild(self._bbox_sep)
        self._root.addChild(self._bbox_switch)

        from freecad.fields.core.fld_settings import get_render_debug_mode
        self._bbox_switch.whichChild = 0 if get_render_debug_mode() else -1

        mat = coin.SoMaterial()
        mat.transparency.setValue(1.0)
        self._bbox_sep.addChild(mat)
        pick = coin.SoPickStyle()
        pick.style.setValue(coin.SoPickStyle.UNPICKABLE)
        self._bbox_sep.addChild(pick)
        self._bbox_coords = coin.SoCoordinate3()
        self._bbox_sep.addChild(self._bbox_coords)
        bbox_lines = coin.SoIndexedLineSet()
        bbox_lines.coordIndex.setValues(0, 36, [
            0,1,-1, 1,3,-1, 3,2,-1, 2,0,-1,
            4,5,-1, 5,7,-1, 7,6,-1, 6,4,-1,
            0,4,-1, 1,5,-1, 2,6,-1, 3,7,-1
        ])
        self._bbox_sep.addChild(bbox_lines)

        # 2. Shader separator (holds callbacks + bbox expansion geometry)
        self._shader_sep = coin.SoSeparator()
        for attr in ["renderCulling", "renderCaching", "boundingBoxCaching"]:
            try:
                getattr(self._shader_sep, attr).setValue(coin.SoSeparator.OFF)
            except AttributeError as e:
                fld_logger.render_debug(f"FldSceneVoxelRenderer._setup_nodes: _shader_sep caching attr '{attr}' unset failed: {e}")

        quad_mat = coin.SoMaterial()
        quad_mat.transparency.setValue(0.0)
        self._shader_sep.addChild(quad_mat)

        # Multi-pass SSAO render callback (replaces SoShaderProgram)
        self._render_cb_node = coin.SoCallback()
        self._render_cb_node.setCallback(self._render_gl_callback)
        self._shader_sep.addChild(self._render_cb_node)

        # Degenerate geometry: 8 bbox-corner points rendered as degenerate triangles
        # so Coin3D computes a valid bounding box and does not cull the renderer.
        hints = coin.SoShapeHints()
        hints.vertexOrdering.setValue(coin.SoShapeHints.UNKNOWN_ORDERING)
        self._shader_sep.addChild(hints)

        self._coords = coin.SoCoordinate3()
        # 8 placeholder points; actual values set by _rebuild()
        self._coords.point.setValues(0, 8, [(0, 0, 0)] * 8)
        self._shader_sep.addChild(self._coords)

        faceset = coin.SoIndexedFaceSet()
        indices = []
        for i in range(8):
            indices.extend([i, i, i, -1])   # degenerate: each "triangle" is one point
        faceset.coordIndex.setValues(0, len(indices), indices)
        self._shader_sep.addChild(faceset)

        self._root.addChild(self._shader_sep)

    # -- Compute dispatch callback --

    def unregister_field(self, label):
        from freecad.fields.core.render import scene_update_api as sua
        sua.unregister_field(self, label)

    def set_field_visible(self, label, visible):
        from freecad.fields.core.render import scene_update_api as sua
        sua.set_field_visible(self, label, visible)

    def update_field(self, label, field):
        from freecad.fields.core.render import scene_update_api as sua
        sua.update_field(self, label, field)

    def begin_update_batch(self):
        from freecad.fields.core.render import scene_update_api as sua
        sua.begin_update_batch(self)

    def end_update_batch(self):
        from freecad.fields.core.render import scene_update_api as sua
        sua.end_update_batch(self)

    def gc_fields(self):
        from freecad.fields.core.render import scene_update_api as sua
        sua.gc_fields(self)

    def refresh_appearance(self):
        """Recompute appearance properties and mark _field_meta_dirty = True."""
        from freecad.fields.core.render import scene_update_api as sua
        sua.refresh_appearance(self)

    def on_prefs_changed(self):
        from freecad.fields.core.render import scene_update_api as sua
        sua.on_prefs_changed(self)

    def _init_gl_programs(self):
        """Compile the 3 post-process programs, build the SSAO kernel + noise
        texture, and resolve the GL entry points the SSBO path needs.
        Must be called inside an active GL context (i.e. from a SoCallback).
        """
        from freecad.fields.core.gl.gl_compute import check_compute_support
        from freecad.fields.core.render import ssao_resources

        self._gl_buf = ssao_resources.load_buffer_entrypoints()
        self._max_compute_texture_units = ssao_resources.max_compute_texture_units()
        # A fresh context means the old buffer id names nothing here any more.
        self._field_meta.forget_gl()

        self._compute_supported = check_compute_support()
        if not self._compute_supported:
            fld_logger.warn("SceneVoxel: OpenGL Compute Shaders are not supported on this system. SDF rendering will be disabled.")

        (self._prog_ssao, self._prog_blur,
         self._prog_comp) = ssao_resources.build_post_process_programs()
        self._ssao_kernel_flat = ssao_resources.make_ssao_kernel()   # 192 floats
        self._noise_tex_id     = ssao_resources.make_noise_texture()

    def _build_fbos(self, w: int, h: int):
        """See ssao_resources.build_fbos -- kept as a method because the render
        callback and the size-limit tests both reach for it by that name."""
        from freecad.fields.core.render import ssao_resources
        return ssao_resources.build_fbos(w, h)

    def _destroy_fbo_set(self, gbuf_fbo, ssao_fbo, blur_fbo, depth_tex):
        """Free GL resources for a (gbuf, ssao, blur, depth_tex) tuple from _build_fbos."""
        from freecad.fields.core.render import ssao_resources
        ssao_resources.destroy_fbo_set(gbuf_fbo, ssao_fbo, blur_fbo, depth_tex)


    def pick_surface_at(self, x: int, y: int):
        from freecad.fields.core.render import voxel_render_passes as vp
        return vp.pick_surface_at(self, x, y)

    def _compute_program_for(self, src, key=None):
        """The compiled program for this source or key, compiling only if new."""
        return self._prog_cache.get(src, key=key, protected=getattr(self, "_voxel_prog", None))

    def _render_gl_callback_inner(self, userdata, action):
        """Delegate to voxel_render_passes.render_frame.

        Kept as a method although SV-005 said to delete it:
        test_gl_texture3d_callback.py calls it unbound off the class, and
        bench_pass4_run.py names it. It is a plain delegate -- it must NOT
        patch vp.coin or any other module global.
        """
        from freecad.fields.core.render import voxel_render_passes as vp
        return vp.render_frame(self, userdata, action)

    def _render_gl_callback(self, userdata, action):
        """Execute 4-pass SSAO render inside Coin3D's GL context."""
        t0 = time.perf_counter()
        self._cb_counted = False
        try:
            self._render_gl_callback_inner(userdata, action)
        except Exception as e:
            fld_logger.error(f"SceneVoxel: render callback exception: {e}")
        finally:
            if getattr(self, "_cb_counted", False):
                self._perf.add("callback_total", time.perf_counter() - t0)


    def _compute_grid_params(self, field, cell_size: float) -> dict:
        """The clamped and padded bounding box for one field.
        See field_bbox.compute_grid_params; kept as a method because the size
        limit tests and the bake path both reach for it by that name."""
        from freecad.fields.core.render import field_bbox
        return field_bbox.compute_grid_params(field, cell_size)

    def _check_bbox_margin_safety(self, field, label, bmin, bmax):
        """Warn when a field's own bounding box is too tight for it.
        See field_bbox.check_bbox_margin_safety. Passed to the registry as a
        bound method, which is why it stays a method rather than a plain call."""
        from freecad.fields.core.render import field_bbox
        field_bbox.check_bbox_margin_safety(field, label, bmin, bmax)


    def _rebuild(self):
        from freecad.fields.core.render import scene_rebuild
        scene_rebuild.rebuild(self)


