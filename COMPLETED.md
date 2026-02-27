# FreeCAD Direct Modeling — Completed Tasks

Tasks moved here from `TODO.md` after verification.

---

## Milestone 1: SDF Pipeline (Legacy — Fully Removed)

### ✅ Replace `libfive` C++ with pure NumPy SDF meshing
- **Completed**: `sdf_mesher.py` — Surface Nets + QEF in NumPy. Later deleted.

### ✅ Fix Coin3D / Python-C++ crashes
- **Completed**: All document mutations deferred via `QTimer.singleShot(0, fn)`.

### ✅ Interactive 3D view dragging
- **Completed**: `PrimitiveCreatorBase` with `get_point_on_plane()`, `event_cb()`.

### ✅ Centralized logging
- **Completed**: `dm_logger.py` — `debug`, `info`, `warn`, `error`.

### ✅ DM Settings dialog
- **Completed**: `command_dm_settings.py` — Qt dialog, persisted via `FreeCAD.ParamGet`.

### ✅ Box primitive — 3-click creation
- **Completed**: `box_creator.py` + `box_task_panel.py`. Will be removed in NURBS refactor.

### ✅ Open Sketcher command
- **Completed**: `command_open_sketcher.py`.

### ✅ Replace bare `print()` with `dm_logger`
- **Completed**: All logging through `dm_logger`.

### ✅ BUG-A, BUG-B, BUG-C fixes
- **Completed**: Context menu suppression, empty geometry fix, boolean property mismatch.

### ✅ SDF-A, SDF-B, SDF-C
- **Completed**: SDF function reconstruction, link-based booleans, parametric editing.

### ✅ Orange icon, placement, container type
- **Completed**: `Part::FeaturePython` with orange XPM icon, proper placement.

---

## Milestone 2: SDF → NURBS Transition

### ✅ Remove all SDF meshing code
- **Completed**: Deleted `sdf_mesher.py`, `sdf_utils.py`, all SDF evaluators.

### ✅ Core Object refactoring
- **Completed**: Renamed `sdf_object.py` → `dm_object.py`. Removed SDF properties.

### ✅ NURBS primitive builders
- **Completed**: `nurbs_primitives.py` with builders for Box, Sphere, Cone, Torus, Curve.

### ✅ Refactor primitive creators to NURBS
- **Completed**: All creators produce NURBS shapes via `Part.BSplineSurface` / `Part.makeBox` etc.

### ✅ Clean up tests
- **Completed**: All tests reference NURBS modules only.

---

## Milestone 3: Work Plane & Mouse Coordinates

### ✅ WorkPlaneManager
- **Completed**: `work_plane.py` — Coin3D grid, face-tangent detection, `distToShape` UV.

### ✅ Centralized mouse coordinate mapping
- **Completed**: Ray-plane intersection in `primitive_base.py`.

### ✅ Unified primitive creator base
- **Completed**: `PrimitiveBase` + `NURBSPrimitiveCreator` with consistent event handling.

### ✅ Phase 0a: Delete primitive creators and commands
- **Completed**: Removed all primitive-specific creators (box, sphere, cone, torus) and their associated commands to align with the NURBS-centric architecture. Updated InitGui.py and package initialization.

### ✅ Phase 0b: Delete command_tweak
- **Completed**: Removed the Tweak command entirely. Deleted dm_commands/command_tweak.py and removed its references from InitGui.py.

### ✅ Phase 0c: Delete mesh_features.py and surface_fitting.py
- **Completed**: Removed all meshing and surface fitting code. Deleted FCDirectModeling/mesh_features.py and FCDirectModeling/surface_fitting.py. Deleted FCDirectModeling/tests/test_nurbs_primitives.py.

### ✅ Phase 0d: Strip primitive shape types from dm_object.py
- **Completed**: Removed box, sphere, cone, and torus shape types from DMObjectProxy initialization and shape building. Only 'curve' remains, with a placeholder for future 'surface' patches.

### ✅ Phase 0e: Strip primitive builders from nurbs_primitives.py
- **Completed**: Removed build_box(), build_sphere(), build_cone(), and build_torus() from nurbs_primitives.py. Updated the module to focus exclusively on NURBS curve builders.

### ✅ Phase 0f: Clean up primitive_base.py
- **Completed**: Removed dead code from primitive_base.py preview logic. Simplified _update_preview_object() to only handle NURBS curves.
