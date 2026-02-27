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

### 2. Remove `sdf_mesher.py` and all SDF meshing code [x] (Complexity: 2/10)

- **Goal**: Delete the SDF voxel grid mesher module entirely — it is no longer needed with NURBS primitives.
- **Files to delete**:
  - `FCDirectModeling/sdf_mesher.py`
  - `test_sdf_mesher.py`
  - `test_primitives.py` (if SDF-specific)
- **Files to modify**:
  - `FCDirectModeling/sdf_object.py` — remove `mesh_sdf()` function, `_to_mesh_facets()`, and all imports of `sdf_mesher`.
  - Any file importing from `sdf_mesher` — find with `grep -r "sdf_mesher" .`.
- **Steps**:
  1. Delete the files listed above.
  2. Remove all references to `extract_mesh_numpy`, `mesh_sdf`, `_to_mesh_facets` from the codebase.
  3. Remove the DM Settings for meshing algorithm and resolution (both final and preview) from `command_dm_settings.py`.
- **Acceptance**: `grep -r "sdf_mesher\|mesh_sdf\|extract_mesh_numpy\|MeshAlgorithm\|MeshResolution\|PreviewResolution" .` returns zero results (excluding docs). The workbench loads without errors.

---

### 3. Remove SDF distance functions from `sdf_object.py` [x] (Complexity: 2/10)

- **Goal**: Delete the SDF evaluator functions (`_sdf_box`, `_sdf_sphere`, `_sdf_cone`, `_sdf_torus`, `_sdf_boolean`, `_SDF_BUILDERS`). These are replaced by NURBS builders.
- **Files to read**:
  - `FCDirectModeling/sdf_object.py` — lines 54–140 contain the SDF functions.
- **Files to modify**:
  - `FCDirectModeling/sdf_object.py` — remove lines 54–140 (all `_sdf_*` functions and `_SDF_BUILDERS` dict).
- **Steps**:
  1. Delete all `_sdf_box`, `_sdf_sphere`, `_sdf_cone`, `_sdf_torus`, `_sdf_boolean` function definitions.
  2. Delete the `_SDF_BUILDERS` dictionary.
  3. Remove the `import numpy as np` if no longer needed.
- **Acceptance**: `grep -r "_sdf_box\|_sdf_sphere\|_sdf_cone\|_sdf_torus\|_sdf_boolean\|_SDF_BUILDERS" .` returns zero results. No import errors.

---

### 4. Remove DM Settings for meshing algorithm and resolution (Complexity: 2/10)

- **Goal**: Strip the meshing-specific settings (algorithm dropdown, resolution spinbox, preview resolution) from the DM Settings dialog. Keep the wireframe toggle.
- **Files to read**:
  - `dm_commands/command_dm_settings.py` — the settings dialog implementation.
  - `FCDirectModeling/sdf_object.py` — getter/setter functions for mesh settings.
- **Files to modify**:
  - `dm_commands/command_dm_settings.py` — remove the "Meshing Algorithm" combo box, "Resolution" spinbox, and "Preview Resolution" spinbox.
  - `FCDirectModeling/sdf_object.py` (or the new `dm_object.py`) — remove `get_mesh_algorithm`, `set_mesh_algorithm`, `get_mesh_resolution`, `set_mesh_resolution`, `get_preview_resolution`, `set_preview_resolution`.
- **Steps**:
  1. In the settings dialog, remove the algorithm and resolution widgets from the layout.
### 3. Rename `sdf_object.py` to `dm_object.py` and refactor proxy [x] (Complexity: 3/10)

- **Goal**: Rename the core object module and refactor `SDFObjectProxy` → `DMObjectProxy`, `SDFViewProvider` → `DMViewProvider`. Strip all SDF-related properties and logic.
- **Files to read**:
  - `FCDirectModeling/sdf_object.py` — the full file.
- **Files to modify/create**:
  - Rename `FCDirectModeling/sdf_object.py` → `FCDirectModeling/dm_object.py`.
  - **Every file** that imports from `sdf_object` — find with `grep -r "sdf_object" .`.
- **Steps**:
  1. Rename the file.
  2. Rename `SDFObjectProxy` → `DMObjectProxy`.
  3. Rename `SDFViewProvider` → `DMViewProvider`.
  4. Remove properties: `SDFType`, `SDFParams`, `SDFOp`, `SDFChildren`, `SDFMesh`.
  5. Replace with: `ShapeType` (string), typed properties per primitive (Length, Width, Height, Radius, etc.).
  6. Replace `build_sdf()` with `build_shape()` — returns a `Part.Shape` instead of an SDF function.
  7. Replace `execute()` — calls `build_shape()` and assigns `fp.Shape` directly (no child mesh needed).
  8. Rename `create_sdf_object()` → `create_dm_object()`.
  9. The factory should create a `Part::FeaturePython` with no child `Mesh::Feature` (NURBS shapes render natively).
  10. Update all imports across the codebase.
- **Acceptance**: The workbench loads. Creating a box produces a `Part::FeaturePython` with the orange icon and a visible NURBS shape. No child mesh in the tree view.

---

### 4. Refactor `SDFObjectProxy` to remove legacy properties [x] (Complexity: 2/10)

- **Goal**: The boolean commands (`DM_Fuse`, `DM_Cut`, `DM_Common`) should operate on `Part::Feature` shapes using OpenCASCADE boolean operations instead of SDF composition.
- **Files to read**:
  - `dm_commands/command_boolean.py` — the current boolean command implementations.
- **Files to modify**:
  - `dm_commands/command_boolean.py` — rename internal references from SDF to BRep booleans.
- **Steps**:
  1. Replace the SDF composition logic with `Part.Shape.fuse()`, `.cut()`, `.common()` calls.
  2. Validation: check that selected objects have a `Shape` property (not `SDFType`).
  3. Result: create a new `Part::FeaturePython` with the boolean result shape.
  4. Remove all references to `create_sdf_object`, `SDFType`, `SDFParams`.
- **Acceptance**: Select two NURBS primitives → Fuse → a new boolean result appears as a single `Part::FeaturePython`.

---

### 7. Create NURBS primitive builders (Complexity: 4/10)

- **Goal**: Implement `nurbs_primitives.py` with functions that return `Part.Shape` objects for each primitive type.
- **Files to read**:
  - FreeCAD `Part` module documentation — `Part.makeBox()`, `Part.makeSphere()`, `Part.makeCone()`, `Part.makeTorus()`, and `Part.BSplineSurface`.
- **Files to create**:
  - `FCDirectModeling/nurbs_primitives.py`
- **Steps**:
  1. Implement `build_box(length, width, height) → Part.Shape`.
  2. Implement `build_sphere(radius) → Part.Shape`.
  3. Implement `build_cone(radius, height) → Part.Shape`.
  4. Implement `build_torus(major_r, minor_r) → Part.Shape`.
  5. Each function should return a valid `Part.Shape` centered at the local origin.
  6. Use `Part.makeBox`, `Part.makeSphere`, etc. for initial implementation — these produce BRep shapes that can later be converted to explicit NURBS surfaces if needed.
- **Acceptance**: Each builder returns a valid non-null `Part.Shape`. Shapes display correctly in FreeCAD's 3D view.

---

### 8. Refactor primitive creators to use NURBS instead of SDF (Complexity: 5/10)

- **Goal**: Update `primitives/base.py` and all creator subclasses to produce NURBS shapes instead of SDF meshes during preview and finalization.
- **Files to read**:
  - `FCDirectModeling/primitives/base.py` — `PrimitiveCreatorBase`, `SDFMeshPrimitiveCreator`.
  - `FCDirectModeling/primitives/box_creator.py`, `sphere_creator.py`, `cone_creator.py`, `torus_creator.py`.
- **Files to modify**:
  - `FCDirectModeling/primitives/base.py` — rename `SDFMeshPrimitiveCreator` → `NURBSPrimitiveCreator`. Replace SDF evaluation + meshing with calls to `nurbs_primitives.py` builders.
  - All creator subclasses — update to call `build_box()`, `build_sphere()`, etc. instead of constructing SDF params.
- **Steps**:
  1. In `base.py`, remove all SDF preview queue logic (`_process_preview_queue`, SDF evaluation, mesh assignment).
  2. Replace with: on each drag update, call the appropriate NURBS builder with current dimensions, assign `preview_obj.Shape = shape`.
  3. On finalize (3rd click), call `create_dm_object()` with the final dimensions.
  4. Update each creator subclass to pass dimensions to the NURBS builder instead of constructing SDF param dicts.
- **Acceptance**: Click-drag to create a Box → live NURBS shape preview updates in viewport → on 3rd click, final `Part::FeaturePython` appears with correct dimensions.

---

### 9. Remove `sdf_utils.py` [x] (Complexity: 1/10)

- **Goal**: Delete the SDF utility module. Any needed factory logic moves to `dm_object.py`.
- **Files to delete**:
  - `FCDirectModeling/sdf_utils.py`
- **Files to modify**:
  - Any file importing from `sdf_utils` — find with `grep -r "sdf_utils" .`.
- **Acceptance**: `grep -r "sdf_utils" .` returns zero results. No import errors.

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
  - `FCDirectModeling/primitives/base.py` — understand the event callback pattern for mouse interaction.
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

### 12. Remove legacy `command_draw_box.py` (Complexity: 1/10)

- **Goal**: Delete the legacy draw box command that is no longer used.
- **Files to delete**:
  - `dm_commands/command_draw_box.py`
- **Files to modify**:
  - `InitGui.py` — remove any reference to `DM_DrawBox` if present.
- **Acceptance**: The file is gone. No import errors.

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

### 16. Update `guidelines_prompt.md` for NURBS architecture (Complexity: 1/10)

- **Goal**: Update the documentation generation prompt to reflect the NURBS-based architecture instead of SDF.
- **Files to modify**:
  - `guidelines_prompt.md` — replace all SDF references with NURBS/BRep equivalents.
- **Acceptance**: The prompt generates documentation consistent with the NURBS architecture.

---

### 17. Clean up test files (Complexity: 1/10)

- **Goal**: Remove or rewrite test files that reference the old SDF pipeline.
- **Files to delete**:
  - `test_sdf_mesher.py`
  - `test_primitives.py`
- **Files to create** (optional):
  - `test_nurbs_primitives.py` — basic tests for the NURBS builders.
- **Acceptance**: No test files reference SDF modules. New tests pass.