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

        self._dirty_fields = set()    # labels with pending shader recompile
        self._compiled_fields = {}    # label -> (expr, ctx, bbox_min, bbox_max)
        self._bbox_coords  = None

        # Multi-pass SSAO renderer state
        self._active_uniforms  = {}   # populated by _rebuild(), consumed by render callback
        self._prog_gbuf  = None       # GLProgram: G-buffer ray march
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
        except Exception:
            pass

    def _detach(self):
        if not self._attached:
            return
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            sg = view.getSceneGraph()
            sg.removeChild(self._switch)
        except Exception:
            pass

        # Destroy multi-pass GL resources (best-effort; called outside GL context)
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
        self._attached = False



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
        self._compiled_fields.pop(label, None)
        self._dirty_fields.discard(label)

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
        try:
            if FreeCADGui.activeView():
                FreeCADGui.activeView().redraw()
        except Exception:
            pass
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

    def _get_is_subtractive(self, label):
        try:
            parts = label.split(".", 1)
            if len(parts) != 2:
                return False
            doc = FreeCAD.getDocument(parts[0])
            obj = doc.getObject(parts[1]) if doc else None
            return getattr(obj, "Group", "Group 1") == "Group 2"
        except Exception:
            return False

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

        _had_pending = False
        if getattr(self, "_pending_frag_gbuf_source", None):
            _had_pending = True
            try:
                self._prog_gbuf.compile(_VERT_PASSTHROUGH, self._pending_frag_gbuf_source)
                self._active_frag_gbuf_uniforms = self._pending_frag_gbuf_uniforms
                self._active_is_analytical = True
                self._pending_frag_gbuf_source = None
            except Exception as e:
                dm_logger.error(f"SceneRayMarch: analytical shader compile failed: {e}")
                self._pending_frag_gbuf_source = None

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

        # Resize FBOs if viewport changed — force a full expensive render on resize
        _force_full = False
        if (w, h) != self._vp_size:
            try:
                self._resize_fbos(w, h)
                self._vp_size = (w, h)
                _force_full = True
            except Exception as e:
                dm_logger.debug(f"SceneRayMarch: _resize_fbos failed: {e}")
                return

        # Save Coin3D's current FBO
        prev_fbo = (ctypes.c_int * 1)(0)
        glGetIntegerv(0x8CA6, prev_fbo)   # GL_FRAMEBUFFER_BINDING

        # Render framerate cap: skip passes 1-3 (expensive) but always run pass 4
        # (composite) so the SDF overlay stays visible every Coin3D frame.
        _now = time.perf_counter()
        _run_expensive = _force_full or _had_pending or (_now - self._last_render_t) >= (1.0 / 30.0)
        if _run_expensive:
            self._last_render_t = _now

            # Read projection matrix (only needed for SSAO pass)
            proj_data = (ctypes.c_float * 16)()
            glGetFloatv(0x0BA7, proj_data)    # GL_PROJECTION_MATRIX

            # ── Pass 1: G-buffer (ray march SDF) ──
            self._gbuf_fbo.bind()
            glViewport(0, 0, w, h)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            self._prog_gbuf.use()

            # Fixed key light: normalize(1.0, 1.667, 1.105) ≈ 45° elevation
            self._prog_gbuf.set_3f("u_light_dir", 0.4472, 0.7454, 0.4943)

            self._prog_gbuf.set_uniforms_from_ctx(
                getattr(self, "_active_frag_gbuf_uniforms", [])
            )

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

        # ── Pass 4: Composite cached FBO results → restore main FBO ──
        # Runs every Coin3D frame (cheap) so the SDF overlay never disappears.
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
        glUseProgram(0)

    def _rebuild(self):
        """Compile all registered SDF fields to an analytical GLSL fragment shader.
        All SDF fields must implement to_glsl(). Fields missing it are skipped with a warning.
        """
        visible = [(label, f) for label, (f, vis) in self._fields.items()
                   if vis and f is not None]

        if not visible:
            self._switch.whichChild = -1
            return

        from core.sdf.glsl_compiler import compile_field_to_glsl, build_multi_raymarch_fragment_shader

        analytical_data = []
        for label, f in visible:
            # Use cached compilation if not dirty
            if label in self._compiled_fields and label not in self._dirty_fields:
                expr, ctx, bmin, bmax = self._compiled_fields[label]
            else:
                try:
                    expr, ctx = compile_field_to_glsl(f, prefix=label)
                    try:
                        bmin, bmax = f.bounding_box()
                    except Exception:
                        bmin, bmax = FreeCAD.Vector(-10, -10, -10), FreeCAD.Vector(10, 10, 10)
                    # Cache it
                    self._compiled_fields[label] = (expr, ctx, bmin, bmax)
                except NotImplementedError:
                    dm_logger.warn(
                        f"SceneRayMarch: '{label}' ({type(f).__name__}) has no to_glsl() — skipped."
                    )
                    continue
                except Exception as e:
                    dm_logger.error(f"SceneRayMarch: GLSL compile failed for '{label}': {e}")
                    continue

            analytical_data.append({
                "expr": expr,
                "ctx": ctx,
                "bbox_min": bmin,
                "bbox_max": bmax,
                "is_subtractive": self._get_is_subtractive(label),
            })
        
        # Clear dirty flags after rebuild
        self._dirty_fields.clear()

        if not analytical_data:
            self._switch.whichChild = -1
            return

        source = build_multi_raymarch_fragment_shader(analytical_data)
        all_uniforms = []
        for d in analytical_data:
            all_uniforms.extend(d["ctx"].uniforms)

        # Only recompile when source structure changes; uniform values update every frame
        if source != getattr(self, "_last_analytical_source", None):
            self._pending_frag_gbuf_source = source
            self._last_analytical_source = source

        self._pending_frag_gbuf_uniforms = all_uniforms
        if getattr(self, "_active_is_analytical", False):
            self._active_frag_gbuf_uniforms = all_uniforms

        self._active_uniforms = {"n_fields": len(analytical_data)}

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

