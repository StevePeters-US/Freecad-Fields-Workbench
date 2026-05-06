---
name: DM Architecture Overview
description: Quick reference for the Direct Modeling workbench architecture, class relationships, and data flow.
---

# DM Architecture Quick Reference

## Data Flow

```
User Input → Tool (DMBase subclass) → SdfField → GPU Ray March → Viewport
                                    ↓
                             DMObjectProxy (stores field on proxy)
                                    ↓
                             DMSceneRayMarchRenderer (bakes field to 3D texture, renders via GLSL)
```

## File Map

| Area | Key Files | Purpose |
|------|-----------|---------| 
| **Fields** | `core/sdf/sdf_field.py` | Abstract base for all SDF fields |
| | `core/sdf/sdf/*.py` | Concrete SDF primitives (box.py, sphere.py, cylinder.py) |
| | `core/sdf/sdf_composer.py` | Boolean field composition (union/intersection/subtraction) |
| **Rendering** | `core/dm_scene_ray_march_renderer.py` | Singleton GPU ray march compositor |
| | `core/sdf/sdf_baker.py` | CPU field → 3D voxel grid |
| | `core/gl_texture3d.py` | OpenGL 3D texture management |
| **Meshing** | `core/dm_mesher.py` | Marching cubes + surface nets (for SDF-to-Shape export) |
| **Document** | `core/dm_object.py` | `DMObjectProxy` (data), `DMViewProvider` (rendering), factory |
| **Tools** | `tools/dm_base.py` | `DMBase` (event dispatch), `NURBSPrimitiveCreator`, `DragTimerMixin` |
| | `tools/primitive_tool.py` | `PrimitiveCreatorBase` + Box/Sphere/Cylinder creators |
| | `tools/edit_tool.py` | Curve point editing (`EditTool`) + SDF corner editing (`SdfEditTool`) |
| | `tools/curve_tool.py` | Curve creation |
| | `tools/work_plane_tool.py` | Workplane creation with face snapping |
| **Commands** | `commands/cmd_*.py` | FreeCAD command wrappers (menu/toolbar entries) |
| **UI** | `InitGui.py` | Workbench registration, toolbar/menu setup |
| **Input** | `core/input_manager.py` | Qt event filter singleton, coordinate tracking, tool dispatch |

## SDF Pipeline Detail

1. **Tool creates field**: e.g., `BoxCreator._make_field()` → `SdfBoxField(center, size, placement)`
2. **Preview loop**: Tool sets `proxy.SdfField = field`, calls `DMSceneRayMarchRenderer.update_field()`
3. **Baking**: `bake_sdf_to_volume(field, cell_size)` evaluates field on a 3D grid → float32 volume
4. **GPU upload**: Volume packed as RGBA8 → `GLTexture3D` uploads to GPU
5. **Ray march shader**: Full-screen quad, sphere-tracing through 3D texture atlas, Phong shading, depth write

## Tool State Machine

Most tools follow a multi-state pattern managed by `DMBase` with `ToolState` enum:

- **IDLE (0)**: Tool activated, waiting for first interaction
- **ACTIVE (1)**: First click placed; mouse moves update preview (e.g., box footprint)
- **DRAGGING (2)**: Second click placed; mouse drags second parameter (e.g., height)
- **FINALIZED (3)**: Creation complete, transitioning to edit or new tool
- **EDIT_MODE (4)**: Editing an existing object's control points

## DM Settings (Stored in FreeCAD Preferences)

| Setting | Key | Default | Used By |
|---------|-----|---------|---------| 
| Ray March Cell Size | `RayMarchCellSize` | 2.0 mm | `DMSceneRayMarchRenderer._rebuild()` |
| Max Bounds | `MaxBounds` | 10000.0 mm | `SdfField.bounding_box()` fallback |
| Picking Radius | `PickingRadius` | 5.0 mm | Curve close detection |
| Line Width | `LineWidth` | 3.0 | All DM objects |
| Point Size | `PointSize` | 6.0 | All DM objects |
| Perf Profiler | `EnablePerfProfiler` | false | `MeshTimer.summary()` |
