# FreeCAD Direct Modeling — TODO

---

## Bugs



- **Curve points are not all being drawn in tool editor**

- **[COMPLETED] Find missing icons or replace with valid references** `Gemini Low`
  - **Goal**: Resolve "Cannot find icon: Part_Box/Sphere" warnings in the log when creating FRep primitives.
  - **Files to read**: `commands/cmd_primitive.py`
  - **Files to modify/create**: `commands/cmd_primitive.py` (or add icon files)
  - **Steps**:
    1. Identify available standard FreeCAD icons for primitives or create new SVG icons in the `resources` folder.
    2. Update `CommandDMCreation._ICONS` mapping to valid existing ones or register new icons via `FreeCADGui.addIcon`.
  - **Acceptance**: No more "Cannot find icon" warnings in DM creation tools.

- **Investigate `_get_geometry_point` null shape F-Rep face hits** `Gemini Low`
  - **Goal**: Fully support deep geometry snapping (vertex/edge detection) on FRep meshes instead of just using the generic hit point.
  - **Files to read**: `tools/dm_base.py`, `core/frep_mesher.py`, `core/dm_object.py`
  - **Files to modify/create**: `tools/dm_base.py`
  - **Steps**:
    1. Revisit `_get_geometry_point` to handle `obj.Shape.isNull()` appropriately while still providing deep topological snapping for FRep fields.
    2. We might need to query the `FRepField` directly by reconstructing a local raycast to discover corners/edges of the FRep body without a real BRep `Part.Shape`.
  - **Acceptance**: Snapping logic handles FRep fields gracefully and allows precise corner snapping.

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
---

## Refactor: AnalyticField & Code Cleanup







### Unify Tool Cleanup Lifecycle `Gemini Low`
- **Goal**: Merge the parallel cleanup paths in `PrimitiveCreatorBase._do_terminate()` and `NURBSPrimitiveCreator.terminate()` to eliminate duplication and prevent missed cleanup.
- **Files to read**: `tools/primitive_tool.py`, `tools/dm_base.py`
- **Files to modify**: `tools/dm_base.py`, `tools/primitive_tool.py`
- **Steps**:
  1. Audit both cleanup methods and identify shared logic (removing active/preview objects, clearing callbacks).
  2. Consolidate into a single `_do_terminate()` chain that both FRep and NURBS tools use.
  3. Ensure `_finished` guard and QTimer deferral are consistent across all paths.
- **Acceptance**: All tools (box, sphere, cylinder, curve, workplane, translate) terminate cleanly without stale objects or callbacks.

### Add Bounding Box Utility Helpers `Gemini Flash`
- **Goal**: Extract the repeated min/max bounding box merge logic from `UnionField.bounding_box()` and `IntersectionField.bounding_box()` into a shared utility.
- **Files to read**: `core/frep/frep_composer.py`
- **Files to modify**: `core/frep/frep_composer.py`
- **Steps**:
  1. Add `_merge_bb(bb_a, bb_b, mode='union')` helper that computes the spatial union (min of mins, max of maxes) or intersection (max of mins, min of maxes) of two bounding boxes.
  2. Refactor `UnionField.bounding_box()` and `IntersectionField.bounding_box()` to use the helper.
- **Acceptance**: Boolean field bounding boxes produce identical results. Code is DRYer.

---
---

## Phase 1: F-Rep Field Engine — Class Architecture

> All field and meshing classes. The `FrepStorageType` DM setting (0=Marching Cubes, 1=Adaptive MC, 2=NURBS F-Rep) selects which mesher is used; the field definitions are shared across all three.

### Class Map

```
core/frep/                      ← F-Rep Module
  frep_field.py                 ← FRepField (ABC base protocol)
  marching_cubes/               ← Storage Type 0
    mc_field.py                 ← MarchingCubesField(FRepField)
    plane.py                    ← MCPlaneField(MarchingCubesField)
    box.py                      ← MCBoxField(MarchingCubesField)
    sphere.py                   ← MCSphereField(MarchingCubesField)
    cylinder.py                 ← MCCylinderField(MarchingCubesField)
  adaptive/                     ← Storage Type 1
    adaptive_field.py           ← AdaptiveField(FRepField)
    plane.py, box.py, ...       ← (Child implementations)
  nurbs/                        ← Storage Type 2
    nurbs_field.py              ← NurbsField(FRepField)
    plane.py, box.py, ...       ← (Child implementations)
  frep_composer.py              ← Boolean composition nodes
  frep_mesher.py                ← Isosurface extraction protocols
```

---

### 1a. `FRepField` — Base Field Protocol
- **Goal**: Form the overall parent class for all F-Rep implementations.
- **Files to create**: `core/frep/frep_field.py`
- **Steps**:
  1. Define `FRepField` ABC with `evaluate(point)`, `gradient(point)`, `bounding_box()`, `sign_at(point)`.

### 1b. `MarchingCubesField` — Storage Type 0 Parent
- **Goal**: Intermediate base class for all Marching Cubes primitives.
- **Files to create**: `core/frep/marching_cubes/mc_field.py`
- **Steps**:
  1. `class MarchingCubesField(FRepField):`
  2. Implement `evaluate_grid(points)` optimization.

### 1c. `MCPlaneField` — Marching Cubes Plane
- **Goal**: Half-space divided by a plane for the MC backend.
- **Files to create**: `core/frep/marching_cubes/plane.py`
- **Steps**:
  1. `class MCPlaneField(MarchingCubesField):` implementing `evaluate` mapped to the plane SDF.

### 1d. `MCBoxField` — Marching Cubes Box
- **Goal**: Axis-aligned box SDF for the MC backend.
- **Files to create**: `core/frep/marching_cubes/box.py`
- **Steps**:
  1. `class MCBoxField(MarchingCubesField):`

### 1e. `MCSphereField` & `MCCylinderField` — Marching Cubes analytical primitives
- **Goal**: Analytical shapes for the MC backend.
- **Files to create**: `core/frep/marching_cubes/sphere.py`, `core/frep/marching_cubes/cylinder.py`

### 1f. `AdaptiveField` and `NurbsField` architecture
- **Goal**: Skeleton out the parent classes and their files for the other storage types.
- **Files to create**: `core/frep/adaptive/adaptive_field.py`, `core/frep/nurbs/nurbs_field.py` and empty primitive files.

---
---

## Phase 2: Field Composition & Booleans

> Composition tree that combines fields using min/max/blend.

### [COMPLETED] 2a. `frep_composer.py` — Composition Nodes (Minimum LLM: Gemini Low)
- **Goal**: Boolean combination of fields.
- **Files to create**: `core/frep/frep_composer.py`
- **Classes**:
  - `UnionField(a, b)` — `evaluate = min(a, b)`
  - `IntersectionField(a, b)` — `evaluate = max(a, b)`
  - `SubtractionField(a, b)` — `evaluate = max(a, −b)`
  - All implement `FRepField`. Bounding boxes: union=hull, intersection=overlap, subtraction=box(A).
- **Acceptance**: `SubtractionField(SphereField, BoxField)` returns expected signs.

### 2b. `SmoothUnionField` — Smooth Blend (Minimum LLM: Gemini High)
- **Goal**: Fillet-like smooth boolean.
- **Files to modify**: `core/frep_composer.py`
- **Class**: `SmoothUnionField(a, b, blend_radius)` using smooth-min: `f = min(a,b) − k²·max(k−|a−b|, 0)²/(4k)`.
- **Acceptance**: Two overlapping spheres produce a filleted isosurface.

### 2c. Wire Boolean Commands to F-Rep (Minimum LLM: Gemini Low)
- **Goal**: `DM_Fuse/Cut/Common` create composition fields instead of OCCT booleans.
- **Files to modify**: `commands/cmd_boolean.py`, `core/dm_object.py`
- **Steps**:
  1. Store `FRepField` reference on `DMObjectProxy`.
  2. `DM_Fuse` → `UnionField`, `DM_Cut` → `SubtractionField`, `DM_Common` → `IntersectionField`.
  3. Result object re-meshes from composed field.
- **Acceptance**: Boolean of two F-Rep objects produces correct merged shape.

---
---

## Phase 3: Isosurface Extraction & Display

> Three mesher classes behind a common protocol, selected by the `FrepStorageType` setting.

### [COMPLETED] 3a. `FRepMesher` — Base Mesher Protocol (Minimum LLM: Gemini Flash)
- **Goal**: Define the abstract mesher interface. All meshers produce a `Part.Shape` from an `FRepField`.
- **Files to create**: `core/frep_mesher.py`
- **Steps**:
  1. `FRepMesher` (ABC) with method `mesh(field: FRepField, resolution: int) -> Part.Shape`.
  2. `get_active_mesher() -> FRepMesher` — reads `get_frep_storage_type()` and returns the correct mesher instance.
- **Acceptance**: `get_active_mesher()` returns the right subclass for each setting value.

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

### 3c. `AdaptiveMCMesher` — Octree Marching Cubes (Minimum LLM: Claude)
- **Goal**: Octree-accelerated adaptive MC for better quality/performance tradeoff.
- **Files to modify**: `core/frep_mesher.py`
- **Class**: `AdaptiveMCMesher(FRepMesher)`
- **Steps**:
  1. Build an octree over the bounding box.
  2. Subdivide only cells where field changes sign (surface-crossing).
  3. Apply MC to leaf cells.
  4. Stitch mesh to avoid T-junctions at level boundaries.
- **Acceptance**: 3–5× faster than uniform MC at equivalent surface quality. No cracks.

### 3d. `NurbsFRepMesher` — NURBS Surface Fitting (Minimum LLM: Claude)
- **Goal**: Instead of triangle mesh, fit NURBS surfaces to the isosurface for native BRep output.
- **Files to modify**: `core/frep_mesher.py`
- **Class**: `NurbsFRepMesher(FRepMesher)`
- **Steps**:
  1. Sample the isosurface using marching cubes at moderate resolution.
  2. Cluster triangles into patch regions by normal similarity.
  3. Fit `Part.BSplineSurface` patches to each region.
  4. Stitch patches into a `Part.Shell` → `Part.Solid`.
  5. Return native BRep `Part.Shape`.
- **Acceptance**: A sphere field produces a smooth BRep solid (not faceted). Editable as NURBS.

### 3e. Connect Mesher to DM Object Pipeline (Minimum LLM: Gemini Low)
- **Goal**: Wire the mesher into `DMObjectProxy.build_shape()` so F-Rep objects render automatically.
- **Files to modify**: `core/dm_object.py`
- **Steps**:
  1. Add `"frep"` shape type to `DMObjectProxy.__init__()`.
  2. In `build_shape()`, when `ShapeType == "frep"`, call `get_active_mesher().mesh(field, resolution)`.
  3. Store `FRepField` on the proxy. Store mesh as `Shape`.
  4. Re-mesh when field tree or `FrepStorageType` setting changes.
- **Acceptance**: Creating an F-Rep object renders in the viewport. Changing the storage type re-meshes.

---
---

## Phase 4: NURBS Surface → F-Rep Field

> The key innovation — converting NURBS surfaces into signed distance fields using closest-point projection.

### 4a. Closest-Point-on-NURBS Projection (Minimum LLM: Gemini High)
- **Goal**: Implement a function that, given a query point `P` and a NURBS surface, returns the closest point `Q` on the surface, the surface normal `n̂` at `Q`, and the distance `|P − Q|`.
- **Files to read**: `core/nurbs_geometry.py`, FreeCAD `Part.BSplineSurface` API docs
- **Files to create/modify**: `core/frep_field.py` (add `NurbsSurfaceField`)
- **Steps**:
  1. Use `surface.parameter(P)` → `(u, v)` to get the parameter-space projection (FreeCAD's OCCT binding provides this).
  2. Evaluate `Q = surface.value(u, v)` for the closest point.
  3. Evaluate `n̂ = surface.normal(u, v)` for the surface normal.
  4. Compute `sign = dot(P − Q, n̂)` and `distance = |P − Q|`. Return `sign * distance` as the field value.
  5. Handle edge cases: points projected outside the surface parameter domain (clamp to boundary), degenerate normals.
- **Acceptance**: Given a flat NURBS plane, the field behaves identically to `PlaneField`. Given a curved NURBS surface, the field correctly reports inside/outside relative to the normal direction.

### 4b. Bounded NURBS Field with Clipping Planes (Minimum LLM: Gemini Low)
- **Goal**: Restrict a `NurbsSurfaceField`'s influence to a finite region using bounding planes.
- **Files to read**: `core/frep_field.py`
- **Files to modify**: `core/frep_field.py` (extend `NurbsSurfaceField`)
- **Steps**:
  1. For each `NurbsSurfaceField`, auto-generate clipping planes from the surface's parameter boundaries (trim curves or edge planes).
  2. `evaluate(P)` returns `max(f_nurbs(P), f_clip1(P), f_clip2(P), …)` — only negative (inside) when inside both the surface and all clips.
  3. Allow user-defined clipping planes to override the auto-generated ones.
- **Acceptance**: A bounded NURBS field only produces a finite-extent enclosed region, not an infinite field.

### 4c. Surface-From-Curves Tool (Minimum LLM: Gemini High)
- **Goal**: Create a NURBS surface by lofting between two or more curves, then wrap it in a `NurbsSurfaceField`.
- **Files to read**: `core/nurbs_geometry.py`, `core/frep_field.py`, `tools/dm_base.py`
- **Files to create**: `tools/surface_tool.py`, `commands/cmd_surface.py`
- **Files to modify**: `core/dm_object.py`, `InitGui.py`
- **Steps**:
  1. User selects 2+ curves → `Part.BSplineSurface` loft.
  2. Wrap the surface in a `NurbsSurfaceField` with auto-generated boundary clips.
  3. Connect to the mesher for viewport display.
  4. Register `DM_CreateSurface` command with hotkey `S`.
- **Acceptance**: Select two curves → invoke surface tool → a lofted surface appears and is usable in F-Rep boolean operations.

---
---

## Phase 5: Interactive Primitive Tools (F-Rep Backed)

> Re-implement the creation tools to produce F-Rep objects instead of Part shapes.



---
---

## Phase 6: Polish & UX

### 6a. F-Rep Mesh Resolution in DM Settings (Minimum LLM: Gemini Flash)
- **Goal**: Add a "Mesh Resolution" slider to the DM Settings dialog controlling the marching cubes grid density.
- **Files to read**: `commands/cmd_settings.py`, `core/frep_mesher.py`
- **Files to modify**: `commands/cmd_settings.py`
- **Steps**:
  1. Add a `MeshResolution` parameter (integer, default 64, range 16–256) to the settings dialog.
  2. Wire it into the mesher so re-meshing uses the new resolution.
- **Acceptance**: Changing the slider live-updates the mesh quality of F-Rep objects.

### 6b. Register Hotkeys for All Commands (Minimum LLM: Gemini Flash)
- **Goal**: Add `'Accel'` entries to all command `GetResources()` methods.
- **Files to modify**: All `cmd_*.py` files in `commands/`
- **Steps**:
  1. Add `'Accel': '<key>'` to each command's `GetResources()`.
  2. Point: `P`, Curve: `C`, Surface: `S`, Box: `B`, Extrude: `E`, Fuse: `Ctrl+F`, Cut: `Ctrl+X`, Common: `Ctrl+I`.
- **Acceptance**: Each hotkey activates the correct command.

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

## Phase 7: Advanced F-Rep

### 7a. Extrude Curve to F-Rep Solid (Minimum LLM: Gemini High)
- **Goal**: Extrude a closed curve along a direction to create an F-Rep solid. The curve defines the cross-section boundary, and the extrusion direction defines the depth.
- **Files to read**: `core/nurbs_geometry.py`, `core/frep_field.py`, `core/frep_composer.py`
- **Files to create**: `commands/cmd_extrude.py`
- **Files to modify**: `core/dm_object.py`, `InitGui.py`
- **Steps**:
  1. Convert the closed curve to a 2D signed distance field (distance to curve boundary, positive outside, negative inside).
  2. Extend this 2D field into 3D by ignoring the extrusion axis component.
  3. Intersect with two bounding planes at the top and bottom of the extrusion.
  4. User interaction: select curve → enter interactive mode → drag to set distance → finalize.
- **Acceptance**: Draw a closed curve → extrude → a solid F-Rep object appears with the curve as its cross-section.

### 7b. Revolve Curve to F-Rep Solid (Minimum LLM: Gemini High)
- **Goal**: Revolve a curve profile around an axis to create a solid of revolution as an F-Rep field.
- **Files to read**: `core/frep_field.py`, `core/frep_composer.py`
- **Files to create**: `commands/cmd_revolve.py`
- **Files to modify**: `core/dm_object.py`, `InitGui.py`
- **Steps**:
  1. Given a curve profile and an axis, create a field where evaluation maps the query point to cylindrical coordinates relative to the axis, then evaluates the 2D profile SDF at `(r, z)`.
  2. Support partial revolution (angle < 360°) by intersecting with wedge planes.
- **Acceptance**: A half-circle profile revolved around the Y axis produces a sphere-like F-Rep body.

### 7c. Lattice Infill via Field Modulation (Minimum LLM: Claude)
- **Goal**: Apply a periodic lattice pattern (e.g., gyroid, Schwarz-P) to a solid by intersecting its field with a triply periodic minimal surface field.
- **Files to read**: `core/frep_field.py`, `core/frep_composer.py`
- **Files to create**: `core/frep_lattice.py`
- **Steps**:
  1. Implement `GyroidField(cell_size, thickness)` — `f(P) = sin(x/s)·cos(y/s) + sin(y/s)·cos(z/s) + sin(z/s)·cos(x/s) − threshold`.
  2. Implement `SchwartzPField(cell_size, thickness)`.
  3. `LatticeInfillField(solid_field, lattice_field)` = `IntersectionField(solid, lattice)`.
  4. Add UI to select a solid, choose lattice type, and set cell size/thickness.
- **Acceptance**: Applying gyroid infill to a box produces a lattice structure visible in the viewport. The infill conforms perfectly to the original solid's boundary.

### 7d. Shell / Hollow Operation (Minimum LLM: Gemini High)
- **Goal**: Hollow out a solid by subtracting a smaller version of itself from the interior.
- **Files to read**: `core/frep_field.py`, `core/frep_composer.py`
- **Files to modify**: `core/frep_composer.py`
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

Hiding dm part does not hide the coin3d nodes

The cube does not respect the workplane