# Direct Modeling Workbench — Mesh Optimization Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

All four meshers in `core/dm_mesher.py` currently sample the SDF on a **uniform grid**.
This produces identical triangle density on flat faces and sharp corners.
The goal is to concentrate vertices near high-curvature features (edges, corners)
and keep flat regions sparse — dramatically reducing total vert/tri count
without sacrificing visual quality.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `FRepField.evaluate_grid(pts)` | `core/frep/frep_field.py:30` | Batch SDF eval `(N,3) → (N,)` |
| `FRepField.gradient(pt, h)` | `core/frep/frep_field.py:13` | Central-difference gradient `→ FreeCAD.Vector` |
| `FRepField.bounding_box()` | `core/frep/frep_field.py:20` | AABB `→ (Vector, Vector)` |
| `DMMesher.mesh(field, cell_size)` | `core/dm_mesher.py:93` | Base mesher contract `→ (flat_verts, flat_idx)` |
| `mesh_timer.start/stop(stage)` | `core/dm_mesher.py:47-54` | Performance instrumentation |
| `dm_logger.*` | `core/dm_logger.py` | Logging (info/debug/warn/error) |

### Output format

`flat_verts`: `(N*3, 3) float32` — one row per triangle vertex  
`flat_idx`: `(N*4,) int32` — Coin3D `SoIndexedFaceSet` with `-1` sentinels

---

## Tier 1 — Batch Gradient & Curvature Infrastructure (Do First)

These tasks provide the vectorized gradient and curvature primitives that
all adaptive algorithms depend on. No mesher changes yet.

### M-001: Add `gradient_grid()` batch method to `FRepField`

**File:** `core/frep/frep_field.py`  
**What:** Add a new method `gradient_grid(self, points: np.ndarray, h: float = 1e-4) -> np.ndarray` that
computes central-difference gradients over an `(N,3)` array and returns `(N,3) float64`.
This is the vectorized counterpart to the existing single-point `gradient()` at line 13.

**Implementation:**
```python
def gradient_grid(self, points: np.ndarray, h: float = 1e-4) -> np.ndarray:
    """Batch central-difference gradient over (N,3) points → (N,3) float64."""
    dx_p = points.copy(); dx_p[:, 0] += h
    dx_m = points.copy(); dx_m[:, 0] -= h
    dy_p = points.copy(); dy_p[:, 1] += h
    dy_m = points.copy(); dy_m[:, 1] -= h
    dz_p = points.copy(); dz_p[:, 2] += h
    dz_m = points.copy(); dz_m[:, 2] -= h
    gx = (self.evaluate_grid(dx_p) - self.evaluate_grid(dx_m)) / (2 * h)
    gy = (self.evaluate_grid(dy_p) - self.evaluate_grid(dy_m)) / (2 * h)
    gz = (self.evaluate_grid(dz_p) - self.evaluate_grid(dz_m)) / (2 * h)
    return np.column_stack([gx, gy, gz])
```

**Why first:** Every curvature / adaptation algorithm needs batch gradients.
The existing `gradient()` is per-point and used in DC's inner loop (line 599) — this replaces it batch-wise.

---

### M-002: Add `curvature_grid()` method to `FRepField`

**File:** `core/frep/frep_field.py`  
**What:** Add `curvature_grid(self, points: np.ndarray, h: float = 1e-3) -> np.ndarray` that
estimates the mean curvature magnitude at each point using the Laplacian of the SDF:
`κ ≈ |∇·n| = |Laplacian(SDF)| / |∇SDF|`.

**Implementation:**
```python
def curvature_grid(self, points: np.ndarray, h: float = 1e-3) -> np.ndarray:
    """Approximate mean curvature via Laplacian of SDF. Returns (N,) float64."""
    f0 = self.evaluate_grid(points)
    lap = np.zeros(len(points), dtype=np.float64)
    for axis in range(3):
        p_plus = points.copy(); p_plus[:, axis] += h
        p_minus = points.copy(); p_minus[:, axis] -= h
        lap += (self.evaluate_grid(p_plus) + self.evaluate_grid(p_minus) - 2 * f0) / (h * h)
    grad = self.gradient_grid(points, h)
    grad_mag = np.linalg.norm(grad, axis=1)
    grad_mag = np.maximum(grad_mag, 1e-12)
    return np.abs(lap) / grad_mag
```

**Depends on:** M-001

---

## Tier 2 — Adaptive Marching Cubes (Priority Mesher)

Replace the `AdaptiveMCMesher` stub with a real octree-based adaptive implementation.
This is the highest-impact optimization: a 10× box can go from ~5000 triangles
(uniform) to ~200 (adaptive: 6 flat faces × ~32 tris each + tight corner detail).

### M-003: Implement `AdaptiveMCMesher` — octree grid refinement

**File:** `core/dm_mesher.py` — replace stub at lines 241-245  
**What:** Build an octree that starts at a coarse grid (e.g. `cell_size * 4`) and
recursively subdivides cells where curvature exceeds a threshold.

**Algorithm:**
1. Start with bounding box divided into coarse cells (`cell_size * 4`).
2. Evaluate SDF at all coarse grid corners.
3. For each active cell (at least one sign change), estimate curvature at the cell center
   using `field.curvature_grid()` (from M-002).
4. If curvature > threshold **or** cell_size > target `cell_size`, subdivide into 8 children.
5. Repeat until max depth is reached (depth = `log2(coarse_size / cell_size)`).
6. Run standard MC on each leaf cell.
7. Handle "crack patching" at T-junctions between cells of different depths:
   - For each face shared by a coarse cell and its finer neighbors, snap the
     coarse cell's MC edge-crossing vertices onto the finer cell's edge-crossings.

**Performance target:** At least 4× fewer triangles than uniform MC for a 20mm box
at cell_size=1.0, while preserving edge sharpness within 0.1mm.

**Wrap stages:** `mesh_timer.start/stop` with `amc_` prefix:
`amc_coarse_eval`, `amc_curvature`, `amc_subdivide`, `amc_leaf_mc`, `amc_crack_patch`, `mesh_build`

**Depends on:** M-001, M-002

---

### M-004: Curvature threshold as a user setting

**File:** `core/dm_object.py`  
**What:** Add FreeCAD preference accessors:
- `get_curvature_threshold() -> float` (default `0.1`)
- `set_curvature_threshold(val: float)`

Store at `User parameter:FCDirectModeling.CurvatureThreshold`.

**File:** `commands/cmd_settings.py`  
**What:** Add a labeled `QDoubleSpinBox` for "Curvature Threshold" (range 0.01–1.0, step 0.01)
in the existing settings dialog. Read/write via the accessors above.
Follow the existing pattern for other preference controls in that dialog.

**Depends on:** M-003

---

## Tier 3 — Post-Mesh Decimation (All Meshers)

These tasks reduce triangle count on the **output** of any mesher,
independent of the meshing algorithm itself.

### M-005: Add a flat-triangle decimation pass

**File:** `core/dm_mesher.py` — new function at module level  
**What:** Add `decimate_flat_tris(verts, indices, angle_tol=5.0) -> (verts, indices)` that
merges coplanar adjacent triangles whose face-normal angle is below `angle_tol` degrees.

**Algorithm:**
1. Compute per-face normals from `flat_verts` / `flat_idx`.
2. Build a face adjacency graph (shared edge = shared two vertex positions within ε).
3. BFS/flood-fill to group faces with normals within `angle_tol` degrees of each other.
4. For each coplanar group: re-triangulate the merged polygon using ear-clipping
   or a fan triangulation from the centroid.
5. Rebuild `flat_verts` / `flat_idx` with reduced triangle count.

**Usage:** Optionally called at the end of each mesher's `mesh()` before returning.
Gate behind a preference `get_decimate_enabled()` (default `True`).

---

### M-006: Wire decimation into the mesher pipeline

**File:** `core/dm_mesher.py`  
**What:** At the end of each mesher's `mesh()` method (MC lines ~228-238, SN lines ~460-476,
DC lines ~716-728), add:
```python
if get_decimate_enabled():
    mesh_timer.start("decimate")
    flat_verts, flat_idx = decimate_flat_tris(flat_verts, flat_idx)
    mesh_timer.stop("decimate")
```

**File:** `core/dm_object.py`  
**What:** Add `get_decimate_enabled() -> bool` / `set_decimate_enabled(val)` preference accessors.

**Depends on:** M-005

---

## Tier 4 — Vertex Deduplication & Index Buffer Sharing

All meshers currently emit **unique vertices per triangle** (no shared verts).
Deduplication reduces GPU memory by ~3× and enables smooth shading.

### M-007: Add vertex deduplication utility

**File:** `core/dm_mesher.py` — new function  
**What:** Add `deduplicate_verts(flat_verts, flat_idx) -> (unique_verts, new_idx)`:
1. Round vertex positions to a tolerance (e.g. `1e-5`).
2. Use `np.unique` with `return_inverse=True` on the rounded array.
3. Remap `flat_idx` to the unique vertex indices.
4. Return `(unique_verts, new_idx)` in the same Coin3D format.

This can also be optionally called at the end of each mesher.

---

## Tier 5 — Dual Contouring QEF Vectorization (Performance)

The current DC mesher has a Python `for` loop over every crossing edge (line 597-614).
This is the main performance bottleneck for DC.

### M-008: Vectorize DC `accumulate_qef` inner loop

**File:** `core/dm_mesher.py` lines 578-618  
**What:** Replace the per-edge Python loop at line 597 with batch gradient evaluation
using `field.gradient_grid()` (from M-001).

**Current (slow):**
```python
for m in range(len(ei)):
    p = pts_edge[m]
    n_vec = field.gradient(FreeCAD.Vector(*p))
    ...
```

**Target (fast):**
```python
grads = field.gradient_grid(pts_edge)  # (K, 3) batch
norms = np.linalg.norm(grads, axis=1, keepdims=True)
valid = norms.ravel() > 1e-12
n = grads[valid] / norms[valid]
# Vectorized outer product accumulation via np.einsum
```

**Depends on:** M-001

---

## Tier 6 — Surface Nets Relaxation (Quality)

### M-009: Add Laplacian relaxation to `SurfaceNetsMesher`

**File:** `core/dm_mesher.py` — inside `SurfaceNetsMesher.mesh()`, after line 386  
**What:** After computing initial cell vertices (average of crossings), run 2-3 iterations
of constrained Laplacian smoothing:
1. Build vertex adjacency from quad connectivity.
2. Move each vertex toward the average of its neighbors.
3. After each iteration, project back onto the SDF zero-set using one Newton step:
   `v -= field.evaluate(v) / |∇field(v)|² * ∇field(v)`

This produces smoother meshes without adding triangles.

---

## Agent Skills

See `.agents/skills/` for project-specific knowledge:

| Skill | Purpose |
|-------|---------|
| `dm_mesher_architecture` | Mesher class hierarchy, output format, timer instrumentation |
| `dm_logging` | Logging conventions for the DM workbench |

See `.agents/workflows/` for executable workflows:

| Workflow | Purpose |
|----------|---------|
| `/fix-task` | Fix a single task from this list by ID (e.g. `/fix-task M-003`) |
