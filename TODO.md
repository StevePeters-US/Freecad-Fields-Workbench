# FreeCAD Direct Modeling — TODO

---

## Critical Bugs (will crash / broken code path)
---

- **Fix `get_projected_point()` return value** `Claude Low`
  - **Goal**: The function returns undefined variable `result` — any caller gets a `NameError`.
  - **File**: `core/input_manager.py` — find `get_projected_point()`, compute and return the correct projected point instead of `result`.
  - **Skills**: `dm_event_pipeline`
  - **Acceptance**: Function returns a valid `FreeCAD.Vector` representing the projected position.

- **Fix `get_geometry_info()` undefined method** `Claude Low`
  - **Goal**: `work_plane_tool.py:148` calls `self.projector.get_geometry_info()` which does not exist on `ViewProjector`. The correct private method is `_get_geometry_point()` with different arguments.
  - **Files**: `core/view_projector.py` (read), `tools/work_plane_tool.py:148` (fix call site or add method).
  - **Skills**: `dm_event_pipeline`
  - **Acceptance**: WorkPlane tool face-snapping does not crash.

---

## Refactoring
---
- **Extract Render Logic from DMObject** `Gemini High`
  - **Goal**: Move Coin3D scene graph and visual rendering (meshes, curves, points) from `DMViewProvider` in `dm_object.py` to a dedicated `DMRenderer` class.
  - **Files to read**: `core/dm_object.py`
  - **Files to create/modify**: `core/dm_renderer.py`, `core/dm_object.py`
  - **Acceptance**: `DMViewProvider` instantiates `DMRenderer` and calls its methods to draw points/meshes. Tools can also call `renderer.draw_handles(points)` when editing.

- **Evaluate and Rename `DMObject`/`DMViewProvider`** `Gemini Low`
  - **Goal**: Evaluate if `DMObject` is the best name for the class generating FreeCAD BRep proxies, and rename it if a better fit (e.g., `DMFeature` or `DMNode`) is found across the codebase.
  - **Acceptance**: Chosen name makes more sense for a FreeCAD proxy object and is consistently used.

- **Unify Event Pipeline** `Claude High`
  - **Goal**: Eliminate the dual Qt + Coin3D event routing. Choose one primary channel; demote or remove the other. All hotkeys handled in one consistent place.
  - **Files**: `core/input_manager.py`, `tools/dm_base.py`
  - **Skills**: `dm_event_pipeline`
  - **Steps**:
    1. Audit every hotkey: which channel claims it, which executes it.
    2. Decide: Coin3D `event_cb` is the primary (since it lives in the viewport); Qt filter handles only app-level events (menus, shortcuts FreeCAD would steal).
    3. Remove duplicate handling from the channel that loses.
    4. Document the remaining single flow in `dm_event_pipeline` skill.
  - **Acceptance**: A keypress fires exactly one handler. No hotkey silently fires twice.

- **Standardize Tool Lifecycle** `Claude Medium`
  - **Goal**: All tools follow the same finish/terminate sequence. No dialog closed twice, no cleanup bypassed.
  - **Files**: `tools/dm_base.py`, `tools/point_tool.py`, `tools/work_plane_tool.py`
  - **Skills**: `dm_event_pipeline`
  - **Steps**:
    1. Define the canonical chain: `finish()` → `_do_finish()` → `terminate()` → `_do_terminate()`.
    2. `PointCreator._do_finish()` must call `super()._do_finish()` for object renaming.
    3. Remove the redundant dialog-close call in `NURBSPrimitiveCreator._do_finish()` (already done by parent's `_do_terminate()`).
    4. `WorkPlaneCreator._do_terminate()` already calls `super()` — verify it does so at the correct point.
  - **Acceptance**: Create and cancel each tool type; no Coin3D nodes leak, no signals stay connected, no double dialog-close errors in the console.

- **Remove Unused MeshingType Options** `Claude Low`
  - **Goal**: MeshingType 1 ("Adaptive Marching Cubes") and 2 ("NURBS F-Rep") are not implemented but selectable. Either implement or remove them.
  - **Files**: `core/dm_object.py` (property definition), `commands/cmd_primitive.py` (warning), `core/dm_mesher.py`
  - **Acceptance**: Users cannot select unimplemented meshing types, or a placeholder is shown with a clear "not yet implemented" error in the console.

---

## Bugs
> Agent note: To run or test any Python that imports FreeCAD, use the skill at `.agents/skills/freecad_env/SKILL.md`.
---
The cube does not respect the workplane
---
WE NEED A show dm wireframe option, as well as a show dm points option
---
fix r click context menu over report view
--- 

second time attemting to drag a workplane corner quits the tool
---

- **Curve points are not all being drawn in tool editor**



- **Investigate `_get_geometry_point` null shape SDF face hits** `Gemini Low`
  - **Goal**: Fully support deep geometry snapping (vertex/edge detection) on SDF meshes instead of just using the generic hit point.
  - **Files to read**: `tools/dm_base.py`, `core/frep_mesher.py`, `core/dm_object.py`
  - **Files to modify/create**: `tools/dm_base.py`
  - **Steps**:
    1. Revisit `_get_geometry_point` to handle `obj.Shape.isNull()` appropriately while still providing deep topological snapping for signed distance fields.
    2. We might need to query the `SdfField` directly by reconstructing a local raycast to discover corners/edges of the SDF body without a real BRep `Part.Shape`.
  - **Acceptance**: Snapping logic handles signed distance fields gracefully and allows precise corner snapping.

- **Fix Box Tool Workplane Respect** `Gemini Low`
  - **Goal**: Ensure the box primitive tool correctly aligns and scales relative to the active workplane rather than the global coordinate system.
  - **Files to read**: `tools/primitive_tool.py`, `core/work_plane.py`
  - **Files to modify/create**: `tools/primitive_tool.py`
  - **Steps**:
    1. Update `BoxCreator`'s event handling or placement logic to multiply the generated box dimensions and positions by the workplane's coordinate system transform.
    2. Verify that `get_point_on_plane` correctly returns points in the workplane's local space.
  - **Acceptance**: When drawing a box on an angled workplane, the box aligns to the workplane's surface and extrudes along the workplane's normal.

### Viewport Plane Focus Hotkey (Minimum LLM: Gemini Low)
- **Goal**: Add a hotkey to instantly orient the camera to face the active curve's plane (or a plane derived from 3 points for a 3D curve).
- **Files to read**: `tools/edit_tool.py`
- **Files to modify**: `tools/edit_tool.py`
- **Steps**:
  1. Intercept a hotkey (e.g., `F` or `Space`) during curve editing in `handle_keyboard`.
  2. Calculate the optimal plane normal for the curve.
  3. Use `view.setViewDirection()` to rotate the camera perpendicular to that plane.
- **Acceptance**: Pressing the focal hotkey snaps the camera to a flat 2D viewing angle relative to the curve.

### Angle Snapping for Curve Handles (Minimum LLM: Gemini Low)
- **Goal**: Allow handles to snap to specific angular increments (default 15 degrees, via DM Settings) while dragging.
- **Files to read**: `tools/edit_tool.py`, `core/dm_object.py` (for settings)
- **Files to modify**: `tools/edit_tool.py`
  1. In `EditTool.handle_move`, check if a modifier key (Shift/Ctrl) is held during handle drag.
  2. Calculate the handle angle, round to the nearest increment, and enforce the output vector.
- **Acceptance**: Holding the modifier tightly snaps the handle angle.


### Workplane Snapping Improvements (Minimum LLM: Gemini Low)
- **Goal**: Add snap-to-grid, snap-to-center, and snap-to-radius for the workplane.
- **Files to read**: `core/work_plane.py`, `tools/dm_base.py`
- **Files to modify**: `core/work_plane.py`, `tools/dm_base.py`
- **Steps**:
  1. Add `snap_to_grid(point, grid_size)` method to `WorkPlaneManager`.
  2. Add `snap_to_center(point)` — snaps to the workplane origin.
  3. Add `snap_to_radius(point, radius)` — snaps to a circle of given radius from center.
  4. Wire snapping into `get_point_on_plane()` with toggle via DM Settings.
- **Acceptance**: Points placed near grid intersections snap to them. Snap modes are toggleable.

---

## Phase 3: Isosurface Extraction & Display

### 3a. `FRepMesher` — Base Mesher Protocol (Minimum LLM: Gemini Flash)
- **Goal**: Define the abstract mesher interface. Produces a `Part.Shape` from an `SdfField`.
- **Files to create**: `core/frep_mesher.py`
- **Steps**:
  1. `FRepMesher` (ABC) with method `mesh(field: SdfField, resolution: int) -> Part.Shape`.

### [COMPLETED] 3b. `MarchingCubesMesher` — Standard Marching Cubes ★ PRIORITY (Minimum LLM: Gemini High)
- **Goal**: Uniform-grid marching cubes producing a triangle mesh at the f=0 isosurface.
- **Files to modify**: `core/frep_mesher.py`
- **Class**: `MarchingCubesMesher(FRepMesher)`
- **Steps**:
  1. Build a 3D grid over `field.bounding_box()`, clamped to `get_max_bounds()`.
  2. Evaluate `field.evaluate_grid()` at every grid vertex (NumPy vectorized).
  3. For each cube with a sign change, look up the edge table and interpolate vertex positions.
  4. Emit triangles. Use the standard 256-entry MC lookup table.
  5. Convert triangles to `Mesh.Mesh` → `Part.Shape`.
  6. `resolution` parameter controls grid divisions per axis (default from DM Settings).
- **Acceptance**: `MarchingCubesMesher().mesh(SphereField(O, 10), 32)` produces a recognizable sphere mesh.


---

## Phase 4: NURBS Surface → Signed Distance Field

> The key innovation — converting NURBS surfaces into signed distance fields using mathematical evaluation.

### 4a. Closest-Point-on-NURBS Projection (Minimum LLM: Gemini High)
- **Goal**: Implement a function that, given a query point `P` and a NURBS surface, returns the closest point `Q` on the surface, the surface normal `n̂` at `Q`, and the distance `|P − Q|`.
- **Files to read**: `core/nurbs_geometry.py`, FreeCAD `Part.BSplineSurface` API docs
- **Files to create/modify**: `core/sdf_field.py` (add `NurbsSurfaceField`)
- **Steps**:
  1. Use `surface.parameter(P)` → `(u, v)` to get the parameter-space projection (FreeCAD's OCCT binding provides this).
  2. Evaluate `Q = surface.value(u, v)` for the closest point.
  3. Evaluate `n̂ = surface.normal(u, v)` for the surface normal.
  4. Compute `sign = dot(P − Q, n̂)` and `distance = |P − Q|`. Return `sign * distance` as the field value.
  5. Handle edge cases: points projected outside the surface parameter domain (clamp to boundary), degenerate normals.
- **Acceptance**: Given a flat NURBS plane, the field behaves identically to `SdfPlaneField`. Given a curved NURBS surface, the field correctly reports inside/outside relative to the normal direction.

### 4b. Bounded NURBS Field with Clipping Planes (Minimum LLM: Gemini Low)
- **Goal**: Restrict a `NurbsSurfaceField`'s influence to a finite region using bounding planes.
- **Files to read**: `core/sdf_field.py`
- **Files to modify**: `core/sdf_field.py` (extend `NurbsSurfaceField`)
- **Steps**:
  1. For each `NurbsSurfaceField`, auto-generate clipping planes from the surface's parameter boundaries (trim curves or edge planes).
  2. `evaluate(P)` returns `max(f_nurbs(P), f_clip1(P), f_clip2(P), …)` — only negative (inside) when inside both the surface and all clips.
  3. Allow user-defined clipping planes to override the auto-generated ones.
- **Acceptance**: A bounded NURBS field only produces a finite-extent enclosed region, not an infinite field.

### 4c. Surface-From-Curves Tool (Minimum LLM: Gemini High)
- **Goal**: Create a NURBS surface by lofting between two or more curves, then wrap it in a `NurbsSurfaceField`.
- **Files to read**: `core/nurbs_geometry.py`, `core/sdf_field.py`, `tools/dm_base.py`
- **Files to create**: `tools/surface_tool.py`, `commands/cmd_surface.py`
- **Files to modify**: `core/dm_object.py`, `InitGui.py`
- **Steps**:
  1. User selects 2+ curves → `Part.BSplineSurface` loft.
  2. Wrap the surface in a `NurbsSurfaceField` with auto-generated boundary clips.
  3. Connect to the mesher for viewport display.
  4. Register `DM_CreateSurface` command with hotkey `S`.
- **Acceptance**: Select two curves → invoke surface tool → a lofted surface appears and is usable in implicit boolean operations.


---

## Phase 6: Polish & UX

### 6c. Radial Menu System (Minimum LLM: Gemini High)
- **Goal**: Right-click radial menu at cursor position for quick tool access.
- **Files to create**: `core/radial_menu.py`, `commands/cmd_radial_menu.py`
- **Files to modify**: `InitGui.py`
- **Steps**:
  1. `RadialMenu` class using Coin3D `SoSeparator` with text labels at angular intervals.
  2. Mouse callback: highlight nearest sector, click to activate, Esc to dismiss.
  3. Bind to `Space`.
- **Acceptance**: Press Space → radial menu → click item → command activates.

### 6d. Instance and Copy Commands (Minimum LLM: Gemini Low)
- **Goal**: Create linked instances (`App::Link`) and independent copies of DM objects.
- **Files to read**: `core/dm_object.py`
- **Files to create**: `commands/cmd_instance.py`
- **Files to modify**: `InitGui.py`
- **Steps**:
  1. `DM_Instance`: select → `App::Link` → offset placement. Hotkey `I`.
  2. `DM_Copy`: select → deep copy with cloned field tree. Hotkey `Ctrl+D`.
- **Acceptance**: Instance updates when original changes. Copy does not.

---
---

## Phase 7: Advanced SDF Modifiers

### 7a. Extrude Curve to SDF Solid (Minimum LLM: Gemini High)
- **Goal**: Extrude a closed curve along a direction to create an SDF solid. The curve defines the cross-section boundary, and the extrusion direction defines the depth.
- **Files to read**: `core/nurbs_geometry.py`, `core/sdf_field.py`, `core/field_composer.py`
- **Files to create**: `commands/cmd_extrude.py`
- **Files to modify**: `core/dm_object.py`, `InitGui.py`
- **Steps**:
  1. Convert the closed curve to a 2D signed distance field (distance to curve boundary, positive outside, negative inside).
  2. Extend this 2D field into 3D by ignoring the extrusion axis component.
  3. Intersect with two bounding planes at the top and bottom of the extrusion.
  4. User interaction: select curve → enter interactive mode → drag to set distance → finalize.
- **Acceptance**: Draw a closed curve → extrude → a solid SDF object appears with the curve as its cross-section.

### 7b. Revolve Curve to SDF Solid (Minimum LLM: Gemini High)
- **Goal**: Revolve a curve profile around an axis to create a solid of revolution as an signed distance field.
- **Files to read**: `core/sdf_field.py`, `core/field_composer.py`
- **Files to create**: `commands/cmd_revolve.py`
- **Files to modify**: `core/dm_object.py`, `InitGui.py`
- **Steps**:
  1. Given a curve profile and an axis, create a field where evaluation maps the query point to cylindrical coordinates relative to the axis, then evaluates the 2D profile SDF function at `(r, z)`.
  2. Support partial revolution (angle < 360°) by intersecting with wedge planes.
- **Acceptance**: A half-circle profile revolved around the Y axis produces a sphere-like SDF body.

### 7c. Lattice Infill via Field Modulation (Minimum LLM: Claude)
- **Goal**: Apply a periodic lattice pattern (e.g., gyroid, Schwarz-P) to a solid by intersecting its field with a triply periodic minimal surface field.
- **Files to read**: `core/sdf_field.py`, `core/field_composer.py`
- **Files to create**: `core/analytic_lattice.py`
- **Steps**:
  1. Implement `GyroidField(cell_size, thickness)` — `f(P) = sin(x/s)·cos(y/s) + sin(y/s)·cos(z/s) + sin(z/s)·cos(x/s) − threshold`.
  2. Implement `SchwartzPField(cell_size, thickness)`.
  3. `LatticeInfillField(solid_field, lattice_field)` = `IntersectionField(solid, lattice)`.
  4. Add UI to select a solid, choose lattice type, and set cell size/thickness.
- **Acceptance**: Applying gyroid infill to a box produces a lattice structure visible in the viewport. The infill conforms perfectly to the original solid's boundary.

### 7d. Shell / Hollow Operation (Minimum LLM: Gemini High)
- **Goal**: Hollow out a solid by subtracting a smaller version of itself from the interior.
- **Files to read**: `core/sdf_field.py`, `core/field_composer.py`
- **Files to modify**: `core/field_composer.py`
- **Steps**:
  1. `ShellField(original_field, wall_thickness)`: `f(P) = max(original(P), −original_offset(P))` where `original_offset(P) = original(P) + wall_thickness`.
  2. Wire into a `DM_Shell` command.
- **Acceptance**: Shelling a sphere produces a hollow sphere with uniform wall thickness.

---
---

## Phase N: Research & Future

- [ ] **Move Existing Workplane** — Allow moving/reorienting a workplane after it has been created using the Workplane tool.
- [ ] **Viewport Workplane Selection** — Make workplanes selectable directly in the 3D viewport by clicking their grid/handles.
add bevel/chamfer to curve points

when placing points, the point should be placed against the first valid object. Now it seems to prioritize geometry over workplanes



- [ ] **NURBS Surface Cell Decomposition** — Divide complex surfaces into bounded cells with individual clipping planes for multi-patch models.
- [ ] **R-Union Stitching** — Smooth transitions between adjacent NURBS patches that have small gaps, using parametric blending.
- [ ] **GPU Field Evaluation** — Port field evaluation to compute shaders for real-time interactive feedback.
- [ ] **Export to STL/3MF** — Direct mesh export from the F-Rep field without going through Part shapes.
- [ ] **Custom Control Point Effects** — Allow control points to modulate local field parameters (e.g., blend radius, wall thickness).
- [ ] **Radial Menu Raycast Selection** — Long-press radial menu listing all objects under the cursor for hidden topology selection.

