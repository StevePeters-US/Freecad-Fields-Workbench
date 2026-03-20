---
name: Coin3D Shader API (pivy)
description: Reference for adding GLSL shaders to Coin3D scene graphs via pivy. Required reading before creating or modifying DMRayMarchRenderer or any code that uses SoShaderProgram.
---

# Coin3D Shader API (pivy)

Coin3D (accessed via `pivy.coin`) supports custom GLSL shaders through scene graph nodes.
This skill documents the exact API used by `DMRayMarchRenderer`.

---

## Import Guard

Always guard Coin3D usage:

```python
try:
    from pivy import coin
except ImportError:
    coin = None
```

Never call coin APIs outside `if coin:` blocks.

---

## Shader Program Setup

```python
prog = coin.SoShaderProgram()

vert = coin.SoVertexShader()
vert.sourceType.setValue(coin.SoShader.GLSL_PROGRAM)
vert.sourceProgram.setValue("void main() { gl_Position = ftransform(); }")

frag = coin.SoFragmentShader()
frag.sourceType.setValue(coin.SoShader.GLSL_PROGRAM)
frag.sourceProgram.setValue("void main() { gl_FragColor = vec4(1,0,0,1); }")

prog.shaderObject.set1Value(0, vert)
prog.shaderObject.set1Value(1, frag)

sep = coin.SoSeparator()
sep.addChild(prog)
# ... add proxy geometry after the shader node
```

**Order matters**: the `SoShaderProgram` node must appear in the separator *before* the
geometry nodes it should shade.

---

## Recompiling at Runtime

To update shader source after initial setup (e.g. SDF topology changed):

```python
frag.sourceProgram.setValue(new_frag_src)
vert.sourceProgram.setValue(new_vert_src)
```

Coin3D recompiles the GL program on the next render pass. This is the only required step —
no manual teardown needed.

---

## Uniform Parameters

Uniforms are attached to the `SoVertexShader` or `SoFragmentShader` node's `parameter` field
(not the `SoShaderProgram`). Attach to the shader stage that uses them:

```python
u = coin.SoUniformShaderParameter1f()
u.name.setValue("myFloat")
u.value.setValue(3.14)
frag.parameter.set1Value(0, u)
```

### Uniform Node Types

| GLSL type | Coin3D class | Value setter |
|-----------|-------------|--------------|
| `float` | `SoUniformShaderParameter1f` | `u.value.setValue(float_val)` |
| `vec2` | `SoUniformShaderParameter2f` | `u.value.setValue(coin.SbVec2f(x, y))` |
| `vec3` | `SoUniformShaderParameter3f` | `u.value.setValue(coin.SbVec3f(x, y, z))` |
| `vec4` | `SoUniformShaderParameter4f` | `u.value.setValue(coin.SbVec4f(x, y, z, w))` |
| `int` | `SoUniformShaderParameter1i` | `u.value.setValue(int_val)` |

### Updating Uniform Values Without Recompiling

Uniform *values* can be updated by mutating the existing node — no shader recompile:

```python
# At setup time: store the node reference
self._u_bbox_min = coin.SoUniformShaderParameter3f()
self._u_bbox_min.name.setValue("u_bbox_min")
self._u_bbox_min.value.setValue(coin.SbVec3f(0, 0, 0))
frag.parameter.set1Value(1, self._u_bbox_min)

# Later, when the bbox changes:
self._u_bbox_min.value.setValue(coin.SbVec3f(x, y, z))  # dirty-marked; updated next frame
```

---

## 3D Volume Texture (SoTexture3)

`SoTexture3` uploads a 3D texture to the GPU. Used by `DMRayMarchRenderer` to hold the
baked SDF volume.

```python
tex = coin.SoTexture3()

# Wrapping — clamp so out-of-bounds UVW returns edge values (not wrapped SDF)
tex.wrapR.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
tex.wrapS.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
tex.wrapT.setValue(coin.SoTexture3.CLAMP_TO_EDGE)

# Filtering — NEAREST is required when using packed float bytes.
# Linear interpolation of IEEE 754 packed bytes produces garbage values.
tex.minFilter.setValue(coin.SoTexture3.NEAREST)
tex.magFilter.setValue(coin.SoTexture3.NEAREST)

# Upload image data: (SbVec3s size, int num_channels, bytes data)
size = coin.SbVec3s(resolution, resolution, resolution)
tex.image.setValue(size, 4, rgba_bytes)   # nc=4 → GL_RGBA8 internally
```

**Placement in scene graph:** `SoTexture3` must appear *before* the proxy geometry nodes.
Coin3D binds it to texture unit 0 automatically. The GLSL sampler uniform must be set to `0`:

```python
u_vol = coin.SoUniformShaderParameter1i()
u_vol.name.setValue("u_sdf_vol")
u_vol.value.setValue(0)           # texture unit 0
frag.parameter.set1Value(0, u_vol)
```

**Updating the texture** (when the SDF changes — no shader recompile needed):

```python
tex.image.setValue(coin.SbVec3s(res, res, res), 4, new_rgba_bytes)
```

**Packed float encoding** (Python → GLSL round-trip):

```python
# Python: pack float32 values as RGBA bytes (little-endian: R=LSB, A=MSB)
rgba = sdf_vals.astype(np.float32).view(np.uint8).reshape(-1, 4)
```

```glsl
// GLSL 1.30+: unpack RGBA bytes back to float32
float unpack_float(vec4 c) {
    uvec4 b    = uvec4(round(c * 255.0));
    uint  bits = b.r | (b.g << 8u) | (b.b << 16u) | (b.a << 24u);
    return uintBitsToFloat(bits);
}
```

`uintBitsToFloat` requires `#version 130`. Use `texture(sampler3D, uvw)` (not `texture3D`)
for GLSL 1.30+.

---

## Proxy Geometry Pattern (World-Space AABB)

`DMRayMarchRenderer` uses `SoCoordinate3` + `SoIndexedFaceSet` instead of `SoCube + SoTransform`
so that vertex positions land in **world/SDF space** with no model matrix to unpack.

```python
coords = coin.SoCoordinate3()
# 8 AABB corners (set/update whenever the SDF changes):
corners = [
    (x0,y0,z0), (x1,y0,z0), (x1,y1,z0), (x0,y1,z0),
    (x0,y0,z1), (x1,y0,z1), (x1,y1,z1), (x0,y1,z1),
]
coords.point.setValues(0, 8, corners)

faces = coin.SoIndexedFaceSet()
face_idx = [
    0,1,2,3,-1,   # z-min
    4,7,6,5,-1,   # z-max
    0,4,5,1,-1,   # y-min
    1,5,6,2,-1,   # x-max
    2,6,7,3,-1,   # y-max
    0,3,7,4,-1,   # x-min
]
faces.coordIndex.setValues(0, len(face_idx), face_idx)
```

**Update on SDF change:**
```python
coords.point.setValues(0, 8, new_corners)
```

---

## Disabling Face Culling

The proxy box needs both faces visible (camera may be inside the AABB for close-up views):

```python
hints = coin.SoShapeHints()
hints.vertexOrdering.setValue(coin.SoShapeHints.UNKNOWN_ORDERING)
sep.addChild(hints)   # must appear before the geometry nodes
```

---

## Built-in GLSL Matrices in Coin3D

Coin3D runs on OpenGL compatibility profile, so GLSL 1.20 built-ins are available:

| GLSL built-in | Meaning |
|---------------|---------|
| `gl_ModelViewMatrix` | ModelView (= ViewMatrix × ModelMatrix) |
| `gl_ProjectionMatrix` | Projection |
| `gl_ModelViewMatrixInverse` | Inverse of ModelView |
| `gl_ModelViewProjectionMatrix` | MVP combined |
| `ftransform()` | Equivalent to `gl_ModelViewProjectionMatrix * gl_Vertex` |
| `gl_Vertex` | Current vertex in object/model space |
| `gl_LightSource[i]` | Light struct (position, diffuse, specular, etc.) |

**Recovering world-space camera position** (when proxy vertices are in world space with
no SoTransform, so ModelMatrix ≈ Identity):

```glsl
vec3 cam_world = (gl_ModelViewMatrixInverse * vec4(0.0, 0.0, 0.0, 1.0)).xyz;
```

**Detecting orthographic vs perspective**:

```glsl
bool is_ortho = (gl_ProjectionMatrix[3][3] > 0.5);
```

**Writing correct depth** (so ray-marched objects occlude other scene geometry):

```glsl
vec4 clip = gl_ProjectionMatrix * gl_ModelViewMatrix * vec4(hit_world_pos, 1.0);
gl_FragDepth = (clip.z / clip.w + 1.0) * 0.5;
```

---

## Scene Graph Integration with FreeCAD ViewObjects

`DMRayMarchRenderer` adds its separator to `vobj.RootNode`:

```python
vobj.RootNode.addChild(self._sep)
```

This is the same pattern used by `DMRenderer` (see `dm_renderer.py:22`). The separator is
a sibling of any FreeCAD-managed Part renderer nodes. Because the ray marching path sets
`fp.Shape = Part.Shape()` (empty), FreeCAD's Part renderer contributes nothing.

To hide the renderer, use `SoSwitch` instead of `SoSeparator`:

```python
sw = coin.SoSwitch()
sw.whichChild.setValue(0)      # 0 = first child visible
sw.whichChild.setValue(-1)     # -1 = none visible (hidden)
sw.addChild(inner_sep)
```

---

## Files to Read Before Editing

1. `core/dm_ray_march_renderer.py` — main consumer of this API
2. `core/dm_renderer.py` — reference for how Coin3D nodes are structured in this project
3. `core/dm_object.py:400` — `DMViewProvider.attach()` where the renderer is instantiated
4. `core/sdf/glsl_assembler.py` — produces the shader source strings consumed here
