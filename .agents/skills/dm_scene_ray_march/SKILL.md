---
name: Scene-Level Baked SDF Ray March Renderer
description: Architecture reference for the scene-level ray march renderer that combines all F-Rep fields into a single baked 3D texture atlas and renders them with one full-screen quad. Covers the singleton lifecycle, field registry, combined baking, depth compositing, and how it replaces per-object DMRayMarchRenderer instances. Generic — works with any SDF, no per-primitive GLSL formulas.
---

# Scene-Level Baked SDF Ray March Renderer

The scene-level renderer replaces per-object `DMRayMarchRenderer` instances with a
single `DMSceneRayMarchRenderer` singleton. One full-screen quad renders ALL F-Rep
fields combined. The SDF is evaluated via a **baked 3D texture** — every field's
`evaluate_grid()` is called on the CPU, the results are composed into a single scalar
grid, baked to a uint16 atlas, and uploaded as one texture. This is generic: any
`FRepField` subclass works automatically, no per-primitive GLSL formulas needed.

---

## Why Per-Object Renderers Fail

Each `DMRayMarchRenderer` creates its own full-screen quad covering the entire
viewport. With N F-Rep objects, N quads execute the fragment shader at every pixel.
Problems:

1. **Depth compositing** — N independent `gl_FragDepth` writes compete per pixel.
   Later quads overwrite earlier ones in scene graph order, not depth order.
2. **Overlapping SDFs** — No correct per-pixel union/intersection compositing.
3. **Performance** — N full-screen quads = N× the work.
4. **Work plane occlusion** — work plane (transparent pass) cannot depth-test
   against multiple independent opaque quads correctly.

---

## Why Baked (Not Analytical GLSL)

| Aspect | Analytical GLSL | Baked 3D Texture |
|--------|----------------|-----------------|
| Works with any SDF | No — each primitive needs hand-written GLSL | Yes — uses `evaluate_grid()` |
| User-defined SDFs | Impossible without GLSL codegen | Works automatically |
| Noise/procedural fields | Requires GLSL translation | Works automatically |
| Shader complexity | Grows with tree depth | Fixed (texture sample) |
| Resolution setting | Not needed | Required (cell_size) |
| Surface quality | Mathematically exact | Limited by grid resolution |
| Sharp edges | Perfect | Rounded by grid spacing |

The baked approach is the **only** approach that scales to arbitrary SDFs. Analytical
GLSL could be added later as an optional fast path for known primitives, but it must
never be the only path.

---

## Architecture

```
Viewer SceneGraph
├── ... (FreeCAD standard nodes, work plane, NURBS objects)
└── _switch (SoSwitch)
    └── _root (SoSeparator)
        ├── _bbox_sep         ← combined AABB of all fields
        └── _shader_sep       ← single quad, single baked texture
```

### Key Difference from Per-Object

| Aspect | Per-Object | Scene-Level |
|--------|-----------|-------------|
| Attachment | `vobj.RootNode.addChild()` | `view.getSceneGraph().addChild()` |
| Lifetime | One per ViewProvider | Singleton per viewport |
| SDF evaluation | One baked texture per object | One combined baked texture |
| Shader | Fixed per-object | Fixed (same shader, different texture) |
| Depth | N independent writes | One `gl_FragDepth` per pixel |
| Compositing | Scene graph order wins | Correct union at texture level |

---

## Combined Baking Strategy

When fields change, the renderer:

1. Collects all visible fields from the registry
2. Computes the **union bounding box** of all visible fields
3. Creates a uniform grid over that combined bbox at `cell_size` resolution
4. For each grid point, evaluates `min(field_1(p), field_2(p), ...)` — the union SDF
5. Bakes the result via `bake_sdf_to_atlas()` (existing uint16 pipeline)
6. Uploads the single texture + updates uniforms

For non-union compositions (intersection, subtraction), the `FRepField` tree already
handles this via `ComposerField.evaluate_grid()`. The scene renderer just unions all
top-level objects — each object's internal CSG tree is already baked into its field.

---

## Singleton Lifecycle

```python
sr = DMSceneRayMarchRenderer.get_instance()

# Register a field (triggers re-bake + attach)
sr.register_field("Box", box_field)

# Update a field (triggers re-bake)
sr.update_field("Box", new_box_field)

# Toggle visibility (triggers re-bake with filtered fields)
sr.set_field_visible("Box", False)

# Remove a field (triggers re-bake or hide if no fields left)
sr.unregister_field("Box")

# Destroy on workbench deactivation
DMSceneRayMarchRenderer.destroy()
```

### When Re-Bake Happens

- `register_field()` — new field added
- `update_field()` — field parameters changed
- `set_field_visible()` — field shown/hidden
- `unregister_field()` — field removed

---

## Integration with DMViewProvider

In `DMViewProvider.attach()`, when `RENDER_MODE_RAY_MARCH`:
- Do NOT create a `DMRayMarchRenderer`
- Store `self._scene_rm_label = vobj.Object.Label`
- The field isn't available yet at attach time

In `DMViewProvider.updateData()`, when the field becomes available:
- Call `sr.update_field(label, field)` (registers if new)

In `DMViewProvider.onChanged()` for `Visibility`:
- Call `sr.set_field_visible(label, vobj.Visibility)`

In `DMViewProvider.onDelete()`:
- Call `sr.unregister_field(label)`

---

## Uniform Upload

The renderer reuses the same uniform set as the per-object renderer:

| Uniform | Type | Description |
|---------|------|-------------|
| `u_sdf_tex` | `sampler2D` | Combined baked atlas |
| `u_nx`, `u_ny`, `u_nz` | `int` | Grid dimensions |
| `u_atz` | `int` | Atlas z-tiles per row |
| `u_atlas_w`, `u_atlas_h` | `float` | Atlas pixel dimensions |
| `u_bbox_min`, `u_bbox_max` | `vec3` | Combined bounding box |
| `u_max_dist` | `float` | SDF clamp distance |
| `u_debug_mode` | `int` | Debug visualization mode |

---

## Fragment Shader

The fragment shader is **identical** to the existing per-object shader from G-002.
No changes needed — it samples the baked texture, sphere-traces, shades, and writes
depth. The only difference is that the texture now contains the combined SDF of all
visible fields instead of a single object's SDF.

---

## Depth Compositing

Because there is only ONE full-screen quad writing ONE `gl_FragDepth` per pixel:
- The combined SDF naturally produces the correct nearest-surface hit
- Work plane depth testing works correctly (one opaque surface to test against)
- No z-fighting between objects

---

## Scene Graph Structure (Detail)

```
_switch (SoSwitch, whichChild=0 when fields exist, -1 when empty)
└── _root (SoSeparator)
    ├── _bbox_sep (SoSeparator)
    │   ├── SoMaterial (transparency=1.0)
    │   ├── SoPickStyle (UNPICKABLE)
    │   ├── _bbox_coords (SoCoordinate3, 8 corners)
    │   └── SoIndexedLineSet (12 edges)
    └── _shader_sep (SoSeparator)
        ├── SoMaterial (transparency=0.0)  ← force opaque pass
        ├── SoDepthBuffer (test=True, write=True)
        ├── SoComplexity (textureQuality=0.0)  ← GL_NEAREST
        ├── SoTexture2 (combined atlas)
        ├── SoShaderProgram (vertex + fragment)
        ├── SoShapeHints (UNKNOWN_ORDERING)
        ├── SoCoordinate3 (quad: -1..1)
        └── SoIndexedFaceSet (2 triangles)
```

---

## Files to Read Before Editing

1. `core/dm_ray_march_renderer.py` — the per-object renderer (pattern to follow)
2. `core/frep/sdf_baker.py` — `bake_sdf_to_atlas()` (reused for combined baking)
3. `core/dm_object.py` — `DMViewProvider.attach()` and `updateData()` (wiring)
4. `core/frep/frep_composer.py` — `UnionField` for combining fields
5. `.agents/skills/dm_coin3d_depth_ordering/SKILL.md` — depth buffer management
6. `.agents/skills/dm_ray_march_scene_graph/SKILL.md` — scene graph structure
