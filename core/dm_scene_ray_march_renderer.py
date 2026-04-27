"""
core/dm_scene_ray_march_renderer.py

Scene-level GPU ray march renderer. One full-screen quad renders ALL SDF
fields combined via a single baked 3D texture atlas.

Two bake paths:
  GPU (GL 4.3+): SDF field tree → compile to GLSL → compute shader writes
                 directly to 3D texture. Near-instant (<1ms).
  CPU fallback:  field.evaluate_grid() on numpy grid → upload to GPU.
"""
import math
import time
import ctypes
import FreeCAD
import FreeCADGui
import pivy.coin as coin
from core import dm_logger
from core.gl_texture3d import GLTexture3D

_VERT_PASSTHROUGH = """
#version 330 compatibility
out vec2 v_uv;
void main() {
    v_uv = gl_Vertex.xy;
    gl_Position = vec4(gl_Vertex.xy, 0.0, 1.0);
}
"""

_FRAG_GBUF = """
#version 330 compatibility
in vec2 v_uv;

uniform sampler3D u_sdf_vol;
uniform int   u_num_fields;
uniform int   u_nx[8], u_ny[8], u_nz[8], u_z_offset[8], u_z_total;
uniform int   u_is_subtractive[8];
uniform vec3  u_bbox_min[8], u_bbox_max[8];
uniform vec3  u_light_dir;

layout(location = 0) out vec4 out_color;
layout(location = 1) out vec4 out_vspos;
layout(location = 2) out vec4 out_vsnorm;

float decode_texel(ivec3 tc) {
    return texelFetch(u_sdf_vol, tc, 0).r;
}

float sample_sdf_field(int fi, vec3 p) {
    vec3 uvw = (p - u_bbox_min[fi]) / (u_bbox_max[fi] - u_bbox_min[fi]);
    uvw = clamp(uvw, vec3(0.0), vec3(1.0));
    vec3 tc = vec3(
        uvw.x * float(u_nx[fi]),
        uvw.y * float(u_ny[fi]),
        float(u_z_offset[fi]) + uvw.z * float(u_nz[fi])
    );
    tc = clamp(tc, vec3(0.0), vec3(float(u_nx[fi]), float(u_ny[fi]),
               float(u_z_offset[fi] + u_nz[fi])));
    ivec3 c0 = ivec3(floor(tc));
    ivec3 c1 = min(c0 + 1, ivec3(u_nx[fi], u_ny[fi], u_z_offset[fi] + u_nz[fi]));
    vec3 f = tc - vec3(c0);
    float d000 = decode_texel(ivec3(c0.x, c0.y, c0.z));
    float d100 = decode_texel(ivec3(c1.x, c0.y, c0.z));
    float d010 = decode_texel(ivec3(c0.x, c1.y, c0.z));
    float d110 = decode_texel(ivec3(c1.x, c1.y, c0.z));
    float d001 = decode_texel(ivec3(c0.x, c0.y, c1.z));
    float d101 = decode_texel(ivec3(c1.x, c0.y, c1.z));
    float d011 = decode_texel(ivec3(c0.x, c1.y, c1.z));
    float d111 = decode_texel(ivec3(c1.x, c1.y, c1.z));
    float dx00 = mix(d000, d100, f.x);
    float dx10 = mix(d010, d110, f.x);
    float dx01 = mix(d001, d101, f.x);
    float dx11 = mix(d011, d111, f.x);
    float dxy0 = mix(dx00, dx10, f.y);
    float dxy1 = mix(dx01, dx11, f.y);
    return mix(dxy0, dxy1, f.z);
}

vec3 sdf_normal_field(int fi, vec3 p) {
    float cell = (u_bbox_max[fi].x - u_bbox_min[fi].x) / max(float(u_nx[fi]), 1.0);
    float h = cell * 3.0;
    vec2 k = vec2(1.0, -1.0);
    vec3 g = k.xyy * sample_sdf_field(fi, p + k.xyy*h) +
             k.yyx * sample_sdf_field(fi, p + k.yyx*h) +
             k.yxy * sample_sdf_field(fi, p + k.yxy*h) +
             k.xxx * sample_sdf_field(fi, p + k.xxx*h);
    float len2 = dot(g, g);
    return (len2 > 1e-10) ? g * inversesqrt(len2) : vec3(0.0, 0.0, 1.0);
}

vec2 intersect_aabb(vec3 ro, vec3 rd, vec3 bmin, vec3 bmax) {
    vec3 t1 = (bmin - ro) / rd;
    vec3 t2 = (bmax - ro) / rd;
    vec3 tmin_ = min(t1, t2);
    vec3 tmax_ = max(t1, t2);
    return vec2(max(max(tmin_.x, tmin_.y), tmin_.z),
                min(min(tmax_.x, tmax_.y), tmax_.z));
}

void main() {
    vec4 ndc_near = vec4(v_uv, -1.0, 1.0);
    vec4 world_near = gl_ModelViewProjectionMatrixInverse * ndc_near;
    world_near /= world_near.w;
    vec4 ndc_far = vec4(v_uv, 1.0, 1.0);
    vec4 world_far = gl_ModelViewProjectionMatrixInverse * ndc_far;
    world_far /= world_far.w;

    vec3 ro = world_near.xyz;
    vec3 rd = normalize(world_far.xyz - world_near.xyz);
    float ray_tmax = length(world_far.xyz - world_near.xyz);

    vec3 scene_min = u_bbox_min[0];
    vec3 scene_max = u_bbox_max[0];
    for (int fi = 1; fi < 8; fi++) {
        if (fi >= u_num_fields) break;
        scene_min = min(scene_min, u_bbox_min[fi]);
        scene_max = max(scene_max, u_bbox_max[fi]);
    }
    vec2 tBox = intersect_aabb(ro, rd, scene_min, scene_max);
    float tNear = max(tBox.x, 0.0);
    float tFar  = min(tBox.y, ray_tmax);
    if (tNear > tFar) {
        out_color = vec4(0.0); out_vspos = vec4(0.0); out_vsnorm = vec4(0.0);
        return;
    }

    float ftn[8]; float ftf[8];
    for (int fi = 0; fi < 8; fi++) {
        if (fi < u_num_fields) {
            vec2 fi_int = intersect_aabb(ro, rd, u_bbox_min[fi], u_bbox_max[fi]);
            ftn[fi] = max(fi_int.x, 0.0);
            ftf[fi] = min(fi_int.y, ray_tmax);
        } else { ftn[fi] = 1.0e10; ftf[fi] = -1.0e10; }
    }

    float global_hit_thresh = 1.0;
    for (int fi = 0; fi < 8; fi++) {
        if (fi >= u_num_fields) break;
        float c = (u_bbox_max[fi].x - u_bbox_min[fi].x) / max(float(u_nx[fi]), 1.0);
        global_hit_thresh = min(global_hit_thresh, c * 0.1);
    }

    float t = tNear; bool hit = false; int hit_field = 0;
    for (int i = 0; i < 512; i++) {
        vec3 p = ro + t * rd; float min_d = 1.0e10;
        for (int fi = 0; fi < 8; fi++) {
            if (fi >= u_num_fields) break;
            if (ftn[fi] > ftf[fi]) continue;
            if (t > ftf[fi]) continue;
            if (t < ftn[fi]) { min_d = min(min_d, ftn[fi] - t); continue; }
            float d = sample_sdf_field(fi, p);
            float cell = (u_bbox_max[fi].x - u_bbox_min[fi].x) / max(float(u_nx[fi]), 1.0);
            if (abs(d) < cell * 0.1) { hit = true; hit_field = fi; break; }
            min_d = min(min_d, abs(d));
        }
        if (hit) break;
        t += max(min_d * 0.9, global_hit_thresh * 0.5);
        if (t > tFar) break;
    }

    if (!hit) {
        out_color = vec4(0.0); out_vspos = vec4(0.0); out_vsnorm = vec4(0.0);
        return;
    }

    vec3 hp = ro + t * rd;
    vec3 n  = sdf_normal_field(hit_field, hp);
    vec3 vd = normalize(-rd);

    float diff = max(dot(n, u_light_dir), 0.0);
    float spec = pow(max(dot(reflect(-u_light_dir, n), vd), 0.0), 32.0);

    vec3 base_color = (u_is_subtractive[hit_field] == 1)
        ? vec3(0.3, 0.5, 1.0) : vec3(1.0, 0.5, 0.0);
    vec3 color = base_color * (0.25 + 0.70 * diff) + vec3(0.3) * spec;

    vec4 vs   = gl_ModelViewMatrix * vec4(hp, 1.0);
    vec3 vs_n = normalize(mat3(gl_ModelViewMatrix) * n);

    out_color  = vec4(color, 1.0);
    out_vspos  = vec4(vs.xyz, 1.0);
    out_vsnorm = vec4(vs_n,   1.0);

    vec4 clip   = gl_ModelViewProjectionMatrix * vec4(hp, 1.0);
    float ndc_z = clip.z / clip.w;
    gl_FragDepth = gl_DepthRange.near + gl_DepthRange.diff * (ndc_z * 0.5 + 0.5);
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

const float RADIUS = 50.0;
const float BIAS   = 0.025;

void main() {
    vec2 uv = v_uv * 0.5 + 0.5;

    vec3 frag_pos = texture(u_pos, uv).xyz;
    float hit_w   = texture(u_pos, uv).w;

    if (hit_w < 0.5) {
        gl_FragColor = vec4(1.0);
        return;
    }

    vec3 normal   = normalize(texture(u_normal, uv).rgb);
    vec3 rand_vec = normalize(texture(u_noise, uv * u_noise_scale).rgb);

    vec3 tangent   = normalize(rand_vec - normal * dot(rand_vec, normal));
    vec3 bitangent = cross(normal, tangent);
    mat3 TBN       = mat3(tangent, bitangent, normal);

    float occlusion = 0.0;
    for (int i = 0; i < 64; i++) {
        vec3 s = frag_pos + TBN * u_samples[i] * RADIUS;

        vec4 offset = u_proj * vec4(s, 1.0);
        offset.xyz /= offset.w;
        offset.xyz  = offset.xyz * 0.5 + 0.5;

        float sample_depth = texture(u_pos, offset.xy).z;
        float range_check  = smoothstep(0.0, 1.0, RADIUS / abs(frag_pos.z - sample_depth));
        occlusion += (sample_depth >= s.z + BIAS ? 1.0 : 0.0) * range_check;
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

void main() {
    vec2 uv = v_uv * 0.5 + 0.5;
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

_FRAG_COMP = """
#version 330 compatibility
in vec2 v_uv;

uniform sampler2D u_color;
uniform sampler2D u_occlusion;
uniform sampler2D u_depth;

void main() {
    vec2 uv    = v_uv * 0.5 + 0.5;
    vec4 color = texture(u_color, uv);

    if (color.a < 0.5) discard;

    float ao    = texture(u_occlusion, uv).r;
    vec3 result = color.rgb * ao;
    result      = pow(result, vec3(1.0 / 2.2));

    gl_FragColor = vec4(result, 1.0);
    gl_FragDepth = texture(u_depth, uv).r;
}
"""


class DMSceneRayMarchRenderer:
    """Singleton scene-level ray march renderer."""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def destroy(cls):
        if cls._instance is not None:
            cls._instance._detach()
            cls._instance = None

    def __init__(self):
        self._fields = {}          # label -> (field, visible)
        self._attached = False
        self._u = {}               # uniform nodes
        self._gl_tex = GLTexture3D(fmt='r32f')
        self._bbox_coords = None
        self._cam_sensor    = None
        self._zoom_timer    = None
        self._baked_cache  = {}    # label → baked metadata dict
        self._dirty_fields = set() # labels needing rebake on next _rebuild()

        # Multi-pass SSAO renderer state
        self._active_uniforms = {}    # populated by _rebuild(), consumed by render callback
        self._prog_gbuf  = None       # GLProgram: G-buffer ray march
        self._prog_ssao  = None       # GLProgram: SSAO
        self._prog_blur  = None       # GLProgram: blur
        self._prog_comp  = None       # GLProgram: composition + gamma
        self._gbuf_fbo   = None       # GLFramebuffer: color0+vspos+vsnorm+depth
        self._ssao_fbo   = None       # GLFramebuffer: R16F occlusion
        self._blur_fbo   = None       # GLFramebuffer: R16F blurred occlusion
        self._noise_tex_id = 0        # GL texture id: 4x4 random rotation vectors
        self._ssao_kernel_flat = []   # 192 floats: 64 hemisphere samples (x,y,z each)
        self._vp_size    = (0, 0)     # Last known viewport (w, h) for resize detection

        # GPU compute state
        self._gpu_supported = None  # None = not yet checked, True/False after check
        self._gpu_programs = {}     # label -> {"program": GLComputeShader, "source": str}
        self._pending_dispatches = []  # queued for execution in GL callback

        self._root = coin.SoSeparator()
        for attr in ["renderCulling", "renderCaching", "cullCaching", "boundingBoxCaching"]:
            try:
                getattr(self._root, attr).setValue(coin.SoSeparator.OFF)
            except AttributeError:
                pass
        self._switch = coin.SoSwitch()
        self._switch.addChild(self._root)
        self._switch.whichChild = -1
        self._setup_nodes()

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

        # Destroy multi-pass GL resources (must be done in GL context; best-effort here)
        for prog in (self._prog_gbuf, self._prog_ssao, self._prog_blur, self._prog_comp):
            if prog is not None:
                try:
                    prog.destroy()
                except Exception:
                    pass
        self._prog_gbuf = self._prog_ssao = self._prog_blur = self._prog_comp = None

        for fbo in (self._gbuf_fbo, self._ssao_fbo, self._blur_fbo):
            if fbo is not None:
                try:
                    fbo.destroy()
                except Exception:
                    pass
        self._gbuf_fbo = self._ssao_fbo = self._blur_fbo = None

        if self._noise_tex_id:
            from core.gl_texture3d import _loader
            glDeleteTextures = _loader.get("glDeleteTextures",
                [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
            arr = (ctypes.c_uint * 1)(self._noise_tex_id)
            glDeleteTextures(1, arr)
            self._noise_tex_id = 0

        self._active_uniforms = {}
        self._vp_size = (0, 0)

        # Clean up GPU compute programs
        for info in self._gpu_programs.values():
            try:
                info["program"].destroy()
            except Exception:
                pass
        self._gpu_programs.clear()
        self._pending_dispatches.clear()
        self._attached = False

    def _attach_camera_sensor(self, view):
        try:
            from pivy.coin import SoNodeSensor
            cam = view.getCameraNode()
            if cam is None:
                return
            self._cam_sensor = SoNodeSensor(self._on_camera_changed, None)
            self._cam_sensor.attach(cam)
        except Exception as e:
            dm_logger.debug(f"SceneRayMarch: Failed to attach camera sensor: {e}")

    def _on_camera_changed(self, userdata, sensor):
        """Immediately redraw with existing texture, then debounce a rebake."""
        from PySide import QtCore
        try:
            if FreeCADGui.activeView():
                FreeCADGui.activeView().redraw()
        except Exception:
            pass

        if self._zoom_timer is not None:
            self._zoom_timer.stop()
        self._zoom_timer = QtCore.QTimer()
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.timeout.connect(self._on_zoom_settled)
        self._zoom_timer.start(400)

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

    def _setup_nodes(self):
        # 1. Bounding-box debug wireframe (toggled by render debug mode)
        self._bbox_switch = coin.SoSwitch()
        self._bbox_sep = coin.SoSeparator()
        self._bbox_switch.addChild(self._bbox_sep)
        self._root.addChild(self._bbox_switch)

        from core.dm_object import get_render_debug_mode
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
        for attr in ["renderCulling", "renderCaching", "cullCaching", "boundingBoxCaching"]:
            try:
                getattr(self._shader_sep, attr).setValue(coin.SoSeparator.OFF)
            except AttributeError:
                pass

        quad_mat = coin.SoMaterial()
        quad_mat.transparency.setValue(0.0)
        self._shader_sep.addChild(quad_mat)

        # 3D texture upload callback (binds SDF atlas to unit 0)
        self._shader_sep.addChild(self._gl_tex.callback_node)

        # GPU compute dispatch callback
        self._compute_cb_node = coin.SoCallback()
        self._compute_cb_node.setCallback(self._compute_gl_callback)
        self._shader_sep.addChild(self._compute_cb_node)

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

    def _compute_gl_callback(self, userdata, action):
        """Execute pending GPU compute dispatches within the GL context."""
        if not action.isOfType(coin.SoGLRenderAction.getClassTypeId()):
            return
        if not self._pending_dispatches:
            return

        # Check GPU compute support on first call
        if self._gpu_supported is None:
            try:
                from core.gl_compute import check_compute_support
                self._gpu_supported = check_compute_support()
                dm_logger.debug(
                    f"SceneRayMarch: GPU compute {'supported' if self._gpu_supported else 'not available (need GL 4.3+)'}")
            except Exception as e:
                dm_logger.debug(f"SceneRayMarch: compute support check failed: {e}")
                self._gpu_supported = False

        if not self._gpu_supported:
            # CPU fallback dispatches are already handled in _rebuild
            self._pending_dispatches.clear()
            return

        from core.gl_compute import bind_image_texture, memory_barrier

        dispatches = self._pending_dispatches
        self._pending_dispatches = []

        # Ensure texture is allocated (callback_node will do this,
        # but we need tex_id NOW for image binding)
        self._gl_tex._ensure_tex_id()

        for d in dispatches:
            try:
                prog = d["program"]
                if d["needs_compile"]:
                    prog.compile(d["source"])

                prog.use()
                bind_image_texture(0, self._gl_tex.tex_id)
                prog.set_vec3("u_grid_min", d["grid_min"])
                prog.set_vec3("u_grid_step", d["grid_step"])
                prog.set_ivec3("u_grid_count", d["grid_count"])
                prog.set_int("u_z_offset", d["z_offset"])
                prog.set_uniforms_from_ctx(d["uniforms"])

                gx = (d["grid_count"][0] + 7) // 8
                gy = (d["grid_count"][1] + 7) // 8
                gz = (d["grid_count"][2] + 7) // 8
                prog.dispatch(gx, gy, gz)
            except Exception as e:
                dm_logger.debug(f"SceneRayMarch: GPU dispatch failed for '{d.get('label','')}': {e}")

        memory_barrier()

    # -- Public API --

    def register_field(self, label, field):
        dm_logger.debug(f"SceneRayMarch: Registering field '{label}'")
        self._fields[label] = (field, True)
        self._dirty_fields.add(label)
        self._attach()
        self._rebuild()
        if FreeCADGui.activeView():
            FreeCADGui.activeView().redraw()

    def unregister_field(self, label):
        if label in self._fields:
            dm_logger.debug(f"SceneRayMarch: Unregistering field '{label}'")
            self._fields.pop(label)
        self._baked_cache.pop(label, None)
        self._dirty_fields.discard(label)
        # Clean up GPU program
        gpu_info = self._gpu_programs.pop(label, None)
        if gpu_info:
            try:
                gpu_info["program"].destroy()
            except Exception:
                pass

        if not self._fields:
            self._switch.whichChild = -1
        else:
            self._rebuild()

        try:
            active_view = FreeCADGui.ActiveDocument.ActiveView
            if active_view:
                active_view.redraw()
        except Exception as e:
            dm_logger.debug(f"DMSceneRayMarchRenderer: Failed to redraw view: {e}")

    def set_field_visible(self, label, visible):
        if label in self._fields:
            field, _ = self._fields[label]
            self._fields[label] = (field, visible)
            self._rebuild()

    def update_field(self, label, field):
        from core.dm_object import get_perf_profiler_enabled
        _t0 = time.perf_counter()
        visible = self._fields.get(label, (None, True))[1]
        self._fields[label] = (field, visible)
        self._dirty_fields.add(label)
        if not self._attached:
            self._attach()
        self._rebuild()
        if get_perf_profiler_enabled():
            _t1 = time.perf_counter()
            FreeCAD.Console.PrintMessage(
                f"[update_field] '{label}' total={1000*(_t1-_t0):.1f}ms\n"
            )

    def gc_fields(self):
        if not self._fields:
            return
        to_remove = []
        for label in self._fields:
            try:
                parts = label.split(".")
                if len(parts) != 2: continue
                doc_name, obj_name = parts
                doc = FreeCAD.getDocument(doc_name)
                if not doc or not doc.getObject(obj_name):
                    to_remove.append(label)
            except Exception:
                pass
        if to_remove:
            dm_logger.debug(f"SceneRayMarch: GC-ing orphaned fields: {to_remove}")
            for label in to_remove:
                self._fields.pop(label, None)
            self._rebuild()
            try:
                active_view = FreeCADGui.ActiveDocument.ActiveView
                if active_view:
                    active_view.redraw()
            except Exception as e:
                dm_logger.debug(f"DMSceneRayMarchRenderer: Failed to redraw view: {e}")

    def on_prefs_changed(self):
        from core.dm_object import get_render_debug_mode
        debug = get_render_debug_mode()
        self._bbox_switch.whichChild = 0 if debug else -1
        if self._fields:
            self._dirty_fields = set(self._fields.keys())
            self._rebuild()

    def _compute_cell_size(self, field, view):
        from core.dm_object import get_ray_march_cell_size
        base_cell  = get_ray_march_cell_size()
        n_texels   = 2  # 2 screen pixels per texel; LOD only kicks in when object is small on screen
        try:
            bb_min, bb_max = field.bounding_box()
            field_size = max(
                abs(bb_max.x - bb_min.x),
                abs(bb_max.y - bb_min.y),
                abs(bb_max.z - bb_min.z),
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

            px_per_world = vp_h / max(cam_world_h, 1e-3)
            field_screen_px = field_size * px_per_world
            linear_cell  = field_size / max(field_screen_px / n_texels, 1.0)
            linear_ratio = max(linear_cell / base_cell, 1.0)
            LOD_EXPONENT = 0.5
            MAX_RATIO    = 16.0
            ratio = min(linear_ratio, MAX_RATIO) ** LOD_EXPONENT
            return base_cell * ratio
        except Exception:
            return base_cell

    MAX_FIELDS = 8

    def _get_view_frustum_aabb(self, view):
        import numpy as np
        try:
            cam = view.getCameraNode()
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
            rot = cam.orientation.getValue()
            right   = rot.multVec(coin.SbVec3f(1,  0,  0))
            up      = rot.multVec(coin.SbVec3f(0,  1,  0))
            forward = rot.multVec(coin.SbVec3f(0,  0, -1))
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
                corners = np.array([
                    p + f*n + r*sx*hw + u*sy*hh
                    for sx in (-1, 1) for sy in (-1, 1) for n in (near, far)
                ])
            else:
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

    def _compute_cell_size_for_region(self, bbox_override, view):
        from core.dm_object import get_ray_march_cell_size
        base_cell = get_ray_march_cell_size()
        n_texels  = 2
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

    def _get_is_subtractive(self, label):
        try:
            parts = label.split(".", 1)
            if len(parts) != 2:
                return False
            doc = FreeCAD.getDocument(parts[0])
            obj = doc.getObject(parts[1]) if doc else None
            return bool(getattr(obj, "IsSubtractive", False))
        except Exception:
            return False

    def _compute_grid_params(self, field, cell_size, bbox_override=None):
        """Compute grid parameters for a field bake (shared by GPU and CPU paths).

        Returns dict with keys: nx, ny, nz, bbox_min, bbox_max, cell_size,
                                grid_min (tuple), grid_step (tuple), grid_count (tuple)
        """
        if bbox_override is not None:
            mn, mx = bbox_override
        else:
            mn, mx = field.bounding_box()

        from core.dm_object import get_max_sdf_render_size
        max_size = get_max_sdf_render_size()
        
        # Clamp bounding box if it exceeds MaxSdfRenderSize
        if mx.x - mn.x > max_size:
            mid = (mx.x + mn.x) / 2
            mn.x = mid - max_size / 2
            mx.x = mid + max_size / 2
        if mx.y - mn.y > max_size:
            mid = (mx.y + mn.y) / 2
            mn.y = mid - max_size / 2
            mx.y = mid + max_size / 2
        if mx.z - mn.z > max_size:
            mid = (mx.z + mn.z) / 2
            mn.z = mid - max_size / 2
            mx.z = mid + max_size / 2

        def _pad(lo, hi):
            if hi - lo < 1e-4:
                mid = (lo + hi) / 2
                return mid - 1.0, mid + 1.0
            return lo - cell_size, hi + cell_size

        x0, x1 = _pad(mn.x, mx.x)
        y0, y1 = _pad(mn.y, mx.y)
        z0, z1 = _pad(mn.z, mx.z)

        nx = max(1, int(math.ceil((x1 - x0) / cell_size)))
        ny = max(1, int(math.ceil((y1 - y0) / cell_size)))
        nz = max(1, int(math.ceil((z1 - z0) / cell_size)))

        return {
            "nx": nx, "ny": ny, "nz": nz,
            "bbox_min": FreeCAD.Vector(x0, y0, z0),
            "bbox_max": FreeCAD.Vector(
                x0 + nx * cell_size,
                y0 + ny * cell_size,
                z0 + nz * cell_size),
            "cell_size": cell_size,
            "grid_min": (x0, y0, z0),
            "grid_step": (cell_size, cell_size, cell_size),
            "grid_count": (nx + 1, ny + 1, nz + 1),
        }

    def _try_gpu_compile(self, field):
        """Try to compile a field's GLSL expression. Returns (expr, ctx) or None."""
        try:
            from core.sdf.glsl_compiler import compile_field_to_glsl
            expr, ctx = compile_field_to_glsl(field)
            return expr, ctx
        except NotImplementedError:
            return None
        except Exception as e:
            dm_logger.debug(f"SceneRayMarch: GLSL compile failed: {e}")
            return None

    def _init_gl_programs(self):
        """Compile all 4 SSAO pipeline programs and upload noise texture.
        Must be called inside an active GL context (i.e. from a SoCallback).
        """
        import random, math, ctypes
        from core.gl_program import GLProgram
        from core.gl_texture3d import _loader

        self._prog_gbuf = GLProgram()
        self._prog_gbuf.compile(_VERT_PASSTHROUGH, _FRAG_GBUF)

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

    def _resize_fbos(self, w: int, h: int):
        """Create or recreate all SSAO FBOs at viewport size (w, h).
        Must be called inside an active GL context.
        """
        from core.gl_framebuffer import GLFramebuffer
        if self._gbuf_fbo is None:
            self._gbuf_fbo = GLFramebuffer()
            self._ssao_fbo = GLFramebuffer()
            self._blur_fbo = GLFramebuffer()
        # G-buffer: Phong color + view-space pos + view-space normal + depth
        self._gbuf_fbo.create(w, h, ['rgba16f', 'rgba16f', 'rgba16f', 'depth24'])
        # SSAO: raw occlusion float
        self._ssao_fbo.create(w, h, ['r16f'])
        # Blur: blurred occlusion float
        self._blur_fbo.create(w, h, ['r16f'])

    def _set_gbuf_uniforms(self, prog):
        """Upload per-field and scene uniforms to the G-buffer shader program."""
        u = self._active_uniforms

        prog.set_1i("u_sdf_vol",    0)           # texture unit 0
        prog.set_1i("u_num_fields", u["n_fields"])
        prog.set_1i("u_z_total",    u["z_total"])

        # Fixed key light: normalize(1.0, 1.667, 1.105) ≈ 45° elevation
        prog.set_3f("u_light_dir", 0.4472, 0.7454, 0.4943)

        for fi, fd in enumerate(u["fields"]):
            prog.set_1i(f"u_nx[{fi}]",             fd["nx"])
            prog.set_1i(f"u_ny[{fi}]",             fd["ny"])
            prog.set_1i(f"u_nz[{fi}]",             fd["nz"])
            prog.set_1i(f"u_z_offset[{fi}]",       fd["z_offset"])
            prog.set_1i(f"u_is_subtractive[{fi}]", fd["is_subtractive"])
            prog.set_3f(f"u_bbox_min[{fi}]",       *fd["bbox_min"])
            prog.set_3f(f"u_bbox_max[{fi}]",       *fd["bbox_max"])

    def _render_gl_callback(self, userdata, action):
        """Execute 4-pass SSAO render inside Coin3D's GL context."""
        import ctypes
        if not action.isOfType(coin.SoGLRenderAction.getClassTypeId()):
            return
        if not self._active_uniforms or self._active_uniforms.get("n_fields", 0) == 0:
            return

        from core.gl_texture3d import _loader

        # Lazy-initialise programs + noise texture on first call
        if self._prog_gbuf is None:
            try:
                self._init_gl_programs()
            except Exception as e:
                dm_logger.debug(f"SceneRayMarch: _init_gl_programs failed: {e}")
                return

        if getattr(self, "_pending_frag_gbuf_source", None):
            try:
                self._prog_gbuf.compile(_VERT_PASSTHROUGH, self._pending_frag_gbuf_source)
                self._active_frag_gbuf_uniforms = self._pending_frag_gbuf_uniforms
                self._active_is_analytical = True
                self._pending_frag_gbuf_source = None
            except Exception as e:
                dm_logger.debug(f"SceneRayMarch: dynamic shader compile failed: {e}")
                self._active_is_analytical = False
                self._pending_frag_gbuf_source = None
        elif getattr(self, "_pending_restore_default_gbuf", False):
            self._prog_gbuf.compile(_VERT_PASSTHROUGH, _FRAG_GBUF)
            self._active_is_analytical = False
            self._pending_restore_default_gbuf = False

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

        GL_COLOR_BUFFER_BIT = 0x4000
        GL_DEPTH_BUFFER_BIT = 0x0100
        GL_FRAMEBUFFER      = 0x8D40
        GL_TEXTURE_2D       = 0x0DE1
        GL_TEXTURE_3D       = 0x806F

        # Viewport size
        vp = (ctypes.c_int * 4)(0, 0, 0, 0)
        glGetIntegerv(0x0BA2, vp)   # GL_VIEWPORT
        w, h = vp[2], vp[3]
        if w <= 0 or h <= 0:
            return

        # Resize FBOs if viewport changed
        if (w, h) != self._vp_size:
            try:
                self._resize_fbos(w, h)
                self._vp_size = (w, h)
            except Exception as e:
                dm_logger.debug(f"SceneRayMarch: _resize_fbos failed: {e}")
                return

        # Save Coin3D's current FBO
        prev_fbo = (ctypes.c_int * 1)(0)
        glGetIntegerv(0x8CA6, prev_fbo)   # GL_FRAMEBUFFER_BINDING

        # Read projection matrix (already set by Coin3D)
        proj_data = (ctypes.c_float * 16)()
        glGetFloatv(0x0BA7, proj_data)    # GL_PROJECTION_MATRIX

        self._gbuf_fbo.bind()
        glViewport(0, 0, w, h)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self._prog_gbuf.use()

        # Fixed key light: normalize(1.0, 1.667, 1.105) ≈ 45° elevation
        self._prog_gbuf.set_3f("u_light_dir", 0.4472, 0.7454, 0.4943)

        if getattr(self, "_active_is_analytical", False):
            self._prog_gbuf.set_uniforms_from_ctx(self._active_frag_gbuf_uniforms)
        else:
            glActiveTexture(0x84C0)   # GL_TEXTURE0
            glBindTexture(GL_TEXTURE_3D, self._gl_tex.tex_id)
            self._set_gbuf_uniforms(self._prog_gbuf)

        self._prog_gbuf.draw_fullscreen_quad()

        # ── Pass 2: SSAO ──
        self._ssao_fbo.bind()
        glViewport(0, 0, w, h)
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
        self._prog_ssao.set_2f("u_noise_scale", w / 4.0, h / 4.0)
        self._prog_ssao.set_3fv("u_samples", 64, self._ssao_kernel_flat)
        self._prog_ssao.set_mat4("u_proj", list(proj_data))
        self._prog_ssao.draw_fullscreen_quad()

        # ── Pass 3: Blur ──
        self._blur_fbo.bind()
        glViewport(0, 0, w, h)
        glClear(GL_COLOR_BUFFER_BIT)
        self._prog_blur.use()
        glActiveTexture(0x84C0)
        glBindTexture(GL_TEXTURE_2D, self._ssao_fbo.color_texture(0))
        self._prog_blur.set_1i("u_ssao", 0)
        self._prog_blur.set_2f("u_texel_size", 1.0 / w, 1.0 / h)
        self._prog_blur.draw_fullscreen_quad()

        # ── Pass 4: Composition → restore main FBO ──
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
        glBindTexture(GL_TEXTURE_2D, self._gbuf_fbo.depth_texture())   # depth
        self._prog_comp.set_1i("u_depth", 2)
        self._prog_comp.draw_fullscreen_quad()

        # ── Cleanup: unbind textures, restore shader state ──
        for unit in (0x84C2, 0x84C1, 0x84C0):
            glActiveTexture(unit)
            glBindTexture(GL_TEXTURE_2D, 0)
        glBindTexture(GL_TEXTURE_3D, 0)
        glUseProgram(0)

    def _rebuild(self):
        """Bake dirty fields. Uses GPU compute if available, else CPU numpy."""
        import numpy as np

        _t0 = time.perf_counter()

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

        frustum = self._get_view_frustum_aabb(view) if view else None
        _t_frustum = time.perf_counter()

        fields_to_bake = []
        all_analytical = True
        analytical_data = []
        
        for label, f in visible:
            if label in self._dirty_fields or label not in self._baked_cache:
                fields_to_bake.append((label, f))
                
            # Attempt to gather analytical GLSL data
            gpu_result = self._try_gpu_compile(f)
            if gpu_result is None:
                all_analytical = False
            elif all_analytical:
                expr, ctx = gpu_result
                try:
                    bmin, bmax = f.bounding_box()
                except Exception:
                    bmin, bmax = FreeCAD.Vector(-10, -10, -10), FreeCAD.Vector(10, 10, 10)
                is_subtractive = self._get_is_subtractive(label)
                analytical_data.append({
                    "expr": expr,
                    "ctx": ctx,
                    "bbox_min": bmin,
                    "bbox_max": bmax,
                    "is_subtractive": is_subtractive
                })

        if all_analytical and analytical_data:
            from core.sdf.glsl_compiler import build_multi_raymarch_fragment_shader
            source = build_multi_raymarch_fragment_shader(analytical_data)
            self._pending_frag_gbuf_source = source
            all_uniforms = []
            for d in analytical_data:
                all_uniforms.extend(d["ctx"].uniforms)
            self._pending_frag_gbuf_uniforms = all_uniforms
            self._active_uniforms = {"n_fields": len(analytical_data)}
            self._dirty_fields.clear()
            
            # Compute combined bbox so Coin3D doesn't cull the quad
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
            return
        elif getattr(self, "_active_is_analytical", False):
            self._pending_restore_default_gbuf = True

        # Compute grid params and try GPU compile for dirty fields
        _n_baked = 0
        use_gpu = (self._gpu_supported is not False)  # True or None (not yet checked)

        for label, f in fields_to_bake:
            try:
                # Frustum clipping
                bbox_override = None
                if frustum is not None:
                    try:
                        f_min, f_max = f.bounding_box()
                        fr_min, fr_max = frustum
                        clip_min = FreeCAD.Vector(
                            max(f_min.x, fr_min.x), max(f_min.y, fr_min.y),
                            max(f_min.z, fr_min.z))
                        clip_max = FreeCAD.Vector(
                            min(f_max.x, fr_max.x), min(f_max.y, fr_max.y),
                            min(f_max.z, fr_max.z))
                        if (clip_min.x < clip_max.x and clip_min.y < clip_max.y
                                and clip_min.z < clip_max.z):
                            bbox_override = (clip_min, clip_max)
                        else:
                            self._baked_cache.pop(label, None)
                            continue
                    except Exception:
                        pass

                cs = (self._compute_cell_size_for_region(bbox_override, view)
                      if bbox_override else self._compute_cell_size(f, view))
                params = self._compute_grid_params(f, cs, bbox_override)

                # Try GPU path
                gpu_result = self._try_gpu_compile(f) if use_gpu else None

                if gpu_result is not None:
                    expr, ctx = gpu_result
                    from core.sdf.glsl_compiler import build_compute_shader
                    source = build_compute_shader(expr, ctx)
                    # Check if we can reuse compiled program
                    cached_gpu = self._gpu_programs.get(label)
                    if cached_gpu and cached_gpu["source"] == source:
                        program = cached_gpu["program"]
                        needs_compile = False
                    else:
                        from core.gl_compute import GLComputeShader
                        program = GLComputeShader()
                        needs_compile = True
                        self._gpu_programs[label] = {
                            "program": program, "source": source}

                    # Store metadata (no volume_bytes for GPU path)
                    params["gpu"] = True
                    params["glsl_source"] = source
                    params["ctx_uniforms"] = ctx.uniforms
                    self._baked_cache[label] = params
                    _n_baked += 1
                else:
                    # CPU fallback bake
                    from core.sdf.sdf_baker import bake_sdf_to_volume
                    baked = bake_sdf_to_volume(f, cs, bbox_override=bbox_override)
                    baked["cell_size"] = cs
                    baked["gpu"] = False
                    self._baked_cache[label] = baked
                    _n_baked += 1

            except Exception as e:
                dm_logger.debug(f"SceneRayMarch: Bake failed for '{label}': {e}")
                self._baked_cache.pop(label, None)

        self._dirty_fields.clear()
        _t_bake = time.perf_counter()

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

        # Atlas dimensions
        new_max_nx = max(b["nx"] for _, b in baked_list) + 1
        new_max_ny = max(b["ny"] for _, b in baked_list) + 1
        new_total_nz = sum(b["nz"] + 1 for _, b in baked_list)

        dims_changed = (
            new_max_nx   != getattr(self, "_atlas_nx", 0) or
            new_max_ny   != getattr(self, "_atlas_ny", 0) or
            new_total_nz != getattr(self, "_atlas_nz", 0) or
            n_fields     != getattr(self, "_atlas_n",  0)
        )

        # Compute Z-offsets
        z_offsets = []
        z_cursor = 0
        for _, b in baked_list:
            z_offsets.append(z_cursor)
            z_cursor += b["nz"] + 1

        # Check if any field uses GPU
        any_gpu = any(b.get("gpu", False) for _, b in baked_list)
        all_gpu = all(b.get("gpu", False) for _, b in baked_list)

        if any_gpu:
            # GPU path: allocate texture, queue compute dispatches
            if dims_changed:
                self._gl_tex.allocate(new_max_nx, new_max_ny, new_total_nz)
                self._atlas_nx = new_max_nx
                self._atlas_ny = new_max_ny
                self._atlas_nz = new_total_nz
                self._atlas_n  = n_fields

            # Queue GPU dispatches
            # If dims changed, ALL fields must be re-dispatched (texture reallocated)
            # If dims unchanged, only newly baked fields need dispatch
            for fi, (label, b) in enumerate(baked_list):
                if not b.get("gpu", False):
                    continue
                # Dispatch if this field was just baked, or dims changed (texture wiped)
                was_just_baked = (label in [l for l, _ in fields_to_bake])
                if was_just_baked or dims_changed:
                    cached_gpu = self._gpu_programs.get(label)
                    if cached_gpu:
                        self._pending_dispatches.append({
                            "label": label,
                            "program": cached_gpu["program"],
                            "needs_compile": not cached_gpu["program"].is_compiled,
                            "source": cached_gpu["source"],
                            "uniforms": b["ctx_uniforms"],
                            "grid_min": b["grid_min"],
                            "grid_step": b["grid_step"],
                            "grid_count": b["grid_count"],
                            "z_offset": z_offsets[fi],
                        })

            # For any CPU-baked fields in a mixed scenario, upload their data too
            if not all_gpu:
                combined = np.zeros((new_total_nz, new_max_ny, new_max_nx),
                                    dtype=np.float32)
                for fi, (label, b) in enumerate(baked_list):
                    if b.get("gpu", False):
                        continue  # GPU will fill this slice
                    nx1, ny1, nz1 = b["nx"] + 1, b["ny"] + 1, b["nz"] + 1
                    vol = np.frombuffer(b["volume_bytes"],
                                        dtype=np.float32).reshape(nz1, ny1, nx1)
                    combined[z_offsets[fi]:z_offsets[fi] + nz1, :ny1, :nx1] = vol
                # Upload CPU portions (GPU dispatch will overwrite GPU portions)
                self._gl_tex.upload_numpy(new_max_nx, new_max_ny,
                                          new_total_nz, combined)

        else:
            # Pure CPU path
            combined = np.zeros((new_total_nz, new_max_ny, new_max_nx),
                                dtype=np.float32)
            for fi, (label, b) in enumerate(baked_list):
                nx1, ny1, nz1 = b["nx"] + 1, b["ny"] + 1, b["nz"] + 1
                vol = np.frombuffer(b["volume_bytes"],
                                    dtype=np.float32).reshape(nz1, ny1, nx1)
                combined[z_offsets[fi]:z_offsets[fi] + nz1, :ny1, :nx1] = vol

            self._gl_tex.upload_numpy(new_max_nx, new_max_ny,
                                      new_total_nz, combined)
            if dims_changed:
                self._atlas_nx = new_max_nx
                self._atlas_ny = new_max_ny
                self._atlas_nz = new_total_nz
                self._atlas_n  = n_fields

        _t_atlas = time.perf_counter()

        # Store uniform data for _render_gl_callback to consume each frame
        fields_data = []
        for fi, (label, b) in enumerate(baked_list):
            mn, mx = b["bbox_min"], b["bbox_max"]
            fields_data.append({
                "nx": int(b["nx"]),
                "ny": int(b["ny"]),
                "nz": int(b["nz"]),
                "z_offset": int(z_offsets[fi]),
                "bbox_min": (mn.x, mn.y, mn.z),
                "bbox_max": (mx.x, mx.y, mx.z),
                "is_subtractive": 1 if self._get_is_subtractive(label) else 0,
            })
        self._active_uniforms = {
            "n_fields": n_fields,
            "z_total":  int(new_total_nz),
            "fields":   fields_data,
        }

        # Combined bbox proxy
        all_mn = [b["bbox_min"] for _, b in baked_list]
        all_mx = [b["bbox_max"] for _, b in baked_list]
        mn_all = FreeCAD.Vector(
            min(v.x for v in all_mn), min(v.y for v in all_mn),
            min(v.z for v in all_mn))
        mx_all = FreeCAD.Vector(
            max(v.x for v in all_mx), max(v.y for v in all_mx),
            max(v.z for v in all_mx))
        pts = [
            (mn_all.x, mn_all.y, mn_all.z), (mx_all.x, mn_all.y, mn_all.z),
            (mn_all.x, mx_all.y, mn_all.z), (mx_all.x, mx_all.y, mn_all.z),
            (mn_all.x, mn_all.y, mx_all.z), (mx_all.x, mn_all.y, mx_all.z),
            (mn_all.x, mx_all.y, mx_all.z), (mx_all.x, mx_all.y, mx_all.z),
        ]
        self._bbox_coords.point.setValues(0, 8, pts)
        self._coords.point.setValues(0, 8, pts)
        self._switch.whichChild = 0

        from core.dm_object import get_perf_profiler_enabled
        if get_perf_profiler_enabled():
            _t_end = time.perf_counter()
            bake_method = "GPU" if any_gpu else "CPU"
            FreeCAD.Console.PrintMessage(
                f"[rebuild] {n_fields} fields ({_n_baked} baked, {bake_method}) | "
                f"atlas={new_max_nx}x{new_max_ny}x{new_total_nz} | "
                f"frustum={1000*(_t_frustum-_t0):.1f}ms  "
                f"compile+bake={1000*(_t_bake-_t_frustum):.1f}ms  "
                f"atlas={1000*(_t_atlas-_t_bake):.1f}ms  "
                f"uniforms={1000*(_t_end-_t_atlas):.1f}ms  "
                f"TOTAL={1000*(_t_end-_t0):.1f}ms\n"
            )
