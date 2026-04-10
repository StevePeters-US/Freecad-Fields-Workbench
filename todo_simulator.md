# Direct Modeling Workbench — SimulatorGL SSAO Renderer Task List

> Tasks are ordered by dependency. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

FreeCAD 1.1's new SimulatorGL uses a 4-pass deferred rendering pipeline with
Screen-Space Ambient Occlusion (SSAO), lifted from its `FragShaderSSAO`,
`FragShaderSSAOBlur`, and `FragShaderSSAOLighting` shaders. This gives CNC-simulated
workpieces strong visual depth cues and contact shadows.

Our SDF ray-march renderer currently uses a single-pass `SoShaderProgram` with
camera-as-light Phong shading and no ambient occlusion. Surfaces look flat because
all light comes from the viewer direction, and there is no secondary bounce or
occlusion darkening.

This task list replaces that single-pass system with the SimulatorGL-derived 4-pass
pipeline:
1. **G-buffer pass** — ray-march as before, but write Phong color, view-space
   position, and view-space normal to three FBO color attachments.
2. **SSAO pass** — sample a 64-point hemisphere kernel around each fragment's
   view-space position, count occluded samples, output occlusion [0,1].
3. **Blur pass** — 4×4 box filter on the raw SSAO to remove noise.
4. **Composition pass** — multiply G-buffer color by blurred AO, apply gamma
   correction (pow 1/2.2), re-emit depth from the G-buffer depth attachment.

All four passes run inside a single `SoCallback` that saves and restores Coin3D's
FBO binding. The existing `SoShaderProgram` + `SoShaderParameter` nodes are removed;
uniforms are set via ctypes `glUniform*` calls. Two new helper modules (`gl_program.py`,
`gl_framebuffer.py`) follow the `GLFunctionLoader` pattern from `gl_texture3d.py`.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `DMSceneRayMarchRenderer` | `core/dm_scene_ray_march_renderer.py:21` | Singleton renderer to modify |
| `_rebuild()` | `core/dm_scene_ray_march_renderer.py:829` | Bakes fields; currently updates `self._u` SoShaderParameter nodes |
| `_setup_nodes()` | `core/dm_scene_ray_march_renderer.py:156` | Builds Coin3D scene graph; contains `SoShaderProgram` to remove |
| `_detach()` | `core/dm_scene_ray_march_renderer.py:76` | Cleanup; needs new GL resource destruction |
| `GLTexture3D` | `core/gl_texture3d.py:112` | 3D texture wrapper; reference for ctypes pattern |
| `_loader` | `core/gl_texture3d.py:109` | Global `GLFunctionLoader` — reuse in `gl_program.py` |
| `self._u` dict | `core/dm_scene_ray_march_renderer.py:401` | SoShaderParameter nodes — replaced by `_active_uniforms` |
| `self._coords` | `core/dm_scene_ray_march_renderer.py:445` | Coin3D coord node; points 8–11 (quad) removed, 0–7 (bbox) kept |

---

## Tier 1 — Infrastructure (Do First)

These two helpers are used by all subsequent tasks. Complete both before starting Tier 2.

### SIM-001: Create `core/gl_program.py` — GLProgram class

**File:** `core/gl_program.py` — new file

**What:** ctypes wrapper for a GLSL vertex+fragment shader program. Reuses `_loader`
from `gl_texture3d.py` to avoid loading a second copy of libGL.

**Implementation:**

```python
"""core/gl_program.py

Thin ctypes wrapper for OpenGL shader programs.
Reuses the GLFunctionLoader from gl_texture3d.py.
"""
import ctypes
from core.gl_texture3d import _loader

GL_VERTEX_SHADER   = 0x8B31
GL_FRAGMENT_SHADER = 0x8B30
GL_COMPILE_STATUS  = 0x8B81
GL_LINK_STATUS     = 0x8B82
GL_INFO_LOG_LENGTH = 0x8B84


class GLProgram:
    """Manages a compiled GLSL vertex+fragment shader program."""

    def __init__(self):
        self._prog_id = 0

    def compile(self, vert_src: str, frag_src: str):
        """Compile and link shaders. Must be called inside an active GL context."""
        glCreateShader     = _loader.get("glCreateShader",     [ctypes.c_uint], ctypes.c_uint)
        glShaderSource     = _loader.get("glShaderSource",     [ctypes.c_uint, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_int)], None)
        glCompileShader    = _loader.get("glCompileShader",    [ctypes.c_uint], None)
        glGetShaderiv      = _loader.get("glGetShaderiv",      [ctypes.c_uint, ctypes.c_uint,
                                ctypes.POINTER(ctypes.c_int)], None)
        glGetShaderInfoLog = _loader.get("glGetShaderInfoLog", [ctypes.c_uint, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_int), ctypes.c_char_p], None)
        glCreateProgram    = _loader.get("glCreateProgram",    [], ctypes.c_uint)
        glAttachShader     = _loader.get("glAttachShader",     [ctypes.c_uint, ctypes.c_uint], None)
        glLinkProgram      = _loader.get("glLinkProgram",      [ctypes.c_uint], None)
        glGetProgramiv     = _loader.get("glGetProgramiv",     [ctypes.c_uint, ctypes.c_uint,
                                ctypes.POINTER(ctypes.c_int)], None)
        glGetProgramInfoLog = _loader.get("glGetProgramInfoLog", [ctypes.c_uint, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_int), ctypes.c_char_p], None)
        glDeleteShader     = _loader.get("glDeleteShader",     [ctypes.c_uint], None)

        def _compile(shader_type, src):
            sid = glCreateShader(shader_type)
            src_bytes = src.encode('utf-8')
            src_p = ctypes.c_char_p(src_bytes)
            glShaderSource(sid, 1, ctypes.byref(src_p), None)
            glCompileShader(sid)
            status = ctypes.c_int(0)
            glGetShaderiv(sid, GL_COMPILE_STATUS, ctypes.byref(status))
            if not status.value:
                buf_len = ctypes.c_int(0)
                glGetShaderiv(sid, GL_INFO_LOG_LENGTH, ctypes.byref(buf_len))
                buf = ctypes.create_string_buffer(max(buf_len.value, 256))
                glGetShaderInfoLog(sid, buf_len.value, None, buf)
                raise RuntimeError(f"Shader compile error:\n{buf.value.decode(errors='replace')}")
            return sid

        if self._prog_id:
            self.destroy()

        vsid = _compile(GL_VERTEX_SHADER,   vert_src)
        fsid = _compile(GL_FRAGMENT_SHADER, frag_src)
        prog = glCreateProgram()
        glAttachShader(prog, vsid)
        glAttachShader(prog, fsid)
        glLinkProgram(prog)

        status = ctypes.c_int(0)
        glGetProgramiv(prog, GL_LINK_STATUS, ctypes.byref(status))
        if not status.value:
            buf_len = ctypes.c_int(0)
            glGetProgramiv(prog, GL_INFO_LOG_LENGTH, ctypes.byref(buf_len))
            buf = ctypes.create_string_buffer(max(buf_len.value, 256))
            glGetProgramInfoLog(prog, buf_len.value, None, buf)
            raise RuntimeError(f"Program link error:\n{buf.value.decode(errors='replace')}")

        glDeleteShader(vsid)
        glDeleteShader(fsid)
        self._prog_id = prog

    def use(self):
        glUseProgram = _loader.get("glUseProgram", [ctypes.c_uint], None)
        glUseProgram(self._prog_id)

    def _loc(self, name: str) -> int:
        glGetUniformLocation = _loader.get("glGetUniformLocation",
            [ctypes.c_uint, ctypes.c_char_p], ctypes.c_int)
        return glGetUniformLocation(self._prog_id, name.encode('utf-8'))

    def set_1i(self, name: str, v: int):
        f = _loader.get("glUniform1i", [ctypes.c_int, ctypes.c_int], None)
        f(self._loc(name), int(v))

    def set_1f(self, name: str, v: float):
        f = _loader.get("glUniform1f", [ctypes.c_int, ctypes.c_float], None)
        f(self._loc(name), float(v))

    def set_2f(self, name: str, x: float, y: float):
        f = _loader.get("glUniform2f", [ctypes.c_int, ctypes.c_float, ctypes.c_float], None)
        f(self._loc(name), float(x), float(y))

    def set_3f(self, name: str, x: float, y: float, z: float):
        f = _loader.get("glUniform3f",
            [ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_float], None)
        f(self._loc(name), float(x), float(y), float(z))

    def set_3fv(self, name: str, count: int, flat_list):
        """Set a vec3 array. flat_list is [x0,y0,z0, x1,y1,z1, ...] (count*3 floats)."""
        f = _loader.get("glUniform3fv",
            [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_float)], None)
        arr = (ctypes.c_float * len(flat_list))(*flat_list)
        f(self._loc(name), count, arr)

    def set_mat4(self, name: str, mat16):
        """Set a mat4 from a flat 16-float column-major sequence."""
        f = _loader.get("glUniformMatrix4fv",
            [ctypes.c_int, ctypes.c_int, ctypes.c_bool,
             ctypes.POINTER(ctypes.c_float)], None)
        arr = (ctypes.c_float * 16)(*mat16)
        f(self._loc(name), 1, False, arr)

    def draw_fullscreen_quad(self):
        """Draw a CCW triangle strip covering NDC [-1,1]×[-1,1]."""
        glBegin   = _loader.get("glBegin",    [ctypes.c_uint], None)
        glEnd     = _loader.get("glEnd",      [], None)
        glVertex2f = _loader.get("glVertex2f", [ctypes.c_float, ctypes.c_float], None)
        glBegin(0x0008)      # GL_TRIANGLE_STRIP
        glVertex2f(-1.0, -1.0)
        glVertex2f( 1.0, -1.0)
        glVertex2f(-1.0,  1.0)
        glVertex2f( 1.0,  1.0)
        glEnd()

    def destroy(self):
        if self._prog_id:
            f = _loader.get("glDeleteProgram", [ctypes.c_uint], None)
            f(self._prog_id)
            self._prog_id = 0
```

---

### SIM-002: Create `core/gl_framebuffer.py` — GLFramebuffer class

**File:** `core/gl_framebuffer.py` — new file

**What:** ctypes wrapper for an OpenGL FBO with a variable list of texture attachments.
Color attachments occupy `GL_COLOR_ATTACHMENT0+N` slots in order; one `'depth24'` entry
maps to `GL_DEPTH_ATTACHMENT`.

**Implementation:**

```python
"""core/gl_framebuffer.py

OpenGL framebuffer object (FBO) manager via ctypes.
Reuses _loader from gl_texture3d.py.
"""
import ctypes
from core.gl_texture3d import _loader

GL_FRAMEBUFFER          = 0x8D40
GL_COLOR_ATTACHMENT0    = 0x8CE0
GL_DEPTH_ATTACHMENT     = 0x8D00
GL_TEXTURE_2D           = 0x0DE1
GL_RGBA16F              = 0x881A
GL_RGB16F               = 0x881B
GL_R16F                 = 0x822D
GL_DEPTH_COMPONENT24    = 0x81A5
GL_RGBA                 = 0x1908
GL_RGB                  = 0x1907
GL_RED                  = 0x1903
GL_DEPTH_COMPONENT      = 0x1902
GL_FLOAT                = 0x1406
GL_UNSIGNED_INT         = 0x1405
GL_NEAREST              = 0x2600
GL_CLAMP_TO_EDGE        = 0x812F
GL_TEXTURE_MIN_FILTER   = 0x2801
GL_TEXTURE_MAG_FILTER   = 0x2800
GL_TEXTURE_WRAP_S       = 0x2802
GL_TEXTURE_WRAP_T       = 0x2803

# format-string → (internalFormat, format, type, is_depth)
_FMT = {
    'rgba16f': (GL_RGBA16F,          GL_RGBA,           GL_FLOAT,       False),
    'rgb16f':  (GL_RGB16F,           GL_RGB,            GL_FLOAT,       False),
    'r16f':    (GL_R16F,             GL_RED,            GL_FLOAT,       False),
    'depth24': (GL_DEPTH_COMPONENT24, GL_DEPTH_COMPONENT, GL_UNSIGNED_INT, True),
}


class GLFramebuffer:
    """Owns one FBO and its texture attachments.

    attachment_specs: ordered list of format strings from _FMT, e.g.
        ['rgba16f', 'rgba16f', 'rgba16f', 'depth24']
    Color attachments are assigned GL_COLOR_ATTACHMENT0+N in list order.
    One 'depth24' entry maps to GL_DEPTH_ATTACHMENT.
    """

    def __init__(self):
        self._fbo_id   = 0
        self._textures = []   # list of (tex_id: int, is_depth: bool)
        self._specs    = []

    def create(self, width: int, height: int, attachment_specs):
        """Allocate FBO + textures. Destroys any existing allocation first."""
        self.destroy()
        self._specs = list(attachment_specs)

        glGenFramebuffers      = _loader.get("glGenFramebuffers",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        glBindFramebuffer      = _loader.get("glBindFramebuffer",
            [ctypes.c_uint, ctypes.c_uint], None)
        glGenTextures          = _loader.get("glGenTextures",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        glBindTexture          = _loader.get("glBindTexture",
            [ctypes.c_uint, ctypes.c_uint], None)
        glTexImage2D           = _loader.get("glTexImage2D",
            [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
             ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p], None)
        glTexParameteri        = _loader.get("glTexParameteri",
            [ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)
        glFramebufferTexture2D = _loader.get("glFramebufferTexture2D",
            [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)
        glDrawBuffers          = _loader.get("glDrawBuffers",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)

        fbo = ctypes.c_uint(0)
        glGenFramebuffers(1, ctypes.byref(fbo))
        self._fbo_id = fbo.value
        glBindFramebuffer(GL_FRAMEBUFFER, self._fbo_id)

        color_idx = 0
        color_attachments = []
        for spec in self._specs:
            ifmt, fmt, dtype, is_depth = _FMT[spec]
            tex = ctypes.c_uint(0)
            glGenTextures(1, ctypes.byref(tex))
            tid = tex.value
            glBindTexture(GL_TEXTURE_2D, tid)
            glTexImage2D(GL_TEXTURE_2D, 0, ifmt, width, height, 0, fmt, dtype, None)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

            if is_depth:
                attachment = GL_DEPTH_ATTACHMENT
            else:
                attachment = GL_COLOR_ATTACHMENT0 + color_idx
                color_attachments.append(attachment)
                color_idx += 1

            glFramebufferTexture2D(GL_FRAMEBUFFER, attachment, GL_TEXTURE_2D, tid, 0)
            self._textures.append((tid, is_depth))

        if color_attachments:
            arr = (ctypes.c_uint * len(color_attachments))(*color_attachments)
            glDrawBuffers(len(color_attachments), arr)

        glBindTexture(GL_TEXTURE_2D, 0)
        glBindFramebuffer(GL_FRAMEBUFFER, 0)

    def bind(self):
        f = _loader.get("glBindFramebuffer", [ctypes.c_uint, ctypes.c_uint], None)
        f(GL_FRAMEBUFFER, self._fbo_id)

    def unbind(self):
        f = _loader.get("glBindFramebuffer", [ctypes.c_uint, ctypes.c_uint], None)
        f(GL_FRAMEBUFFER, 0)

    def color_texture(self, color_slot: int = 0) -> int:
        """Return texture ID for the N-th color attachment (0-indexed)."""
        idx = 0
        for tid, is_depth in self._textures:
            if not is_depth:
                if idx == color_slot:
                    return tid
                idx += 1
        return 0

    def depth_texture(self) -> int:
        """Return texture ID for the depth attachment, or 0 if none."""
        for tid, is_depth in self._textures:
            if is_depth:
                return tid
        return 0

    def resize(self, width: int, height: int):
        """Destroy and recreate at a new resolution."""
        specs = list(self._specs)
        self.destroy()
        self.create(width, height, specs)

    def destroy(self):
        if not self._fbo_id:
            return
        glDeleteFramebuffers = _loader.get("glDeleteFramebuffers",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        glDeleteTextures = _loader.get("glDeleteTextures",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        fbo_arr = (ctypes.c_uint * 1)(self._fbo_id)
        glDeleteFramebuffers(1, fbo_arr)
        if self._textures:
            ids = [t for t, _ in self._textures]
            arr = (ctypes.c_uint * len(ids))(*ids)
            glDeleteTextures(len(ids), arr)
        self._fbo_id = 0
        self._textures = []
        self._specs = []
```

---

## Tier 2 — Shader Sources

Four GLSL shader strings stored as module-level constants in
`core/dm_scene_ray_march_renderer.py`. Add them immediately after the imports
(before the `DMSceneRayMarchRenderer` class definition).

### SIM-003: Add `_VERT_PASSTHROUGH` and `_FRAG_GBUF` shader strings

**File:** `core/dm_scene_ray_march_renderer.py` — insert after `from core import dm_logger`
(line 17), before the class definition (line 21)

**What:** Passthrough vertex shader (identical to the existing one) and G-buffer fragment
shader. The G-buffer shader is the current ray-march logic with three changes:
(1) three `layout(location=N) out vec4` outputs instead of `gl_FragColor`;
(2) view-space position and normal written to outputs 1 and 2;
(3) fixed world-space key light via `u_light_dir` uniform instead of `vec3 ld = vd`.

**Implementation:**

```python
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
    float h = cell * 2.0;
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
```

---

### SIM-004: Add `_FRAG_SSAO` shader string

**File:** `core/dm_scene_ray_march_renderer.py` — append immediately after `_FRAG_GBUF`
(after SIM-003 insertion point)

**What:** SSAO pass. Samples a 64-point hemisphere kernel in view space, counts
occluded points, outputs occlusion in [0,1]. Adapted from SimulatorGL `FragShaderSSAO`.

**Implementation:**

```python
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
```

---

### SIM-005: Add `_FRAG_BLUR` shader string

**File:** `core/dm_scene_ray_march_renderer.py` — append after `_FRAG_SSAO`

**What:** 4×4 box blur on the raw SSAO texture. Adapted from SimulatorGL
`FragShaderSSAOBlur`. Softens the hemisphere-kernel noise.

**Implementation:**

```python
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
```

---

### SIM-006: Add `_FRAG_COMP` shader string

**File:** `core/dm_scene_ray_march_renderer.py` — append after `_FRAG_BLUR`

**What:** Composition pass. Multiplies G-buffer color by blurred AO, applies gamma
correction, and re-emits the G-buffer depth to `gl_FragDepth` so Coin3D correctly
composites SDF objects with NURBS geometry and the work plane.

**Implementation:**

```python
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
```

---

## Tier 3 — Renderer State

Modify `DMSceneRayMarchRenderer` to hold the new multi-pass state and update its
lifecycle methods.

### SIM-007: Add multi-pass instance variables to `__init__` and cleanup to `_detach()`

**File:** `core/dm_scene_ray_march_renderer.py`

**Part A — `__init__` additions** (line 37, inside `__init__`, after line 46
`self._dirty_fields = set()`):

```python
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
```

**Part B — `_detach()` additions** (line 76, inside `_detach()`, after the existing
`self._gl_tex.destroy()` call at line 91):

```python
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
```

Also add `import ctypes` at the top of `_detach()` or confirm it is imported at module
level. `ctypes` is already a stdlib import — add it to the top-level imports if missing.

---

### SIM-008: Replace SoShaderProgram nodes in `_setup_nodes()`

**File:** `core/dm_scene_ray_march_renderer.py:156` — `_setup_nodes()` method

**What:** Remove the `SoShaderProgram`, `SoVertexShader`, `SoFragmentShader`, all
`SoShaderParameter` nodes, and the quad vertices (points 8–11). Keep the bbox debug
switch, the `_gl_tex.callback_node`, and the compute dispatch callback. Add
`_render_cb_node`. Keep `self._coords` with only 8 bbox-corner points (indices 0–7)
and a degenerate face set for Coin3D bounding-box expansion.

Replace the entire `_setup_nodes` body with:

```python
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
```

**Depends on:** SIM-007

---

### SIM-009: Replace `self._u` uniform updates in `_rebuild()` with `_active_uniforms` dict

**File:** `core/dm_scene_ray_march_renderer.py:1030` — inside `_rebuild()`,
replace lines 1030–1050 (the `for fi in range(self.MAX_FIELDS):` loop + the two
`self._u["u_num_fields"]` and `self._u["u_z_total"]` lines).

**What:** Instead of writing to Coin3D `SoShaderParameter` nodes, store the per-field
data in `self._active_uniforms` for the render callback to read each frame.
Also update line 1068: `self._coords.point.setValues(0, 8, pts)` stays as-is
(bbox coords for Coin3D culling); remove the second call
`self._coords.point.setValues(8, 4, pts)` if present.

Replace lines 1030–1050 with:

```python
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
```

**Depends on:** SIM-007

---

## Tier 4 — GL Methods

Add four new methods to `DMSceneRayMarchRenderer`. Insert them just before `_rebuild()`
(line 829) so they are grouped with the GL helpers.

### SIM-010: Add `_init_gl_programs()` method

**File:** `core/dm_scene_ray_march_renderer.py` — insert before `_rebuild()` (line 829)

**What:** Compile all four GLSL programs, generate the SSAO hemisphere kernel, and
upload the 4×4 noise texture. Called lazily on the first render callback invocation.

**Implementation:**

```python
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
```

**Depends on:** SIM-001, SIM-003, SIM-004, SIM-005, SIM-006

---

### SIM-011: Add `_resize_fbos(w, h)` method

**File:** `core/dm_scene_ray_march_renderer.py` — insert after `_init_gl_programs()`
(after SIM-010 insertion point)

**What:** Create or recreate all three FBOs at the given viewport size. Safe to call
repeatedly — `GLFramebuffer.create()` destroys before recreating.

**Implementation:**

```python
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
```

**Depends on:** SIM-002, SIM-007

---

### SIM-012: Add `_set_gbuf_uniforms(prog)` method

**File:** `core/dm_scene_ray_march_renderer.py` — insert after `_resize_fbos()`
(after SIM-011 insertion point)

**What:** Set all uniforms for the G-buffer ray-march shader program using ctypes
`glUniform*` calls via `GLProgram`'s helpers. Reads from `self._active_uniforms`
(populated by `_rebuild()`). Also sets the fixed world-space key light direction.

**Implementation:**

```python
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
```

**Depends on:** SIM-001, SIM-007, SIM-009

---

### SIM-013: Add `_render_gl_callback()` method — 4-pass render loop

**File:** `core/dm_scene_ray_march_renderer.py` — insert after `_set_gbuf_uniforms()`
(after SIM-012 insertion point)

**What:** Main render callback. Runs all 4 SSAO passes inside Coin3D's GL context,
saving and restoring the bound FBO. Lazily initialises programs and FBOs on first call.

**Implementation:**

```python
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

        # ── Pass 1: G-buffer ray march ──
        self._gbuf_fbo.bind()
        glViewport(0, 0, w, h)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self._prog_gbuf.use()
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
```

**Depends on:** SIM-010, SIM-011, SIM-012

---

## Agent Skills

| Skill | Purpose |
|-------|---------|
| `dm_ssao_renderer` | FBO pipeline layout, GLProgram/GLFramebuffer API, SSAO kernel math, `_active_uniforms` dict structure, GL constant hex values |
| `dm_opengl33_shader` | GLSL 330 compat syntax, MRT layout declarations, depth write pattern |
| `dm_renderer_refactor` | Why `_active_uniforms` replaces `self._u`; RayMarchCellSize setting context |
