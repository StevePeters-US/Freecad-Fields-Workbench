# todo_openvdb.md — OpenVDB Sparse Tree Migration

Read `.agents/skills/dm_openvdb_migration/SKILL.md` before starting any task.

**Motivation**: Replace our entire dense numpy SDF pipeline with OpenVDB sparse level-set trees. The current architecture evaluates `O(n^3)` grid points for baking, meshing, and boolean ops — all points, not just near the surface. OpenVDB stores only a narrow band around the surface (typically 3-5 voxel widths), giving orders-of-magnitude memory savings and enabling sub-0.1mm voxel sizes for machinable accuracy. Blender uses the same approach for its geometry-nodes volume pipeline.

**This is a full replacement, not a dual pipeline.** The old dense numpy baking, GLSL compute shader baking, and hand-rolled marching cubes are all removed. VDB becomes the single path for baking, preview, meshing, and export.

**Target accuracy**: 0.05mm voxel size for final export, 0.5-2mm for interactive preview.

**Dependency**: `pyopenvdb` — required (not optional). Available via `pip install pyopenvdb` or bundled with FreeCAD's Python on some distros. Fallback: build from source (cmake + boost + tbb).

---

## Phase 1: Foundation — VDB Grid Infrastructure

### Task VDB-001: Install and verify pyopenvdb `Gemini Flash`

- **Goal**: Confirm pyopenvdb imports and basic operations work in the FreeCAD Python environment
- **Files to read**: `AGENTS.md`, `core/sdf/sdf_field.py`
- **Files to modify**: `tests/test_openvdb_basic.py` (new)
- **Steps**:
  1. Create `tests/test_openvdb_basic.py` that imports `openvdb`, creates a `FloatGrid`, sets a few voxels, reads them back
  2. Test `openvdb.tools.createLevelSetSphere(radius=10.0, center=(0,0,0), voxelSize=0.5)` — confirm it returns a FloatGrid with sparse active voxels
  3. Test `grid.evalActiveVoxelBoundingBox()` returns expected index bounds
  4. Test `openvdb.tools.volumeToMesh(grid, isovalue=0.0)` returns vertices and quads
  5. Print active voxel count vs dense equivalent to confirm sparsity
- **Acceptance**: Script runs without errors, prints voxel count showing >90% sparsity for a sphere

---

### Task VDB-002: Add `to_vdb()` method to `SdfField` base class `Gemini Flash`

- **Goal**: Each SdfField produces an OpenVDB FloatGrid. Base class provides a generic narrow-band sampling implementation; primitives override with native VDB constructors where available.
- **Files to read**: `core/sdf/sdf_field.py`, `core/sdf/sdf/sphere.py`, `core/sdf/sdf/box.py`, `core/sdf/sdf/cylinder.py`
- **Files to modify**: `core/sdf/sdf_field.py`, `core/sdf/sdf/sphere.py`, `core/sdf/sdf/box.py`, `core/sdf/sdf/cylinder.py`
- **Steps**:
  1. In `SdfField`, add method:
     ```python
     def to_vdb(self, voxel_size=0.5, half_width=3.0):
         """Convert this field to an OpenVDB FloatGrid level set.

         Args:
             voxel_size: Grid spacing in mm (default 0.5mm for preview, use 0.05 for export)
             half_width: Narrow band half-width in voxels (default 3.0)
         Returns:
             openvdb.FloatGrid with grid class LEVEL_SET
         """
     ```
  2. Base implementation: call `bounding_box()`, create a dense numpy grid over the bbox padded by `half_width * voxel_size`, call `evaluate_grid()`, then use `openvdb.FloatGrid.copyFromArray()` and flood-fill to establish the level set.
  3. In `SdfSphereField`, override `to_vdb()` to use `openvdb.tools.createLevelSetSphere(radius, center, voxelSize, halfWidth)` — this is exact and fast.
  4. In `SdfBoxField` and `SdfCylinderField`, use the base class implementation (no native VDB constructors for arbitrary boxes/cylinders).
  5. All grids must have `grid.transform` set so that index-space maps to world-space mm coordinates: `grid.transform = openvdb.createLinearTransform(voxelSize)` and `grid.transform.translate(offset)`.
- **Acceptance**: `SdfSphereField(center, radius).to_vdb(0.5)` returns a FloatGrid; `grid.evalActiveVoxelBoundingBox()` corresponds to the sphere's bbox; active voxel count << dense grid size.

---

### Task VDB-003: VDB boolean operations in composer `Gemini Flash`

- **Goal**: Replace the numpy min/max boolean path with VDB-native CSG. The `evaluate()` and `evaluate_grid()` methods on ComposerField are replaced with VDB operations.
- **Files to read**: `core/sdf/sdf_composer.py`, `core/sdf/sdf_field.py`
- **Files to modify**: `core/sdf/sdf_composer.py`
- **Steps**:
  1. Add `to_vdb()` overrides to `UnionField`, `IntersectionField`, `SubtractionField`:
     ```python
     class UnionField(ComposerField):
         def to_vdb(self, voxel_size=0.5, half_width=3.0):
             grid_a = self.a.to_vdb(voxel_size, half_width)
             grid_b = self.b.to_vdb(voxel_size, half_width)
             openvdb.tools.csgUnion(grid_a, grid_b)  # modifies grid_a in-place
             return grid_a
     ```
  2. `IntersectionField.to_vdb()` → `openvdb.tools.csgIntersection(grid_a, grid_b)`
  3. `SubtractionField.to_vdb()` → `openvdb.tools.csgDifference(grid_a, grid_b)`
  4. Keep `evaluate()` / `evaluate_grid()` for now — they're still used by `SdfField.ray_march()` for CPU hit-testing. These will be removed in Phase 5 when hit-testing moves to VDB.
- **Acceptance**: Create a union of two spheres, call `.to_vdb()`, mesh the result with `volumeToMesh` — verify the mesh shows two merged spheres.

---

## Phase 2: Meshing Pipeline

### Task VDB-004: Replace MarchingCubesMesher with VDB mesher `Gemini Flash`

- **Goal**: Delete `MarchingCubesMesher` and all hand-rolled MC table code. Replace with `VDBMesher` using `openvdb.tools.volumeToMesh()`.
- **Files to read**: `core/dm_mesher.py` (full file), `commands/cmd_sdf_export.py`
- **Files to modify**: `core/dm_mesher.py`
- **Steps**:
  1. Delete `MarchingCubesMesher`, `AdaptiveMCMesher` (if exists), all MC lookup tables (`_EDGE_TABLE`, `_TRI_TABLE`, `_EDGE_C1`, `_EDGE_C2`, `_OFF_X/Y/Z`), and SurfaceNets code.
  2. Delete `core/sdf/marching_cubes/` directory entirely (MC tables).
  3. Replace with `VDBMesher`:
     ```python
     class VDBMesher:
         def mesh(self, field, cell_size, adaptivity=0.0):
             """Mesh an SdfField using OpenVDB level-set meshing.
             Returns (flat_verts, flat_idx) for Part.Shape construction.
             """
             grid = field.to_vdb(voxel_size=cell_size)
             points, quads = openvdb.tools.volumeToMesh(grid, isovalue=0.0, adaptivity=adaptivity)
             # Convert quads to triangles for Part.Shape compatibility
             # Each quad (a,b,c,d) → two triangles (a,b,c) + (a,c,d)
             ...
     ```
  4. The return format: `(flat_verts: float32 (N,3), flat_idx: int32 (M,4))` where column 3 of flat_idx is the face-vertex count (3 for triangles).
  5. `volumeToMesh` returns index-space coordinates — multiply by `voxel_size` and add the grid transform offset to convert back to world-space mm.
  6. Update `get_active_mesher()` to return `VDBMesher()` unconditionally.
  7. Keep `MeshTimer` — just update stage names for VDB stages.
- **Acceptance**: Export a box SDF at 0.1mm cell size → resulting `Part.Shape` is watertight, face count is reasonable, dimensions match the analytic bbox within 0.15mm.

---

### Task VDB-005: Adaptivity parameter for VDB meshing `Gemini Flash`

- **Goal**: Expose `volumeToMesh`'s `adaptivity` parameter (0.0–1.0) in the export dialog for user control.
- **Files to read**: `commands/cmd_sdf_export.py`, `core/dm_mesher.py`
- **Files to modify**: `commands/cmd_sdf_export.py`, `core/dm_mesher.py`
- **Steps**:
  1. `VDBMesher.mesh()` already has `adaptivity` parameter from VDB-004
  2. In the export dialog, add a "Mesh Adaptivity" slider (0.0–1.0) next to the existing cell size control
  3. For machinable output: recommend adaptivity=0.0, cell_size=0.05
  4. For visualization: recommend adaptivity=0.5, cell_size=0.5
- **Acceptance**: Exporting a sphere at adaptivity=0.0 vs 0.5 shows significantly fewer faces at 0.5 while maintaining shape fidelity.

---

## Phase 3: Renderer — Full VDB Pipeline

### Task VDB-006: Replace `bake_sdf_to_volume()` with VDB bake `Gemini Flash`

- **Goal**: Delete `bake_sdf_to_volume()` and replace with a VDB-based bake that outputs the same dense dict the renderer's 3D texture upload expects. The field is baked to VDB first (sparse, fast), then the narrow band is extracted to a dense numpy array for the GL texture.
- **Files to read**: `core/sdf/sdf_baker.py`, `core/dm_scene_ray_march_renderer.py:_rebuild`
- **Files to modify**: `core/sdf/sdf_baker.py`
- **Steps**:
  1. Delete `bake_sdf_to_volume()`.
  2. Replace with:
     ```python
     def bake_sdf(field, voxel_size, half_width=3.0):
         """Bake an SdfField via VDB, return dense volume dict for renderer.

         Pipeline: field.to_vdb() → grid.copyToArray() → dense numpy → volume_bytes
         """
         grid = field.to_vdb(voxel_size, half_width)
         return vdb_grid_to_dense(grid, voxel_size)

     def vdb_grid_to_dense(grid, voxel_size):
         """Extract a VDB grid's active region to the dense dict format the renderer expects."""
         bbox = grid.evalActiveVoxelBoundingBox()
         imin, imax = bbox
         # Pad by 1 voxel
         imin = tuple(i - 1 for i in imin)
         imax = tuple(i + 2 for i in imax)
         shape = tuple(mx - mn for mn, mx in zip(imin, imax))
         dense = np.full(shape, grid.background, dtype=np.float32)
         grid.copyToArray(dense, imin)
         # Convert index-space to world-space via grid.transform
         world_min = grid.transform.indexToWorld(imin)
         world_max = grid.transform.indexToWorld(imax)
         vol = dense.transpose(2, 1, 0)  # OpenGL convention
         return {
             "volume_bytes": vol.tobytes(),
             "nx": shape[0], "ny": shape[1], "nz": shape[2],
             "bbox_min": FreeCAD.Vector(*world_min),
             "bbox_max": FreeCAD.Vector(*world_max),
             "max_dist": voxel_size * 8.0,
         }
     ```
  3. Add timing logs using the same pattern as the old function.
- **Acceptance**: Renderer displays a sphere identically to before. Profiling log shows bake time.

---

### Task VDB-007: Remove GLSL compute shader bake path `Gemini Flash`

- **Goal**: Delete the GPU compute shader bake pipeline (`glsl_compiler.py`, `gl_compute.py`, `to_glsl()` on all fields). VDB bake is now the only path — no more GLSL compilation of the field tree.
- **Files to read**: `core/sdf/glsl_compiler.py`, `core/gl_compute.py`, `core/dm_scene_ray_march_renderer.py` (GPU dispatch logic in `_rebuild`)
- **Files to modify/delete**:
  - Delete `core/sdf/glsl_compiler.py`
  - Delete `core/gl_compute.py`
  - Remove `to_glsl()` from `core/sdf/sdf_field.py`, `core/sdf/sdf/sphere.py`, `core/sdf/sdf/box.py`, `core/sdf/sdf/cylinder.py`, `core/sdf/sdf_composer.py`
  - Remove GPU compute dispatch from `core/dm_scene_ray_march_renderer.py:_rebuild()`
- **Steps**:
  1. In `dm_scene_ray_march_renderer.py:_rebuild()`, remove all branches that check for GLSL compile / GPU dispatch. Replace with a single call to `bake_sdf(field, cell_size)`.
  2. Delete `to_glsl()` method from `SdfField` base and all subclasses.
  3. Delete `glsl_compiler.py` and `gl_compute.py` files.
  4. Remove any `GlslContext` imports throughout the codebase.
  5. The fragment shader (ray march visualization) stays — it reads the 3D texture, not the compute shader.
- **Acceptance**: Renderer works with VDB-only bake path. No references to `glsl_compiler`, `gl_compute`, or `to_glsl` remain in the codebase.

---

### Task VDB-008: Update renderer rebuild to use VDB directly `Gemini Flash`

- **Goal**: `DMSceneRayMarchRenderer._rebuild()` uses VDB as its sole bake path. Remove all dense-grid logic, grid-param computation, and the old CPU eval path. The flow is: field → `to_vdb()` → `vdb_grid_to_dense()` → 3D texture upload.
- **Files to read**: `core/dm_scene_ray_march_renderer.py` (specifically `_rebuild`, `_compute_cell_size`, `_compute_grid_params`)
- **Files to modify**: `core/dm_scene_ray_march_renderer.py`
- **Steps**:
  1. In `_rebuild()`, for each dirty field:
     - Call `bake_sdf(field, cell_size)` (from updated `sdf_baker.py`)
     - Use the returned dict to upload to the 3D texture atlas (existing `GLTexture3D` code)
  2. Delete `_compute_grid_params()` — VDB computes its own grid extents from `to_vdb()`.
  3. The adaptive cell size logic (`_compute_cell_size`) stays — it determines the voxel size passed to `bake_sdf()`.
  4. The atlas construction (`z_offsets`, `GLTexture3D`) stays — it receives dense slices from `vdb_grid_to_dense()`.
  5. The fragment shader stays unchanged — it reads the 3D texture.
- **Acceptance**: Create box + sphere → they render correctly. Zoom in/out → adaptive cell size still works. No references to old `bake_sdf_to_volume` remain.

---

## Phase 4: File I/O and Persistence

### Task VDB-009: Save/Load .vdb files `Gemini Flash`

- **Goal**: Add VDB file export/import. Users can save their SDF scene as `.vdb` for external tools (Blender, Houdini) and reload later.
- **Files to read**: `commands/cmd_sdf_export.py`
- **Files to modify**: `commands/cmd_sdf_export.py` (or new `commands/cmd_vdb_export.py`)
- **Steps**:
  1. Add "Export as VDB" option to the export dialog (or new command `DM_SdfExportVDB`)
  2. Implementation:
     ```python
     grid = field.to_vdb(voxel_size=export_cell_size)
     grid.name = obj.Label
     openvdb.write(filepath, grids=[grid])
     ```
  3. Add "Import VDB" command that reads a `.vdb` file:
     ```python
     grids = openvdb.readAll(filepath)
     # Create a DMObject with ShapeType="sdf" that wraps the grid
     ```
  4. For import, create a new `VDBWrapperField(SdfField)` that wraps an `openvdb.FloatGrid` and implements `evaluate()` via `grid.probeValue()` and `evaluate_grid()` via `grid.copyToArray()` + interpolation, and `to_vdb()` returns the grid directly.
- **Acceptance**: Export sphere as .vdb → reimport → renders identically. File opens in Blender's volume import.

---

### Task VDB-010: Persistent VDB cache on DMObjectProxy `Gemini Flash`

- **Goal**: Store the baked VDB grid on the FreeCAD object so it doesn't need to be re-baked on every renderer rebuild or export. The analytic `SdfField` tree remains the source of truth; the VDB cache is invalidated when field parameters change.
- **Files to read**: `core/dm_object.py` (DMObjectProxy.execute, onChanged)
- **Files to modify**: `core/dm_object.py`
- **Steps**:
  1. Add `_vdb_cache = None` and `_vdb_cache_voxel_size = None` attributes on `DMObjectProxy`
  2. In `execute()`, after updating the SdfField, invalidate: `self._vdb_cache = None`
  3. Add method:
     ```python
     def get_vdb_grid(self, voxel_size=0.5):
         if self._vdb_cache is None or self._vdb_cache_voxel_size != voxel_size:
             self._vdb_cache = self.SdfField.to_vdb(voxel_size)
             self._vdb_cache_voxel_size = voxel_size
         return self._vdb_cache
     ```
  4. The renderer and mesher call `get_vdb_grid()` instead of `field.to_vdb()` directly, benefiting from caching across rebuilds at the same cell size.
- **Acceptance**: Creating a sphere, then exporting twice without editing → second export skips the bake step (cache hit logged).

---

## Phase 5: Cleanup — Remove All Legacy Code

### Task VDB-011: Delete old dense baking and MC infrastructure `Gemini Flash`

- **Goal**: Remove all remnants of the pre-VDB pipeline. No dead code should remain.
- **Files to read**: Full codebase grep for: `bake_sdf_to_volume`, `evaluate_grid`, `MarchingCubesMesher`, `glsl_compiler`, `gl_compute`, `to_glsl`, `GlslContext`
- **Files to modify/delete**: Various
- **Steps**:
  1. Delete `core/sdf/marching_cubes/` directory entirely
  2. Confirm `core/sdf/glsl_compiler.py` and `core/gl_compute.py` are already deleted (VDB-007)
  3. Remove `evaluate_grid()` and `gradient_grid()` and `curvature_grid()` from `SdfField` base class — VDB handles all grid evaluation now. Keep `evaluate()` (single-point) for CPU ray_march hit-testing.
  4. Remove `evaluate_grid()` overrides from all subclasses (`SdfBoxField`, `SdfSphereField`, `SdfCylinderField`, all ComposerFields)
  5. Remove unused numpy vectorization code in primitives that was only for `evaluate_grid()`
  6. Grep the entire codebase for any remaining references to deleted functions/files and clean up imports
- **Acceptance**: `grep -r "evaluate_grid\|MarchingCubesMesher\|bake_sdf_to_volume\|glsl_compiler\|gl_compute\|to_glsl\|GlslContext" core/ tools/ commands/` returns zero matches.

---

### Task VDB-012: Replace CPU ray_march with VDB probe `Gemini Flash`

- **Goal**: The `SdfField.ray_march()` method currently calls `self.evaluate()` per step. Replace with VDB-based ray intersection or at minimum use `grid.probeValue()` for faster lookups during sphere tracing.
- **Files to read**: `core/sdf/sdf_field.py:ray_march`, `core/view_projector.py` (callers of ray_march)
- **Files to modify**: `core/sdf/sdf_field.py`
- **Steps**:
  1. Add a cached VDB grid on the field (or accept one as parameter):
     ```python
     def ray_march(self, ray_origin, ray_direction, grid=None, ...):
         if grid is None:
             grid = self.to_vdb(voxel_size=0.5)  # coarse for hit-testing
         # Use grid for sphere tracing lookups
     ```
  2. At each step, convert world-space position to index-space via `grid.transform.worldToIndex()`, then `grid.probeValue()` for the distance
  3. This makes `evaluate()` (single-point, Python-level) no longer needed for ray marching — VDB's C++ tree traversal is much faster
  4. Gradient can be computed via VDB's built-in gradient accessor or central differences on `probeValue()`
- **Acceptance**: Hit-testing a sphere via `ray_march()` returns the same hit point (within 0.1mm) as before, but faster.

---

### Task VDB-013: Update all project documentation `Gemini Flash`

- **Goal**: Update AGENTS.md, INDEX.md, and all affected skill files to reflect the VDB-only pipeline. Remove all references to the old dense/GLSL paths.
- **Files to read**: `AGENTS.md`, `INDEX.md`, all `.agents/skills/*/SKILL.md` files
- **Files to modify**: `AGENTS.md`, `INDEX.md`, `.agents/skills/dm_sdf_primitive_pattern/SKILL.md`, `.agents/skills/dm_mesher_architecture/SKILL.md`, `.agents/skills/dm_boolean_architecture/SKILL.md`, `.agents/skills/dm_renderer_architecture/SKILL.md`, `.agents/skills/dm_openvdb_migration/SKILL.md`
- **Steps**:
  1. In `AGENTS.md`, replace the "SDF Pipeline" section:
     ```
     SdfField subclass (analytic function + to_vdb())
       → field.to_vdb() → OpenVDB FloatGrid (sparse level set)
         → [Preview]  vdb_grid_to_dense() → GL_R32F 3D texture → fragment shader ray march
         → [Export]   volumeToMesh() → Part.Shape
         → [File I/O] openvdb.write() → .vdb file
         → [Boolean]  csgUnion/csgIntersection/csgDifference
     ```
  2. Remove references to: GLSL compute shader baking, `evaluate_grid()`, `MarchingCubesMesher`, `gl_compute.py`, `glsl_compiler.py`
  3. Update Key Classes table: remove MC mesher, add `VDBMesher`, `VDBWrapperField`
  4. Update INDEX.md file map: remove deleted files, add new ones
  5. Update each skill file listed above with VDB-only patterns
  6. Update `dm_openvdb_migration/SKILL.md` to remove "dual pipeline" language — it's now the only pipeline
  7. Add installation note: `pip install pyopenvdb` is a **required** dependency
- **Acceptance**: No references to `evaluate_grid`, `glsl_compiler`, `gl_compute`, `MarchingCubesMesher`, or "dual pipeline" remain in any docs or skill files.

---

### Task VDB-014: Accuracy validation test `Gemini Flash`

- **Goal**: Verify that VDB meshing at 0.05mm voxel size produces shapes within 0.1mm of analytic truth.
- **Files to read**: `tests/test_sdf_baker.py`
- **Files to modify**: `tests/test_vdb_accuracy.py` (new)
- **Steps**:
  1. Create test that generates known primitives (sphere r=25mm, box 50x30x20mm, cylinder r=15 h=40mm)
  2. For each: call `field.to_vdb(voxel_size=0.05)`, mesh with `volumeToMesh`, convert to `Part.Shape`
  3. Measure: bounding box dimensions vs analytic, volume vs analytic (`4/3 * pi * r^3` etc.)
  4. Assert dimensional error < 0.1mm, volume error < 0.5%
  5. Also test at 0.1mm voxel size to confirm it still meets 0.1mm accuracy
  6. Test a boolean (sphere - box) at 0.05mm — verify the cut is clean
- **Acceptance**: All assertions pass. Print a table of primitive / voxel_size / dimensional_error / volume_error.

---

## Phase 6: Advanced (Future)

### Task VDB-015: Smooth boolean operations via VDB `Gemini Flash`

- **Goal**: Implement smooth union/subtraction/intersection. VDB's native CSG doesn't support smooth blending, so sample the analytic smooth-min function into a VDB grid directly.
- **Files to read**: `core/sdf/sdf_composer.py`, `.agents/skills/dm_openvdb_migration/SKILL.md`
- **Files to modify**: `core/sdf/sdf_composer.py`
- **Steps**:
  1. Add `SmoothUnionField`, `SmoothSubtractionField`, `SmoothIntersectionField` with `k` blend radius parameter
  2. Keep `evaluate()` using the analytic smooth-min formula (needed for `ray_march` hit-testing)
  3. For `to_vdb()`: build VDB grids of both children, compute the union of their active voxel regions (expanded by `k`), then sample the analytic smooth-min at each active voxel to produce the result grid
  4. This means smooth composers use `evaluate()` per-voxel inside `to_vdb()`, not VDB CSG ops
- **Acceptance**: Smooth union of two spheres at k=2mm → mesh shows fillet at junction, no artifacts.

---

### Task VDB-016: NanoVDB GPU ray marching (replace 3D texture) `Gemini Flash`

- **Goal**: Long-term: replace the GL_R32F 3D texture ray marcher with NanoVDB on GPU. This eliminates the dense texture entirely — the sparse VDB tree is uploaded as a flat buffer (SSBO) and traversed directly in the fragment shader.
- **Files to read**: `core/dm_scene_ray_march_renderer.py`, `.agents/skills/dm_openvdb_migration/SKILL.md`
- **Files to modify**: (research task — document findings, don't implement yet)
- **Steps**:
  1. Research NanoVDB (Ken Museth's GPU-friendly VDB): it serializes VDB trees into a flat buffer that can be uploaded to GPU as an SSBO
  2. Document whether Coin3D/OpenGL pipeline can support SSBO-based ray marching
  3. Evaluate: is NanoVDB Python binding mature enough? Or do we need a C++ extension?
  4. Write findings in `.agents/skills/dm_nanovdb_renderer/SKILL.md`
- **Acceptance**: Research document exists with clear recommendation on feasibility and timeline.
