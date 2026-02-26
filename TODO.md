# FreeCAD Direct Modeling — TODO

> Tasks are grouped by difficulty. Each task is scoped so a single LLM session
> (or developer) can pick it up with minimal context. Start from the top.

---

## 🟢 Easy (single-file, well-defined scope)

### SDF Rendering — Marching Cubes Implementation
- [ ] **Implement Marching Cubes in `sdf_mesher.py`**
  - Add a `marching_cubes(sdf_values_3d, spacing)` function alongside `extract_mesh_numpy`.
  - Input: 3D NumPy array of SDF values evaluated on a regular grid + grid spacing.
  - Output: `(verts, tris)` — same format as `extract_mesh_numpy`.
  - Use the classic 256-entry edge table and tri table (can be embedded as constants).
  - Interpolate vertex positions along edges using the two SDF values at each edge endpoint.
  - Reference: Paul Bourke's Marching Cubes tables.

- [ ] **Wire Marching Cubes into the `mesh_sdf()` dispatcher in `sdf_object.py`**
  - In the `mesh_sdf()` function, add a branch for `algorithm == "marching_cubes"`.
  - Call the new `marching_cubes()` from `sdf_mesher.py`.
  - Ensure the resolution parameter maps to grid dimensions the same way as Surface Nets.

- [ ] **Verify Marching Cubes via DM Settings**
  - Open DM Settings, select "Marching Cubes", create a Box primitive.
  - Confirm the preview mesh and final mesh render correctly.
  - Check that switching algorithms back to "Surface Nets" still works.

### DM Settings Enhancements
- [ ] **Add preview-resolution setting to DM Settings dialog**
  - In `command_dm_settings.py`, add a second `QSpinBox` for "Preview Resolution" (range 8–32, default 15).
  - Store/retrieve via `FreeCAD.ParamGet("User parameter:FCDirectModeling")` using key `"PreviewResolution"`.
  - Add getter/setter pair (`get_preview_resolution` / `set_preview_resolution`) in `sdf_object.py`.
  - Update `SDFMeshPrimitiveCreator.update_sdf_preview()` in `base.py` to use this value instead of the hardcoded `resolution=20`.

- [ ] **Add sharp-features toggle to DM Settings**
  - Add a `QCheckBox` "Sharp Features (QEF)" in the settings dialog, default on.
  - Store as `"SharpFeatures"` parameter (bool).
  - Pass through to `extract_mesh_numpy(..., sharp=value)` via `mesh_sdf()`.

### Logging Cleanup
- [ ] **Audit all files for bare `print()` or `FreeCAD.Console.Print*` calls**
  - Replace with `sdf_logger.debug/info/warn/error` as appropriate.
  - Files to check: `mesh_features.py` (uses `print()`), `surface_fitting.py`, `sdf_utils.py`, `dm_part.py`.

---

## 🟡 Medium (multi-file, moderate complexity)

### SDF Rendering — Dual Contouring
- [ ] **Implement Dual Contouring in `sdf_mesher.py`**
  - Add a `dual_contouring(sdf_func, mn, mx, resolution)` function.
  - For each voxel cell, find zero-crossings on all 12 edges.
  - Compute Hermite data (position + gradient) at each crossing.
  - Solve the QEF (Quadratic Error Function) per cell to place the vertex.
  - Connect adjacent cells sharing a sign-change edge to form quads/tris.
  - Output: `(verts, tris)` matching the existing format.

- [ ] **Wire Dual Contouring into `mesh_sdf()` in `sdf_object.py`**
  - Add the `"dual_contouring"` branch (currently it falls through to Surface Nets).
  - Ensure bounding-box padding and resolution handling are consistent.

### Primitive Stabilization
- [ ] **Fix Cone rendering** — `cone_creator.py` does not produce a valid mesh; debug the SDF function `_sdf_cone` in `sdf_object.py` and the cone's `handle_move` / `handle_click` in `cone_creator.py`.
- [ ] **Fix Torus rendering** — similar to Cone; verify `_sdf_torus` parameters and creator interaction.
- [ ] **Standardize all primitive TaskPanels** — Box has `box_task_panel.py`; create equivalent panels for Sphere, Cone, and Torus with matching dimension spinboxes and accept/cancel buttons.

### Mesh Preview Reliability
- [ ] **Fix viewport update on `.Mesh` replacement**
  - In `_process_preview_queue()` (`base.py`), after assigning `.Mesh`, schedule a deferred `doc.recompute()` via `QTimer.singleShot(0, ...)`.
  - Guard against re-entrancy (skip if a recompute is already pending).
  - Verify across Box, Sphere, Cone, Torus.

### Boolean Operations
- [ ] **Implement SDF boolean composition end-to-end**
  - `_sdf_boolean` in `sdf_object.py` already handles fuse/cut/common at the SDF level.
  - Wire the boolean commands (`command_boolean.py`) to create a new `Mesh::FeaturePython` with `SDFObjectProxy` that takes two children.
  - Ensure the result meshes correctly with the selected algorithm.

---

## 🔴 Hard (architectural, multi-module, research-heavy)

### SDF to Curves (Feature Extraction)
- [ ] **2D contour extraction**
  - In `mesh_features.py` or a new `curve_extraction.py`, implement adaptive contouring.
  - Walk the SDF zero-crossing on a 2D slice, fitting Bézier segments.
  - Output: list of `Part.BSplineCurve` or `Part.Edge` objects.

- [ ] **3D feature-edge extraction**
  - From the meshed surface, detect sharp edges (dihedral angle > threshold) using `mesh_features.py`'s `compute_dihedral_angles`.
  - Chain sharp edges into polylines / fit B-splines.
  - Expose as a `DM_ExtractCurves` command.

### SDF to BRep Conversion
- [ ] **Level-set → CSG tree approximation**
  - Fit the SDF with a hierarchy of analytic primitives (plane, sphere, cylinder, cone) using `surface_fitting.py`.
  - Segment the mesh (`segment_mesh`), fit each patch, then reconstruct a `Part::Compound` or `Part::Boolean`.
  - This is the path to STEP/IGES export without faceting.

### BMesh to SDF
- [ ] **Mesh → SDF voxelization**
  - Given an imported mesh (STL, OBJ), compute a signed distance field.
  - Approach: voxelize the mesh, compute unsigned distance via KD-tree, determine sign via ray-casting or angle-weighted pseudo-normals.
  - Output: a callable `sdf_func(X, Y, Z)` compatible with the existing pipeline.

### Array & Transform Operations
- [ ] **Array command** — repeat an SDF object along a vector, circular pattern, or grid. Operate at the SDF level (translate the query point) rather than duplicating meshes.
- [ ] **Transform command** — apply translate / rotate / scale to an SDF object's parameters, updating the mesh via recompute.

### New Sketch Integration
- [ ] **Sketch-driven SDF extrusion**
  - After the user closes the Sketcher, read the resulting `Sketcher::SketchObject` wire.
  - Convert the 2D profile to an SDF (e.g., signed distance from polygon).
  - Extrude along the sketch normal to create a 3D SDF object.

---

## 🔧 Infrastructure / Continuous

- [ ] **Unit tests** — expand `test_primitives.py` and `test_sdf_mesher.py` with coverage for all algorithms and all primitive SDF functions.
- [ ] **CI** — set up GitHub Actions to run headless FreeCAD tests on push.
- [ ] **Icons** — create or commission SVG icons for all toolbar commands (currently some use FreeCAD stock icons).
- [ ] **README update** — keep `README.md` in sync with actual project structure and features.
- [ ] **Documentation** — auto-generate API docs from docstrings (Sphinx or pdoc).
