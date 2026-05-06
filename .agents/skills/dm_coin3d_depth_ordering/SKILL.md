---
name: Coin3D Depth Ordering for Custom Shaders
description: Reference for correct depth buffer management when using custom GLSL shaders with gl_FragDepth in Coin3D. Covers opaque vs transparent render passes, SoDepthBuffer, gl_DepthRange, and common depth ordering bugs. Required reading before modifying depth-related code in DMRayMarchRenderer.
---

# Coin3D Depth Ordering for Custom Shaders

When using custom fragment shaders that write `gl_FragDepth`, correct depth ordering
with the rest of the Coin3D scene requires understanding Coin3D's two-pass rendering
pipeline and how material classification affects depth buffer writes.

---

## Coin3D's Two-Pass Rendering Pipeline

Coin3D renders geometry in two passes:

| Pass | Material condition | Depth test | Depth write | Purpose |
|------|--------------------|------------|-------------|---------|
| **Opaque** | `transparency == 0.0` | Enabled | **Enabled** | Standard geometry |
| **Transparent** | `transparency > 0.0` | Enabled | **Disabled** | Blended overlays |

**Classification rule:** Coin3D checks the active `SoMaterial.transparency` value at
traversal time. If transparency > 0, the geometry node is deferred to the transparent
pass.

**Critical consequence:** If a shader separator has no `SoMaterial` node, it inherits
from the parent context. If the parent's material has *any* transparency > 0, the
full-screen quad is classified as transparent and rendered with depth writes disabled.
All `gl_FragDepth` values are computed but never stored in the depth buffer.

---

## The Bug Pattern

```
ViewProvider RootNode
├── SoMaterial (transparency = 0.3)   ← set by FreeCAD or user
├── ...standard nodes...
└── _switch (SoSwitch)
    └── root (SoSeparator)
        └── _shader_sep (SoSeparator)     ← NO SoMaterial
            ├── SoShaderProgram
            ├── SoCoordinate3 (quad)
            └── SoIndexedFaceSet           ← inherits transparency=0.3
                                           ← classified as TRANSPARENT
                                           ← depth writes DISABLED
```

**Symptoms:**
- Work plane grid renders on top of ray-marched SDF (should be behind)
- Multiple SDF objects overwrite each other in scene graph order (not per-pixel)
- NURBS objects (standard Coin3D rendering) depth-sort correctly with work plane

---

## The Fix: Explicit Opaque Material

Add an `SoMaterial` with `transparency=0.0` as the first child of the shader
separator:

```python
quad_mat = coin.SoMaterial()
quad_mat.transparency.setValue(0.0)
self._shader_sep.addChild(quad_mat)  # MUST be before geometry
```

This overrides any inherited transparency and forces opaque classification.

---

## SoDepthBuffer (Belt-and-Suspenders)

Coin3D 3.x+ provides `SoDepthBuffer` for explicit depth control:

```python
try:
    depth_buf = coin.SoDepthBuffer()
    depth_buf.test.setValue(True)    # glEnable(GL_DEPTH_TEST)
    depth_buf.write.setValue(True)   # glDepthMask(GL_TRUE)
    # depth_buf.function defaults to LEQUAL
    self._shader_sep.addChild(depth_buf)
except AttributeError:
    pass  # Not available in all pivy builds
```

Place it after `SoMaterial` and before geometry in the separator.

---

## gl_FragDepth Formula

### Standard Formula (assumes glDepthRange(0, 1))

```glsl
vec4 clip    = gl_ModelViewProjectionMatrix * vec4(hp, 1.0);
gl_FragDepth = (clip.z / clip.w + 1.0) * 0.5;
```

### Robust Formula (respects actual depth range)

```glsl
vec4 clip     = gl_ModelViewProjectionMatrix * vec4(hp, 1.0);
float ndc_z   = clip.z / clip.w;
gl_FragDepth  = gl_DepthRange.near
              + gl_DepthRange.diff * (ndc_z * 0.5 + 0.5);
```

`gl_DepthRange` is a GLSL 1.20 built-in struct:

```glsl
struct gl_DepthRangeParameters {
    float near;  // glDepthRange near value
    float far;   // glDepthRange far value
    float diff;  // far - near
};
uniform gl_DepthRangeParameters gl_DepthRange;
```

The robust formula produces identical results when `glDepthRange(0, 1)` (the default)
but correctly handles non-standard ranges that Coin3D or FreeCAD may configure.

---

## Key Points for `gl_FragDepth`

1. **Use `gl_ModelViewProjectionMatrix`** (pre-multiplied) instead of
   `gl_ProjectionMatrix * gl_ModelViewMatrix` — saves one matrix multiply per fragment.

2. **`hp` must be in the same space as `gl_ModelViewProjectionMatrix` expects** —
   if the vertex shader bypasses MVP (e.g. `gl_Position = vec4(xy, 0, 1)`), the
   fragment shader's `hp` from NDC unprojection via `gl_ModelViewProjectionMatrixInverse`
   is in object space. Transforming it back with `gl_ModelViewProjectionMatrix` recovers
   the correct clip-space position.

3. **`discard` prevents all writes** — for fragments that miss the SDF, `discard`
   prevents both color and depth writes. The depth buffer retains whatever was there
   before. This is correct behavior.

4. **Early-Z is disabled when writing `gl_FragDepth`** — ALL fragments of the
   full-screen quad execute the fragment shader, even those that will ultimately be
   depth-occluded. This is a performance cost but does not affect correctness.

---

## Scene Graph Structure (Correct)

```
self.root (SoSeparator)
├── _bbox_sep (SoSeparator)              ← bbox proxy (no shader)
│   ├── SoMaterial (transparency=1.0)    ← transparent so bbox is invisible
│   ├── SoPickStyle (UNPICKABLE)
│   ├── SoCoordinate3 (8 corners)
│   └── SoIndexedLineSet (12 edges)
└── _shader_sep (SoSeparator)            ← shader-scoped group
    ├── SoMaterial (transparency=0.0)    ← FORCES OPAQUE CLASSIFICATION
    ├── SoDepthBuffer (test=T, write=T)  ← optional, explicit depth control
    ├── SoComplexity (textureQuality=0)
    ├── SoTexture2
    ├── SoShaderProgram
    ├── SoShapeHints (UNKNOWN_ORDERING)
    ├── SoCoordinate3 (quad)
    └── SoIndexedFaceSet (2 triangles)
```

---

## Files to Read Before Editing

1. `core/dm_ray_march_renderer.py` — the renderer being fixed
2. `.agents/skills/dm_ray_march_scene_graph/SKILL.md` — scene graph structure
3. `.agents/skills/coin3d_shader_api/SKILL.md` — shader setup patterns
