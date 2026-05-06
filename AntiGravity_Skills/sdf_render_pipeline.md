---
name: SDF Render Pipeline
description: Reference for the SDF preview rendering pipeline architecture, bottlenecks, and optimization targets.
---

# SDF Render Pipeline Quick Reference

## Pipeline Flow

```
User drag/zoom → Tool (primitive_tool.py)
    ↓
_schedule_update() throttle [25ms]           ← BOTTLENECK: drops new fields
    ↓
_apply_preview_field(field)
    ├─ proxy.SdfField = field
    ├─ obj.touch() + doc.recompute()         ← BOTTLENECK: unnecessary FreeCAD cycle
    ↓
DMObject.execute() → fp.Shape = Part.Shape() (no-op for sdf)
    ↓
updateData() → SdfRendererStrategy.update()
    ↓
DMSceneRayMarchRenderer.update_field(label, field)
    ├─ _dirty_fields.add(label)
    ├─ _rebuild()
    │   ├─ bake_sdf_to_volume(field, cell_size)  ← CPU numpy grid eval
    │   │   ├─ np.meshgrid + field.evaluate_grid()
    │   │   └─ vol.astype(float32).tobytes()     ← BOTTLENECK: redundant copy
    │   ├─ Build combined atlas (always rebuilds full atlas)
    │   │   └─ combined.astype(float32).tobytes() ← BOTTLENECK: redundant copy
    │   └─ gl_tex.upload() → from_buffer_copy()   ← BOTTLENECK: extra copy
    └─ view.redraw()                              ← BOTTLENECK: called 2-3x per update

Camera zoom → SoNodeSensor._on_camera_changed()
    ├─ 100ms debounce (STALE imagery during zoom) ← BOTTLENECK
    └─ _on_zoom_settled() → marks ALL fields dirty → full _rebuild()
```

## Key Files

| File | Role |
|------|------|
| `core/dm_scene_ray_march_renderer.py` | Singleton GPU renderer, `_rebuild()`, camera sensor, GLSL shader |
| `core/sdf/sdf_baker.py` | `bake_sdf_to_volume()` — CPU grid eval to 3D float32 volume |
| `core/gl_texture3d.py` | OpenGL 3D texture management, `upload()`, `update_slice()` |
| `tools/dm_base.py` | `_schedule_update()` throttle, `DragTimerMixin` |
| `tools/primitive_tool.py` | `PrimitiveCreatorBase`, `update_preview()`, `_apply_preview_field()` |
| `core/dm_object.py` | `DMObjectProxy.execute()`, prefs getters |
| `core/dm_renderer.py` | `SdfRendererStrategy.update()` — bridge to scene renderer |

## Current Throttle/Debounce Values

| Target | Mechanism | Default |
|--------|-----------|---------|
| Interactive drag preview | `_schedule_update()` QTimer | 25ms |
| Camera zoom rebuild | SoNodeSensor + QTimer debounce | 100ms |
| Drag polling timer | DragTimerMixin QTimer | 16ms |

## SDF Baking

`bake_sdf_to_volume(field, cell_size, bbox_override=None)` in `sdf_baker.py`:
- Pads bounding box by `cell_size`
- Creates meshgrid: `(nx+1) * (ny+1) * (nz+1)` points
- Single vectorized `field.evaluate_grid(pts)` call
- Outputs float32 volume as bytes, packed as RGBA8 for GPU

## GPU Shader

Fragment shader in `dm_scene_ray_march_renderer.py`:
- Full-screen quad, ray march through baked 3D texture atlas
- 512 iteration sphere tracing with adaptive step
- Per-field AABB intersection test (early discard)
- Manual trilinear interpolation (NEAREST filtering, shader does interp)
- Tetrahedron normal via finite differences, Phong shading, depth write

## Baked Cache

```python
_baked_cache = {}      # label -> baked dict {volume_bytes, nx, ny, nz, bbox_min, bbox_max}
_dirty_fields = set()  # labels needing rebake
```

Only dirty fields are rebaked. Camera zoom marks ALL fields dirty.
The combined atlas is always rebuilt from all cached bakes (even unchanged ones are re-copied).
