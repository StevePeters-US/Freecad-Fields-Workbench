---
name: DM OpenGL 3.3 Shader Patterns
description: Reference for writing GLSL 3.30 shaders in Coin3D via pivy. Required reading before modifying ray march renderers or creating new GPU shaders. Covers GLSL 3.30 syntax, Coin3D compatibility profile workarounds, texture format migration, and SoTexture3 3D texture usage.
---

# DM OpenGL 3.3 Shader Patterns

This project targets **OpenGL 3.3 compatibility profile** as the minimum. All shaders
must use `#version 330 compatibility` to access both modern GLSL features and legacy
Coin3D built-ins (`gl_ModelViewProjectionMatrix`, `gl_LightSource`, etc.).

---

## GLSL 3.30 Compatibility Mode

Coin3D runs on the OpenGL **compatibility profile**, which means legacy built-in uniforms
and `gl_FragColor` are still available. Use `#version 330 compatibility` to combine
modern features with Coin3D integration.

### Version Directive

Every shader MUST begin with:

```glsl
#version 330 compatibility
```

This enables:
- `in` / `out` qualifiers (replace `varying` / `attribute`)
- `texture()` function (replaces `texture2D()`, `texture3D()`)
- `uint`, `uvec4`, bitwise operations
- `uintBitsToFloat()`, `intBitsToFloat()`
- `sampler3D` with `texture()` (no atlas tiling needed)
- Layout qualifiers for attributes
- `gl_FragDepth` (still available)

While retaining:
- `gl_ModelViewProjectionMatrix`, `gl_ModelViewMatrixInverse`
- `gl_ProjectionMatrix`, `gl_ModelViewMatrix`
- `gl_LightSource[i]`, `gl_DepthRange`
- `gl_Vertex` (in vertex shader)
- `gl_FragColor` (in fragment shader — preferred over declaring `out vec4`)

### Syntax Migration Table

| Old (GLSL 1.20) | New (GLSL 3.30 compat) |
|------------------|------------------------|
| `varying vec2 v_uv;` (vertex) | `out vec2 v_uv;` |
| `varying vec2 v_uv;` (fragment) | `in vec2 v_uv;` |
| `attribute vec3 position;` | `in vec3 position;` |
| `texture2D(sampler, uv)` | `texture(sampler, uv)` |
| `texture3D(sampler, uvw)` | `texture(sampler, uvw)` |
| No bitwise ops | `uint`, `uvec4`, `<<`, `>>`, `&`, `\|` |
| No `uintBitsToFloat` | `uintBitsToFloat(bits)` available |

---

## 3D Texture (SoTexture3) for SDF Volume

OpenGL 3.3 guarantees `GL_TEXTURE_3D` support. Use `SoTexture3` instead of the
2D atlas tiling hack:

```python
tex3d = coin.SoTexture3()
tex3d.wrapR.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
tex3d.wrapS.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
tex3d.wrapT.setValue(coin.SoTexture3.CLAMP_TO_EDGE)

# NEAREST filtering — shader does its own trilinear if needed
tex3d.minFilter.setValue(coin.SoTexture3.NEAREST)
tex3d.magFilter.setValue(coin.SoTexture3.NEAREST)

# Upload: (SbVec3s(w, h, d), num_channels, bytes_data)
size = coin.SbVec3s(nx + 1, ny + 1, nz + 1)
tex3d.images.setValue(size, 4, rgba_bytes)  # 4 channels = GL_RGBA8
```

### Float32 Packing via RGBA8

Pack `float32` SDF values as raw bytes in 4-channel RGBA texture:

```python
# Python: pack float32 → 4×uint8
sdf_f32 = sdf_values.astype(np.float32)
rgba_bytes = sdf_f32.view(np.uint8)  # reinterpret as bytes
# Shape: (nz+1, ny+1, nx+1, 4) for Coin3D row-major upload
```

```glsl
// GLSL 3.30: unpack RGBA bytes → float32
uniform sampler3D u_sdf_vol;

float sample_sdf(vec3 uvw) {
    vec4 c = texture(u_sdf_vol, uvw);
    uvec4 b = uvec4(round(c * 255.0));
    uint bits = b.r | (b.g << 8u) | (b.b << 16u) | (b.a << 24u);
    return uintBitsToFloat(bits);
}
```

This gives **exact float32 precision** — no quantization, no uint16 intermediate.

### Alternative: Hardware Trilinear with Normalized SDF

If you want GPU trilinear interpolation (cheaper than manual 8-tap):

```python
# Python: normalize SDF to [0, 1] for unorm16 texture
max_dist = cell_size * 8.0
normalized = (sdf_values / max_dist + 1.0) * 0.5
packed = (normalized * 65535.0).clip(0, 65535).astype(np.uint16)
# Upload as 1-channel uint16 (GL_R16)
hi = (packed >> 8).astype(np.uint8)
lo = (packed & 0xFF).astype(np.uint8)
two_chan = np.stack([hi, lo], axis=-1)
```

```glsl
// GLSL: reconstruct from 2-channel texture (LUMINANCE_ALPHA in compat)
float raw = texture(u_sdf_vol, uvw).r;  // hardware trilinear on normalized value
float sdf = (raw * 2.0 - 1.0) * u_max_dist;
```

**Recommended approach**: Use float32 packing with NEAREST filter + shader trilinear
for maximum precision. Use normalized + hardware trilinear only when performance is
critical and precision loss is acceptable.

---

## Coin3D Uniform Nodes (unchanged from GLSL 1.20)

The uniform attachment API is the same regardless of GLSL version:

```python
u = coin.SoShaderParameter1f()
u.name.setValue("u_my_uniform")
u.value.setValue(3.14)
frag.parameter.set1Value(index, u)
```

See `coin3d_shader_api` skill for full type table.

---

## SoTexture3 vs SoTexture2 — Coin3D Availability

`SoTexture3` is available in **Coin3D 4.0+** (all modern FreeCAD builds).
If pivy lacks `SoTexture3`, fall back to the 2D atlas approach:

```python
try:
    tex = coin.SoTexture3()
    HAS_TEX3D = True
except AttributeError:
    tex = coin.SoTexture2()
    HAS_TEX3D = False
```

---

## Depth Write Pattern (unchanged)

```glsl
vec4 clip = gl_ModelViewProjectionMatrix * vec4(hit_pos, 1.0);
float ndc_z = clip.z / clip.w;
gl_FragDepth = gl_DepthRange.near + gl_DepthRange.diff * (ndc_z * 0.5 + 0.5);
```

---

## Camera Ray Unprojection (unchanged)

```glsl
vec4 world_near = gl_ModelViewProjectionMatrixInverse * vec4(ndc.xy, -1.0, 1.0);
world_near /= world_near.w;
vec4 world_far = gl_ModelViewProjectionMatrixInverse * vec4(ndc.xy, 1.0, 1.0);
world_far /= world_far.w;
vec3 cam = (gl_ModelViewMatrixInverse * vec4(0,0,0,1)).xyz;
```

---

## Files to Read Before Editing

1. `core/dm_ray_march_renderer.py` — single-field ray march renderer (to be migrated)
2. `core/dm_scene_ray_march_renderer.py` — multi-field scene renderer (to be migrated)
3. `core/frep/sdf_baker.py` — SDF baking (to be migrated to float32)
4. `.agents/skills/coin3d_shader_api/SKILL.md` — Coin3D shader setup patterns
5. `.agents/skills/dm_ray_march_scene_graph/SKILL.md` — scene graph structure
