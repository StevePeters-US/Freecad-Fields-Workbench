---
name: DM SSAO Multi-Pass Renderer
description: Reference for the SimulatorGL-derived SSAO upgrade to dm_scene_ray_march_renderer.py. Covers GLProgram/GLFramebuffer ctypes helpers, 4-pass FBO pipeline inside a SoCallback, G-buffer MRT layout, SSAO kernel math, and uniform plumbing.
---

# DM SSAO Multi-Pass Renderer

This skill covers the 4-pass deferred SSAO renderer that replaces the single-pass
`SoShaderProgram` in `DMSceneRayMarchRenderer`. Adapted from FreeCAD 1.1 SimulatorGL.

---

## Pipeline Overview

All 4 passes run inside a single `SoCallback` (`_render_gl_callback`):

```
Pass 1 — G-buffer (ray march):
  Inputs:  u_sdf_vol (3D texture, unit 0), per-field bbox/grid uniforms
  Outputs: gbuf_fbo attachment 0 = Phong color (RGBA16F, alpha=1 if hit)
           gbuf_fbo attachment 1 = view-space position (RGBA16F, w=1 if hit)
           gbuf_fbo attachment 2 = view-space normal   (RGBA16F)
           gbuf_fbo depth        = gl_FragDepth (DEPTH24)

Pass 2 — SSAO:
  Inputs:  gbuf color-slot 1 (vs_pos), gbuf color-slot 2 (vs_norm), noise tex
  Outputs: ssao_fbo color-slot 0 = occlusion float (R16F, 1.0 = unoccluded)

Pass 3 — Blur:
  Inputs:  ssao_fbo color-slot 0
  Outputs: blur_fbo color-slot 0 = blurred occlusion (R16F)

Pass 4 — Composition:
  Inputs:  gbuf color-slot 0 (color), blur color-slot 0 (ao), gbuf depth texture
  Outputs: main FBO color (gamma-corrected) + gl_FragDepth (re-emitted from gbuf)
```

---

## G-buffer FBO Attachments

```python
self._gbuf_fbo.create(w, h, ['rgba16f', 'rgba16f', 'rgba16f', 'depth24'])
# slot 0: color_texture(0) → Phong color (rgb) + hit flag (a)
# slot 1: color_texture(1) → view-space position (xyz) + hit flag (w)
# slot 2: color_texture(2) → view-space normal (xyz)
# depth:  depth_texture()  → raw [0,1] depth for gl_FragDepth re-emit
```

```python
self._ssao_fbo.create(w, h, ['r16f'])
self._blur_fbo.create(w, h, ['r16f'])
```

---

## GLProgram API (core/gl_program.py)

```python
prog = GLProgram()
prog.compile(vert_src, frag_src)   # Must be called inside a GL context
prog.use()                          # glUseProgram
prog.set_1i(name, v)               # glUniform1i
prog.set_1f(name, v)               # glUniform1f
prog.set_2f(name, x, y)            # glUniform2f
prog.set_3f(name, x, y, z)         # glUniform3f
prog.set_3fv(name, count, flat_list)  # glUniform3fv — flat list [x0,y0,z0, ...]
prog.set_mat4(name, mat16)         # glUniformMatrix4fv — flat 16-float column-major
prog.draw_fullscreen_quad()         # glBegin(TRIANGLE_STRIP) at NDC [-1,1]x[-1,1]
prog.destroy()                      # glDeleteProgram
```

Reuses `_loader` from `core/gl_texture3d.py` — do NOT load a second copy of libGL.

---

## GLFramebuffer API (core/gl_framebuffer.py)

```python
fbo = GLFramebuffer()
fbo.create(w, h, ['rgba16f', 'rgba16f', 'rgba16f', 'depth24'])
fbo.bind()                          # glBindFramebuffer(GL_FRAMEBUFFER, self._fbo_id)
fbo.unbind()                        # glBindFramebuffer(GL_FRAMEBUFFER, 0)
fbo.color_texture(slot)            # GL texture id of N-th color attachment (0-indexed)
fbo.depth_texture()                 # GL texture id of depth attachment
fbo.resize(w, h)                    # destroy + recreate at new size
fbo.destroy()
```

Attachment format string → (internalFormat, format, type, is_depth):
- `'rgba16f'` → GL_RGBA16F, GL_RGBA, GL_FLOAT, False
- `'rgb16f'`  → GL_RGB16F,  GL_RGB,  GL_FLOAT, False
- `'r16f'`    → GL_R16F,    GL_RED,  GL_FLOAT, False
- `'depth24'` → GL_DEPTH_COMPONENT24, GL_DEPTH_COMPONENT, GL_UNSIGNED_INT, True

---

## Key GL Constants (hex)

```python
GL_FRAMEBUFFER          = 0x8D40
GL_FRAMEBUFFER_BINDING  = 0x8CA6
GL_VIEWPORT             = 0x0BA2
GL_PROJECTION_MATRIX    = 0x0BA7
GL_MODELVIEW_MATRIX     = 0x0BA6
GL_TEXTURE0             = 0x84C0
GL_TEXTURE1             = 0x84C1
GL_TEXTURE2             = 0x84C2
GL_TEXTURE_2D           = 0x0DE1
GL_TEXTURE_3D           = 0x806F
GL_COLOR_BUFFER_BIT     = 0x4000
GL_DEPTH_BUFFER_BIT     = 0x0100
GL_TRIANGLE_STRIP       = 0x0005
GL_COLOR_ATTACHMENT0    = 0x8CE0
GL_DEPTH_ATTACHMENT     = 0x8D00
GL_RGBA16F              = 0x881A
GL_R16F                 = 0x822D
GL_DEPTH_COMPONENT24    = 0x81A5
GL_DEPTH_COMPONENT      = 0x1902
GL_FLOAT                = 0x1406
GL_UNSIGNED_INT         = 0x1405
GL_REPEAT               = 0x2901
GL_NEAREST              = 0x2600
GL_RGB32F               = 0x8815
GL_RGB                  = 0x1907
```

---

## Saving / Restoring the Main FBO

Coin3D may or may not be rendering into FBO 0. Always save and restore:

```python
prev = (ctypes.c_int * 1)(0)
glGetIntegerv(0x8CA6, prev)           # GL_FRAMEBUFFER_BINDING
# ... all our passes ...
glBindFramebuffer(0x8D40, prev[0])    # GL_FRAMEBUFFER, restore
```

---

## Reading GL Matrices Inside a SoCallback

Coin3D has already loaded the camera matrices into the fixed-function pipeline by the
time the SoCallback fires. Read them directly — no Python matrix construction needed:

```python
proj_data = (ctypes.c_float * 16)()
mv_data   = (ctypes.c_float * 16)()
glGetFloatv(0x0BA7, proj_data)   # GL_PROJECTION_MATRIX (column-major)
glGetFloatv(0x0BA6, mv_data)     # GL_MODELVIEW_MATRIX
```

Pass `list(proj_data)` to `prog.set_mat4("u_proj", ...)`.

The G-buffer shader uses `gl_ModelViewProjectionMatrixInverse` (Coin3D compat built-in)
for ray unprojection — no explicit uniform needed there.

---

## `_active_uniforms` Dict Structure

`_rebuild()` stores this dict for the render callback to consume:

```python
self._active_uniforms = {
    "n_fields": int,
    "z_total":  int,
    "fields": [
        {
            "nx": int, "ny": int, "nz": int,
            "z_offset": int,
            "bbox_min": (x, y, z),    # floats
            "bbox_max": (x, y, z),
            "is_subtractive": 0 or 1,
        },
        ...  # one entry per visible field (max 8)
    ]
}
```

---

## SSAO Kernel Generation

```python
import random, math

kernel_flat = []
for i in range(64):
    # Random direction in upper hemisphere
    s = [random.uniform(-1.0, 1.0),
         random.uniform(-1.0, 1.0),
         random.uniform(0.0, 1.0)]
    length = math.sqrt(sum(x*x for x in s))
    if length < 1e-8: length = 1.0
    s = [x / length for x in s]
    # Accelerate samples toward origin
    scale = i / 64.0
    scale = 0.1 + scale * scale * 0.9   # lerp(0.1, 1.0, scale^2)
    kernel_flat.extend([x * scale for x in s])
# kernel_flat: 192 floats → set via prog.set_3fv("u_samples", 64, kernel_flat)
```

---

## Noise Texture

4×4 GL_RGB32F texture with random XY rotation vectors (Z=0).
Uploaded once in `_init_gl_programs()`. Bound to texture unit 2 for SSAO pass.
Wraps with GL_REPEAT — SSAO shader tiles it: `uv * u_noise_scale` where
`u_noise_scale = (viewport_w / 4.0, viewport_h / 4.0)`.

---

## MRT Output in GLSL 330 compat

```glsl
#version 330 compatibility
layout(location = 0) out vec4 out_color;   // gbuf attachment 0
layout(location = 1) out vec4 out_vspos;   // gbuf attachment 1
layout(location = 2) out vec4 out_vsnorm;  // gbuf attachment 2
// gl_FragDepth still available alongside layout-out declarations
```

Do NOT mix `gl_FragColor` with `layout(location=N) out` — use only the latter for MRT.

---

## Key Light Direction

Fixed normalized world-space direction. Set as a uniform, not computed in the shader.

```python
# normalize(1.0, 1.667, 1.105) ≈ 45° elevation, slight right offset
LIGHT_DIR = (0.4472, 0.7454, 0.4943)
prog.set_3f("u_light_dir", *LIGHT_DIR)
```

---

## Viewport / FBO Resize

FBOs are sized to the current viewport. Detect changes in `_render_gl_callback`:

```python
vp = (ctypes.c_int * 4)(0, 0, 0, 0)
glGetIntegerv(0x0BA2, vp)   # GL_VIEWPORT
w, h = vp[2], vp[3]
if (w, h) != self._vp_size:
    self._resize_fbos(w, h)
    self._vp_size = (w, h)
```

`_resize_fbos(w, h)` calls `fbo.create(w, h, specs)` on each FBO (which destroys and
recreates). Do NOT call `fbo.resize()` before FBOs are initialized — check `is None`.

---

## Depth Re-emit in Composition Pass

The G-buffer depth attachment (`GL_DEPTH_COMPONENT24`) is sampled as `sampler2D`.
When `GL_TEXTURE_COMPARE_MODE` is `GL_NONE` (the default), it returns the raw [0,1]
depth value in `.r`. Write directly to `gl_FragDepth` in the composition shader:

```glsl
gl_FragDepth = texture(u_depth, uv).r;
```

This makes Coin3D's depth buffer aware of our SDF surface positions, enabling correct
compositing with NURBS objects and the work plane.

---

## Scene Graph After Refactor

```
self._switch (SoSwitch, whichChild=0 when active)
└── self._root (SoSeparator, caching off)
    ├── self._bbox_switch (SoSwitch)
    │   └── self._bbox_sep (debug wireframe)
    └── self._shader_sep (SoSeparator, caching off)
        ├── self._gl_tex.callback_node  (SoCallback: upload 3D SDF atlas)
        ├── self._compute_cb_node       (SoCallback: GPU compute dispatch)
        ├── self._render_cb_node        (SoCallback: 4-pass SSAO render) ← NEW
        ├── self._coords (SoCoordinate3, 8 bbox-corner points, indices 0-7)
        └── SoIndexedFaceSet (8× degenerate [i,i,i,-1] for Coin3D bbox)
```

No `SoShaderProgram`, `SoVertexShader`, `SoFragmentShader`, or `SoShaderParameter`
nodes remain after the refactor.

---

## Files to Read Before Editing

1. `core/dm_scene_ray_march_renderer.py` — full file (renderer to modify)
2. `core/gl_texture3d.py:1-110` — GLFunctionLoader pattern to replicate in gl_program.py
3. `.agents/skills/dm_opengl33_shader/SKILL.md` — GLSL 330 compat patterns
4. `.agents/skills/dm_renderer_refactor/SKILL.md` — why `_active_uniforms` replaces `self._u`
