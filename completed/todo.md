# FreeCAD Direct Modeling — Todo

---

## Meshing

The mesher pipeline lives in [core/dm_mesher.py](core/dm_mesher.py). The abstract base is `DMMesher.mesh(field, cell_size) -> (flat_verts, flat_idx)` where:
- `flat_verts`: `(N*3, 3) float32` — one row per triangle vertex
- `flat_idx`: `(N*4,) int32` — Coin3D `SoIndexedFaceSet` format with -1 sentinels after every triangle

Grid sampling and field evaluation follow the same pattern as `MarchingCubesMesher` (the reference implementation). `FRepField.evaluate_grid(pts)` accepts `(N,3) ndarray` and returns `(N,) float32`. `FRepField.gradient(point, h=1e-4)` returns a `FreeCAD.Vector` via central differences.

The meshing type is dispatched by `get_active_mesher()` in `dm_mesher.py`:
- `0` → `MarchingCubesMesher` (done)
- `1` → `AdaptiveMCMesher` (stub)
- `2` → `SurfaceNetsMesher` (stub — currently falls back to MC)

`get_meshing_type()` / `set_meshing_type()` in [core/dm_object.py](core/dm_object.py) read/write a FreeCAD preference int.

---

### SN-1: Implement `SurfaceNetsMesher`

**What:** Replace the stub in `SurfaceNetsMesher.mesh()` with a vectorized Naive Surface Nets implementation.

**Algorithm (Naive Surface Nets — Gibson 1998):**
1. Sample the field on the same uniform grid as MC (reuse the grid-building logic from `MarchingCubesMesher`).
2. Detect *net cells*: any cell where at least one of its 12 edges has a sign change (one corner negative, one positive).
3. For each net cell, compute one representative vertex = average of all edge crossing points (linear interpolation on each sign-change edge, identical to MC edge interpolation).
4. Build quads by connecting the representative vertices of the four net cells that share each sign-change edge:
   - For each X-axis edge (i→i+1) with a sign change at `(i,j,k)`: connect the representative vertices of cells `(i,j,k)`, `(i,j-1,k)`, `(i,j-1,k-1)`, `(i,j,k-1)` — wound to face the gradient direction.
   - Repeat for Y-axis and Z-axis edges.
5. Triangulate each quad into two triangles. Flip winding so outward normals match the MC convention (SDF negative-inside → surface normal points outward = positive gradient direction).
6. Return `flat_verts, flat_idx` in the same format as `MarchingCubesMesher`.

**Vectorization target:** Aim for the same stages as MC — no Python loops over cells. Use `np.argwhere` for active cell detection, broadcast for edge crossing computation, and gather/scatter via index arrays for quad construction.

**Agent prompt (Gemini Flash):**
> Implement `SurfaceNetsMesher.mesh()` in `core/dm_mesher.py` for a FreeCAD F-Rep workbench. The method signature is `mesh(self, field: FRepField, cell_size: float) -> tuple`. `FRepField` has `evaluate_grid(pts: ndarray) -> ndarray` and `bounding_box() -> (FreeCAD.Vector, FreeCAD.Vector)`. The output must be `(flat_verts, flat_idx)` where `flat_verts` is `(N*3, 3) float32` and `flat_idx` is a Coin3D SoIndexedFaceSet index array with -1 sentinels: `[0,1,2,-1, 3,4,5,-1, ...]` as `int32`. Implement Naive Surface Nets: uniform grid, one vertex per active cell (average of edge crossing points), quads connecting the four cells sharing each sign-change edge, triangulated with correct outward winding (SDF negative-inside convention). Vectorize with NumPy — no Python loops over cells. Wrap each meshing stage with `mesh_timer.start(stage)` / `mesh_timer.stop(stage)` using stage names prefixed `sn_`. Do not touch any other class or method. The existing `MarchingCubesMesher` implementation is the reference for grid setup, edge interpolation, and output format.

---

### SN-2: Add `MeshingType` enum entries for Surface Nets and Dual Contouring

**What:** The `get_meshing_type()` docstring in [core/dm_object.py](core/dm_object.py) currently lists type `2` as "NURBS based F-Rep approach" which is stale. Update the dispatch table and docstrings to match the actual plan:
- `0` → Marching Cubes
- `1` → Adaptive Marching Cubes
- `2` → Surface Nets
- `3` → Dual Contouring

Update `get_active_mesher()` in [core/dm_mesher.py](core/dm_mesher.py) to add `elif st == 3: return DualContouringMesher()`. Add a `DualContouringMesher` stub (fallback to MC) so the dispatch doesn't crash before DC is implemented.

**Agent prompt (Gemini Flash):**
> In `core/dm_object.py`, update the `get_meshing_type()` docstring: type 2 is "Surface Nets", type 3 is "Dual Contouring". In `core/dm_mesher.py`, add a `DualContouringMesher` stub class (inheriting `DMMesher`) that logs a warning and falls back to `MarchingCubesMesher`, then update `get_active_mesher()` to return it when `st == 3`. No other changes.

---

### DC-1: Implement `DualContouringMesher` — edge intersection and QEF data collection

**What:** Implement the first half of Dual Contouring (Ju et al. 2002): per-cell QEF setup.

**Algorithm:**
1. Same uniform grid as MC.
2. For every grid edge with a sign change, compute:
   - Crossing point `p` via linear interpolation (same as MC edge interpolation).
   - Surface normal `n` = normalized gradient at `p` via `field.gradient(FreeCAD.Vector(*p))`.
3. Accumulate per-cell QEF matrices. For each cell `c` that owns a crossing edge, add a plane constraint `(n · (x - p) = 0)` to cell `c`'s QEF. In matrix form: `A^T A x = A^T b` where each edge contributes one row `n^T` to `A` and `n · p` to `b`.
4. Store per active cell: `AtA` (3×3), `Atb` (3,), `mass_point` (sum of crossing points), `crossing_count`.

This step produces the raw QEF data; DC-2 solves it and builds the mesh.

**Agent prompt (Gemini Flash):**
> Implement the QEF data collection phase for `DualContouringMesher` in `core/dm_mesher.py`. Given a uniform grid of SDF values (same grid-building logic as `MarchingCubesMesher`), find all grid edges with sign changes. For each crossing edge: interpolate the crossing point `p`, evaluate the gradient via `field.gradient(FreeCAD.Vector(*p))` (returns a `FreeCAD.Vector`; normalize it), then accumulate into the owning cell's QEF: `AtA += np.outer(n, n)`, `Atb += n * np.dot(n, p)`, `mass_point += p`, `crossing_count += 1`. Process X, Y, Z edge families separately (each is a shifted array slice of the sign grid). Return dictionaries/arrays keyed by active cell index. Use NumPy for per-family batch processing — minimize Python loops.

---

### DC-2: Implement `DualContouringMesher` — QEF solve and quad mesh assembly

**What:** Second half of Dual Contouring: solve per-cell QEFs and build the output mesh.

**Algorithm:**
1. For each active cell with QEF data from DC-1:
   - Solve via SVD-regularized least squares: `v = pinv(AtA) @ Atb`, clamped to the cell's bounding box to prevent vertex blow-up.
   - If rank is degenerate (all singular values below threshold), fall back to the mass point (average crossing point).
2. Assign each active cell a sequential vertex index.
3. For each sign-change edge shared by 4 cells (the "dual quad"), emit a quad of the 4 cells' dual vertices. Wind the quad so the outward normal agrees with the gradient direction at the crossing point.
4. Triangulate each quad into two triangles. Return `flat_verts, flat_idx` in the standard format.

**Performance note:** SVD per cell cannot be easily vectorized with NumPy's standard `lstsq` on batches; use `np.linalg.svd` on a `(N_active, 3, 3)` batch — NumPy broadcasts this.

**Agent prompt (Gemini Flash):**
> Complete `DualContouringMesher.mesh()` in `core/dm_mesher.py`. Given per-cell QEF arrays `AtA (N,3,3)`, `Atb (N,3)`, `mass_points (N,3)`, and cell bounding boxes, batch-solve the QEFs using `np.linalg.svd` on the full `(N,3,3)` stack. Regularize: zero out singular values below `1e-4 * max_sv`, reconstruct pseudoinverse, compute `v = pinv @ Atb`. Clamp `v` to each cell's axis-aligned bounding box. For degenerate cells (all sv < threshold), use mass point. Then for each sign-change edge (the "primal edge"), look up the 4 surrounding active cells and emit a quad of their dual vertices, wound so the outward normal agrees with the SDF gradient at the crossing. Triangulate quads into two triangles each. Output `(flat_verts, flat_idx)` matching the Coin3D format used by `MarchingCubesMesher`. Wrap stages with `mesh_timer.start/stop` using `dc_` prefixed stage names.

---

### MESH-PERF-1: Extend `MeshTimer` for Surface Nets and Dual Contouring stages

**What:** `MeshTimer._STAGES` and `_SUB_STAGES` are hardcoded lists in [core/dm_mesher.py](core/dm_mesher.py) for MC stage names. The timer's `start()`/`stop()` already work for any string key, but `summary()` iterates `_STAGES` so new stages are silently dropped from the log.

Update `MeshTimer` to collect and display *any* stage that was timed, not just those in `_STAGES`, so Surface Nets and Dual Contouring stage names automatically appear in the perf log without modifying the timer class per algorithm.

**Agent prompt (Gemini Flash):**
> In `MeshTimer` in `core/dm_mesher.py`, refactor `summary()` so it iterates over all stages that have non-zero totals (i.e., the keys of `self._totals` that are > 0), not just `_STAGES`. Keep `_STAGES` for ordering the existing MC stages first; append any other timed stages alphabetically after. Keep `_SUB_STAGES` for indentation. Update `reset()` to clear `_totals` of any dynamically added keys. No interface change — `start()`/`stop()` remain the same.

---

### MESH-UI-1: Expose meshing type in the DM Preferences panel

**What:** The meshing type is currently only changeable programmatically. Add a `QComboBox` to the DM preferences/settings dialog so users can select between Marching Cubes, Surface Nets, and Dual Contouring.

**Depends on:** SN-2 (enum entries finalized).

**Agent prompt (Gemini Flash):**
> Find the DM preferences or settings dialog in the workbench (likely in `ui/` or `commands/`). Add a labeled `QComboBox` for "Meshing Algorithm" with entries: "Marching Cubes (0)", "Adaptive MC (1)", "Surface Nets (2)", "Dual Contouring (3)". On dialog open, read the current value via `get_meshing_type()` from `core.dm_object`. On apply/OK, call `set_meshing_type(index)`. Follow the existing pattern for other preference controls in that dialog.
