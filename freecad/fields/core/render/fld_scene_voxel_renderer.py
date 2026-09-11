# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/fld_scene_voxel_renderer.py

Scene-level GPU voxel renderer. Evaluates analytic SDF fields via compute shaders
into distance (r16f), field-ID (r16ui) and normal (rgba8_snorm) 3D volumes, followed by G-buffer sphere-tracing
and multi-pass post-processing.
"""
import FreeCAD
import FreeCADGui
import pivy.coin as coin
import numpy as np
from freecad.fields.core import fld_logger
import math
import time
import ctypes
from freecad.fields.core.render.scene_volume import VOLUME_SLACK_FRAC

_VERT_PASSTHROUGH = """
#version 330 compatibility
out vec2 v_uv;
void main() {
    v_uv = gl_Vertex.xy;
    gl_Position = vec4(gl_Vertex.xy, 0.0, 1.0);
}
"""

_FRAG_SSAO = """
#version 330 compatibility
in vec2 v_uv;

uniform sampler2D u_pos;
uniform sampler2D u_normal;
uniform sampler2D u_noise;
uniform vec3      u_samples[64];
uniform mat4      u_proj;
uniform vec2      u_noise_scale;
uniform float     u_res_scale;

const float RADIUS = 50.0;
const float BIAS   = 0.025;

void main() {
    // G-buffer textures are allocated at native window size but only the
    // bottom-left (content_uv * u_res_scale) sub-rect holds this frame's
    // render — the rest is stale/garbage from a previous resolution. See
    // u_res_scale usage in the compute/composite passes for the same scheme.
    vec2 content_uv = v_uv * 0.5 + 0.5;
    vec2 uv = content_uv * u_res_scale;

    vec3 frag_pos = texture(u_pos, uv).xyz;
    vec3 n_raw    = texture(u_normal, uv).rgb;

    if (dot(n_raw, n_raw) < 0.25) {
        gl_FragColor = vec4(1.0);
        return;
    }

    vec3 normal   = normalize(n_raw);
    vec3 rand_vec = normalize(texture(u_noise, content_uv * u_noise_scale).rgb);

    vec3 tangent   = normalize(rand_vec - normal * dot(rand_vec, normal));
    vec3 bitangent = cross(normal, tangent);
    mat3 TBN       = mat3(tangent, bitangent, normal);

    float occlusion = 0.0;
    for (int i = 0; i < 64; i++) {
        vec3 s = frag_pos + TBN * u_samples[i] * RADIUS;

        vec4 offset = u_proj * vec4(s, 1.0);
        offset.xyz /= offset.w;
        offset.xyz  = offset.xyz * 0.5 + 0.5;
        vec2 sample_uv = clamp(offset.xy, 0.0, 1.0) * u_res_scale;

        float sample_depth = texture(u_pos, sample_uv).z;
        vec3  sample_n     = texture(u_normal, sample_uv).rgb;
        if (dot(sample_n, sample_n) > 0.25) {
            float range_check  = smoothstep(0.0, 1.0, RADIUS / abs(frag_pos.z - sample_depth));
            occlusion += (sample_depth >= s.z + BIAS ? 1.0 : 0.0) * range_check;
        }
    }

    float ao = 1.0 - (occlusion / 64.0);
    gl_FragColor = vec4(ao, ao, ao, 1.0);
}
"""

_FRAG_BLUR = """
#version 330 compatibility
in vec2 v_uv;

uniform sampler2D u_ssao;
uniform vec2      u_texel_size;
uniform float     u_res_scale;

void main() {
    vec2 uv = (v_uv * 0.5 + 0.5) * u_res_scale;
    float result = 0.0;
    for (int x = -2; x < 2; x++) {
        for (int y = -2; y < 2; y++) {
            vec2 offset = vec2(float(x), float(y)) * u_texel_size;
            result += texture(u_ssao, uv + offset).r;
        }
    }
    gl_FragColor = vec4(result / 16.0);
}
"""

_FRAG_COMP = """#version 430 compatibility
// 430, not 330, so this pass can read the same FieldMeta rows the march does.
// The outline colour is per object (ViewObject.SelectionColor) and there is no
// second copy of it: no palette uniform, no lookup texture beside the SSBO.
// A runtime-indexed uniform ARRAY would have been the other way to do it and is
// the one thing that must not come back here -- CP-014, NVIDIA register spill.
// The #version line stays first: comments before it are legal GLSL and are also
// the kind of thing a driver gets wrong.
in vec2 v_uv;

uniform sampler2D u_color;
uniform sampler2D u_pos;
uniform sampler2D u_occlusion;
uniform sampler2D u_depth;
uniform sampler2D u_norm;      // .w is the surface id the march wrote (TR-008)
uniform int       u_outline_size;
uniform int       u_field_count;
uniform float     u_res_scale;
uniform bool      u_any_selected;  // false → skip outline loop entirely (VR-030)

struct FieldMeta {
    vec4 bmin;
    vec4 bmax;
    vec4 diffuse;
    vec4 specular;
    vec4 flags;         // .x = selected (0/1), .yzw = selection outline colour
};
layout(std430, binding = 3) readonly buffer FieldMetaBlock { FieldMeta u_fields[]; };

// The id volume can outlive the table that was uploaded with it -- delete a
// field and the table shrinks while a clean volume is not rebaked -- so an id
// read back from the g-buffer is bounds-checked before it indexes the SSBO.
// Out of range means no outline, never a read past the end of the buffer.
bool outline_color_at(ivec2 pos, out vec3 col) {
    float sid = texelFetch(u_norm, pos, 0).w;
    int fid = int(sid + 0.5);
    if (sid < 0.0 || fid >= u_field_count) return false;
    col = u_fields[fid].flags.yzw;
    return true;
}

void main() {
    vec2 uv    = (v_uv * 0.5 + 0.5) * u_res_scale;
    ivec2 tex_size = textureSize(u_color, 0);
    // Only the bottom-left (tex_size * u_res_scale) sub-rect holds this frame's
    // content — clamp neighbor lookups to that, not the full backing texture,
    // or edge detection near the downscaled frame's border reads stale/unrelated data.
    ivec2 content_size = ivec2(vec2(tex_size) * u_res_scale);
    ivec2 center_pos = ivec2(gl_FragCoord.xy * u_res_scale);
    vec4 color = texelFetch(u_color, center_pos, 0);

    // img_color.a is the selection channel: 1.0 unselected, 0.5 selected.
    // It used to carry a third code, 0.3, meaning "selected AND subtractive",
    // read only to choose between two hardcoded outline colours. The colour now
    // comes from the selected object's own FieldMeta row, so that code had no
    // remaining reader and the march no longer writes it.
    float center_sel = (color.a > 0.2 && color.a < 0.6) ? 1.0 : 0.0;
    float edge = 0.0;
    ivec2 sel_pos = center_pos;   // the SELECTED side of the edge; its row wins

    // Only perform selection edge detection if the pixel is not on an unselected object (alpha ~1.0).
    // u_any_selected is false when the last-marched SSBO had no selected fields, so the
    // 289-iteration neighbourhood sweep is an identity and can be skipped entirely (VR-030).
    if (u_any_selected && color.a < 0.8) {
        int size = clamp(u_outline_size, 0, 8);
        for (int dx = -8; dx <= 8; dx++) {
            if (abs(dx) > size) continue;
            for (int dy = -8; dy <= 8; dy++) {
                if (abs(dy) > size) continue;
                if (float(dx*dx + dy*dy) > float(size*size) + 0.5) continue;
                if (dx == 0 && dy == 0) continue;
                ivec2 offset_pos = clamp(ivec2((gl_FragCoord.xy + vec2(float(dx), float(dy))) * u_res_scale), ivec2(0), content_size - ivec2(1));
                float neighbor_a = texelFetch(u_color, offset_pos, 0).a;
                float neighbor_sel = (neighbor_a > 0.2 && neighbor_a < 0.6) ? 1.0 : 0.0;
                if (center_sel != neighbor_sel) {
                    edge = 1.0;
                    sel_pos = (center_sel > 0.5) ? center_pos : offset_pos;
                    break;
                }
            }
            if (edge > 0.5) break;
        }
    }

    if (edge > 0.5) {
        vec3 outline_color;
        if (outline_color_at(sel_pos, outline_color)) {
            gl_FragColor = vec4(outline_color, 1.0);
            gl_FragDepth = texture(u_depth, uv).r;
            return;
        }
    }

    if (color.a < 0.2) discard;

    float ao    = texture(u_occlusion, uv).r;
    vec3 result = color.rgb * ao;
    result      = pow(result, vec3(1.0 / 2.2));

    float vis_alpha = texelFetch(u_pos, center_pos, 0).w;
    bool transparent = (vis_alpha < 0.95);
    gl_FragColor = vec4(result, transparent ? vis_alpha : 1.0);
    gl_FragDepth = transparent ? 0.9999 : texture(u_depth, uv).r;
}
"""


_RS_QUALITY     = 0   # full analytic shader + SSAO
_RS_INTERACTIVE = 1   # downscaled analytic shader, fewer march steps


try:
    from PySide.QtCore import QObject, QEvent, QTimer
    from PySide.QtWidgets import QApplication
except ImportError:
    from PySide.QtCore import QObject, QEvent, QTimer
    from PySide.QtWidgets import QApplication


class _FldNavFilter(QObject):
    """App-level event filter that drives the renderer state machine.

    MouseButtonPress  → _RS_INTERACTIVE (downscaled, fewer march steps)
    MouseButtonRelease → _RS_QUALITY    (native res, full march steps) + view.redraw()
    Wheel             → _RS_INTERACTIVE + 200ms debounce timer → _RS_QUALITY
    """

    def __init__(self, renderer):
        super().__init__()
        self._r = renderer
        self._wheel_timer = QTimer(self)
        self._wheel_timer.setSingleShot(True)
        self._wheel_timer.timeout.connect(self._on_wheel_end)

    def _redraw(self):
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            if view:
                view.redraw()
        except Exception as e:
            fld_logger.render_debug(f"_FldNavFilter._redraw failed: {e}")

    def _on_wheel_end(self):
        self._r._render_state = _RS_QUALITY
        self._redraw()

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.MouseButtonPress:
            self._wheel_timer.stop()
            self._r._render_state = _RS_INTERACTIVE
        elif t == QEvent.MouseButtonRelease:
            self._wheel_timer.stop()
            self._r._render_state = _RS_QUALITY
            self._redraw()
        elif t == QEvent.Wheel:
            self._r._render_state = _RS_INTERACTIVE
            self._wheel_timer.start(200)   # restart; 200ms after last scroll → quality
        return False   # never consume events


class RenderProfiler:
    """Accumulates per-pass render timings and periodically emits an [PERF]
    summary log, then resets. Gated on get_perf_profiler_enabled() — when off,
    stages are still cheaply tallied (a couple of perf_counter() calls) but
    never logged, matching the mesh_timer pattern in core/fld_mesher.py.

    Each stage tracks its own call count instead of sharing one frame counter:
    Pass 1-3 only run on 'expensive' frames (throttled to 30fps at rest) while
    Pass 4 (composite) runs every callback, so a shared divisor would
    understate the true per-call cost of the throttled passes.
    """
    # voxel_bake is split by KIND, not merged: a full bake and a region rebake
    # are the same call costing wildly different amounts, and which one a drag
    # gets is the open question (SceneVolume.ensure remaps the world->voxel
    # mapping whenever the scene box grows, and bake() then refuses a region).
    # One averaged column cannot answer it; two counts and two averages can.
    # `inherited_queue` is first because it is not our cost: it is the host's
    # queued drawing, drained at a known point so that no pass below inherits
    # it. Before it existed, Pass 4 -- the only unthrottled pass, and so usually
    # the first to glFinish -- was billed for all of it (LE-001).
    _STAGES = [
        "inherited_queue",
        "callback_total", "ssbo_pack",
        "tex3d_upload", "voxel_bake", "voxel_bake_full", "voxel_bake_region",
        "pass1_dispatch", "pass2_ssao", "pass3_blur", "pass4_composite",
        "rebuild_total", "rebuild_compile", "rebuild_shadergen",
    ]

    def __init__(self):
        self.reset()
        self.session_reset()

    def reset(self):
        self._totals = {}
        self._counts = {}
        self._frame_events = 0
        self._flush_t = time.perf_counter()

    def session_reset(self):
        """Start a new named span (a tool drag). Independent of the periodic
        flush above, which resets itself every couple of seconds and so cannot
        answer 'where did this 30-second drag go?'."""
        self._s_totals = {}
        self._s_counts = {}
        self._s_frames = 0

    def session_snapshot(self):
        """(totals, counts, frames) accumulated since the last session_reset."""
        return dict(self._s_totals), dict(self._s_counts), self._s_frames

    def add(self, stage, seconds):
        self._totals[stage] = self._totals.get(stage, 0.0) + seconds
        self._counts[stage] = self._counts.get(stage, 0) + 1
        self._s_totals[stage] = self._s_totals.get(stage, 0.0) + seconds
        self._s_counts[stage] = self._s_counts.get(stage, 0) + 1

    def note_frame(self):
        self._frame_events += 1
        self._s_frames += 1

    def maybe_flush(self, min_interval=2.0):
        """Emit + reset roughly every `min_interval` seconds (not every N
        frames) so a slow render reports quickly instead of waiting for a
        frame count that a heavy scene may take a long time to reach."""
        from freecad.fields.core.objects.fld_object import get_perf_profiler_enabled
        if not get_perf_profiler_enabled():
            self.reset()
            return
        now = time.perf_counter()
        elapsed = now - self._flush_t
        if elapsed < min_interval or self._frame_events == 0:
            return
        fps = self._frame_events / elapsed if elapsed > 0 else 0.0
        lines = [f"[PERF] SceneVoxel — {self._frame_events} callback(s) over {elapsed:.1f}s ({fps:.1f} fps)"]
        dynamic = sorted(s for s in self._totals if s not in self._STAGES)
        for s in self._STAGES + dynamic:
            n = self._counts.get(s, 0)
            if n == 0:
                continue
            t = self._totals[s] * 1000
            lines.append(f"  {s:<18} {t:7.1f} ms  ({t/n:5.2f} ms/call, {n} calls)")
        fld_logger.info("\n".join(lines))
        self.reset()


class _FldSelectionObserver:
    def __init__(self, renderer):
        self._renderer = renderer

    def addSelection(self, doc_name, obj_name, sub_name, pnt):
        label = f"{doc_name}.{obj_name}"
        self._renderer._selected_labels.add(label)
        self._renderer._field_meta_dirty = True
        self._redraw()

    def removeSelection(self, doc_name, obj_name, sub_name):
        label = f"{doc_name}.{obj_name}"
        self._renderer._selected_labels.discard(label)
        self._renderer._field_meta_dirty = True
        self._redraw()

    def clearSelection(self, doc_name):
        self._renderer._selected_labels.clear()
        self._renderer._field_meta_dirty = True
        self._redraw()

    def _redraw(self):
        try:
            if FreeCADGui.activeView():
                FreeCADGui.activeView().redraw()
        except Exception as e:
            fld_logger.debug(
                f"_FldSelectionObserver._redraw: activeView().redraw() failed "
                f"({e}); the selection highlight will refresh on the next frame."
            )


class _FldDocumentObserver:
    """Releases a scene field when its document object goes away.

    Every deletion path ends in App's document, but only one of them used to reach
    the renderer. `FldViewProvider.onDelete` does not fire for `doc.removeObject`,
    which is what `fld_modifier_stack._do_remove` calls when the stack panel's delete
    button is pressed, and nothing fires at all when a document is closed.

    Measured 2026-08-26 (MS-010): after deleting a modifier from the panel,
    `doc.getObject("Sphere_Noise")` was `None` while `_registry` still listed
    `MS9.Sphere_Noise` and `_last_analytical_data` still carried it with a live
    bbox -- a ghost that was still being baked, not a stale dictionary key. The
    same purge found `NB001.Box` and `NB001.Noise` still registered after
    `FreeCAD.closeDocument("NB001")`.

    Observing App is the one place all three paths pass through, so it is the only
    place this is done -- the onDelete copy was removed rather than kept beside it.
    """

    def __init__(self, renderer):
        self._renderer = renderer

    def slotDeletedObject(self, obj):
        doc = getattr(obj, "Document", None)
        name = getattr(obj, "Name", None)
        if doc is None or not name:
            return
        try:
            self._renderer.unregister_field(f"{doc.Name}.{name}")
        except Exception as e:
            fld_logger.render_debug(f"_FldDocumentObserver.slotDeletedObject failed: {e}")

    def slotDeletedDocument(self, doc):
        # The document's objects are still reachable here -- verified live: the slot
        # fires before the teardown, and no per-object slot follows a close.
        try:
            names = [getattr(o, "Name", None) for o in (getattr(doc, "Objects", None) or [])]
            for name in names:
                if name:
                    self._renderer.unregister_field(f"{doc.Name}.{name}")
        except Exception as e:
            fld_logger.render_debug(f"_FldDocumentObserver.slotDeletedDocument failed: {e}")


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
        """Hook App's deletion slots. Once per singleton, not per view.

        This deliberately does not live in `_attach`: field lifetime is a document
        concern, not a view one, so it must not be tied to whether a view happens to
        have the switch in it.
        """
        if self._doc_observer is not None:
            return
        add = getattr(FreeCAD, "addDocumentObserver", None)
        if add is None:
            return
        try:
            self._doc_observer = _FldDocumentObserver(self)
            add(self._doc_observer)
        except Exception as e:
            self._doc_observer = None
            fld_logger.render_debug(f"FldSceneVoxelRenderer: document observer install failed: {e}")

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
        self._field_meta_dirty         = True
        self._field_meta_capacity      = 0
        self._field_meta_buf_id        = 0
        self._voxel_prog               = None  # the fixed march program
        self._last_analytical_data     = None  # what the baker consumes
        self._selected_labels          = set() # set of "<Doc>.<Obj>" labels currently selected
        self._selected_labels_last_march = set() # snapshot as of the last SSBO upload (VR-030)
        self._sel_observer             = None

        self._render_state          = _RS_QUALITY   # _RS_QUALITY or _RS_INTERACTIVE
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
        """Accumulate dirty work until the next bake. `region=None` means 'full bake'.

        Called once per _rebuild(); several rebuilds may run between two GL frames,
        so this must union, never overwrite. Full-bake is absorbing: once set it
        survives every later narrow region until the bake clears it.
        """
        if self._dirty_region_full:
            return
        if region is None:
            self._dirty_region_full = True
            self._dirty_region = None
            return
        if self._dirty_region is None:
            self._dirty_region = region
        else:
            (amn, amx), (bmn, bmx) = self._dirty_region, region
            self._dirty_region = (
                FreeCAD.Vector(min(amn.x, bmn.x), min(amn.y, bmn.y), min(amn.z, bmn.z)),
                FreeCAD.Vector(max(amx.x, bmx.x), max(amx.y, bmx.y), max(amx.z, bmx.z))
            )

    @staticmethod
    def _active_scene_graph():
        """The active view's Coin scene graph, or None when there is no view."""
        try:
            view = getattr(getattr(FreeCADGui, "ActiveDocument", None), "ActiveView", None)
            return view.getSceneGraph() if view else None
        except Exception as e:
            fld_logger.render_debug(f"FldSceneVoxelRenderer: active scene graph unavailable: {e}")
            return None

    @staticmethod
    def _live_scene_graphs():
        """Every open 3D view's scene graph. Pivy wrappers compare by node with `==`,
        not by identity -- two `getSceneGraph()` calls on one view give `is` False and
        `==` True (verified live), so membership must be tested with `==`.
        """
        graphs = []
        try:
            for name in FreeCAD.listDocuments():
                gdoc = FreeCADGui.getDocument(name)
                for view in (gdoc.mdiViewsOfType("Gui::View3DInventor") or []):
                    sg = view.getSceneGraph()
                    if sg is not None:
                        graphs.append(sg)
        except Exception as e:
            fld_logger.render_debug(f"FldSceneVoxelRenderer._live_scene_graphs failed: {e}")
        return graphs

    def _prune_attached_graphs(self):
        """Forget graphs no open view owns any more.

        `_attached_graphs` is a strong reference, and a Coin graph is ref-counted -- so
        without this, closing a document would leave its whole scene graph, and every
        geometry node under it, alive for the rest of the session just because the
        renderer's switch had once been added to it.
        """
        live = self._live_scene_graphs()
        if not live:
            return
        self._attached_graphs = [g for g in self._attached_graphs
                                 if any(g == l for l in live)]

    def _switch_in(self, sg):
        """True when the switch is already a child of sg. Ask, do not remember."""
        try:
            return sg.findChild(self._switch) >= 0
        except Exception as e:
            fld_logger.render_debug(f"FldSceneVoxelRenderer._switch_in failed: {e}")
            return False

    def _attach(self):
        """Put the switch into the *active* view's scene graph if it is not there.

        RA-013. This used to early-out on a single `self._attached` bool. FreeCAD is
        multi-document and multi-view, and the switch lives in a view's scene graph --
        so when the document it had attached to was closed, the graph went with it,
        the flag stayed True, and no later document ever got the switch. Fields geometry
        then stopped drawing in every document, with no error and nothing in the log.

        Measured 2026-08-26: `_attached` True, the switch absent from the active
        view's graph, and `_volume_dirty` never clearing across repeated `redraw()`
        calls because the render callback was not in the tree to run; forcing a
        re-attach made the very next redraw bake. It is also the likeliest reason
        CW-012's 2026-08-25 sweep read `Bakes in tick = 0` at every N -- the bench
        opens its own document.

        A bool cannot answer "is my switch in THIS graph", so the graph is asked
        directly and the answer is not cached. Calling this repeatedly is cheap and
        idempotent, which is why `update_field` now calls it unconditionally.
        """
        self._prune_attached_graphs()
        sg = self._active_scene_graph()
        if sg is not None and not self._switch_in(sg):
            try:
                sg.addChild(self._switch)
                self._attached_graphs.append(sg)
            except Exception as e:
                fld_logger.render_debug(f"FldSceneVoxelRenderer._attach addChild failed: {e}")
        if self._nav_filter is None and getattr(QApplication, "instance", None) and QApplication.instance():
            self._nav_filter = _FldNavFilter(self)
            QApplication.instance().installEventFilter(self._nav_filter)
        if self._sel_observer is None and getattr(FreeCADGui, "Selection", None):
            try:
                self._sel_observer = _FldSelectionObserver(self)
                FreeCADGui.Selection.addObserver(self._sel_observer)
                self._selected_labels = {f"{obj.Document.Name}.{obj.Name}" for obj in FreeCADGui.Selection.getSelection()}
            except Exception as e:
                fld_logger.render_debug(f"FldSceneVoxelRenderer._attach selection observer failed: {e}")

    def _detach(self):
        # Every graph the switch was added to, not just the active one -- the whole
        # point of RA-013 is that "the active view" is not where it necessarily is.
        # No early-out on an attachment flag: the GL resources torn down below can
        # exist whether or not the switch is in a tree.
        for sg in self._attached_graphs:
            try:
                sg.removeChild(self._switch)
            except Exception as e:
                fld_logger.render_debug(f"FldSceneVoxelRenderer._detach removeChild failed: {e}")
        self._attached_graphs = []

        # Destroy multi-pass GL resources (best-effort; called outside GL context)
        for prog in (self._prog_ssao, self._prog_blur, self._prog_comp):
            if prog is not None:
                try:
                    prog.destroy()
                except Exception as e:
                    fld_logger.render_debug(f"FldSceneVoxelRenderer._detach prog.destroy failed: {e}")
        self._prog_ssao = self._prog_blur = self._prog_comp = None

        # Every compute program lives in the cache, including the active one, so
        # destroying the cache covers _prog_compute too — destroying it separately
        # as well would be a double free.
        if getattr(self, "_prog_cache", None) is not None:
            self._prog_cache.destroy_all()

        if getattr(self, "_cage_ssbo_cache", None) and getattr(self, "_glDeleteBuffers", None) is not None:
            try:
                for entry in self._cage_ssbo_cache.values():
                    if entry and entry.get("buf_id"):
                        bid = ctypes.c_uint(entry["buf_id"])
                        self._glDeleteBuffers(1, ctypes.byref(bid))
            except Exception as e:
                fld_logger.render_debug(f"FldSceneVoxelRenderer._detach delete SSBOs failed: {e}")
            self._cage_ssbo_cache = {}

        if getattr(self, "_tex3d_cache", None):
            for entry in self._tex3d_cache.values():
                if entry and entry.get("tex"):
                    try:
                        entry["tex"].destroy()
                    except Exception as e:
                        fld_logger.render_debug(f"FldSceneVoxelRenderer._detach: failed to destroy 3D texture: {e}")
            self._tex3d_cache = {}

        if getattr(self, "_scene_volume", None) is not None:
            try:
                self._scene_volume.destroy()
            except Exception as e:
                fld_logger.error(f"SceneVoxel: scene volume destroy failed: {e}")
            self._scene_volume = None

        for fbo in (self._gbuf_fbo, self._ssao_fbo, self._blur_fbo):
            if fbo is not None:
                try:
                    fbo.destroy()
                except Exception as e:
                    fld_logger.render_debug(f"FldSceneVoxelRenderer._detach fbo.destroy failed: {e}")
        self._gbuf_fbo = self._ssao_fbo = self._blur_fbo = None

        if self._noise_tex_id or self._depth_img_tex:
            try:
                from freecad.fields.core.gl.gl_texture3d import _loader
                glDeleteTextures = _loader.get("glDeleteTextures",
                    [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
                if self._noise_tex_id and glDeleteTextures:
                    arr = (ctypes.c_uint * 1)(self._noise_tex_id)
                    glDeleteTextures(1, arr)
                    self._noise_tex_id = 0
                if self._depth_img_tex and glDeleteTextures:
                    arr = (ctypes.c_uint * 1)(self._depth_img_tex)
                    glDeleteTextures(1, arr)
                    self._depth_img_tex = 0
            except Exception as e:
                fld_logger.render_debug(f"FldSceneVoxelRenderer._detach glDeleteTextures failed: {e}")

        from freecad.fields.core.render.scene_shader_builder import CompiledScene
        self._scene = CompiledScene()
        self._active_uniforms = {}
        self._selected_labels.clear()
        self._vp_size = (0, 0)

        if self._sel_observer is not None and getattr(FreeCADGui, "Selection", None):
            try:
                FreeCADGui.Selection.removeObserver(self._sel_observer)
            except Exception as e:
                fld_logger.render_debug(f"FldSceneVoxelRenderer._detach removeObserver failed: {e}")
            self._sel_observer = None

        if self._nav_filter is not None:
            try:
                QApplication.instance().removeEventFilter(self._nav_filter)
            except Exception as e:
                fld_logger.render_debug(f"FldSceneVoxelRenderer._detach removeEventFilter failed: {e}")
            self._nav_filter = None
        self._render_state = _RS_QUALITY



    def register_heightmap_texture(self, label, image_path):
        """Queue a heightmap image path for GL upload on the next render frame."""
        self._hmap_cache.queue_path(image_path)

    def _upload_pending_heightmap_textures(self):
        """Upload queued heightmap images/arrays as GL textures. Must be called inside the GL callback."""
        self._hmap_cache.upload_pending()
        self._hmap_cache.gc(self._fields.values())

    def _get_field_texture3d(self, label, sampler_name, provider, action):
        """Return {"tex": GLTexture3D, "uniforms": {...}} for one sampler3D, or None.

        Bakes and uploads only when the provider's key changes. `texture3d_key()` is
        called on every bake and must stay cheap; `texture3d_data()` is the expensive
        half and is reached only on a miss -- a deform cage's payload costs 100.3 ms.
        Must be called inside the GL callback: GLTexture3D.upload queues, and
        _gl_callback performs, the actual GL call.
        """
        from freecad.fields.core.gl.gl_texture3d import GLTexture3D

        cache_key = (label, sampler_name)
        entry = self._tex3d_cache.get(cache_key)
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
        self._perf.add("tex3d_upload", time.perf_counter() - _t0)

        entry = {"tex": tex, "key": key, "uniforms": data.get("uniforms", {})}
        self._tex3d_cache[cache_key] = entry
        return entry




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
        existed = self._registry.unregister(label)

        # MS-010 put a document observer in front of this, so it now sees every object
        # deleted anywhere in the application -- Part::Box, spreadsheets, sketches.
        # Rebuilding the scene shader and forcing a redraw for a label that was never
        # registered is pure cost, and `existed` was already computed and thrown away.
        if not (existed
                or any(k[0] == label for k in (getattr(self, "_tex3d_cache", None) or ()))
                or any(k == label or k.startswith(f"{label}:")
                       for k in (getattr(self, "_cage_ssbo_cache", None) or ()))):
            return

        # Clean up SSBO cache for this label
        if getattr(self, "_cage_ssbo_cache", None):
            to_del = [k for k in self._cage_ssbo_cache if k.startswith(f"{label}:") or k == label]
            for k in to_del:
                entry = self._cage_ssbo_cache.pop(k)
                buf_id = entry.get("buf_id")
                if buf_id and getattr(self, "_glDeleteBuffers", None) is not None:
                    try:
                        buf_id_val = ctypes.c_uint(buf_id)
                        self._glDeleteBuffers(1, ctypes.byref(buf_id_val))
                    except Exception as e:
                        fld_logger.warn(
                            f"unregister_field: glDeleteBuffers failed for cage "
                            f"SSBO '{k}' (buf {buf_id}): {e}. The buffer is "
                            f"dropped from the cache and leaks on the GPU."
                        )

        if getattr(self, "_tex3d_cache", None):
            to_del = [k for k in self._tex3d_cache if k[0] == label]
            for k in to_del:
                entry = self._tex3d_cache.pop(k)
                if entry and entry.get("tex"):
                    try:
                        entry["tex"].destroy()
                    except Exception as e:
                        fld_logger.render_debug(f"SceneVoxel: failed to destroy 3D texture for '{label}' on unregister: {e}")

        if not self._registry:
            self._switch.whichChild = -1
            self._hmap_cache.clear_bindings_to_make()
            # `_rebuild` is what normally refreshes this, and it is skipped here --
            # so without the clear, the last field of a closed document stayed in
            # `_last_analytical_data` with a live bbox after its registry entry was
            # gone (measured 2026-08-26, MS-010). Nothing draws it while the switch
            # is off, but it is still a reference the baker reads at pass time.
            self._last_analytical_data = None
        else:
            self._rebuild()

        try:
            self._hmap_cache.gc(self._registry.fields.values())
        except Exception as e:
            fld_logger.render_debug(
                f"SceneVoxel: heightmap GC after unregistering '{label}' failed: {e}")

        try:
            active_view = FreeCADGui.ActiveDocument.ActiveView
            if active_view:
                active_view.redraw()
        except Exception as e:
            fld_logger.render_debug(f"FldSceneVoxelRenderer: Failed to redraw view: {e}")

    def set_field_visible(self, label, visible):
        if label in self._registry:
            self._registry.set_visible(label, visible)
            self._rebuild()

    def update_field(self, label, field):
        if hasattr(field, "heightmap_path") and field.heightmap_path:
            self.register_heightmap_texture(label, field.heightmap_path)
        self._registry.update_field(label, field)
        # Unconditional: _attach is idempotent per scene graph, and this is the hook
        # that re-attaches the renderer in a document opened after an earlier one was
        # closed (RA-013).
        self._attach()
        self._rebuild()
        try:
            if FreeCADGui.activeView():
                FreeCADGui.activeView().redraw()
        except Exception as e:
            fld_logger.render_debug(f"FldSceneVoxelRenderer: redraw after field update failed: {e}")

    def begin_update_batch(self):
        """Begin a batch of updates, postponing rebuilds until end_update_batch is called."""
        self._batch_update_count += 1

    def end_update_batch(self):
        """End a batch of updates. If the count reaches 0 and updates occurred, trigger rebuild."""
        self._batch_update_count = max(0, self._batch_update_count - 1)
        if self._batch_update_count == 0 and self._batch_needs_rebuild:
            self._batch_needs_rebuild = False
            self._rebuild()

    def gc_fields(self):
        to_remove = self._registry.gc_orphans()
        if to_remove:
            self._rebuild()
            try:
                active_view = FreeCADGui.ActiveDocument.ActiveView
                if active_view:
                    active_view.redraw()
            except Exception as e:
                fld_logger.render_debug(f"FldSceneVoxelRenderer: Failed to redraw view: {e}")

    def refresh_appearance(self):
        """Recompute appearance properties (colors, specular) without rebuilding shaders, and request redraw."""
        if not self._scene.fields:
            return
        for f in self._scene.fields:
            f.shape_color = self._appearance.shape_color(f.label)
            f.selection_color = self._appearance.selection_color(f.label)
            f.specular = self._appearance.specular_shininess(f.label)
        # The loop above only changed CPU-side state. The FieldMeta SSBO the shader
        # actually samples is repacked behind `if self._field_meta_dirty` (:1542),
        # so without this flag the upload is skipped and the GPU keeps the old
        # colour. This worked before only because the one caller, on_prefs_changed,
        # followed it with _rebuild(), which sets the flag at :1944.
        self._field_meta_dirty = True
        try:
            if FreeCADGui.activeView():
                FreeCADGui.activeView().redraw()
        except Exception as e:
            fld_logger.render_debug(f"FldSceneVoxelRenderer.refresh_appearance redraw failed: {e}")

    def on_prefs_changed(self):
        from freecad.fields.core.fld_settings import get_render_debug_mode
        debug = get_render_debug_mode()
        self._bbox_switch.whichChild = 0 if debug else -1
        self.refresh_appearance()
        if self._registry:
            self._registry.mark_all_dirty()
            self._rebuild()

    def _init_gl_programs(self):
        """Compile all 3 SSAO pipeline programs and upload noise texture.
        Must be called inside an active GL context (i.e. from a SoCallback).
        """
        import random, math, ctypes
        from freecad.fields.core.gl.gl_program import GLProgram
        from freecad.fields.core.gl.gl_texture3d import _loader
        from freecad.fields.core.gl.gl_compute import check_compute_support

        self._glGenBuffers = _loader.get("glGenBuffers", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        self._glBindBuffer = _loader.get("glBindBuffer", [ctypes.c_uint, ctypes.c_uint], None)
        self._glBufferData = _loader.get("glBufferData", [ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint], None)
        self._glBindBufferBase = _loader.get("glBindBufferBase", [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint], None)
        self._glDeleteBuffers = _loader.get("glDeleteBuffers", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)

        GL_MAX_COMPUTE_TEXTURE_IMAGE_UNITS = 0x9138
        max_units = (ctypes.c_int * 1)(32)
        glGetIntegerv = _loader.get("glGetIntegerv", [ctypes.c_uint, ctypes.POINTER(ctypes.c_int)], None)
        if glGetIntegerv:
            glGetIntegerv(GL_MAX_COMPUTE_TEXTURE_IMAGE_UNITS, max_units)
        self._max_compute_texture_units = max_units[0]
        self._field_meta_buf_id = 0

        self._compute_supported = check_compute_support()
        if not self._compute_supported:
            fld_logger.warn("SceneVoxel: OpenGL Compute Shaders are not supported on this system. SDF rendering will be disabled.")

        self._prog_ssao = GLProgram()
        self._prog_ssao.compile(_VERT_PASSTHROUGH, _FRAG_SSAO)

        self._prog_blur = GLProgram()
        self._prog_blur.compile(_VERT_PASSTHROUGH, _FRAG_BLUR)

        self._prog_comp = GLProgram()
        self._prog_comp.compile(_VERT_PASSTHROUGH, _FRAG_COMP)

        # SSAO hemisphere kernel: 64 samples, accelerated toward origin
        kernel = []
        for i in range(64):
            s = [random.uniform(-1.0, 1.0),
                 random.uniform(-1.0, 1.0),
                 random.uniform(0.0, 1.0)]
            length = math.sqrt(sum(x * x for x in s))
            if length < 1e-8:
                length = 1.0
            s = [x / length for x in s]
            scale = i / 64.0
            scale = 0.1 + scale * scale * 0.9   # lerp(0.1, 1.0, scale^2)
            kernel.extend([x * scale for x in s])
        self._ssao_kernel_flat = kernel  # 192 floats

        # 4×4 noise texture: random XY rotation vectors for SSAO tangent-frame
        noise_data = []
        for _ in range(16):
            angle = random.uniform(0.0, 2.0 * math.pi)
            noise_data.extend([math.cos(angle), math.sin(angle), 0.0])
        noise_arr = (ctypes.c_float * len(noise_data))(*noise_data)

        GL_TEXTURE_2D     = 0x0DE1
        GL_RGB32F         = 0x8815
        GL_RGB            = 0x1907
        GL_FLOAT          = 0x1406
        GL_NEAREST        = 0x2600
        GL_REPEAT         = 0x2901
        GL_TEXTURE_MIN_FILTER = 0x2801
        GL_TEXTURE_MAG_FILTER = 0x2800
        GL_TEXTURE_WRAP_S = 0x2802
        GL_TEXTURE_WRAP_T = 0x2803

        glGenTextures   = _loader.get("glGenTextures",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        glBindTexture   = _loader.get("glBindTexture",
            [ctypes.c_uint, ctypes.c_uint], None)
        glTexImage2D    = _loader.get("glTexImage2D",
            [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int,
             ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
             ctypes.c_void_p], None)
        glTexParameteri = _loader.get("glTexParameteri",
            [ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)

        tex = ctypes.c_uint(0)
        glGenTextures(1, ctypes.byref(tex))
        self._noise_tex_id = tex.value
        glBindTexture(GL_TEXTURE_2D, self._noise_tex_id)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB32F, 4, 4, 0,
                     GL_RGB, GL_FLOAT, noise_arr)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT)
        glBindTexture(GL_TEXTURE_2D, 0)

    def _build_fbos(self, w: int, h: int):
        """Build a brand-new set of FBOs + depth texture at size (w, h).

        Returns (gbuf_fbo, ssao_fbo, blur_fbo, depth_tex) as new GL objects —
        does NOT touch whatever is currently assigned to self._gbuf_fbo etc.
        The caller only swaps these in after Pass 1-3 successfully render
        into them, so a failed/partial resize never destroys the last good
        frame's content (which would otherwise leave Pass 4 with nothing
        valid to composite, i.e. the SDF overlay vanishing).
        Must be called inside an active GL context.
        """
        from freecad.fields.core.gl.gl_framebuffer import GLFramebuffer
        from freecad.fields.core.gl.gl_texture3d import _loader as _tex_loader

        gbuf_fbo = GLFramebuffer()
        ssao_fbo = GLFramebuffer()
        blur_fbo = GLFramebuffer()
        # G-buffer: Phong color + view-space pos + view-space normal + depth
        # Only the Phong color needs linear scaling; VS pos/normal/depth must remain nearest.
        gbuf_fbo.create(w, h, ['rgba16f', 'rgba16f', 'rgba32f', 'depth24'], ['linear', 'nearest', 'nearest', 'nearest'])
        # SSAO: raw occlusion float
        ssao_fbo.create(w, h, ['r16f'], ['nearest'])
        # Blur: blurred occlusion float (needs linear scaling for smooth scaling up)
        blur_fbo.create(w, h, ['r16f'], ['linear'])

        # r32f depth image written by the compute shader (window depth in [0,1])
        GL_TEXTURE_2D     = 0x0DE1
        GL_R32F           = 0x822E
        GL_RED            = 0x1903
        GL_FLOAT          = 0x1406
        GL_NEAREST        = 0x2600
        GL_CLAMP_TO_EDGE  = 0x812F
        GL_TEXTURE_MIN_FILTER = 0x2801
        GL_TEXTURE_MAG_FILTER = 0x2800
        GL_TEXTURE_WRAP_S = 0x2802
        GL_TEXTURE_WRAP_T = 0x2803

        glGenTextures    = _tex_loader.get("glGenTextures",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        glBindTexture    = _tex_loader.get("glBindTexture",
            [ctypes.c_uint, ctypes.c_uint], None)
        glTexImage2D     = _tex_loader.get("glTexImage2D",
            [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
             ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p], None)
        glTexParameteri  = _tex_loader.get("glTexParameteri",
            [ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)

        tex = ctypes.c_uint(0)
        glGenTextures(1, ctypes.byref(tex))
        depth_tex = tex.value
        glBindTexture(GL_TEXTURE_2D, depth_tex)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_R32F, w, h, 0, GL_RED, GL_FLOAT, None)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glBindTexture(GL_TEXTURE_2D, 0)

        return gbuf_fbo, ssao_fbo, blur_fbo, depth_tex

    def _destroy_fbo_set(self, gbuf_fbo, ssao_fbo, blur_fbo, depth_tex):
        """Free GL resources for a (gbuf, ssao, blur, depth_tex) tuple from _build_fbos."""
        from freecad.fields.core.gl.gl_texture3d import _loader as _tex_loader
        for fbo in (gbuf_fbo, ssao_fbo, blur_fbo):
            if fbo is not None:
                fbo.destroy()
        if depth_tex:
            glDeleteTextures = _tex_loader.get("glDeleteTextures",
                [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
            arr = (ctypes.c_uint * 1)(depth_tex)
            glDeleteTextures(1, arr)

    def pick_surface_at(self, x: int, y: int):
        """Read back the surface ID at window coordinates (x, y).

        Returns int surface_id (or None if no hit / 65535).
        """
        if not self._gbuf_fbo or not getattr(self._gbuf_fbo, "id", 0):
            return None
        w, h = self._vp_size
        if w <= 0 or h <= 0 or x < 0 or x >= w or y < 0 or y >= h:
            return None

        # Scaling during interactive drag:
        _RS_INTERACTIVE = 0
        _interactive = (getattr(self, "_render_state", 1) == _RS_INTERACTIVE)
        from freecad.fields.core.objects.fld_object import get_interactive_resolution_scaling
        scale = get_interactive_resolution_scaling() if _interactive else 1
        rx = int(x // scale) if scale > 1 else int(x)
        # Flip y: GL origin is bottom-left, Qt is top-left
        ry = int((h - 1 - y) // scale) if scale > 1 else int(h - 1 - y)

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
            glBindFramebuffer(GL_READ_FRAMEBUFFER, self._gbuf_fbo.id)
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

    def _compute_program_for(self, src, key=None):
        """The compiled program for this source or key, compiling only if new."""
        return self._prog_cache.get(src, key=key, protected=getattr(self, "_voxel_prog", None))

    def _render_gl_callback(self, userdata, action):
        """Execute 4-pass SSAO render inside Coin3D's GL context."""
        # Wall time of the whole callback, not just the passes inside it. This
        # is the number that explains a drag whose own tick is 6 ms yet runs at
        # 1 fps: the Qt event loop is blocked here, so the drag timer cannot
        # fire again until it returns. Only counted for callbacks that actually
        # render — Coin3D calls this for other action types too.
        t0 = time.perf_counter()
        self._cb_counted = False
        try:
            self._render_gl_callback_inner(userdata, action)
        except Exception as e:
            fld_logger.error(f"SceneVoxel: render callback exception: {e}")
        finally:
            if getattr(self, "_cb_counted", False):
                self._perf.add("callback_total", time.perf_counter() - t0)

    def _render_gl_callback_inner(self, userdata, action):
        import ctypes
        if not action.isOfType(coin.SoGLRenderAction.getClassTypeId()):
            return
        self._last_action = action
        if not self._active_uniforms or self._active_uniforms.get("n_fields", 0) == 0:
            return

        self._perf.note_frame()
        self._cb_counted = True

        from freecad.fields.core.gl.gl_texture3d import _loader

        # Lazy-initialise programs + noise texture on first call
        if self._prog_ssao is None:
            try:
                self._init_gl_programs()
            except Exception as e:
                fld_logger.render_debug(f"SceneVoxel: _init_gl_programs failed: {e}")
                return



        # Upload pending heightmap images as GL textures (must be inside GL context)
        self._upload_pending_heightmap_textures()

        glGetIntegerv = _loader.get("glGetIntegerv",
            [ctypes.c_uint, ctypes.POINTER(ctypes.c_int)], None)
        glGetFloatv   = _loader.get("glGetFloatv",
            [ctypes.c_uint, ctypes.POINTER(ctypes.c_float)], None)
        glBindFramebuffer = _loader.get("glBindFramebuffer",
            [ctypes.c_uint, ctypes.c_uint], None)
        glActiveTexture   = _loader.get("glActiveTexture", [ctypes.c_uint], None)
        glBindTexture     = _loader.get("glBindTexture",   [ctypes.c_uint, ctypes.c_uint], None)
        glClear           = _loader.get("glClear",         [ctypes.c_uint], None)
        glUseProgram      = _loader.get("glUseProgram",    [ctypes.c_uint], None)
        glViewport        = _loader.get("glViewport",
            [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int], None)
        glGetError        = _loader.get("glGetError", [], ctypes.c_uint)

        def _check_gl_error(checkpoint):
            # glGetError() returns (and clears) only the first error since the
            # last call, so checking at each named checkpoint localizes which
            # GL call actually failed — Python-level try/except never sees
            # these, since ctypes doesn't raise on GL errors.
            if not glGetError:
                return
            err = glGetError()
            if err != 0:
                fld_logger.render_debug_throttled(
                    f"gl_error_{checkpoint}",
                    f"SceneVoxel: GL error 0x{err:04x} at '{checkpoint}'"
                )

        GL_COLOR_BUFFER_BIT = 0x4000
        GL_DEPTH_BUFFER_BIT = 0x0100
        GL_FRAMEBUFFER      = 0x8D40
        GL_TEXTURE_2D       = 0x0DE1
        GL_TEXTURE_3D       = 0x806F

        # Viewport size — check before modifying any GL state
        vp = (ctypes.c_int * 4)(0, 0, 0, 0)
        glGetIntegerv(0x0BA2, vp)   # GL_VIEWPORT
        w, h = vp[2], vp[3]
        if w <= 0 or h <= 0:
            return

        # Save GL state that Coin3D needs to find intact after our passes
        GL_DEPTH_TEST     = 0x0B71
        GL_BLEND          = 0x0BE2
        GL_DEPTH_WRITEMASK = 0x0B72
        depth_enabled = (ctypes.c_uint * 1)(0)
        blend_enabled = (ctypes.c_uint * 1)(0)
        depth_write   = (ctypes.c_uint * 1)(0)
        glGetIntegerv(GL_DEPTH_TEST,     ctypes.cast(depth_enabled, ctypes.POINTER(ctypes.c_int)))
        glGetIntegerv(GL_BLEND,          ctypes.cast(blend_enabled, ctypes.POINTER(ctypes.c_int)))
        glGetIntegerv(GL_DEPTH_WRITEMASK, ctypes.cast(depth_write,  ctypes.POINTER(ctypes.c_int)))

        glEnable    = _loader.get("glEnable",    [ctypes.c_uint], None)
        glDisable   = _loader.get("glDisable",   [ctypes.c_uint], None)
        glDepthMask = _loader.get("glDepthMask", [ctypes.c_uint], None)

        # Our passes need depth write and no blending
        glEnable(GL_DEPTH_TEST)
        glDepthMask(1)
        glDisable(GL_BLEND)

        # Build replacement FBOs only when the actual native window size changes —
        # NOT when the interactive/quality target resolution toggles. FBOs are
        # always allocated at native (w, h); the interactive pass below renders
        # into a (target_w x target_h) sub-rect of them via glViewport, and the
        # SSAO/blur/composite shaders read that sub-rect back via u_res_scale.
        # So toggling downscaling on/off never touches a GL object — previously
        # every single interactive<->quality transition destroyed and recreated
        # all the SSAO/blur FBOs and the depth texture, which (per captured logs)
        # was happening several times a second during ordinary navigation and
        # was the root cause of the SDF randomly vanishing.
        _force_full = False
        _pending_fbo_set = None
        _old_fbo_set = None
        if (w, h) != self._vp_size:
            try:
                _pending_fbo_set = self._build_fbos(w, h)
            except Exception:
                fld_logger.exception(f"SceneVoxel: _build_fbos failed (native={w}x{h})")
                _pending_fbo_set = None
            _check_gl_error("build_fbos")
            if _pending_fbo_set is None:
                # Keep rendering at the last successfully-built native size this frame.
                if self._gbuf_fbo is None:
                    return
                w, h = self._vp_size
            else:
                _old_fbo_set = (self._gbuf_fbo, self._ssao_fbo, self._blur_fbo, self._depth_img_tex)
                self._gbuf_fbo, self._ssao_fbo, self._blur_fbo, self._depth_img_tex = _pending_fbo_set
                _force_full = True

        # Quality state machine: _RS_INTERACTIVE (downscaled) or _RS_QUALITY (native res + SSAO).
        # _FldNavFilter drives transitions via MouseButtonPress/Release and Wheel events.
        # _just_went_quality ensures exactly one analytic frame fires after nav ends, even
        # if Coin3D's render-rate cap would otherwise skip it.
        _interactive = (self._render_state == _RS_INTERACTIVE)
        _just_went_quality = self._prev_interactive_state and not _interactive
        self._prev_interactive_state = _interactive
        from freecad.fields.core.objects.fld_object import get_interactive_resolution_scaling
        scale = get_interactive_resolution_scaling()
        if _interactive and scale > 1:
            target_w = max(1, w // scale)
            target_h = max(1, h // scale)
        else:
            target_w, target_h = w, h
        # Fraction of the native-sized G-buffer/SSAO/blur textures actually
        # populated this frame — used by those shaders to map back into the
        # right sub-rect regardless of which resolution was last rendered.
        _res_scale = (target_w / w) if w > 0 else 1.0

        # Save Coin3D's current FBO
        prev_fbo = (ctypes.c_int * 1)(0)
        glGetIntegerv(0x8CA6, prev_fbo)   # GL_FRAMEBUFFER_BINDING

        from freecad.fields.core.fld_settings import (get_voxel_grid_resolution, get_voxel_band_voxels,
                                      get_interactive_voxel_scaling)
        from freecad.fields.core.objects.fld_object import get_perf_profiler_enabled
        _profiling = get_perf_profiler_enabled()
        if _profiling:
            # Drain whatever the host still had queued BEFORE any of our passes
            # start, and bill it to its own stage.
            #
            # Every pass below is timed by a glFinish at its *end*. A glFinish
            # drains everything queued since the last one -- so without a drain
            # here, the first pass to sync inherits all of Coin3D's drawing for
            # this frame (handles, control cages, lines). Passes 1-3 are
            # throttled and Pass 4 is not, so on most callbacks Pass 4 was the
            # first sync and absorbed the lot: that is why pass4_composite read
            # 4-8 ms/call for one fullscreen quad. Same failure mode as VX-039.
            #
            # Charged to `inherited_queue` rather than hidden: how much work the
            # host arrives with is worth seeing, and it keeps callback_total
            # equal to the sum of its parts.
            _t_inherit = time.perf_counter()
            _gf_pre = _loader.get("glFinish", [], None)
            if _gf_pre:
                _gf_pre()
            self._perf.add("inherited_queue", time.perf_counter() - _t_inherit)
        _has_analytical = (
            self._depth_img_tex is not None
            and getattr(self, "_compute_supported", True)
        )

        # In INTERACTIVE state always re-render (downscaled shader keeps cost low).
        # In QUALITY state cap at 30 fps to reduce idle GPU load.
        _now = time.perf_counter()
        _run_expensive = _force_full or _interactive or _just_went_quality or (_now - self._last_render_t) >= (1.0 / 30.0)

        if _force_full:
            # Not throttled — a native window resize is rare, so logging every
            # occurrence is cheap and lets us correlate it with anything odd.
            fld_logger.render_debug(f"SceneVoxel: native FBO resize attempted this frame -> {w}x{h}")
        if _run_expensive:
            self._last_render_t = _now
            self._run_expensive_last = True

            try:
                # Read projection matrix (only needed for SSAO pass)
                proj_data = (ctypes.c_float * 16)()
                glGetFloatv(0x0BA7, proj_data)    # GL_PROJECTION_MATRIX

                # Read camera matrices (needed for both paths and for depth computation)
                mv_data   = (ctypes.c_float * 16)()
                glGetFloatv(0x0BA6, mv_data)   # GL_MODELVIEW_MATRIX (column-major)

                # ── Pass 1: G-buffer (ray march SDF) ──
                if _has_analytical:
                    from freecad.fields.core.render.scene_volume import SceneVolume
                    from freecad.fields.core.render.voxel_march_shader import VOXEL_MARCH_SRC
                    from freecad.fields.core.gl.gl_program import bind_image_texture, memory_barrier
                    GL_WRITE_ONLY = 0x88B9
                    GL_RGBA16F    = 0x881A
                    GL_R32F       = 0x822E

                    if self._scene_volume is None:
                        self._scene_volume = SceneVolume()
                    if self._voxel_prog is None:
                        self._voxel_prog = self._compute_program_for(VOXEL_MARCH_SRC, key="voxel_march_v1")

                    sv = self._scene_volume
                    ad = self._last_analytical_data or []
                    if ad and self._volume_dirty:
                        mn = FreeCAD.Vector(min(d["bbox_min"].x for d in ad),
                                            min(d["bbox_min"].y for d in ad),
                                            min(d["bbox_min"].z for d in ad))
                        mx = FreeCAD.Vector(max(d["bbox_max"].x for d in ad),
                                            max(d["bbox_max"].y for d in ad),
                                            max(d["bbox_max"].z for d in ad))
                        _t_bake = time.perf_counter()
                        # Bake coarser while dragging. The volume is the whole
                        # cost of an edit -- a growing scene bbox remaps it, so
                        # every tick is a full bake -- and the saving is cubic
                        # in the divisor, unlike the screen downscale above.
                        # This is only read on a frame that was going to bake
                        # anyway, so orbiting an unchanged scene never triggers
                        # the reallocation; the first frame after the drag ends
                        # is a quality frame and bakes at full resolution.
                        scene_extent = (mx.x - mn.x, mx.y - mn.y, mx.z - mn.z)
                        base_res = get_voxel_grid_resolution()
                        for d in ad:
                            fld = d.get("field")
                            if fld is not None and hasattr(fld, "preferred_scene_resolution"):
                                pref = fld.preferred_scene_resolution(scene_extent)
                                if pref is not None:
                                    base_res = max(base_res, pref)

                        if base_res > 384 and not _interactive:
                            longest = max(scene_extent)
                            scene_vox = longest / 384.0
                            fld_logger.warn(f"scene_volume: resolution clamped to 384; scene voxel {scene_vox:.3f} mm")

                        _vox_div = get_interactive_voxel_scaling() if _interactive else 1
                        _vox_res = max(32, min(base_res, 384) // max(1, _vox_div))
                        _slack = VOLUME_SLACK_FRAC if _interactive else 0.0
                        sv.ensure(mn, mx, _vox_res, get_voxel_band_voxels(),
                                  slack_frac=_slack)
                        _region = None if getattr(self, "_dirty_region_full", False) else getattr(self, "_dirty_region", None)
                        # ensure() may have moved the mapping and set sv.dirty,
                        # in which case bake() drops the region -- so ask AFTER
                        # ensure() and BEFORE bake() clears the flag.
                        _was_full = _region is None or sv.dirty
                        sv.bake(ad, self, region=_region)
                        self._dirty_region = None
                        self._dirty_region_full = False
                        self._volume_dirty = False
                        memory_barrier(0x00000020 | 0x00000008)  # IMAGE_ACCESS | TEXTURE_FETCH
                        # VX-039. `sv.bake` only DISPATCHES -- the compute work
                        # is async and the barrier orders it against the march
                        # without blocking the CPU -- so timing it alone
                        # measured the dispatch and nothing else, and its real
                        # cost landed in whichever counter next hit a glFinish.
                        # That was pass1_dispatch, which is why the march
                        # appeared to cost 745 ms/frame during a drag against
                        # 6-12 ms idle on the same texture and camera. Finish
                        # here, inside the bracket, so each counter owns its own
                        # GPU time. Costs a pipeline stall, but only while
                        # profiling and only on frames that actually baked.
                        if _profiling:
                            _gf_bake = _loader.get("glFinish", [], None)
                            if _gf_bake: _gf_bake()
                        _dt_bake = time.perf_counter() - _t_bake
                        self._perf.add("voxel_bake", _dt_bake)
                        self._perf.add(
                            "voxel_bake_full" if _was_full else "voxel_bake_region",
                            _dt_bake)

                    _t_pass1_start = time.perf_counter()
                    import numpy as np
                    proj_np = np.array(list(proj_data), dtype=np.float64).reshape(4, 4, order='F')
                    mv_np   = np.array(list(mv_data),   dtype=np.float64).reshape(4, 4, order='F')
                    inv_mvp = np.linalg.inv(proj_np @ mv_np)
                    inv_mvp_col = inv_mvp.flatten(order='F').astype(np.float32).tolist()

                    GL_RGBA32F = 0x8814
                    bind_image_texture(0, self._gbuf_fbo.color_texture(0), GL_WRITE_ONLY, GL_RGBA16F)
                    bind_image_texture(1, self._gbuf_fbo.color_texture(1), GL_WRITE_ONLY, GL_RGBA16F)
                    bind_image_texture(2, self._gbuf_fbo.color_texture(2), GL_WRITE_ONLY, GL_RGBA32F)
                    bind_image_texture(3, self._depth_img_tex,             GL_WRITE_ONLY, GL_R32F)

                    _prog = self._voxel_prog
                    _prog.use()
                    _prog.set_2i("u_resolution", target_w, target_h)
                    _prog.set_mat4("u_inv_mvp", inv_mvp_col)
                    _prog.set_mat4("u_mv",      list(mv_data))
                    _prog.set_mat4("u_proj",    list(proj_data))
                    _prog.set_3f("u_light_dir", 0.4472, 0.7454, 0.4943)
                    _diag_voxels = 1.75 * max(sv.nx, sv.ny, sv.nz)
                    _steps = max(512, int(2.0 * _diag_voxels))
                    _prog.set_1i("u_max_steps", min(_steps, 1024))
                    _prog.set_3f("u_vol_min", float(sv.vol_min.x), float(sv.vol_min.y), float(sv.vol_min.z))
                    _prog.set_3f("u_vol_max", float(sv.vol_max.x), float(sv.vol_max.y), float(sv.vol_max.z))
                    _prog.set_3f("u_voxel_step", float(sv.step.x), float(sv.step.y), float(sv.step.z))
                    _prog.set_1f("u_band", float(sv.band))
                    _prog.set_1f("u_step_div", float(getattr(sv, "lip", 1.0)))
                    _prog.set_3f("u_base_color", 0.8, 0.8, 0.8)
                    _prog.set_3f("u_spec_color", 1.0, 1.0, 1.0)
                    _prog.set_1f("u_shininess", 32.0)
                    # ParamGet at bind time matches the existing precedent for
                    # u_outline_size (:1732). This block runs once per frame, not
                    # per voxel; if it ever shows up in a drag profile, cache it on
                    # self and invalidate from on_prefs_changed -- do not guess.
                    from freecad.fields.core.fld_settings import (
                        get_hatch_size, get_hatch_color, get_hatch_strength)
                    _hc = get_hatch_color()
                    _prog.set_3f("u_hatch_color", float(_hc[0]), float(_hc[1]), float(_hc[2]))
                    # The preference is in SCREEN pixels. During navigation the
                    # march runs at target_w x target_h and the composite upscales
                    # by u_res_scale, so one march pixel covers 1/_res_scale screen
                    # pixels. Without this factor the hatch period doubles on
                    # mouse-down at scale 0.5 and snaps back on release.
                    _prog.set_1f("u_hatch_period", float(get_hatch_size()) * _res_scale)
                    _prog.set_1f("u_hatch_strength", float(get_hatch_strength()))

                    # Upload FieldMeta SSBO at binding point 3
                    if self._scene and self._scene.fields and getattr(self, "_glGenBuffers", None) is not None:
                        GL_SHADER_STORAGE_BUFFER = 0x90D2
                        GL_DYNAMIC_DRAW = 0x88E8
                        if not getattr(self, "_field_meta_buf_id", 0):
                            buf_id_val = ctypes.c_uint(0)
                            self._glGenBuffers(1, ctypes.byref(buf_id_val))
                            self._field_meta_buf_id = buf_id_val.value
                            self._field_meta_capacity = 0

                        # The table is keyed by surface id and by nothing else.
                        # A field whose root carries no id has no row and is not
                        # drawn with its own appearance -- there is no second key
                        # to fall back to, because a fallback here is what put two
                        # different numbers into one table and made a solid render
                        # with another field's colour.
                        max_sid = -1
                        _no_id = getattr(self, "_no_surface_id_reported", set())
                        for cf in self._scene.fields:
                            sids = cf.field_obj.collect_surface_ids() if hasattr(cf.field_obj, "collect_surface_ids") else set()
                            sid = getattr(cf.field_obj, "surface_id", 65535)
                            if sid < 65535:
                                sids.update(range(sid, sid + cf.field_obj.surface_count()))
                            if not sids:
                                # Once per label, not once per frame: this runs on
                                # the render path and the report view is something
                                # the user actually reads.
                                if cf.label not in _no_id:
                                    _no_id.add(cf.label)
                                    fld_logger.error(
                                        f"FieldMeta: {cf.label} has no surface id; "
                                        f"its appearance cannot be uploaded")
                                continue
                            _no_id.discard(cf.label)
                            max_sid = max(max_sid, max(sids))
                        self._no_surface_id_reported = _no_id
                        table_size = max_sid + 1
                        # Bound for the composite's lookup. Set every frame, not
                        # only when the table is repacked: delete a field and the
                        # table shrinks while a clean volume keeps its ids, so a
                        # stale id must read nothing rather than a stale row.
                        self._field_meta_count = table_size

                        if table_size > 0 and (self._field_meta_dirty
                                               or table_size > getattr(self, "_field_meta_capacity", 0)):
                            meta_arr = np.zeros((table_size, 20), dtype=np.float32)
                            selected_labels = getattr(self, "_selected_labels", set())
                            for _i, cf in enumerate(self._scene.fields):
                                row = [
                                    cf.bbox_min.x, cf.bbox_min.y, cf.bbox_min.z, 0.0,
                                    cf.bbox_max.x, cf.bbox_max.y, cf.bbox_max.z, float(cf.vis_alpha),
                                    cf.shape_color[0], cf.shape_color[1], cf.shape_color[2], 1.0 if cf.is_subtractive else 0.0,
                                    cf.specular[0], cf.specular[1], cf.specular[2], cf.specular[3],
                                    # flags: .x selected, .yzw this object's own
                                    # outline colour, read by the composite pass.
                                    1.0 if cf.label in selected_labels else 0.0,
                                    cf.selection_color[0], cf.selection_color[1], cf.selection_color[2],
                                ]
                                sids = cf.field_obj.collect_surface_ids() if hasattr(cf.field_obj, "collect_surface_ids") else set()
                                sid_base = cf.field_obj.surface_id
                                if sid_base < 65535:
                                    sids.update(range(sid_base, sid_base + cf.field_obj.surface_count()))
                                for s_i in sids:
                                    if 0 <= s_i < table_size:
                                        meta_arr[s_i] = row

                            self._glBindBuffer(GL_SHADER_STORAGE_BUFFER, self._field_meta_buf_id)
                            glBufferSubData = _loader.get("glBufferSubData", [ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_ssize_t, ctypes.c_void_p], None)
                            if table_size <= getattr(self, "_field_meta_capacity", 0) and glBufferSubData:
                                glBufferSubData(GL_SHADER_STORAGE_BUFFER, 0, meta_arr.nbytes, meta_arr.ctypes.data)
                            else:
                                self._glBufferData(GL_SHADER_STORAGE_BUFFER, meta_arr.nbytes, meta_arr.ctypes.data, GL_DYNAMIC_DRAW)
                                self._field_meta_capacity = table_size
                            self._field_meta_dirty = False
                            # Snapshot the selection state that was baked into this SSBO.
                            # Pass 4's u_any_selected gate must reflect THIS state, not
                            # _selected_labels as of the composite frame — they can differ
                            # by one selection event (VR-030).
                            self._selected_labels_last_march = frozenset(self._selected_labels)

                        self._glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 3, self._field_meta_buf_id)

                    GL_TEXTURE0   = 0x84C0
                    GL_TEXTURE_3D = 0x806F
                    glActiveTexture(GL_TEXTURE0 + 10)
                    glBindTexture(GL_TEXTURE_3D, sv.tex_id)
                    _prog.set_1i("u_volume", 10)
                    glActiveTexture(GL_TEXTURE0 + 11)
                    glBindTexture(GL_TEXTURE_3D, sv.id_tex_id)
                    _prog.set_1i("u_id_volume", 11)
                    glActiveTexture(GL_TEXTURE0 + 12)
                    glBindTexture(GL_TEXTURE_3D, getattr(sv, "norm_tex_id", 0))
                    _prog.set_1i("u_norm_volume", 12)
                    _prog.dispatch_compute((target_w + 7) // 8, (target_h + 7) // 8)
                    memory_barrier(0x00000020 | 0x00000008)

                    glActiveTexture(GL_TEXTURE0 + 10)
                    glBindTexture(GL_TEXTURE_3D, 0)
                    glActiveTexture(GL_TEXTURE0 + 11)
                    glBindTexture(GL_TEXTURE_3D, 0)
                    glActiveTexture(GL_TEXTURE0 + 12)
                    glBindTexture(GL_TEXTURE_3D, 0)
                    glActiveTexture(GL_TEXTURE0)
                    bind_image_texture(0, 0, GL_WRITE_ONLY, GL_RGBA16F)
                    bind_image_texture(1, 0, GL_WRITE_ONLY, GL_RGBA16F)
                    bind_image_texture(2, 0, GL_WRITE_ONLY, GL_RGBA32F)
                    bind_image_texture(3, 0, GL_WRITE_ONLY, GL_R32F)

                else:
                    _t_pass1_start = time.perf_counter()
                    # Compute not supported or no active fields: clear FBO and bypass
                    self._gbuf_fbo.bind()
                    glViewport(0, 0, target_w, target_h)
                    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
                    glBindFramebuffer(GL_FRAMEBUFFER, 0)

                if _profiling:
                    glFinish0 = _loader.get("glFinish", [], None)
                    if glFinish0: glFinish0()
                self._perf.add("pass1_dispatch", time.perf_counter() - _t_pass1_start)

                if _has_analytical:
                    _t_pass2_start = time.perf_counter()
                    # ── Pass 2: SSAO ──
                    self._ssao_fbo.bind()
                    glViewport(0, 0, target_w, target_h)
                    glClear(GL_COLOR_BUFFER_BIT)
                    self._prog_ssao.use()
                    glActiveTexture(0x84C0)
                    glBindTexture(GL_TEXTURE_2D, self._gbuf_fbo.color_texture(1))  # vs_pos
                    self._prog_ssao.set_1i("u_pos", 0)
                    glActiveTexture(0x84C1)
                    glBindTexture(GL_TEXTURE_2D, self._gbuf_fbo.color_texture(2))  # vs_normal
                    self._prog_ssao.set_1i("u_normal", 1)
                    glActiveTexture(0x84C2)
                    glBindTexture(GL_TEXTURE_2D, self._noise_tex_id)
                    self._prog_ssao.set_1i("u_noise", 2)
                    self._prog_ssao.set_2f("u_noise_scale", target_w / 4.0, target_h / 4.0)
                    self._prog_ssao.set_3fv("u_samples", 64, self._ssao_kernel_flat)
                    self._prog_ssao.set_mat4("u_proj", list(proj_data))
                    self._prog_ssao.set_1f("u_res_scale", _res_scale)
                    self._prog_ssao.draw_fullscreen_quad()
                    _check_gl_error("pass2_ssao")

                    if _profiling:
                        # Extra sync so SSAO and Blur are attributed separately.
                        # Only paid when profiling is on — Pass 3's existing
                        # glFinish() below already covers both when it's off.
                        _gf_split = _loader.get("glFinish", [], None)
                        if _gf_split: _gf_split()
                    self._perf.add("pass2_ssao", time.perf_counter() - _t_pass2_start)
                    _t_pass3_start = time.perf_counter()

                    # ── Pass 3: Blur ──
                    self._blur_fbo.bind()
                    glViewport(0, 0, target_w, target_h)
                    glClear(GL_COLOR_BUFFER_BIT)
                    self._prog_blur.use()
                    glActiveTexture(0x84C0)
                    glBindTexture(GL_TEXTURE_2D, self._ssao_fbo.color_texture(0))
                    self._prog_blur.set_1i("u_ssao", 0)
                    # Texel size is relative to the native-sized backing texture, not
                    # the (possibly smaller) populated sub-rect — see u_res_scale.
                    self._prog_blur.set_2f("u_texel_size", 1.0 / w, 1.0 / h)
                    self._prog_blur.set_1f("u_res_scale", _res_scale)
                    self._prog_blur.draw_fullscreen_quad()
                    _check_gl_error("pass3_blur")
                    if _profiling:
                        glFinish = _loader.get("glFinish", [], None)
                        if glFinish: glFinish()
                    self._perf.add("pass3_blur", time.perf_counter() - _t_pass3_start)
            except Exception:
                fld_logger.exception("SceneVoxel: Pass 1-3 render failed")
                if _pending_fbo_set is not None:
                    # Roll back to the last good FBOs/resolution; discard the broken new set
                    # so the composite pass below still has valid content to show.
                    fld_logger.error(
                        f"SceneVoxel: rolling back resolution swap to {self._vp_size} "
                        f"after Pass 1-3 failure at target {target_w}x{target_h}"
                    )
                    self._gbuf_fbo, self._ssao_fbo, self._blur_fbo, self._depth_img_tex = _old_fbo_set
                    self._destroy_fbo_set(*_pending_fbo_set)
                    w, h = self._vp_size
                    target_w, target_h = min(target_w, w), min(target_h, h)
                    _res_scale = (target_w / w) if w > 0 else 1.0
                    _pending_fbo_set = None
            else:
                if _pending_fbo_set is not None:
                    fld_logger.render_debug(f"SceneVoxel: native FBO resize committed -> {w}x{h}")
                    self._destroy_fbo_set(*_old_fbo_set)
                    self._vp_size = (w, h)
                    _pending_fbo_set = None

        # ── Pass 4: Composite cached FBO results → restore main FBO ──
        # Runs every Coin3D frame (cheap) so the SDF overlay never disappears.
        # Guard everything the pass dereferences, not just the G-buffer.
        _can_composite = (self._gbuf_fbo is not None and self._blur_fbo is not None
                          and self._prog_comp is not None and w > 0 and h > 0)
        if _can_composite:
            _t_pass4_start = time.perf_counter()
            glBindFramebuffer(GL_FRAMEBUFFER, prev_fbo[0])
            glViewport(0, 0, w, h)
            self._prog_comp.use()
            glActiveTexture(0x84C0)
            glBindTexture(GL_TEXTURE_2D, self._gbuf_fbo.color_texture(0))  # Phong color
            self._prog_comp.set_1i("u_color", 0)
            glActiveTexture(0x84C1)
            glBindTexture(GL_TEXTURE_2D, self._blur_fbo.color_texture(0))  # blurred AO
            self._prog_comp.set_1i("u_occlusion", 1)
            glActiveTexture(0x84C2)
            # Analytical compute path uses r32f depth image; otherwise uses depth24 FBO texture
            _depth_tex = (self._depth_img_tex
                          if _has_analytical and self._depth_img_tex
                          else self._gbuf_fbo.depth_texture())
            glBindTexture(GL_TEXTURE_2D, _depth_tex)
            self._prog_comp.set_1i("u_depth", 2)
            glActiveTexture(0x84C3)
            glBindTexture(GL_TEXTURE_2D, self._gbuf_fbo.color_texture(1))  # view-space pos (vis_alpha in .w)
            self._prog_comp.set_1i("u_pos", 3)
            glActiveTexture(0x84C4)
            glBindTexture(GL_TEXTURE_2D, self._gbuf_fbo.color_texture(2))  # view-space normal (surface id in .w)
            self._prog_comp.set_1i("u_norm", 4)
            # Bounds for the SSBO lookup. The FieldMeta buffer is still bound at
            # binding 3 from the compute pass -- SSBO binding points are context
            # state, not program state -- so this pass reads the very rows the
            # march read, with no second upload.
            self._prog_comp.set_1i("u_field_count", int(getattr(self, "_field_meta_count", 0)))
            # u_texel_size is declared only in _FRAG_BLUR, not in _FRAG_COMP — removed (VR-030).
            from freecad.fields.core.objects.fld_object import get_sdf_selection_outline_size
            self._prog_comp.set_1i("u_outline_size", get_sdf_selection_outline_size())
            # Gate the outline sweep on the selection state as of the last march, not the
            # current frame — they can differ by one event when Pass 4 runs ahead of Pass 1.
            _any_sel = bool(getattr(self, "_selected_labels_last_march", set()))
            self._prog_comp.set_1i("u_any_selected", 1 if _any_sel else 0)
            self._prog_comp.set_1f("u_res_scale", _res_scale)
            GL_SRC_ALPHA           = 0x0302
            GL_ONE_MINUS_SRC_ALPHA = 0x0303
            glBlendFunc = _loader.get("glBlendFunc", [ctypes.c_uint, ctypes.c_uint], None)
            if glBlendFunc:
                glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glEnable(GL_BLEND)
            self._prog_comp.draw_fullscreen_quad()
            _check_gl_error("pass4_composite")
            glDisable(GL_BLEND)
            if _profiling:
                glFinish2 = _loader.get("glFinish", [], None)
                if glFinish2: glFinish2()
            self._perf.add("pass4_composite", time.perf_counter() - _t_pass4_start)
        else:
            fld_logger.error("SceneVoxel: no valid FBO set to composite; skipping Pass 4")

        # ── Cleanup: unbind textures, restore shader + GL state ──
        for unit in (0x84C4, 0x84C3, 0x84C2, 0x84C1, 0x84C0):
            glActiveTexture(unit)
            glBindTexture(GL_TEXTURE_2D, 0)
        glUseProgram(0)

        # Restore depth test / blend / depth-write to what Coin3D had
        if depth_enabled[0]:
            glEnable(GL_DEPTH_TEST)
        else:
            glDisable(GL_DEPTH_TEST)
        if blend_enabled[0]:
            glEnable(GL_BLEND)
        else:
            glDisable(GL_BLEND)
        glDepthMask(depth_write[0])

        if getattr(self, "_run_expensive_last", False):
            self._run_expensive_last = False

        self._perf.maybe_flush()

    def _compute_grid_params(self, field, cell_size: float) -> dict:
        """Helper to get clamped and padded bounding box for a field.
        Used by size limit tests and volume baking.
        """
        from freecad.fields.core.fld_settings import get_max_sdf_render_size
        mn, mx = field.bounding_box()
        
        limit = get_max_sdf_render_size()
        
        # Clamp dimensions to limit around their midpoints
        # Make copies of the vectors so we don't mutate the field's internal state
        bmin = FreeCAD.Vector(mn)
        bmax = FreeCAD.Vector(mx)
        
        for attr in ['x', 'y', 'z']:
            mn_val = getattr(bmin, attr)
            mx_val = getattr(bmax, attr)
            size = mx_val - mn_val
            if size > limit:
                mid = (mn_val + mx_val) * 0.5
                setattr(bmin, attr, mid - limit * 0.5)
                setattr(bmax, attr, mid + limit * 0.5)
                
        # Apply padding
        bmin.x -= cell_size
        bmax.x += cell_size
        bmin.y -= cell_size
        bmax.y += cell_size
        bmin.z -= cell_size
        bmax.z += cell_size
        
        return {
            "bbox_min": bmin,
            "bbox_max": bmax
        }

    def _check_bbox_margin_safety(self, field, label, bmin, bmax):
        """Samples the SDF field just outside each face of its bounding box to check for potential visual clipping."""
        import numpy as np
        import FreeCAD
        from freecad.fields.core import fld_logger
        
        c = (bmin + bmax) * 0.5
        h = (bmax - bmin) * 0.5
        
        # Test offset (1.0 mm outside the box)
        offset = 1.0
        
        pts = np.array([
            [c.x + h.x + offset, c.y, c.z],
            [c.x - h.x - offset, c.y, c.z],
            [c.x, c.y + h.y + offset, c.z],
            [c.x, c.y - h.y - offset, c.z],
            [c.x, c.y, c.z + h.z + offset],
            [c.x, c.y, c.z - h.z - offset]
        ], dtype=np.float32)
        
        try:
            vals = field.evaluate_grid(pts)
            for i, val in enumerate(vals):
                if val < 0.0:
                    face_names = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]
                    fld_logger.warn(
                        f"SDF field '{label}' ({type(field).__name__}) bounding box might be too tight. "
                        f"Point just outside {face_names[i]} face has negative SDF value: {val:.4f} mm (inside solid)."
                    )
                    break
        except Exception as e:
            fld_logger.render_debug(f"_check_bbox_margin_safety failed for '{label}': {e}")

    def _rebuild(self):
        """Compile all registered SDF fields to an analytical GLSL fragment shader.
        All SDF fields must implement to_glsl(). Fields missing it are skipped with a warning.
        """
        if getattr(self, "_batch_update_count", 0) > 0:
            self._batch_needs_rebuild = True
            return

        t0 = time.perf_counter()
        

        if getattr(self, "_tex3d_cache", None):
            for k in list(self._tex3d_cache.keys()):
                if k[0] in self._registry.dirty_fields:
                    entry = self._tex3d_cache.pop(k, None)
                    if entry and entry.get("tex"):
                        try:
                            entry["tex"].destroy()
                        except Exception as e:
                            fld_logger.error(f"SceneVoxel: failed to destroy 3D texture for '{k}': {e}")

        _dirty_snapshot = set(self._registry.dirty_fields)
        visible_compiled, analytical_data, t_compile_total = self._registry.compile_visible(
            self._appearance,
            bbox_checker=self._check_bbox_margin_safety
        )

        if not analytical_data:
            self._switch.whichChild = -1
            return
        
        self._hmap_cache.plan_bindings(analytical_data)

        from freecad.fields.core.render.scene_shader_builder import SceneShaderBuilder
        t_shader_start = time.perf_counter()
        compiled_scene = SceneShaderBuilder.build(analytical_data, visible_compiled)
        t_shader_total = time.perf_counter() - t_shader_start

        self._scene = compiled_scene
        
        dirty_labels = _dirty_snapshot
        old_data_map = {d.get("label", d["ctx"].prefix): d for d in (self._last_analytical_data or []) if "ctx" in d}
        new_data_map = {d.get("label", d["ctx"].prefix): d for d in analytical_data if "ctx" in d}
        labels_changed = set(old_data_map.keys()) != set(new_data_map.keys())

        if not labels_changed and dirty_labels:
            rmin_x = rmin_y = rmin_z = float('inf')
            rmax_x = rmax_y = rmax_z = float('-inf')
            for lbl in dirty_labels:
                # A field's bounding box says where it IS, not where it changed,
                # and for a one-field cage those are wildly different: the box is
                # the whole object on every tick, so the region below degenerates
                # to a full bake. A field that can name the part of space it
                # actually rewrote (an extrusion stack knows only the dragged lobe
                # moved) gets to say so; anything that cannot falls through to the
                # old/new box union, which is always safe and always coarse.
                nd_f = (new_data_map.get(lbl) or {}).get("field")
                od_f = (old_data_map.get(lbl) or {}).get("field")
                narrowed = None
                if nd_f is not None and od_f is not None and nd_f is not od_f:
                    try:
                        fn = getattr(nd_f, "changed_region", None)
                        narrowed = fn(od_f) if fn is not None else None
                    except Exception as e:
                        fld_logger.render_debug(
                            f"SceneVoxel: changed_region failed for '{lbl}': {e}")
                        narrowed = None
                if narrowed is not None:
                    nmin, nmax = narrowed
                    rmin_x = min(rmin_x, nmin.x)
                    rmin_y = min(rmin_y, nmin.y)
                    rmin_z = min(rmin_z, nmin.z)
                    rmax_x = max(rmax_x, nmax.x)
                    rmax_y = max(rmax_y, nmax.y)
                    rmax_z = max(rmax_z, nmax.z)
                    continue
                if lbl in old_data_map:
                    od = old_data_map[lbl]
                    rmin_x = min(rmin_x, od["bbox_min"].x)
                    rmin_y = min(rmin_y, od["bbox_min"].y)
                    rmin_z = min(rmin_z, od["bbox_min"].z)
                    rmax_x = max(rmax_x, od["bbox_max"].x)
                    rmax_y = max(rmax_y, od["bbox_max"].y)
                    rmax_z = max(rmax_z, od["bbox_max"].z)
                if lbl in new_data_map:
                    nd = new_data_map[lbl]
                    rmin_x = min(rmin_x, nd["bbox_min"].x)
                    rmin_y = min(rmin_y, nd["bbox_min"].y)
                    rmin_z = min(rmin_z, nd["bbox_min"].z)
                    rmax_x = max(rmax_x, nd["bbox_max"].x)
                    rmax_y = max(rmax_y, nd["bbox_max"].y)
                    rmax_z = max(rmax_z, nd["bbox_max"].z)
            if rmin_x < rmax_x:
                self._mark_dirty_region((FreeCAD.Vector(rmin_x, rmin_y, rmin_z),
                                         FreeCAD.Vector(rmax_x, rmax_y, rmax_z)))
            else:
                self._mark_dirty_region(None)
        else:
            self._mark_dirty_region(None)

        self._last_analytical_data = analytical_data
        self._volume_dirty = True
        self._field_meta_dirty = True

        self._active_uniforms = compiled_scene.uniform_dict

        t_total = time.perf_counter() - t0
        if hasattr(self, "_drag_session_rebuild_time"):
            self._drag_session_rebuild_time += t_total
        self._perf.add("rebuild_total", t_total)
        self._perf.add("rebuild_compile", t_compile_total)
        self._perf.add("rebuild_shadergen", t_shader_total)

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
        self._bbox_coords.point.setValues(0, 8, pts)
        self._coords.point.setValues(0, 8, pts)
        self._switch.whichChild = 0


