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

- [x] Curve points are not all being drawn in tool editor

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

### [COMPLETED] Fix Workplane 'Place on Geometry' Toggle (Minimum LLM: Gemini Flash)
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

## Phase 1: F-Rep Field Engine — Class Architecture

> All field and meshing classes. The `FrepStorageType` DM setting (0=Marching Cubes, 1=Adaptive MC, 2=NURBS F-Rep) selects which mesher is used; the field definitions are shared across all three.

### Class Map

```
core/frep_field.py          ← field definitions (shared by all storage types)
  FRepField (ABC)           ← base protocol
  PlaneField                ← half-space
  BoxField                  ← 6-plane intersection
  SphereField               ← analytical SDF
  CylinderField             ← capped cylinder SDF
  NurbsSurfaceField         ← closest-point projection SDF (Phase 4)

core/frep_composer.py       ← boolean composition tree
  UnionField                ← min(a, b)
  IntersectionField         ← max(a, b)
  SubtractionField          ← max(a, −b)
  SmoothUnionField          ← smooth-min blend

core/frep_mesher.py         ← meshing / isosurface extraction
  FRepMesher (ABC)          ← base mesher protocol
  MarchingCubesMesher       ← uniform grid (storage type 0)  ★ PRIORITY
  AdaptiveMCMesher          ← octree + MC (storage type 1)
  NurbsFRepMesher           ← NURBS fitting (storage type 2)
  get_active_mesher()       ← factory that reads FrepStorageType setting
```

---

### 1a. `FRepField` — Base Field Protocol (Minimum LLM: Gemini Low)
- **Goal**: Abstract base class for all signed distance fields.
- **Files to create**: `core/frep_field.py`
- **Steps**:
  1. `evaluate(point: Vector) -> float` — returns signed distance (negative=inside).
  2. `gradient(point: Vector) -> Vector` — numerical gradient via finite differences; subclasses may override analytically.
  3. `bounding_box() -> (Vector, Vector)` — `(min_corner, max_corner)`. For unbounded fields, clamp to `get_max_bounds()`.
  4. `sign_at(point) -> int` — returns -1/0/+1 with a tolerance.
  5. `evaluate_grid(points: np.ndarray) -> np.ndarray` — vectorized batch evaluation (default loops; subclasses override for speed).
- **Acceptance**: Import and subclass. A trivial test field `f(P) = P.z` returns correct signs.

### 1b. `PlaneField` (Minimum LLM: Gemini Flash)
- **Goal**: Half-space field dividing space by a plane.
- **Files to modify**: `core/frep_field.py`
- **Class**: `PlaneField(normal, origin)` — `evaluate(P) = dot(P − origin, normal)`.
- **Acceptance**: `PlaneField(Z, O).evaluate(Vector(0,0,5))` → `5.0`.

### 1c. `BoxField` (Minimum LLM: Gemini Low)
- **Goal**: Axis-aligned box via 6 `PlaneField` intersections.
- **Files to modify**: `core/frep_field.py`
- **Class**: `BoxField(center, size)` — `evaluate(P) = max(f1…f6)`.
- **Acceptance**: Center point returns `−half_size`. Points outside return positive.

### 1d. `SphereField` (Minimum LLM: Gemini Flash)
- **Goal**: Sphere as an analytical SDF.
- **Files to modify**: `core/frep_field.py`
- **Class**: `SphereField(center, radius)` — `evaluate(P) = |P − center| − radius`.
- **Acceptance**: Inside point returns negative. Outside returns positive.

### 1e. `CylinderField` (Minimum LLM: Gemini Low)
- **Goal**: Finite cylinder SDF using radial distance + two capping planes.
- **Files to modify**: `core/frep_field.py`
- **Class**: `CylinderField(base_center, axis, radius, height)` — `evaluate(P) = max(radial, cap_top, cap_bottom)`.
- **Acceptance**: Interior points negative. Exterior (beyond radius or caps) positive.

---
---

## Phase 2: Field Composition & Booleans

> Composition tree that combines fields using min/max/blend.

### 2a. `frep_composer.py` — Composition Nodes (Minimum LLM: Gemini Low)
- **Goal**: Boolean combination of fields.
- **Files to create**: `core/frep_composer.py`
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

### 3a. `FRepMesher` — Base Mesher Protocol (Minimum LLM: Gemini Flash)
- **Goal**: Define the abstract mesher interface. All meshers produce a `Part.Shape` from an `FRepField`.
- **Files to create**: `core/frep_mesher.py`
- **Steps**:
  1. `FRepMesher` (ABC) with method `mesh(field: FRepField, resolution: int) -> Part.Shape`.
  2. `get_active_mesher() -> FRepMesher` — reads `get_frep_storage_type()` and returns the correct mesher instance.
- **Acceptance**: `get_active_mesher()` returns the right subclass for each setting value.

### 3b. `MarchingCubesMesher` — Standard Marching Cubes ★ PRIORITY (Minimum LLM: Gemini High)
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
add bevel/chamfer to curve points



- [ ] **NURBS Surface Cell Decomposition** — Divide complex surfaces into bounded cells with individual clipping planes for multi-patch models.
- [ ] **R-Union Stitching** — Smooth transitions between adjacent NURBS patches that have small gaps, using parametric blending.
- [ ] **GPU Field Evaluation** — Port field evaluation to compute shaders for real-time interactive feedback.
- [ ] **Export to STL/3MF** — Direct mesh export from the F-Rep field without going through Part shapes.
- [ ] **Custom Control Point Effects** — Allow control points to modulate local field parameters (e.g., blend radius, wall thickness).
- [ ] **Radial Menu Raycast Selection** — Long-press radial menu listing all objects under the cursor for hidden topology selection.