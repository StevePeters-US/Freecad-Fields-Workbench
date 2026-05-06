---
name: DM Ray March Scene Graph
description: Reference for the Coin3D scene graph structure of the GPU ray march renderer. Required reading before modifying dm_ray_march_renderer.py.
---

# DM Ray March Scene Graph

The GPU ray march renderer uses a full-screen quad with custom GLSL shaders to
perform sphere tracing against a baked SDF texture atlas. The Coin3D scene graph
must be carefully structured to avoid shader leakage and clipping bugs.

---

## Critical Rule: Shader Scope Isolation

The `SoShaderProgram` node affects **all renderable children** that follow it in
the same `SoSeparator`. If a bounding-box proxy or any other helper geometry
shares the same parent separator as the shader, it will be rendered through the
ray march shaders — producing garbage output.

**Always** group the shader, texture, and quad geometry inside their own
`SoSeparator`. Place non-shader geometry (bbox proxy) outside this group.

---

## Correct Scene Graph Structure

```
_switch (SoSwitch)                          — visibility toggle
└── root (SoSeparator)
    ├── _bbox_sep (SoSeparator)             — bounding-box proxy (NO shader)
    │   ├── SoMaterial (transparency=1.0)   — invisible to eye, visible to BBox
    │   ├── SoPickStyle (UNPICKABLE)        — prevent mouse picking
    │   ├── _bbox_coords (SoCoordinate3)    — 8 world-space box corners
    │   └── SoIndexedLineSet (12 edges)     — forces correct near/far planes
    └── _shader_sep (SoSeparator)           — shader-scoped group
        ├── SoComplexity (textureQuality=0) — forces GL_NEAREST filtering
        ├── SoTexture2 (_tex)               — SDF atlas (LUMINANCE_ALPHA)
        ├── SoShaderProgram                 — vertex + fragment shaders
        ├── SoShapeHints (UNKNOWN_ORDERING) — disable backface culling
        ├── SoCoordinate3 (_coords)         — quad at (-1,-1)..(1,1)
        └── SoIndexedFaceSet                — 2 triangles filling viewport
```

### Why bbox proxy is FIRST

The bbox proxy must be traversed first so Coin3D computes the scene's bounding
box from the SDF's world-space extents. This drives the camera's near/far clip
planes to sane values, preventing numerical precision loss in
`gl_ModelViewProjectionMatrixInverse`.

### Why bbox proxy uses transparent SoMaterial (not SoDrawStyle.INVISIBLE)

Coin3D's `SoGetBoundingBoxAction` skips nodes with `SoDrawStyle.INVISIBLE`,
so an invisible proxy doesn't expand the scene bounds. A fully transparent
`SoMaterial` ensures the geometry contributes to the bounding box calculation
while remaining visually invisible.

---

## Texture Atlas Format

| Property | Value |
|:---------|:------|
| Format | LUMINANCE_ALPHA (2 bytes/pixel) |
| Channel 0 (L→R,G,B) | uint16 high byte |
| Channel 1 (A) | uint16 low byte |
| Filtering | GL_NEAREST (via SoComplexity) |
| Wrapping | CLAMP (both axes) |
| Tile layout | `atz` columns × `aty` rows of `(nx+1)×(ny+1)` tiles |

### Reconstruction in GLSL

```glsl
vec4 t = texture2D(u_sdf_tex, vec2(u, v));
float normalized = (t.r * 65280.0 + t.a * 255.0) / 65535.0;
float sdf_value = (normalized * 2.0 - 1.0) * u_max_dist;
```

---

## Files

| File | Purpose |
|:-----|:--------|
| `core/dm_ray_march_renderer.py` | Renderer class, scene graph, shaders |
| `core/sdf/sdf_baker.py` | CPU SDF → uint16 atlas baking |
| `tests/test_sdf_baker.py` | Unit tests for baker output |
