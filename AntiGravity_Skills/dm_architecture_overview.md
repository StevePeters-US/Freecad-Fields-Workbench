---
name: DM Architecture Overview
description: Quick reference for the Direct Modeling workbench architecture, class relationships, and data flow.
---

# DM Architecture Quick Reference

## Data Flow

```
User Input → Tool (DMBase subclass) → FRepField → Mesher → Coin3D Nodes → Viewport
                                    ↓
                             DMObjectProxy (stores field on proxy)
                                    ↓
                             DMViewProvider (pushes verts to Coin3D)
```

## File Map

| Area | Key Files | Purpose |
|------|-----------|---------|
| **Fields** | `core/frep/frep_field.py` | Abstract base for all SDF fields |
| | `core/frep/marching_cubes/*.py` | Concrete SDF SDF primitives |
| | `core/frep/frep_composer.py` | Boolean field composition (union/intersection/subtraction) |
| **Meshing** | `core/frep_mesher.py` | Marching cubes mesher + mesher factory |
| **Document** | `core/dm_object.py` | `DMObjectProxy` (data), `DMViewProvider` (rendering), factory |
| **Tools** | `tools/dm_base.py` | `DMBase` (event loop), `NURBSPrimitiveCreator` (NURBS objects) |
| | `tools/primitive_tool.py` | `PrimitiveCreatorBase` (FRep preview), Box/Sphere/Cylinder creators |
| | `tools/edit_tool.py` | Curve point/handle editing |
| | `tools/curve_tool.py` | Curve creation |
| | `tools/work_plane_tool.py` | Workplane creation with face snapping |
| **Commands** | `commands/cmd_*.py` | FreeCAD command wrappers (menu/toolbar entries) |
| **UI** | `InitGui.py` | Workbench registration, toolbar/menu setup |
| **Input** | `core/input_manager.py` | Global input event filter, context menus |

## FRep Pipeline Detail

1. **Tool creates field**: e.g., `BoxCreator._make_field()` → `MCBoxField(center, size, placement)`
2. **Preview loop**: Tool sets `proxy.FRepField = field`, triggers recompute
3. **Proxy.execute()**: Calls `mesher.mesh(field, resolution)` → returns `(verts, flat_idx)` numpy arrays
4. **ViewProvider.updateData()**: Detects `"Shape"` prop change → calls `_update_frep_mesh(verts, idx)`
5. **Coin3D rendering**: `SoCoordinate3` + `SoIndexedFaceSet` display the triangles directly

## Tool State Machine

Most tools follow a 3-state pattern managed by `DMBase`:

- **State 0**: Tool just activated, waiting for first interaction
- **State 1**: First click placed; mouse moves update preview (e.g., drag to set box footprint)
- **State 2**: Second click placed; mouse moves update second parameter (e.g., drag for height)
- **Finish**: Third click or Enter commits the object

## DM Settings (Stored in FreeCAD Preferences)

| Setting | Key | Default | Used By |
|---------|-----|---------|---------|
| FRep Storage Type | `FrepStorageType` | 0 (MC) | `get_active_mesher()` |
| Max Bounds | `MaxBounds` | 10000.0 mm | `FRepField.bounding_box()` fallback |
| Picking Radius | `PickingRadius` | 5.0 mm | Curve close detection |
| Line Width | `LineWidth` | 3.0 | All DM objects |
| Point Size | `PointSize` | 6.0 | All DM objects |
| Perf Profiler | `EnablePerfProfiler` | false | `MeshTimer.summary()` |
