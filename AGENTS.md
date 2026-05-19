# AGENTS.md

Shared guidance for all AI agents (Claude, Gemini, etc.) working in this repository.

> [!IMPORTANT]
> **READ [INDEX.md](file:///home/steve/Documents/Github/Freecad-Direct-Modeling/INDEX.md) BEFORE DOING ANY WORK.**
> It contains the mapping of files, classes, and skills that will save you many tool calls.

## Project Overview

FreeCAD workbench for direct/implicit modeling using **Signed Distance Fields (SDF)**. NURBS surfaces are evaluated as spatial discriminator functions `f(P)` (inside/outside), composed via min/max trees (union/cut/intersect), and rendered via GPU ray marching or extracted to mesh via marching cubes/dual contouring.

## Running Tests

Tests are ad-hoc scripts in `tests/`. Most mock FreeCAD at import time:

```bash
# From repo root
python tests/test_imports.py
python tests/test_sdf_boolean.py
python tests/test_sdf_baker.py
python tests/test_mesh_deduplication.py
```

No pytest.ini or test runner — run individual scripts directly. Many tests require `numpy`; some require `shapely` or `scipy`.

## Installation (Development)

Symlink repo into FreeCAD's Mod directory, then restart FreeCAD:
```bash
ln -s /path/to/Freecad-Direct-Modeling ~/.FreeCAD/Mod/DirectModeling
```

Dependencies: `pip install shapely scipy` (numpy bundled with FreeCAD).

## Architecture

```
InitGui.py → DirectModelingWorkbench
  ├── Activated() → DMInputManager.get_instance().initialize()
  ├── Deactivated() → DMInputManager.restore() + DMSceneRayMarchRenderer.destroy()
  └── Initialize() → registers commands, toolbars, menus

Event Pipeline (dual):
  Qt level:   DMInputManager.eventFilter()  (singleton, installs on 3D viewport)
  Coin3D level: DMBase.event_cb()           (per-tool SoEventCallback)
  ⚠ Same mouse events can fire on BOTH paths

Tool Lifecycle:
  FreeCAD command → tool.__init__() → tool installs Coin3D callback
  Tool sets DMBase.active_tool (class variable, not instance)
  Right-click / Enter → tool._do_finish() → cleanup
```

### Three Rendering Strategies

| Type | Shape | Coin3D overlay | Class |
|------|-------|----------------|-------|
| NURBS (curve/surface) | FreeCAD `Part.Shape` | Control cage spheres/lines | `DMViewProvider` + `dm_renderer.py` |
| SDF (SDF primitives) | null (empty shape) | GPU ray march quad | `DMSceneRayMarchRenderer` |
| WorkPlane | null | Transient grid lines | `WorkPlaneManager` |

### SDF Pipeline

```
SdfField subclass (core/sdf/sdf/*.py)
  → SdfComposerField (core/sdf/sdf_composer.py)  — min/max boolean tree
  → SdfBaker (core/sdf/sdf_baker.py)               — field → voxel grid
  → DMSceneRayMarchRenderer (core/dm_scene_ray_march_renderer.py) — GPU visualization
  → DMSdfExport / DMSdfSlice                         — mesh/slice export
```

### Key Classes

| File | Class | Role |
|------|-------|------|
| `core/input_manager.py` | `DMInputManager` | Qt event filter singleton; routes mouse/key to active tool |
| `core/view_projector.py` | `DMViewProjector` | Ray-casting, geometry picking, workplane projection |
| `core/dm_object.py` | `DMObjectProxy` / `DMViewProvider` | FreeCAD `Part::FeaturePython` wrapper + Coin3D rendering delegate |
| `core/dm_renderer.py` | `DMRenderer` | Coin3D control cage (spheres, lines) |
| `core/dm_scene_ray_march_renderer.py` | `DMSceneRayMarchRenderer` | Full-screen GPU ray march compositor |
| `core/dm_mesher.py` | `SurfaceNetsMesher` | Marching cubes + dual contouring isosurface extraction |
| `core/work_plane.py` | `WorkPlaneManager` | Coin3D grid visualization |
| `tools/dm_base.py` | `DMBase` / `NURBSPrimitiveCreator` | Base classes for all interactive tools |
| `core/sdf/sdf_field.py` | `SdfField` | Abstract base for all SDF fields |
| `core/sdf/sdf_composer.py` | `SdfComposerField` | Boolean composition tree |

## Code Conventions

### Style
- **Python 3.8+** (FreeCAD 0.21+ / 1.0 / 1.2)
- **PEP 8**: 4-space indent, 100-char soft limit
- `snake_case` functions, `PascalCase` classes, `_` prefix for private
- **Imports**: FreeCAD → PySide → project → stdlib
- **Docstrings**: Google-style on all public classes and functions

### Logging
Use `dm_logger` — never bare `print()` or stdlib `logging`:
```python
from core import dm_logger
dm_logger.debug("msg")   # FreeCAD.Console.PrintMessage
dm_logger.warn("msg")    # FreeCAD.Console.PrintWarning
dm_logger.error("msg")   # FreeCAD.Console.PrintError
```
Set `DEBUG_DM_CRASH=1` to also write to `~/.FreeCAD/DirectModeling.log`.

### Event Safety
All FreeCAD document mutations **must** be deferred:
```python
QTimer.singleShot(0, fn)  # required for: .Shape assignment, object create/delete, doc.recompute(), dialog close
```

### No Silent Exceptions
Every `except` block **must** log via `dm_logger`. Never use bare `pass` in an except block.

### Scope Control
Make only the specific change requested. Do not refactor adjacent code, add error handling, or "improve" style unless explicitly asked.

### Workplane Fallback Rule
The viewport-aligned plane (camera-facing) is the **only** acceptable fallback when a point is clicked in empty space. Never fall back to "last used workplane" — this creates hidden state bugs.

### Interactive Control Points
All 3D interactive handles use `SoSphere` nodes, never `SoMarkerSet`. Hit-test using perpendicular distance from ray, not ray-sphere intersection (orthographic `get_ray()` returns focal-plane origin, not camera position).

### Drag Pattern
`on_button1_down` → hit-test + start `QTimer(16ms)`. Timer polls `DMInputManager._last_qt_pos` and checks `QApplication.mouseButtons() & Qt.LeftButton` (self-terminates if button released). `on_button1_up` → stop timer. Do NOT use `projector.get_mouse_world_pos()` wrapper during drags — call `projector.get_mouse_world_pos(..., place_on_geometry=False)` directly.

## Agent Skills

Specialized implementation patterns are in `.agents/skills/`. Before implementing a feature, check if a relevant skill exists:

- `dm_event_pipeline/` — Qt + Coin3D dual input pipeline
- `dm_workplane_architecture/` — workplane first-click, projection
- `dm_sdf_primitive_pattern/` — SdfField subclass template
- `dm_tool_refactor_pattern/` — DragTimerMixin, state constants, tool lifecycle
- `dm_renderer_refactor/` — RayMarchCellSize, normal kernel
- `dm_ray_march_lod/` — adaptive cell size, camera sensor, dirty flags
- `dm_additive_subtractive/` — IsSubtractive property, shader uniform, Ctrl toggle
- `dm_opengl33_shader/` — GLSL 330, float32 packing, SoTexture3
- `dm_rclick_repeat/` — single-RMB close pattern
- `dm_openvdb_migration/` — VDB sparse tree patterns, `to_vdb()`, CSG, meshing, import guard
- `dm_vdb_bake_backend/` — VDB bake patterns: to_vdb() contract, vdb_grid_to_dense(), import guard
- `dm_sdf_octree/` — SdfOctreeCache interface, walk_leaves() contract, mesher integration pattern
- `dm_nurbs_sdf/` — FreeCAD BSpline API, to_glsl() contract for NURBS curve/surface fields

## Known Bugs

- `input_manager.py`: `get_projected_point()` returns undefined variable `result` — crashes on call
- `edit_tool.py:_hit_test_edge` — references `event_dict` (not a parameter); also missing `import Part`
- `input_manager.py` E-key handler — only dispatches `EditTool` for `curve` ShapeType; SdfEditTool unreachable via E-key
- `dm_scene_ray_march_renderer.py`: `_rebuild()` label lookup uses `doc.getObject("DocName.ObjName")` → always returns None → IsSubtractive always False. Fix: split label on `.` first.

## Active Task Files

- `todo_arch_refactor.md` — CQ/SP/MG/NS/PI phases: code quality, sparse octree, mesher migration, NURBS SDF, pipeline integration
- `todo_inputrefactor.md` — R-001..R-012: input/tool architecture refactor
- `todo_input.md` — current input system work
- `todo_vdb.md` — VDB-001..VDB-011: OpenVDB sparse tree migration (bake backend, booleans, caching)
- `todo_cam.md` — CAM-001..CAM-003: CNC tool path generation from SDF
