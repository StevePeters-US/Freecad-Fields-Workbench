# FreeCAD Direct Modeling — TODO

## How to Write a Task

Each task must be **self-contained** so an LLM or developer can complete it with
no prior context beyond the files listed. Follow this template:

```markdown
- **Task Title** (Minimum LLM: Gemini Flash/Low/High)
  - **Goal**: One sentence describing the desired outcome.
  - **Files to read**: List every file the implementer must read first.
  - **Files to modify/create**: List files that will change.
  - **Steps**:
    1. First concrete step…
    2. Second step…
  - **Acceptance**: How to verify the task is done.
```

- Move completed tasks to `COMPLETED.md` when a milestone is reached.
- Sort by required LLM within each section (Flash first, High last).
- We have as options Gemini Flash, Low, and High. Only use Claude for very difficult programming issues.

---
---

## Phase 0: Stabilize Existing Tools

> These tasks fix known issues in the current codebase before building the F-Rep pipeline.


Curve ponts are not all being drawn in tool editor

### Viewport Plane Focus Hotkey (Minimum LLM: Gemini Low)
- **Goal**: Add a hotkey to instantly orient the camera to face the active curve's plane (or a plane derived from 3 points for a 3D curve).
- **Files to read**: `tools/edit_tool.py`
- **Files to modify**: `tools/edit_tool.py`
- **Steps**:
  1. Intercept a hotkey (e.g., `F` or `Space`) during curve editing in `handle_keyboard`.
  2. Calculate the optimal plane normal for the curve.
  3. Use `view.setViewDirection()` to rotate the camera perpendicular to that plane.
- **Acceptance**: Pressing the focal hotkey snaps the camera to a flat 2D viewing angle relative to the curve.

### [COMPLETED] Curve Handle Type Context Menu (Minimum LLM: Gemini Low)
- **Goal**: Provide a context menu on control points to toggle handle types between Tangent (Smooth), Split (V-shape), and Custom (Sharp).

### Angle Snapping for Curve Handles (Minimum LLM: Gemini Low)
- **Goal**: Allow handles to snap to specific angular increments (default 15 degrees, via DM Settings) while dragging.
- **Files to read**: `tools/edit_tool.py`, `core/dm_object.py` (for settings)
- **Files to modify**: `tools/edit_tool.py`
  1. In `EditTool.handle_move`, check if a modifier key (Shift/Ctrl) is held during handle drag.
  2. Calculate the handle angle, round to the nearest increment, and enforce the output vector.
- **Acceptance**: Holding the modifier tightly snaps the handle angle.

### Fix Workplane 'Place on Geometry' Toggle (Minimum LLM: Gemini Flash)
- **Goal**: Ensure the workplane tool respects the "Place on Geometry" toggle in the context menu.
- **Files to read**: `tools/work_plane_tool.py`, `tools/dm_base.py`
- **Files to modify**: `tools/work_plane_tool.py`
- **Steps**:
  1. Update `get_snapped_placement` to check `self.place_on_geometry` before performing hit tests.
  2. If False, return `None` to bypass face snapping.
- **Acceptance**: With the toggle OFF, the workplane preview does not snap to geometry faces.

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

## Phase 1: F-Rep Field Engine (Plane-Based Primitives)

### 1a. Create `frep_field.py` — Base Field Protocol (Minimum LLM: Gemini Low)
- **Goal**: Define the abstract field interface that all F-Rep primitives will implement.
- **Files to read**: `core/dm_object.py` (to understand the existing object model)
- **Files to create**: `core/frep_field.py`
- **Steps**:
  1. Define an abstract base class `FRepField` with method `evaluate(point: FreeCAD.Vector) -> float`.
  2. Add `gradient(point) -> FreeCAD.Vector` (numerical gradient via finite differences, can be overridden analytically).
  3. Add `bounding_box() -> (FreeCAD.Vector, FreeCAD.Vector)` returning `(min_corner, max_corner)` for spatial queries.
  4. Provide a helper `sign_at(point) -> int` returning -1, 0, or +1 based on a tolerance threshold.
- **Acceptance**: `FRepField` can be imported and subclassed. A trivial test field (e.g., `f(P) = P.z`) returns correct signs.

### 1b. Plane Half-Space Field (Minimum LLM: Gemini Flash)
- **Goal**: Implement the simplest non-trivial field — a plane that divides space into positive and negative half-spaces.
- **Files to read**: `core/frep_field.py`
- **Files to create/modify**: `core/frep_field.py` (add `PlaneField` class)
- **Steps**:
  1. `PlaneField(normal: Vector, point: Vector)` — stores a plane definition.
  2. `evaluate(P)` returns `dot(P − point, normal)` — positive on the normal side, negative on the other.
  3. `gradient(P)` returns the constant normal vector.
  4. `bounding_box()` returns an infinite box (or a very large finite one for practical purposes).
- **Acceptance**: `PlaneField(Vector(0,0,1), Vector(0,0,0)).evaluate(Vector(0,0,5))` returns `5.0`. Points below z=0 return negative values.

### 1c. Box Field via Plane Intersection (Minimum LLM: Gemini Low)
- **Goal**: Construct a box-shaped field by intersecting 6 half-space planes.
- **Files to read**: `core/frep_field.py`
- **Files to create/modify**: `core/frep_field.py` (add `BoxField` class)
- **Steps**:
  1. `BoxField(center, size)` — creates 6 `PlaneField` instances (±X, ±Y, ±Z).
  2. `evaluate(P)` returns `max(f1, f2, …, f6)` — the standard F-Rep intersection.
  3. `bounding_box()` returns the exact box extents.
- **Acceptance**: `BoxField(Vector(0,0,0), Vector(10,10,10)).evaluate(Vector(0,0,0))` returns `-5.0` (inside). Points outside return positive values.

### 1d. Sphere Field (Minimum LLM: Gemini Flash)
- **Goal**: Implement a sphere as an analytical signed distance field.
- **Files to read**: `core/frep_field.py`
- **Files to modify**: `core/frep_field.py` (add `SphereField` class)
- **Steps**:
  1. `SphereField(center, radius)`.
  2. `evaluate(P)` returns `|P − center| − radius`. Negative inside, positive outside.
  3. `gradient(P)` returns the normalized direction `(P − center) / |P − center|`.
- **Acceptance**: `SphereField(Vector(0,0,0), 5).evaluate(Vector(3,0,0))` returns `-2.0`. Points at distance > 5 return positive.

### 1e. Cylinder Field (Minimum LLM: Gemini Low)
- **Goal**: Implement an infinite cylinder SDF, then cap it with plane intersections to make it finite.
- **Files to read**: `core/frep_field.py`
- **Files to modify**: `core/frep_field.py` (add `CylinderField` class)
- **Steps**:
  1. `CylinderField(base_center, axis, radius, height)`.
  2. Infinite cylinder: project point onto the axis, compute radial distance − radius.
  3. Cap with two `PlaneField` intersections at base and top.
  4. `evaluate(P)` returns `max(radial_sdf, cap_top, cap_bottom)`.
- **Acceptance**: Points inside the finite cylinder return negative values. Points outside the caps or beyond the radius return positive.

---
---

## Phase 2: Field Composition & Booleans

> Build the composition tree that combines multiple fields using min/max/blend operators.

### 2a. Create `frep_composer.py` — Composition Tree (Minimum LLM: Gemini Low)
- **Goal**: Implement a tree structure for combining F-Rep fields using boolean operators.
- **Files to read**: `core/frep_field.py`
- **Files to create**: `core/frep_composer.py`
- **Steps**:
  1. Define `UnionField(field_a, field_b)` — `evaluate(P)` returns `min(a(P), b(P))`.
  2. Define `IntersectionField(field_a, field_b)` — `evaluate(P)` returns `max(a(P), b(P))`.
  3. Define `SubtractionField(field_a, field_b)` — `evaluate(P)` returns `max(a(P), −b(P))`.
  4. All composition nodes implement the `FRepField` interface (evaluate, gradient, bounding_box).
  5. `bounding_box()` for union = hull of both boxes; for intersection = overlap of both; for subtraction = box of A.
- **Acceptance**: Combining a `SphereField` and a `BoxField` with `SubtractionField` produces expected sign patterns at test points.

### 2b. Smooth Blend (R-Union) Operator (Minimum LLM: Gemini High)
- **Goal**: Implement smooth blending between two fields, producing a fillet-like transition instead of a sharp seam.
- **Files to read**: `core/frep_field.py`, `core/frep_composer.py`
- **Files to modify**: `core/frep_composer.py` (add `SmoothUnionField`)
- **Steps**:
  1. Implement the standard smooth-min function: `f = min(a, b) − k² * max(k − |a − b|, 0)² / (4k)` where `k` controls fillet radius.
  2. `SmoothUnionField(field_a, field_b, blend_radius)`.
  3. Ensure gradient is smooth across the transition region (no discontinuity).
- **Acceptance**: Two overlapping spheres with `SmoothUnionField` produce a smoothly blended region at the intersection. The isosurface at f=0 shows a fillet, not a sharp crease.

### 2c. Wire Boolean Commands to F-Rep Composer (Minimum LLM: Gemini Low)
- **Goal**: Update the existing `cmd_boolean.py` commands to use the F-Rep composition tree instead of OCCT boolean operations.
- **Files to read**: `commands/cmd_boolean.py`, `core/dm_object.py`, `core/frep_composer.py`
- **Files to modify**: `commands/cmd_boolean.py`, `core/dm_object.py`
- **Steps**:
  1. When a DM object has an associated `FRepField`, store it as a property on the `DMObjectProxy`.
  2. `DM_Fuse` creates a `UnionField` from the two selected objects' fields.
  3. `DM_Cut` creates a `SubtractionField`.
  4. `DM_Common` creates an `IntersectionField`.
  5. The resulting object re-meshes from the composed field.
- **Acceptance**: Selecting two F-Rep objects and clicking Fuse produces a single object whose shape is the union of both fields.

---
---

## Phase 3: Isosurface Extraction & Display

> Convert the evaluated field back into something FreeCAD can display.

### 3a. Create `frep_mesher.py` — Marching Cubes (Minimum LLM: Gemini High)
- **Goal**: Implement or integrate a marching cubes algorithm that extracts a triangle mesh from an F-Rep field at the f=0 isosurface.
- **Files to read**: `core/frep_field.py`, `core/frep_composer.py`
- **Files to create**: `core/frep_mesher.py`
- **Steps**:
  1. Implement a grid-based marching cubes: define a 3D grid over the field's bounding box, evaluate `f(P)` at each grid vertex, and extract triangles where the sign changes.
  2. Use NumPy for vectorized grid evaluation.
  3. Output a `Part.Shape` from the mesh using `Part.Shape(Part.__sortEdges__(...))` or `Mesh.Mesh(triangles)` converted to `Part.Shape`.
  4. Support configurable resolution (grid cell size).
  5. Optionally use `scipy.spatial` for acceleration if available.
- **Acceptance**: Given a `SphereField(Vector(0,0,0), 10)`, marching cubes produces a closed mesh that approximates a sphere. The mesh is valid and renderable in FreeCAD's viewport.

### 3b. Adaptive Resolution / Octree Meshing (Minimum LLM: Claude)
- **Goal**: Replace uniform grid sampling with an octree-based approach that allocates finer resolution near the isosurface and coarser resolution in empty space.
- **Files to read**: `core/frep_mesher.py`, `core/frep_field.py`
- **Files to modify**: `core/frep_mesher.py`
- **Steps**:
  1. Build an octree over the field's bounding box.
  2. Subdivide cells where the field changes sign (surface-crossing cells).
  3. Apply marching cubes only to leaf cells near the isosurface.
  4. Stitch the resulting mesh to avoid T-junctions.
- **Acceptance**: Complex fields mesh 3–5× faster than uniform grids at equivalent surface quality. No visual cracks between regions of different subdivision depth.

### 3c. Connect Mesher to DM Object Pipeline (Minimum LLM: Gemini Low)
- **Goal**: Wire the mesher into the `DMObjectProxy.build_shape()` pipeline so that F-Rep objects automatically render.
- **Files to read**: `core/dm_object.py`, `core/frep_mesher.py`
- **Files to modify**: `core/dm_object.py`
- **Steps**:
  1. Add a `"frep"` shape type to `DMObjectProxy.__init__()`.
  2. In `build_shape()`, when `ShapeType == "frep"`, call the mesher on the object's `FRepField`.
  3. Store the mesh result as the object's `Shape`.
  4. Trigger re-meshing when the field composition tree changes.
- **Acceptance**: Creating an F-Rep object in the document produces a visible shape in the viewport that updates when field parameters change.

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

### 5a. Box Primitive Tool (Minimum LLM: Gemini Low)
- **Goal**: Interactive 2-click box creation on the workplane, backed by `BoxField`.
- **Files to read**: `tools/dm_base.py`, `core/frep_field.py`, `core/dm_object.py`
- **Files to create**: `tools/primitive_tool.py`, `commands/cmd_primitive.py`
- **Files to modify**: `core/dm_object.py`, `InitGui.py`
- **Steps**:
  1. `BoxCreator` extends `DMBase` — 1st click sets corner, drag sets footprint, 2nd click sets height.
  2. On accept, create a `BoxField` with the specified dimensions, positioned at the workplane.
  3. Wire through the mesher for display.
  4. Register `DM_CreateBox` with hotkey `B`.
- **Acceptance**: Click-drag-click creates a box visible in the viewport. The box participates in F-Rep booleans.

### 5b. Sphere Primitive Tool (Minimum LLM: Gemini Flash)
- **Goal**: Interactive sphere creation — click sets center, drag sets radius.
- **Files to read**: Same as 5a.
- **Files to modify**: `tools/primitive_tool.py`, `commands/cmd_primitive.py`, `InitGui.py`
- **Steps**:
  1. `SphereCreator` — click sets center, drag sets radius.
  2. Creates a `SphereField`.
  3. Meshes and displays.
- **Acceptance**: Click → drag → sphere appears. Participates in F-Rep booleans.

### 5c. Cylinder Primitive Tool (Minimum LLM: Gemini Low)
- **Goal**: Interactive cylinder creation — click sets center, drag sets radius, 2nd drag sets height.
- **Files to read**: Same as 5a.
- **Files to modify**: `tools/primitive_tool.py`, `commands/cmd_primitive.py`, `InitGui.py`
- **Steps**:
  1. `CylinderCreator` — click sets center, drag sets radius, 2nd drag sets height.
  2. Creates a `CylinderField` (inherently finite via capping planes).
  3. Meshes and displays.
- **Acceptance**: Interactive cylinder creation produces a renderable field object.

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




- [ ] **NURBS Surface Cell Decomposition** — Divide complex surfaces into bounded cells with individual clipping planes for multi-patch models.
- [ ] **R-Union Stitching** — Smooth transitions between adjacent NURBS patches that have small gaps, using parametric blending.
- [ ] **GPU Field Evaluation** — Port field evaluation to compute shaders for real-time interactive feedback.
- [ ] **Export to STL/3MF** — Direct mesh export from the F-Rep field without going through Part shapes.
- [ ] **Custom Control Point Effects** — Allow control points to modulate local field parameters (e.g., blend radius, wall thickness).
- [ ] **Radial Menu Raycast Selection** — Long-press radial menu listing all objects under the cursor for hidden topology selection.