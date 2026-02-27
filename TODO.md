# FreeCAD Direct Modeling — TODO

## How to Write a Task

Each task must be **self-contained** so an LLM or developer can complete it with
no prior context beyond the files listed. Follow this template:

```markdown
- [ ] **Task Title** (Complexity: N/10)
  - **Goal**: One sentence describing the desired outcome.
  - **Files to read**: List every file the implementer must read first.
  - **Files to modify/create**: List files that will change.
  - **Steps**:
    1. First concrete step…
    2. Second step…
  - **Acceptance**: How to verify the task is done.
```

- Mark in-progress tasks `[/]`, completed tasks `[x]`.
- Move completed tasks to `COMPLETED.md` when a milestone is reached.
- Sort by complexity within each section (lowest first).

---

## Architecture Overview

> **Principle**: NURBS surfaces are the primary data model. Every primitive in the workbench is created as a native `Part::Feature` using FreeCAD's OpenCASCADE NURBS/BRep kernel. No SDF evaluation, no voxel grids, no mesh generation from distance fields. Shapes are exact, smooth, and export-ready.

The following tasks establish the NURBS-native pipeline: strip all SDF code, replace with NURBS primitive builders, update booleans to use BRep operations, and add freeform curve tools.

---

## Tasks (sorted by complexity, lowest → highest)

---

### 8. Refactor primitive creators to use NURBS instead of SDF (Complexity: 5/10)

- **Goal**: Update `primitives/base.py` and all creator subclasses to produce NURBS shapes instead of SDF meshes during preview and finalization.
- **Files to read**:
  - `FCDirectModeling/primitives/primitive_base.py` — `PrimitiveCreatorBase`, `SDFMeshPrimitiveCreator`.
  - `FCDirectModeling/primitives/box_creator.py`, `sphere_creator.py`, `cone_creator.py`, `torus_creator.py`.
- **Files to modify**:
  - `FCDirectModeling/primitives/primitive_base.py` — rename `SDFMeshPrimitiveCreator` → `NURBSPrimitiveCreator`. Replace SDF evaluation + meshing with calls to `nurbs_primitives.py` builders.
  - All creator subclasses — update to call `build_box()`, `build_sphere()`, etc. instead of constructing SDF params.
- **Steps**:
  1. In `primitive_base.py`, remove all SDF preview queue logic (`_process_preview_queue`, SDF evaluation, mesh assignment).
  2. Replace with: on each drag update, call the appropriate NURBS builder with current dimensions, assign `preview_obj.Shape = shape`.
  3. On finalize (3rd click), call `create_dm_object()` with the final dimensions.
  4. Update each creator subclass to pass dimensions to the NURBS builder instead of constructing SDF param dicts.
- **Acceptance**: Click-drag to create a Box → live NURBS shape preview updates in viewport → on 3rd click, final `Part::FeaturePython` appears with correct dimensions.

---

### 10. Create task panels for Sphere, Cone, and Torus (Complexity: 4/10)

- **Goal**: Box has `box_task_panel.py` with dimension spinboxes. Sphere, Cone, and Torus have no panels — create them.
- **Files to read**:
  - `FCDirectModeling/primitives/box_task_panel.py` — the full reference panel (has `QDoubleSpinBox` inputs, `update_values()`, `focus_field()`, `accept()`, `reject()`).
  - `FCDirectModeling/primitives/sphere_creator.py` — needs `radius` input.
  - `FCDirectModeling/primitives/cone_creator.py` — needs `radius` and `height` inputs.
  - `FCDirectModeling/primitives/torus_creator.py` — needs `major_r` and `minor_r` inputs.
  - `dm_commands/command_create_primitives.py` — where the creators are instantiated (you'll need to attach panels here).
- **Files to create**:
  - `FCDirectModeling/primitives/sphere_task_panel.py`
  - `FCDirectModeling/primitives/cone_task_panel.py`
  - `FCDirectModeling/primitives/torus_task_panel.py`
- **Files to modify**:
  - `dm_commands/command_create_primitives.py` — attach each panel to its creator when activated, same way `command_create_box.py` uses `BoxTaskPanel`.
- **Steps**:
  1. Copy `box_task_panel.py` as a template.
  2. For **Sphere**: one `QDoubleSpinBox` for Radius. `update_values(radius)` updates the display.
  3. For **Cone**: two spinboxes — Radius and Height. `update_values(radius, height)`.
  4. For **Torus**: two spinboxes — Major Radius and Minor Radius. `update_values(major_r, minor_r)`.
  5. In each creator, add a `set_panel(panel)` call and in `handle_move` call `self.panel.update_values(...)`.
  6. In `command_create_primitives.py`, after creating the creator, create the panel and show it via `FreeCADGui.Control.showDialog(panel)`.
- **Acceptance**: Activate Sphere/Cone/Torus tool → a panel appears showing live dimensions as you drag.

---

### 11. Implement freeform 3D curve drawing tool (Complexity: 6/10)

- **Goal**: Users can draw freeform B-spline curves directly in the 3D viewport by clicking control points.
- **Files to read**:
  - `FCDirectModeling/primitives/primitive_base.py` — understand the event callback pattern for mouse interaction.
  - FreeCAD `Part.BSplineCurve` API — `interpolate()`, `buildFromPoles()`.
- **Files to create**:
  - `FCDirectModeling/curve_tools.py` — curve construction utilities.
  - `dm_commands/command_draw_curve.py` — `DM_DrawCurve` command.
- **Files to modify**:
  - `InitGui.py` — register `DM_DrawCurve` in the toolbar.
- **Steps**:
  1. In `command_draw_curve.py`, create a `DM_DrawCurve` command that enters a click-to-place-control-point mode.
  2. Each click adds a control point (projected onto the working plane or nearest surface).
  3. Display a live preview of the B-spline curve through the current points using `Part.BSplineCurve().interpolate(points)`.
  4. Double-click or press Enter to finalize — create a `Part::Feature` containing the curve as `Part.Edge`.
  5. Press Escape to cancel.
  6. In `curve_tools.py`, implement helper functions:
     - `build_bspline_curve(points, degree=3) → Part.BSplineCurve`
     - `project_point_to_plane(screen_pos, plane) → FreeCAD.Vector`
- **Acceptance**: Activate Draw Curve → click 4+ points in viewport → a smooth B-spline curve appears through them → finalize → a `Part::Feature` edge is created in the document.

---

### 13. Implement Array command — BRep-level repetition (Complexity: 5/10)

- **Goal**: Repeat a shape along a vector, circular pattern, or grid using BRep boolean unions.
- **Files to read**:
  - `dm_commands/command_boolean.py` — understand how boolean results are composed.
  - FreeCAD `Part` module — `Part.Shape.fuse()`, `Part.Shape.translated()`, `Part.Shape.rotated()`.
- **Files to create**:
  - `dm_commands/command_array.py`
- **Files to modify**:
  - `InitGui.py` — register the new command.
- **Steps**:
  1. Create a dialog with inputs for: count, offset vector, pattern type (linear / circular / grid).
  2. For linear: translate the shape by `i * offset` for each copy, fuse all copies.
  3. For circular: rotate the shape by `i * (360/count)` degrees around an axis for each copy, fuse.
  4. Create the result as a new `Part::FeaturePython` via `create_dm_object()`.
- **Acceptance**: Create a Sphere → Array 5 times along X with spacing 3 → a row of 5 fused spheres appears.

---

### 14. Implement Transform command (Complexity: 4/10)

- **Goal**: Apply translate / rotate / scale to a shape interactively.
- **Files to read**:
  - FreeCAD `Part` module — `Part.Shape.translated()`, `Part.Shape.rotated()`, `Part.Shape.scaled()`.
- **Files to create**:
  - `dm_commands/command_transform.py`
- **Files to modify**:
  - `InitGui.py` — register the command.
- **Steps**:
  1. Create a dialog with translate (X, Y, Z), rotate (angle + axis), and scale inputs.
  2. Apply the transform to the selected shape.
  3. Update the object's shape in place or create a new transformed object.
- **Acceptance**: Select a Box → Transform (rotate 45° around Z) → the shape updates to show the rotated box.

---

### 15. Sketch-driven NURBS extrusion (Complexity: 6/10)

- **Goal**: After the user closes the Sketcher, convert the sketch profile into an extruded NURBS solid.
- **Files to read**:
  - `dm_commands/command_open_sketcher.py` — launches the Sketcher.
  - FreeCAD `Part` module — `Part.Face.extrude()`, `Part.Wire`, `Part.BSplineCurve`.
- **Files to create**:
  - `dm_commands/command_sketch_extrude.py`
- **Steps**:
  1. Read the `Sketcher::SketchObject` wire: extract edges as a `Part.Wire`.
  2. Create a `Part.Face` from the wire.
  3. Extrude the face using `face.extrude(FreeCAD.Vector(0, 0, height))`.
  4. Create a `Part::FeaturePython` with the extruded solid.
- **Acceptance**: Draw a sketch → run extrude → a 3D NURBS extrusion appears matching the sketch profile.

---

### 16. Define visible work plane from mouse cursor context (Complexity: 5/10)

- **Goal**: Before any drawing operation, define and display a work plane. The plane's orientation should be derived from whatever geometry the mouse cursor is hovering over (face normal, edge tangent, etc.), defaulting to the XY origin plane with Z+ as the normal.
- **Files to read**:
  - `FCDirectModeling/primitives/primitive_base.py` — understand `get_point_on_plane()` and the current working plane logic.
  - FreeCAD `Part.Plane`, `Draft.WorkingPlane` API.
  - FreeCAD Coin3D scene graph API for visual plane display.
- **Files to create**:
  - `FCDirectModeling/work_plane.py` — work plane manager (orientation detection, visual display, plane math).
- **Files to modify**:
  - `FCDirectModeling/primitives/primitive_base.py` — integrate work plane detection into the creation flow.
- **Steps**:
  1. On tool activation, raycast from the mouse cursor into the scene.
  2. If the ray hits a face, set the work plane to that face's surface normal at the hit point.
  3. If no geometry is hit, default to the XY plane at the origin with Z+ normal.
  4. Display the work plane as a semi-transparent grid/rectangle in the 3D viewport using Coin3D nodes.
  5. All subsequent drawing operations should project onto this work plane.
  6. Provide a way to reset/change the work plane (e.g., clicking a different face).
- **Acceptance**: Hover over a face → activate a draw tool → a visible work plane appears aligned to that face. Hover over empty space → work plane defaults to XY at origin.

---

### 17. Recreate primitive cube using NURBS surfaces per face (Complexity: 6/10)

- **Goal**: Completely rewrite the box/cube primitive so that each face is an individual NURBS surface (`Part.BSplineSurface`), stitched together into a solid shell. This replaces the current `Part.makeBox()` approach with explicit NURBS face construction.
- **Files to read**:
  - `FCDirectModeling/nurbs_primitives.py` — current `build_box()` implementation.
  - FreeCAD `Part.BSplineSurface`, `Part.Face`, `Part.Shell`, `Part.Solid` API.
- **Files to modify**:
  - `FCDirectModeling/nurbs_primitives.py` — rewrite `build_box()` to construct 6 NURBS surface faces.
- **Steps**:
  1. For each of the 6 cube faces, define a degree-1 B-spline surface with 4 corner control points.
  2. Create a `Part.Face` from each `Part.BSplineSurface`.
  3. Stitch all 6 faces into a `Part.Shell`.
  4. Create a `Part.Solid` from the shell.
  5. Validate the solid is closed and has correct normals.
- **Acceptance**: `build_box(l, w, h)` returns a valid `Part.Solid` made of 6 NURBS faces. The shape displays identically to the current box and passes `Shape.isValid()`.

---

### 18. Chamfer and fillet edges on the cube primitive (Complexity: 5/10)

- **Goal**: Add optional chamfer and fillet operations to the cube primitive, allowing users to apply edge treatments during or after creation.
- **Files to read**:
  - `FCDirectModeling/nurbs_primitives.py` — the NURBS box builder (task #17 prerequisite).
  - FreeCAD `Part.Shape.makeChamfer()`, `Part.Shape.makeFillet()` API.
  - `FCDirectModeling/primitives/box_creator.py` — the box creation flow.
- **Files to modify**:
  - `FCDirectModeling/nurbs_primitives.py` — add `chamfer_edges()` and `fillet_edges()` helpers.
  - `FCDirectModeling/primitives/box_creator.py` — integrate chamfer/fillet options.
  - `FCDirectModeling/primitives/box_task_panel.py` — add UI controls for chamfer/fillet radius and edge selection.
- **Steps**:
  1. Implement `chamfer_edges(shape, edges, distance) → Part.Shape` using `shape.makeChamfer()`.
  2. Implement `fillet_edges(shape, edges, radius) → Part.Shape` using `shape.makeFillet()`.
  3. Add chamfer/fillet radius spinbox and edge selection mode to the box task panel.
  4. Allow selecting individual edges or "all edges" for the operation.
  5. Apply chamfer/fillet after box creation as a post-processing step.
- **Acceptance**: Create a box → select edges → apply fillet with radius 2 → edges are smoothly rounded. Same for chamfer with a flat bevel.

---

### 19. Rewrite cone primitive to keep circle on starting plane (Complexity: 4/10)

- **Goal**: Rewrite the cone primitive so that the base circle stays on the starting work plane, rather than being offset or misaligned during creation.
- **Files to read**:
  - `FCDirectModeling/primitives/cone_creator.py` — current cone creation logic.
  - `FCDirectModeling/nurbs_primitives.py` — `build_cone()` implementation.
  - `FCDirectModeling/primitives/primitive_base.py` — `get_point_on_plane()` and work plane logic.
- **Files to modify**:
  - `FCDirectModeling/primitives/cone_creator.py` — fix placement so base circle aligns to the starting plane.
  - `FCDirectModeling/nurbs_primitives.py` — ensure `build_cone()` places the base at z=0 of the local frame.
- **Steps**:
  1. In `cone_creator.py`, record the starting plane (from work plane or click point) on the first click.
  2. Ensure the cone's base circle center is placed at the first click point on that plane.
  3. The cone's height extends along the plane's normal direction.
  4. Update `build_cone()` to always place the base at the local origin (z=0).
  5. Apply the work plane transform to position the cone correctly in world space.
- **Acceptance**: Click on any plane → drag to set radius → the base circle visually sits on the clicked plane. The cone extends upward along the plane's normal.

---

### 20. Transform selected sub-elements (point, face, edge) (Complexity: 6/10)

- **Goal**: Allow users to select individual sub-elements (vertices, edges, or faces) of a shape and apply translate/rotate/scale transforms to them directly.
- **Files to read**:
  - FreeCAD `Part.Shape` sub-element API — `Shape.Vertexes`, `Shape.Edges`, `Shape.Faces`, `FreeCADGui.Selection.getSelectionEx()`.
  - `dm_commands/command_transform.py` — existing whole-object transform (if present).
  - FreeCAD `Part` module — `BRepOffsetAPI_MakeOffset`, `BRepBuilderAPI_Transform`.
- **Files to create**:
  - `dm_commands/command_transform_subelement.py` — sub-element transform command.
- **Files to modify**:
  - `InitGui.py` — register the command.
- **Steps**:
  1. Use `FreeCADGui.Selection.getSelectionEx()` to get the selected sub-elements (vertices, edges, or faces).
  2. Determine the sub-element type and present appropriate transform options (translate for points, rotate/translate for edges/faces).
  3. Apply the transform to the sub-element using OpenCASCADE's BRep modification APIs.
  4. Rebuild the parent shape with the modified sub-element.
  5. Update the document object with the new shape.
- **Acceptance**: Select a face on a box → translate it 5mm along its normal → the box deforms, stretching that face outward. Select a vertex → drag it → the surrounding geometry updates.

---

### 21. Extrude a face (Complexity: 5/10)

- **Goal**: Select a face on any solid and extrude it along its normal (or a user-specified direction) to add or remove material.
- **Files to read**:
  - FreeCAD `Part` module — `Part.Face.extrude()`, `Part.Shape.fuse()`, `Part.Shape.cut()`.
  - `FreeCADGui.Selection.getSelectionEx()` — for face selection.
- **Files to create**:
  - `dm_commands/command_extrude_face.py` — face extrusion command.
- **Files to modify**:
  - `InitGui.py` — register the command.
- **Steps**:
  1. Get the selected face from `FreeCADGui.Selection.getSelectionEx()`.
  2. Determine the face normal direction.
  3. Present a dialog or interactive drag to set the extrusion distance (positive = add material, negative = cut).
  4. Extrude the face using `face.extrude(normal * distance)` to create a solid.
  5. Fuse (positive) or cut (negative) the extruded solid with the parent shape.
  6. Update the document object with the resulting shape.
- **Acceptance**: Select a face on a box → extrude 10mm outward → the box gains a protrusion on that face. Extrude inward → material is removed.

---

### 22. Dimensioning for edges, radii, and point-to-point (Complexity: 5/10)

- **Goal**: Add a dimensioning tool that displays measurement annotations in the 3D viewport for edges (length), radii (of arcs/circles), or the distance between two selected points.
- **Files to read**:
  - FreeCAD `Part` sub-element API — `Shape.Edges`, `Shape.Vertexes`, edge `Length`, `Curve.Radius`.
  - FreeCAD Coin3D scene graph API — `SoSeparator`, `SoText2`, `SoTranslation` for annotation display.
  - FreeCAD `TechDraw` or `Draft.makeDimension` for reference on dimension annotation patterns.
- **Files to create**:
  - `dm_commands/command_dimension.py` — dimensioning command.
  - `FCDirectModeling/dimension_display.py` — Coin3D-based annotation rendering.
- **Files to modify**:
  - `InitGui.py` — register the command.
- **Steps**:
  1. On activation, enter a selection mode where the user picks edges, arcs, or pairs of points.
  2. For an **edge**: compute its length, display it as a text annotation near the edge midpoint.
  3. For an **arc/circle edge**: compute the radius, display "R = X" near the arc.
  4. For **two points**: compute the Euclidean distance, display it with a leader line between the points.
  5. Render annotations using Coin3D `SoText2` nodes attached to the scene graph.
  6. Annotations should persist until dismissed or the tool is deactivated.
- **Acceptance**: Select an edge → a dimension label showing its length appears in the viewport. Select a circular edge → radius is displayed. Click two points → the distance between them is shown.

---