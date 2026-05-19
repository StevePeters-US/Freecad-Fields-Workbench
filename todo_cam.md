# Direct Modeling Workbench — CNC Tool Path Generation (CAM) Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).

---

## Background

One of the longer-term goals of this project is to generate CNC tool paths directly from the function-based SDF representation — without any intermediate meshing step.

Because SDFs encode geometry as a continuous function rather than a polygon mesh, several CAM problems map onto them naturally:

- **Cutter-radius compensation** — the tool center path for a ball-end mill of radius `r` is the isosurface `f(p) = r`. No mesh offsetting needed; just change the evaluation threshold.
- **Gouge detection** — a tool at position `p` is gouging if `f(p) < r`. A single function evaluation, testable at any point along the path.
- **Surface normals** — `∇f` gives exact, smooth normals everywhere. Ideal for 5-axis tool orientation without mesh normal interpolation artifacts.
- **Adaptive stepover** — principal curvatures can be derived from the Hessian of `f`, enabling scallop-height equalization analytically.
- **Rest machining / pocket detection** — regions accessible to a small tool but not a large one are `{p : f_large(p) > 0 and f_small(p) ≤ r}`. Pure SDF logic.
- **Z-slice roughing** — a 2D cut boundary at height `z_i` is the zero-crossing of `f(x, y, z_i)`, extracted via marching squares on a 2D slice.

This approach only works cleanly with **analytic/function-based** SDFs (not voxel grids), which is why the engine in this workbench evaluates fields procedurally rather than baking them to a volume.

---

## Tier 1 — Conceptual Tasks (Future Work)

### CAM-001: Implement Z-Slice Roughing (2D Marching Squares)

**File:** `core/sdf/sdf_slicer.py` (or new CAM-specific module)

**What:** Extract toolpath boundaries at horizontal plane heights $z_i$ by finding the zero-crossings of $f(x, y, z_i)$.

---

### CAM-002: Implement Cutter-Radius Compensation

**File:** TBD

**What:** Define offset distance functions that shift the SDF evaluation threshold by tool radius $r$ to generate tool center paths directly.

---

### CAM-003: Implement Gouge Detection

**File:** TBD

**What:** Perform point evaluations along toolpath segments to verify $f(p) \ge r$ relative to the raw workpiece/stock model.
