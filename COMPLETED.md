# FreeCAD Direct Modeling — Completed Tasks

Tasks moved here from `TODO.md` after verification.

---

## ✅ Replace `libfive` C++ dependency with pure NumPy SDF meshing
- **Completed**: `FCDirectModeling/sdf_mesher.py` implements Surface Nets + QEF in pure NumPy.
- **Result**: No native compilation required. `extract_mesh_numpy()` takes an SDF function and returns `(verts, tris)`.

## ✅ Fix all Coin3D / Python-C++ boundary crashes and segfaults
- **Completed**: All scene-graph and document-mutating operations are deferred via `QtCore.QTimer.singleShot(0, fn)`.
- **Result**: No more segfaults from mutating the document inside Coin3D event callbacks.

## ✅ Interactive 3D view dragging for shape creation
- **Completed**: `PrimitiveCreatorBase` in `primitives/primitive_base.py` provides `get_point_on_plane()`, `get_closest_point_on_axis()`, and `event_cb()` for orthographic and perspective cameras.
- **Result**: Users can click-drag to define shapes in the viewport with live mesh preview via `SDFMeshPrimitiveCreator._process_preview_queue()`.

## ✅ Centralized logging system
- **Completed**: `FCDirectModeling/dm_logger.py` provides `debug`, `info`, `warn`, `error` functions that log to FreeCAD console (always) and optionally to `~/dm_debug.log` (when `DEBUG_DM_CRASH=1`).

## ✅ DM Settings dialog
- **Completed**: `dm_commands/command_dm_settings.py` provides a Qt dialog for meshing algorithm, resolution, and wireframe toggle. Settings are persisted via `FreeCAD.ParamGet("User parameter:FCDirectModeling")`.

## ✅ Box primitive — 3-click creation (Place → Size → Set)
- **Completed**: `primitives/box_creator.py` + `box_task_panel.py` implement the full 3-click flow with working plane detection, axis locking, and auto cutter/fuse logic.

## ✅ Open Sketcher command
- **Completed**: `dm_commands/command_open_sketcher.py` registers `DM_OpenSketcher` to launch the FreeCAD Sketcher workbench.
## ✅ Replace bare `print()` with `dm_logger`
- **Completed**: Every log statement in the codebase now goes through `dm_logger`.
- **Result**: Consistent logging across the project, supporting both console and file output.

## ✅ Add preview-resolution setting to DM Settings
- **Completed**: Added a "Preview Resolution" setting to the DM Settings dialog.
- **Result**: Users can now customize the density of the mesh during interactive dragging, balancing performance and visual detail.
## ✅ BUG-A. Suppress floating context menu on 3rd click
- **Completed**: In `BoxCreator.event_cb`, return `True` when `state == 2` to consume the event.
- **Result**: No floating menu appears after placing the final primitive.

## ✅ BUG-B. Final placed mesh has no geometry
- **Completed**: Implemented `SDFViewProvider` and robust `DisplayMode` handling in `sdf_object.py`.
- **Result**: Placed meshes are clearly visible in the viewport and correctly oriented.

## ✅ SDF-A. Refactor `SDFObjectProxy` to expose SDF function reconstruction
- **Completed**: Added `SDFObjectProxy.build_sdf()` to centralize SDF reconstruction logic.
- **Result**: Boolean operations and transformations can now compose SDF functions recursively without triggering redundant meshing cycles.

## ✅ SDF Part Container with Orange Icon
- **Completed**: Refactored `create_sdf_object` to use `App::DocumentObjectGroupPython` with an orange stairstep XPM icon.
- **Result**: SDF objects now appear as distinct orange containers in the tree view, better distinguishing them from standard meshes.

## ✅ Fix Preview Visibility and Hierarchy
- **Completed**: Fixed `primitive_base.py` `_process_preview_queue` to correctly reconstruct SDF functions and maintain a temporary Part-Mesh hierarchy during creation.
- **Result**: Real-time visual feedback is now reliably displayed during interactive shape dragging.

## ✅ BUG-C. Fix boolean commands — property mismatch
- **Completed**: Updated the validation loop in `dm_commands/command_boolean.py` to check for `SDFType` property.
- **Result**: Boolean commands now correctly identify and process SDF objects.

## ✅ SDF-B. Make booleans compose SDFs, not labels
- **Completed**: Converted `SDFChildren` to `App::PropertyLinkList` and updated `build_sdf` to use direct object links.
- **Result**: Boolean objects are now robust against renames and duplicates.

## ✅ SDF-C. Parametric editing — modify SDF params and re-mesh
- **Completed**: Added typed properties (`Length`, `Radius`, etc.) to all SDF primitive types and updated `build_sdf` to use them as the source of truth.
- **Result**: Primitives can now be edited parametrically through the FreeCAD property panel after creation.

## ✅ Fix: Object Placement and Container Type
- **Completed**: Switched container to `Part::FeaturePython`, moved child mesh creation to the factory function using `App::PropertyLink`, and implemented `SDFObjectProxy` placement logic for centered primitives. Fixed `dm_logger` calls from `.warning()` to `.warn()`.
- **Result**: Final objects are now correctly placed, and high-resolution meshing completes successfully without document locking or logging errors.

## ✅ Remove `sdf_mesher.py` and all SDF meshing code
- **Completed**: Deleted `sdf_mesher.py`, its tests, and stripped meshing logic from `sdf_object.py` and `command_dm_settings.py`.
- **Result**: Core codebase is now free of legacy voxel meshing logic, ready for the NURBS-native transition.

## ✅ Remove SDF distance functions from `sdf_object.py`
- **Completed**: Deleted legacy `_sdf_*` evaluator functions and `_SDF_BUILDERS` dictionary.
- **Result**: Core object logic is stripped of legacy SDF evaluation code, preparing it for the NURBS build pipeline.

## ✅ Core Object and Boolean Refactoring
- **Completed**: Renamed `sdf_object.py` to `dm_object.py`, refactored `Proxy` and `ViewProvider` for NURBS, and updated boolean operations to use native BRep.
- **Result**: Codebase is transitioned to a NURBS-native architecture; legacy SDF logic and properties are removed.

## ✅ Create NURBS primitive builders
- **Completed**: Implemented `nurbs_primitives.py` with functions returning `Part.Shape` objects for Box, Sphere, Cone, and Torus.
- **Result**: Each builder returns a valid non-null `Part.Shape` that displays correctly in FreeCAD's 3D view.

## ✅ Remove `sdf_utils.py`
- **Completed**: Deleted `FCDirectModeling/sdf_utils.py` and removed all references.
- **Result**: No remaining imports or references to `sdf_utils` in the codebase.

## ✅ Remove legacy `command_draw_box.py`
- **Completed**: Deleted `dm_commands/command_draw_box.py` and removed references from `InitGui.py`.
- **Result**: Legacy draw box command is fully removed with no import errors.

## ✅ Clean up test files
- **Completed**: Removed or rewrote test files that referenced the old SDF pipeline.
- **Result**: No test files reference SDF modules.
