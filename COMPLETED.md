# FreeCAD Direct Modeling — Completed Tasks

Tasks moved here from `TODO.md` after verification.

---

## 🔄 BRep Pivot (March 2026)

Architecture changed from NURBS-surface-centric to BRep direct modeling. Point and curve tools are retained. Primitives now create `Part.Shape` solids via `Part.makeBox()` etc. instead of `Part.BSplineSurface`. Boolean operations use standard OCCT BRep booleans.

---

### ✅ Phase 1b: Create DMCurve class
- **Completed**: Defined the DMCurve class (formerly NurbsEdge) in FCDirectModeling/nurbs_geometry.py. It generates Part.BSplineCurve from sequences of DMPoint objects, supporting interpolation and linear segments.

### ✅ Phase 1f: Create Point tool
- **Completed**: Implemented PointCreator tool and DM_CreatePoint command. Registered the tool in the workbench toolbar and added point support to the core DMObjectProxy.

### ✅ Phase 1a: Create DMPoint class
- **Completed**: Defined the DMPoint class (formerly NurbsPoint) in FCDirectModeling/nurbs_geometry.py. This class wraps FreeCAD.Vector with support for NURBS-specific metadata like handles and weights.

---

## Milestone 3: Work Plane & Mouse Coordinates

### ✅ Unified primitive creator base
- **Completed**: `PrimitiveBase` + `NURBSPrimitiveCreator` with consistent event handling.

### ✅ Centralized mouse coordinate mapping
- **Completed**: Ray-plane intersection in `primitive_base.py`.

### ✅ WorkPlaneManager
- **Completed**: `work_plane.py` — Coin3D grid, face-tangent detection, `distToShape` UV.

### ✅ Phase 0f: Clean up primitive_base.py
- **Completed**: Removed dead code from primitive_base.py preview logic. Simplified _update_preview_object() to only handle NURBS curves.

### ✅ Phase 0e: Strip primitive builders from nurbs_primitives.py
- **Completed**: Removed build_box(), build_sphere(), build_cone(), and build_torus() from nurbs_primitives.py. Updated the module to focus exclusively on NURBS curve builders.

### ✅ Phase 0d: Strip primitive shape types from dm_object.py
- **Completed**: Removed box, sphere, cone, and torus shape types from DMObjectProxy initialization and shape building. Only 'curve' remains, with a placeholder for future 'surface' patches.

### ✅ Phase 0c: Delete mesh_features.py and surface_fitting.py
- **Completed**: Removed all meshing and surface fitting code. Deleted FCDirectModeling/mesh_features.py and FCDirectModeling/surface_fitting.py. Deleted FCDirectModeling/tests/test_nurbs_primitives.py.

### ✅ Phase 0b: Delete command_tweak
- **Completed**: Removed the Tweak command entirely. Deleted dm_commands/command_tweak.py and removed its references from InitGui.py.

### ✅ Phase 0a: Delete primitive creators and commands
- **Completed**: Removed all primitive-specific creators (box, sphere, cone, torus) and their associated commands to align with the NURBS-centric architecture. Updated InitGui.py and package initialization.

---

## Milestone 2: SDF → NURBS Transition

### ✅ Clean up tests
- **Completed**: All tests reference NURBS modules only.

### ✅ Refactor primitive creators to NURBS
- **Completed**: All creators produce NURBS shapes via `Part.BSplineSurface` / `Part.makeBox` etc.

### ✅ NURBS primitive builders
- **Completed**: `nurbs_primitives.py` with builders for Box, Sphere, Cone, Torus, Curve.

### ✅ Core Object refactoring
- **Completed**: Renamed `sdf_object.py` → `dm_object.py`. Removed SDF properties.

### ✅ Remove all SDF meshing code
- **Completed**: Deleted `sdf_mesher.py`, `sdf_utils.py`, all SDF evaluators.

---

## Milestone 1: SDF Pipeline (Legacy — Fully Removed)

### ✅ Orange icon, placement, container type
- **Completed**: `Part::FeaturePython` with orange XPM icon, proper placement.

### ✅ SDF-A, SDF-B, SDF-C
- **Completed**: SDF function reconstruction, link-based booleans, parametric editing.

### ✅ BUG-A, BUG-B, BUG-C fixes
- **Completed**: Context menu suppression, empty geometry fix, boolean property mismatch.

### ✅ Replace bare `print()` with `dm_logger`
- **Completed**: All logging through `dm_logger`.

### ✅ Open Sketcher command
- **Completed**: `command_open_sketcher.py`.

### ✅ Box primitive — 3-click creation
- **Completed**: `box_creator.py` + `box_task_panel.py`. Will be removed in NURBS refactor.

### ✅ DM Settings dialog
- **Completed**: `command_dm_settings.py` — Qt dialog, persisted via `FreeCAD.ParamGet`.

### ✅ Centralized logging
- **Completed**: `dm_logger.py` — `debug`, `info`, `warn`, `error`.

### ✅ Interactive 3D view dragging
- **Completed**: `PrimitiveCreatorBase` with `get_point_on_plane()`, `event_cb()`.

### ✅ Fix Coin3D / Python-C++ crashes
- **Completed**: All document mutations deferred via `QTimer.singleShot(0, fn)`.

### ✅ Replace `libfive` C++ with pure NumPy SDF meshing
- **Completed**: `sdf_mesher.py` — Surface Nets + QEF in NumPy. Later deleted.

### ✅ Phase 1g: Improve Curve Tool Visibility and Functionality
- **Completed**: Significantly improved curve tool visibility (thicker lines, larger points). Fixed a finalization bug where curves couldn't be "dropped". Implemented automatic smooth NURBS handles and persistent handle storage.

### ✅ Phase 1h: Fix Curve Plane Projection, Interpolation, and Fallback Errors
- **Completed**: Fixed plane projection bug where subsequent points drifted off-plane. Added duplicate point filtering to prevent BSpline interpolation crashes (Standard_ConstructionError). Ensured LineWidth and PointSize apply to finalized objects for better visibility.

### ✅ Phase 1i: Configurable Visibility & Robust Finalization
- **Completed**: Moved Line Width and Point Size settings to the DM Settings dialog. Fixed a critical bug in DMCurve where closed curves were finalized as line segments. Added comprehensive fallback logic to preserve polygon geometry if smooth interpolation fails.

### ✅ Phase 1j: Unify Preview and Final Objects
- **Completed**: Eliminated the 'is_preview' flag and the temporary 'DM_Preview' object. Primitives are now created as real objects immediately upon starting the tool, providing direct feedback via their actual properties. Cancelling a tool (Esc) correctly removes the unfinalized object.
