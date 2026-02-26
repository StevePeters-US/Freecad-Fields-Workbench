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

## Tasks (sorted by complexity, lowest → highest)

---

### BUG-C. Fix boolean commands — property mismatch (Complexity: 2/10)

- **Goal**: The boolean commands (`DM_Fuse`, `DM_Cut`, `DM_Common`) fail with `'Cone' is not an SDFObject` because they check `obj.Proxy.sdf_type` (a Python attribute that doesn't exist) instead of `obj.SDFType` (the FreeCAD property that `SDFObjectProxy` actually creates).
- **Files to read**:
  - `dm_commands/command_boolean.py` line 45 — the validation check `hasattr(obj.Proxy, "sdf_type")`.
  - `FCDirectModeling/sdf_object.py` lines 196–216 — `SDFObjectProxy.__init__` adds `obj.SDFType`, `obj.SDFParams`, `obj.SDFOp`, `obj.SDFChildren` as FreeCAD properties. There is no `self.sdf_type` attribute on the proxy.
- **Files to modify**:
  - `dm_commands/command_boolean.py`
- **Steps**:
  1. Change the validation check on line 45 from:
     ```python
     if not hasattr(obj, "Proxy") or not hasattr(obj.Proxy, "sdf_type"):
     ```
     to:
     ```python
     if not hasattr(obj, "SDFType"):
     ```
     This checks for the FreeCAD property that `SDFObjectProxy` actually creates.
- **Acceptance**: Select two SDF primitives → Fuse/Cut/Common → a new combined mesh object appears without errors.

---

## SDF-Native Architecture

> **Principle**: SDFs are the primary data model. Meshes are *only* for visualization. Every object in the workbench stores its SDF definition (type, params, children, operation) as persistent FreeCAD properties. The mesh is regenerated on demand from the SDF and should never be treated as the source of truth.

The following tasks establish the SDF-native pipeline so that all operations (booleans, transforms, arrays, sketches) compose SDFs — not meshes.

---

### SDF-B. Make booleans compose SDFs, not labels (Complexity: 4/10)

- **Goal**: Boolean objects should store references to child *objects* (not label strings) and compose their SDF functions at evaluation time. Currently `_sdf_boolean` in `execute()` looks up children by label, which breaks on rename/duplicate.
- **Depends on**: SDF-A (needs `build_sdf()`).
- **Files to read**:
  - `FCDirectModeling/sdf_object.py` — `SDFObjectProxy.execute()` lines 240–250: children are looked up by `doc.getObjectsByLabel(n)`.
  - `dm_commands/command_boolean.py` — passes `child_names = [sel[0].Label, sel[1].Label]`.
- **Files to modify**:
  - `FCDirectModeling/sdf_object.py` — change `SDFChildren` from `PropertyStringList` (labels) to `App::PropertyLinkList` (object references). Update `execute()` to use links.
  - `dm_commands/command_boolean.py` — pass object references instead of labels.
- **Steps**:
  1. Replace `SDFChildren` property type with `App::PropertyLinkList`.
  2. In `execute()`, iterate `fp.SDFChildren` directly (they are now object references).
  3. Call `child.Proxy.build_sdf(child)` on each child to get their SDF functions.
  4. Compose with `_sdf_boolean`.
- **Acceptance**: Create two boxes → Fuse → rename one child → recompute the boolean → it still works. The boolean SDF is composed from live child SDFs, not string lookups.

---

### SDF-C. Parametric editing — modify SDF params and re-mesh (Complexity: 4/10)

- **Goal**: When a user changes an SDF property (e.g. `SDFParams`) in the property panel, the object automatically re-meshes. This is already partially handled by `execute()`, but the UI should make it easy to tweak individual SDF parameters (e.g. box width) without re-creating the object.
- **Files to read**:
  - `FCDirectModeling/sdf_object.py` — `SDFObjectProxy.execute()`, the property definitions in `__init__`.
- **Files to modify**:
  - `FCDirectModeling/sdf_object.py` — ensure `execute()` properly re-reads params on recompute.
  - Consider adding typed sub-properties (e.g. `BoxWidth`, `BoxHeight`) instead of a single JSON blob, so the FreeCAD property panel shows editable fields.
- **Steps**:
  1. For each primitive type, add dedicated properties (e.g. `App::PropertyFloat` for `BoxWidth`, `BoxHeight`, `BoxDepth`) so they appear as editable fields in FreeCAD's property panel.
  2. In `execute()`, read from these typed properties and rebuild the SDF.
  3. Changes to any property trigger `execute()` automatically via FreeCAD's dependency engine.
- **Acceptance**: Create a box → change `BoxWidth` in the property panel → the mesh updates.

---

### SDF-D. SDF tree visualization in model tree (Complexity: 5/10)

- **Goal**: Boolean and transform objects should show their children as a tree in FreeCAD's model browser, so the user can see the SDF composition hierarchy (e.g. "Fuse" → "Box" + "Sphere").
- **Depends on**: SDF-B (needs `PropertyLinkList` children).
- **Files to read**:
  - `FCDirectModeling/sdf_object.py` — `SDFViewProvider`.
- **Files to modify**:
  - `FCDirectModeling/sdf_object.py` — implement `claimChildren()` in `SDFViewProvider` to return the child objects so FreeCAD nests them visually.
- **Steps**:
  1. In `SDFViewProvider`, add:
     ```python
     def claimChildren(self):
         if hasattr(self.Object, "SDFChildren"):
             return list(self.Object.SDFChildren)
         return []
     ```
  2. Boolean children will now appear nested under their parent in the model tree.
- **Acceptance**: Create two boxes → Fuse → the model tree shows "SDF_Fuse" with "Box" and "Box001" nested underneath.

---

### 1. Add sharp-features toggle to DM Settings (Complexity: 2/10)

- **Goal**: Let the user toggle QEF sharp-feature snapping on/off from DM Settings.
- **Files to read**:
  - `dm_commands/command_dm_settings.py` — existing checkbox pattern (see "Show Wireframe" checkbox on line 65).
  - `FCDirectModeling/sdf_object.py` lines 38–43 — existing get/set boolean pattern (`get_show_wireframe`).
  - `FCDirectModeling/sdf_object.py` `mesh_sdf()` function (lines 139–166) — the `sharp` parameter is passed to `extract_mesh_numpy`.
  - `FCDirectModeling/sdf_mesher.py` `extract_mesh_numpy(sdf_func, mn, mx, resolution, sharp=True)` — `sharp=True` enables QEF.
- **Files to modify**:
  - `FCDirectModeling/sdf_object.py` — add `get_sharp_features()` / `set_sharp_features()`, wire into `mesh_sdf()`.
  - `dm_commands/command_dm_settings.py` — add a `QCheckBox` for "Sharp Features (QEF)".
- **Steps**:
  1. In `sdf_object.py`, add getter/setter (default `True`):
     ```python
     def get_sharp_features():
         return FreeCAD.ParamGet(_PARAM_PATH).GetBool("SharpFeatures", True)
     def set_sharp_features(enabled):
         FreeCAD.ParamGet(_PARAM_PATH).SetBool("SharpFeatures", bool(enabled))
     ```
  2. In `mesh_sdf()`, replace the hardcoded `sharp=False` / `sharp=True` logic. Instead read `sharp = get_sharp_features()` and pass to `extract_mesh_numpy`.
  3. In the settings dialog, add a checkbox modelled after the wireframe checkbox.
- **Acceptance**: Toggle "Sharp Features" off → create a Box → edges appear rounded (Surface Nets default). Toggle on → edges are sharp.

---

### 2. Implement Marching Cubes in `sdf_mesher.py` (Complexity: 4/10)

- **Goal**: Add a real Marching Cubes implementation so the "Marching Cubes" option in DM Settings produces distinct output from Surface Nets.
- **Files to read**:
  - `FCDirectModeling/sdf_mesher.py` — the entire file (224 lines). Understand how `extract_mesh_numpy` evaluates the SDF on a 3D grid, detects active voxels, and generates vertices/triangles. Your new function must return the **same format**: `(verts_ndarray_Nx3, tris_ndarray_Mx3)`.
  - `FCDirectModeling/sdf_object.py` `mesh_sdf()` function (lines 139–166) — this is the dispatcher. Currently all three algorithm branches call `extract_mesh_numpy`. You will add a branch for `"marching_cubes"` → `marching_cubes()`.
- **Files to modify**:
  - `FCDirectModeling/sdf_mesher.py` — add `marching_cubes(sdf_func, mn, mx, resolution)` function.
  - `FCDirectModeling/sdf_object.py` — update `mesh_sdf()` to call the new function.
- **Steps**:
  1. In `sdf_mesher.py`, add the 256-entry edge table and triangle table as module-level constants. These are standard Marching Cubes lookup tables (see Paul Bourke's tables or the public domain tables from `scikit-image`).
  2. Implement `marching_cubes(sdf_func, mn, mx, resolution)`:
     - Build a 3D grid from `mn` to `mx` with `resolution` steps per axis (same grid logic as `extract_mesh_numpy` lines 22–49).
     - Evaluate `sdf_func(X, Y, Z)` on the grid to get a 3D value array `V`.
     - For each cell (8 corners), compute a cube index from the sign of each corner.
     - Look up the edge table to find which edges have crossings.
     - Interpolate vertex positions along those edges using the two endpoint SDF values.
     - Look up the triangle table to connect those vertices into triangles.
     - Deduplicate vertices that share the same grid edge.
     - Return `(np.array(verts), np.array(triangles))`.
  3. In `sdf_object.py` `mesh_sdf()`, replace the `"marching_cubes"` branch:
     ```python
     if algorithm == "marching_cubes":
         from .sdf_mesher import marching_cubes
         res = marching_cubes(sdf_fn, bounds_min, bounds_max, resolution)
     ```
- **Acceptance**: Open DM Settings → select "Marching Cubes" → create a Box → verify a mesh appears. Compare visually with "Surface Nets" — Marching Cubes should look smoother on curves but less sharp on edges.

---

### 3. Fix the boolean commands to work with current `SDFObjectProxy` (Complexity: 4/10)

- **Goal**: The boolean commands (`DM_Fuse`, `DM_Cut`, `DM_Common`) currently fail because `command_boolean.py` line 45 checks `hasattr(obj.Proxy, "sdf_type")`, but `SDFObjectProxy` stores type in FreeCAD properties (`obj.SDFType`), not as a Python attribute.
- **Files to read**:
  - `dm_commands/command_boolean.py` — the entire file (86 lines). The `Activated` method at line 35 validates selection and calls `create_sdf_object`.
  - `FCDirectModeling/sdf_object.py` — `SDFObjectProxy.__init__` (line 189) adds properties `SDFType`, `SDFParams`, `SDFOp`, `SDFChildren` to the FreeCAD object. There is no `self.sdf_type` attribute on the proxy.
- **Files to modify**:
  - `dm_commands/command_boolean.py`
- **Steps**:
  1. Change the validation check on line 45 from:
     ```python
     if not hasattr(obj, "Proxy") or not hasattr(obj.Proxy, "sdf_type"):
     ```
     to:
     ```python
     if not hasattr(obj, "SDFType"):
     ```
     This checks for the FreeCAD property that `SDFObjectProxy` actually creates.
  2. Test by creating two Box primitives, selecting both, and running DM_Fuse. The result should be a new mesh combining both boxes.
- **Acceptance**: Select two SDF primitives → Fuse/Cut/Common → a new combined mesh object appears in the document without errors.

---

### 4. Create task panels for Sphere, Cone, and Torus (Complexity: 4/10)

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

### 6. Implement Dual Contouring in `sdf_mesher.py` (Complexity: 6/10)

- **Goal**: Add a true Dual Contouring algorithm so the "Dual Contouring" option in DM Settings produces distinct output from Surface Nets.
- **Files to read**:
  - `FCDirectModeling/sdf_mesher.py` — understand `extract_mesh_numpy` (Surface Nets + QEF). Dual Contouring is similar but places **one vertex per cell** via full QEF solve, then connects cells sharing a sign-change edge with quads (split into 2 tris).
  - `FCDirectModeling/sdf_object.py` `mesh_sdf()` — the dispatcher.
- **Files to modify**:
  - `FCDirectModeling/sdf_mesher.py` — add `dual_contouring(sdf_func, mn, mx, resolution)`.
  - `FCDirectModeling/sdf_object.py` — update `mesh_sdf()` for `"dual_contouring"` branch.
- **Steps**:
  1. Build the same 3D grid as `extract_mesh_numpy`.
  2. Evaluate SDF on grid corners. Detect active cells (sign change among 8 corners).
  3. For each active cell, find zero-crossings on the 12 edges (same interpolation as `extract_mesh_numpy`).
  4. At each crossing, compute the SDF gradient (normal) via central differences.
  5. Solve QEF: minimize `∑ (nᵢ · (x - pᵢ))²` where `pᵢ` are crossing points and `nᵢ` are normals. Use `np.linalg.lstsq`. Clamp result to cell bounds.
  6. For face-connectivity: for each internal face between two adjacent cells, if the face's 4 edges have a sign change, emit a quad connecting the 4 cell vertices sharing that face. Split each quad into 2 triangles.
  7. Return `(verts, tris)` in the same format.
  8. Wire into `mesh_sdf()` as a new branch.
- **Acceptance**: Select "Dual Contouring" in DM Settings → create a Box → sharp edges should be preserved. Compare with Surface Nets (smoother edges when sharp=False) and Marching Cubes (staircase artifacts on edges).

---

### 7. Mesh to SDF voxelization (Complexity: 6/10)

- **Goal**: Given an imported mesh (STL, OBJ, or existing `Mesh::Feature`), compute a signed distance field that can be used with the existing SDF pipeline.
- **Files to read**:
  - `FCDirectModeling/sdf_object.py` — the `_SDF_BUILDERS` dict (line 127) and `mesh_sdf()` to understand the expected SDF function signature: `sdf(X, Y, Z) → numpy array of distances`.
  - `FCDirectModeling/mesh_features.py` — has mesh utilities (adjacency, face normals) that may be useful.
- **Files to create**:
  - `FCDirectModeling/mesh_to_sdf.py`
  - `dm_commands/command_mesh_to_sdf.py`
- **Steps**:
  1. In `mesh_to_sdf.py`, implement `mesh_to_sdf(mesh_obj) → sdf_func`:
     - Extract vertices and faces from the FreeCAD `Mesh::Feature`.
     - Build a KD-tree from the mesh faces using `scipy.spatial.KDTree` (or manual approach with NumPy).
     - For each query point `(X, Y, Z)`, compute the unsigned distance to the nearest triangle.
     - Determine the sign using the angle-weighted pseudo-normal method: at the closest point, compute the dot product of `(query - closest)` with the surface normal. Negative = inside.
     - Return a closure `sdf(X, Y, Z)` that evaluates this.
  2. In `command_mesh_to_sdf.py`, create a `DM_MeshToSDF` command:
     - Validate selection is a `Mesh::Feature`.
     - Call `mesh_to_sdf()` to get the SDF function.
     - Create an `SDFObjectProxy` that stores the SDF for later boolean composition.
  3. Register the command in `InitGui.py` toolbar.
- **Acceptance**: Import an STL → select it → run "Mesh to SDF" → a new SDF object appears that can be booleaned with other SDF primitives.

---

### 8. SDF to NURBS patches (replacing BRep) (Complexity: 7/10)

- **Goal**: Instead of converting SDFs to BRep (boundary representation with exact analytic faces), convert them to **NURBS surface patches**. This produces smooth, resolution-independent surfaces suitable for CAD export.
- **Files to read**:
  - `FCDirectModeling/surface_fitting.py` — existing primitive fitting (plane, sphere, cylinder, cone). The `segment_mesh` + `best_fit` pipeline segments a mesh into regions and fits analytic primitives.
  - `FCDirectModeling/mesh_features.py` — `segment_mesh()`, `compute_face_normals()`, `compute_dihedral_angles()`.
  - `FCDirectModeling/sdf_object.py` — `SDFObjectProxy.execute()` produces `(verts, tris)`.
- **Files to create**:
  - `FCDirectModeling/sdf_to_nurbs.py`
  - `dm_commands/command_sdf_to_nurbs.py`
- **Steps**:
  1. In `sdf_to_nurbs.py`:
     - **Segment** the mesh into smooth patches using `mesh_features.segment_mesh`.
     - **Sample** each patch: extract the boundary vertices and interior vertices.
     - **Fit NURBS**: Use FreeCAD's `Part.BSplineSurface` to fit each patch. Approximate:
       - Compute a parameterization (e.g., Floater mean-value or conformal) for the patch vertices in (u,v) space.
       - Build a `Part.BSplineSurface` via `buildFromPolesMultsKnots` or `approximate()` fitting the 3D points.
     - **Stitch**: Collect all NURBS patches into a `Part.Compound` or `Part.Shell`.
  2. In `command_sdf_to_nurbs.py`:
     - Validate selection is an SDF object.
     - Re-mesh at high resolution.
     - Call the fitting pipeline.
     - Create a `Part::Feature` with the resulting NURBS compound.
  3. Register in `InitGui.py`.
- **Acceptance**: Create a Sphere SDF → run "SDF to NURBS" → a `Part::Feature` with smooth NURBS patches appears. Export to STEP → re-import → surfaces are smooth, not faceted.

---

### 9. SDF to Curves — feature edge extraction (Complexity: 7/10)

- **Goal**: Extract feature curves (sharp edges, ridges) from an SDF mesh and represent them as `Part.BSplineCurve` objects in FreeCAD.
- **Files to read**:
  - `FCDirectModeling/mesh_features.py` — `build_adjacency()`, `compute_dihedral_angles()`, `segment_mesh()`.
  - `FCDirectModeling/surface_fitting.py` — may be used to identify flat/curved regions.
  - `FCDirectModeling/sdf_object.py` — to get `(verts, tris)` from an existing SDF object.
- **Files to create**:
  - `FCDirectModeling/curve_extraction.py`
  - `dm_commands/command_extract_curves.py`
- **Steps**:
  1. Detect sharp edges:
     - Compute face normals with `compute_face_normals(verts, faces)`.
     - Build adjacency with `build_adjacency(faces)`.
     - Compute dihedral angles with `compute_dihedral_angles(normals, adjacency)`.
     - Mark edges where the dihedral angle > threshold (e.g., 30°) as feature edges.
  2. Chain feature edges into polylines (ordered sequences of connected vertices).
  3. Fit B-splines to each polyline using `Part.BSplineCurve().interpolate(points)`.
  4. Create a `Part::Feature` containing the curves as `Part.Wire` or `Part.Compound`.
  5. Register a `DM_ExtractCurves` command.
- **Acceptance**: Create a Box SDF → run "Extract Curves" → 12 B-spline edges appear outlining the box.

---

### 10. BMesh to SDF (import external mesh as SDF) (Complexity: 7/10)

- **Goal**: Same as task 9 (Mesh to SDF) but specifically handling Blender BMesh data imported via the Live Link, and supporting non-manifold or open meshes gracefully.
- **Files to read**: Same as task 9 plus any Live Link import scripts if present.
- **Note**: This task depends on task 9. Build on the `mesh_to_sdf.py` module. Add handling for:
  - Non-manifold edges (fallback to flood-fill sign determination).
  - Open meshes (unsigned distance only, or user-specified sign convention).
  - Large meshes (octree acceleration for the KD-tree).
- **Acceptance**: Import a Blender BMesh export → convert to SDF → boolean it with a Box SDF.

---

### 11. Array command — SDF-level repetition (Complexity: 7/10)

- **Goal**: Repeat an SDF object along a vector, circular pattern, or grid, all at the SDF level (not duplicating meshes).
- **Files to read**:
  - `FCDirectModeling/sdf_object.py` — understand how `_sdf_boolean` composes child SDFs. Array works similarly: translate the query point before evaluating the child SDF, then union the results.
- **Files to create**:
  - `dm_commands/command_array.py`
- **Files to modify**:
  - `FCDirectModeling/sdf_object.py` — add `_sdf_array(params, child_sdf)` builder.
  - `InitGui.py` — register the new command.
- **Steps**:
  1. Implement `_sdf_array(params, child_sdf)`:
     - `params` contains: `count`, `offset` (vector), `pattern` ("linear" | "circular" | "grid").
     - For linear: evaluate `child_sdf(X - i*offset[0], Y - i*offset[1], Z - i*offset[2])` for each `i`, take `np.minimum` across all.
     - For circular: rotate the query point by `i * (360/count)` degrees around an axis before evaluating.
  2. Create a dialog in `command_array.py` with inputs for count, offset, and pattern.
  3. Create the result via `create_sdf_object` with `sdf_type="array"`.
- **Acceptance**: Create a Sphere → Array it 5 times along X with spacing 3 → a row of 5 spheres appears as a single SDF mesh.

---

### 12. Transform command — SDF-level translate/rotate/scale (Complexity: 5/10)

- **Goal**: Apply translate / rotate / scale to an SDF object at the SDF level (transforming the query point), not by moving the mesh.
- **Files to read**:
  - `FCDirectModeling/sdf_object.py` — `_SDF_BUILDERS`, `SDFObjectProxy.execute()`.
- **Files to create**:
  - `dm_commands/command_transform.py`
- **Files to modify**:
  - `FCDirectModeling/sdf_object.py` — add `_sdf_transform(params, child_sdf)`.
  - `InitGui.py` — register command.
- **Steps**:
  1. `_sdf_transform` wraps a child SDF: apply inverse transform to query point before evaluating.
     - Translate: `sdf(X - tx, Y - ty, Z - tz)`.
     - Rotate: build a rotation matrix, multiply `[X,Y,Z]` by its inverse.
     - Scale: `sdf(X/sx, Y/sy, Z/sz) * min(sx, sy, sz)` (scale the distance too).
  2. Create a dialog with translate/rotate/scale inputs.
  3. Store as `sdf_type="transform"` with child reference.
- **Acceptance**: Create a Box → Transform (rotate 45° around Z) → the mesh updates to show the rotated box.

---

### 13. Sketch-driven SDF extrusion (Complexity: 8/10)

- **Goal**: After the user closes the Sketcher, convert the sketch profile into a 2D SDF and extrude it into a 3D SDF object.
- **Files to read**:
  - `dm_commands/command_open_sketcher.py` — launches the Sketcher.
  - `FCDirectModeling/sdf_object.py` — SDF builder pattern.
- **Files to create**:
  - `FCDirectModeling/sketch_to_sdf.py`
  - `dm_commands/command_sketch_extrude.py`
- **Steps**:
  1. Read the `Sketcher::SketchObject` wire: extract edges as polylines.
  2. Convert to 2D SDF: for each query point `(x, y)`, compute signed distance to the polygon boundary (positive outside, negative inside).
  3. Extrude: `sdf_3d(X, Y, Z) = max(sdf_2d(X, Y), abs(Z) - height/2)`.
  4. Register as `_sdf_extrusion` in `_SDF_BUILDERS`.
- **Acceptance**: Draw a sketch → run extrude → a 3D SDF extrusion appears matching the sketch profile.

---

### 14. 2D contour extraction from SDF (Complexity: 8/10)

- **Goal**: Walk the SDF zero-crossing on a 2D slice, producing Bézier or B-spline curves.
- **Files to read**:
  - `FCDirectModeling/sdf_mesher.py` — grid evaluation and sign-change detection.
  - `FCDirectModeling/sdf_object.py` — SDF function signatures.
- **Files to create**:
  - `FCDirectModeling/contour_extraction.py`
- **Steps**:
  1. Evaluate the SDF on a 2D grid (fixing one axis, e.g. Z=z₀).
  2. Detect cells where corners change sign.
  3. Interpolate crossing points on cell edges.
  4. Chain crossings into ordered polylines (marching squares).
  5. Fit `Part.BSplineCurve` to each polyline.
  6. Return a list of `Part.Edge` objects.
- **Acceptance**: Given a sphere SDF sliced at Z=0 → produces a circle as a B-spline curve.


### add tools for drawing 3d curves and nurbs surfaces