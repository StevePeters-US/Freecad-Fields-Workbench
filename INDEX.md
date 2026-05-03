# Project Index — Fast Lookup

> [!TIP]
> **Read this file first** to find where code lives and which skills apply. This reduces tool calls for file discovery.

## 1. File Map

| File | Layer | Role |
|------|-------|------|
| `InitGui.py` | UI | Workbench registration, toolbar/menu setup |
| `core/input_manager.py` | Input | Qt event filter singleton; routes mouse/key to active tool |
| `core/view_projector.py` | Input | Ray-casting, geometry picking, workplane projection |
| `core/dm_object.py` | Document | `DMObjectProxy` (data) + `DMViewProvider` (rendering) |
| `core/dm_renderer.py` | Render | Coin3D control cage (spheres, lines) |
| `core/dm_scene_ray_march_renderer.py` | Render | Full-screen GPU ray march compositor (singleton) |
| `core/dm_mesher.py` | Meshing | SurfaceNets, Marching Cubes, Dual Contouring |
| `core/work_plane.py` | Logic | `WorkPlaneManager` (Coin3D grid visualization) |
| `core/sdf/sdf_field.py` | SDF | `SdfField` abstract base |
| `core/sdf/sdf_composer.py` | SDF | `ComposerField` (boolean tree) |
| `core/sdf/sdf/` | SDF | Concrete primitives (`box.py`, `sphere.py`, etc.) |
| `tools/dm_base.py` | Tools | `DMBase` + `NURBSPrimitiveCreator` base classes |
| `tools/primitive_tool.py` | Tools | `PrimitiveCreatorBase` + Box/Sphere/Cylinder |
| `tools/edit_tool.py` | Tools | `EditTool` + `SdfEditTool` |

## 2. Class → File Index

| Class | File |
|-------|------|
| `DMBase` | `tools/dm_base.py` |
| `DMInputManager` | `core/input_manager.py` |
| `DMObjectProxy` | `core/dm_object.py` |
| `SdfField` | `core/sdf/sdf_field.py` |
| `ComposerField` | `core/sdf/sdf_composer.py` |
| `DMSceneRayMarchRenderer` | `core/dm_scene_ray_march_renderer.py` |
| `ViewProjector` | `core/view_projector.py` |
| `WorkPlaneManager` | `core/work_plane.py` |
| `PrimitiveCreatorBase` | `tools/primitive_tool.py` |
| `EditTool` | `tools/edit_tool.py` |

## 3. Skill Dispatch Table

| If you are about to... | Read this skill |
|------------------------|-----------------|
| Edit input handling, hotkeys, event_cb | `dm_event_pipeline/SKILL.md` |
| Add a new SdfField subclass | `dm_sdf_primitive_pattern/SKILL.md` |
| Modify ray march renderer | `dm_opengl33_shader/SKILL.md` + `dm_ray_march_scene_graph/SKILL.md` |
| Add workplane projection | `dm_workplane_architecture/SKILL.md` |
| Edit boolean tree logic | `dm_boolean_architecture/SKILL.md` |
| Implement CPU hit testing | `dm_sdf_hit_test/SKILL.md` |
| Create/Edit primitive tool | `dm_primitive_tool/SKILL.md` |
| Work with OpenVDB grids, meshing, export | `dm_openvdb_migration/SKILL.md` |
| Implement VDB bake backend tasks | `dm_vdb_bake_backend/SKILL.md` |

## 4. Known Bugs

| File | Bug | Fix hint |
|------|-----|----------|
| `core/input_manager.py` | `get_projected_point()` returns undefined `result` | Rename return var |
| `tools/edit_tool.py` | `_hit_test_edge` refs `event_dict` (not a param) | Pass `event_dict` correctly |
| `core/dm_scene_ray_march_renderer.py` | label lookup `doc.getObject("DocName.ObjName")` always returns None | Split on `.` first |
| `core/input_manager.py` | E-key handler only dispatches `EditTool` for `curve` | Add `SdfEditTool` branch |

## 5. Development Patterns

- **All doc mutations deferred**: `QTimer.singleShot(0, fn)` (Shape assign, create/delete, recompute, dialog close)
- **No bare `except: pass`**: Every except must log via `dm_logger`
- **Hit-test uses perp distance from ray**, not ray-sphere intersection
- **Drag timer polls `DMInputManager._last_qt_pos`**, not `projector.get_mouse_world_pos()`
- **Viewport fallback = camera-facing plane only** — never "last used workplane"
- **Logging**: `from core import dm_logger` — never `print()` or stdlib `logging`
