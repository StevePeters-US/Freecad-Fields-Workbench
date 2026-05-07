# Direct Modeling Workbench — Architecture Refactor Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.
> Read INDEX.md before starting any task.

---

## Background

The workbench targets 0.05mm resolution over a 1m³ workspace. At target scale a dense uniform
grid over the full bounding box requires 8 trillion voxels (32 TB of float32) — physically
impossible. Every CPU path that calls `np.meshgrid` over the bounding box must be replaced.

**Current state**: All four meshers (`MarchingCubesMesher`, `AdaptiveMCMesher`,
`SurfaceNetsMesher`, `DualContouringMesher`) and the SDF baker construct a full `np.meshgrid`
over the bounding box. Several silent `except: pass` blocks hide runtime errors. Three confirmed
bugs cause crashes. No NURBS-surface SDF field exists.

**Goal state after all tasks complete**:
1. All CPU SDF evaluation goes through `SdfOctreeCache` — a hierarchical evaluator that only
   samples cells near the zero-crossing surface, scaling with surface area not volume.
2. Every `except: pass` block logs via `dm_logger`.
3. Three known bugs are fixed.
4. `SdfNurbsCurveField` and `SdfNurbsSurfaceField` exist with `to_glsl()` implementations.
5. A `SdfCacheManager` singleton provides shared octree instances to all tools.

**Approach**: Phases proceed top-to-bottom. Phase 4 (NURBS SDF) can be worked in parallel
with Phases 2–3 because it adds new files without touching meshers.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `SdfField` | `core/sdf/sdf_field.py:3` | Abstract base — `evaluate()`, `evaluate_grid()`, `bounding_box()`, `to_glsl()` |
| `GlslContext` | `core/sdf/glsl_compiler.py:11` | Collects uniforms and helpers during to_glsl() compilation |
| `MarchingCubesMesher.mesh()` | `core/dm_mesher.py:113` | Returns `(flat_verts: np.ndarray, flat_idx: np.ndarray)` or None |
| `SurfaceNetsMesher.mesh()` | `core/dm_mesher.py:488` | Same signature |
| `DualContouringMesher.mesh()` | `core/dm_mesher.py:809` | Same signature |
| `AdaptiveMCMesher.mesh()` | `core/dm_mesher.py:259` | Same signature — will be removed |
| `bake_sdf_to_volume()` | `core/sdf/sdf_baker.py:13` | Returns dict with `volume_bytes`, `nx/ny/nz`, `bbox_min/max` |
| `get_active_mesher()` | `core/dm_mesher.py` | Factory — returns the currently selected mesher instance |
| `dm_logger.debug/warn/error` | `core/dm_logger` (import as `from core import dm_logger`) | Project logger — never use `print()` or `FreeCAD.Console.*` directly |

---

## Tier 1 — Code Quality (Do First)

Fix silent exception swallowing and known crash bugs before adding new features.

### [x] CQ-001: Fix bare `except: pass` in work_plane.py

**File:** `core/work_plane.py` — line 153

**What:** Replace the bare `except: pass` with a logged exception. This block swallows any
error from camera-height scale computation.

**Implementation:**
```python
# BEFORE (line 152-153):
        except: pass

# AFTER:
        except Exception as e:
            dm_logger.debug(f"WorkPlane scale update failed: {e}")
```

Add `from core import dm_logger` at the top of the file if not already imported.

---

### [x] CQ-002: Fix bare `except: pass` in view_projector.py

**File:** `core/view_projector.py` — line 454

**What:** Replace the silent `except: pass` in the face normal fallback path.

**Implementation:**
```python
# BEFORE (line 453-454):
                        except: pass

# AFTER:
                        except Exception as e:
                            dm_logger.debug(f"face normal parameter fallback failed: {e}")
```

---

### [x] CQ-003: Fix bare `except: pass` in input_manager.py

**File:** `core/input_manager.py` — line 464

**What:** The `view.getPoint()` fallback call silently swallows exceptions. This is a
recurring pattern that should log at debug level.

**Implementation:**
```python
# BEFORE (line 463-464):
            try: scene_pt = view.getPoint(x, y)
            except: pass

# AFTER:
            try:
                scene_pt = view.getPoint(x, y)
            except Exception as e:
                dm_logger.debug(f"view.getPoint fallback failed: {e}")
```

---

### [x] CQ-004: Fix bare `except: pass` in dm_object.py

**File:** `core/dm_object.py` — lines 70–73 (`set_perf_profiler_enabled`)

**What:** The `try/except: pass` around `ParamGet().SetBool()` swallows errors silently.

**Implementation:**
```python
# BEFORE (lines 70-73):
def set_perf_profiler_enabled(val):
    try: # ParamGet might fail in some contexts
        FreeCAD.ParamGet(_PARAM_PATH).SetBool("EnablePerfProfiler", bool(val))
    except:
        pass

# AFTER:
def set_perf_profiler_enabled(val):
    try:
        FreeCAD.ParamGet(_PARAM_PATH).SetBool("EnablePerfProfiler", bool(val))
    except Exception as e:
        dm_logger.debug(f"set_perf_profiler_enabled failed: {e}")
```

Add `from core import dm_logger` at the top of the file if not already imported.

---

### [x] CQ-005: Replace FreeCAD.Console.PrintMessage in sdf_baker.py

**File:** `core/sdf/sdf_baker.py` — lines 70–74

**What:** The perf profiler logging block uses `FreeCAD.Console.PrintMessage()` directly
instead of `dm_logger`. This violates the project logging convention.

**Implementation:**
```python
# BEFORE (lines 67-74):
    from core.dm_object import get_perf_profiler_enabled
    if get_perf_profiler_enabled():
        n_pts = pts.shape[0]
        FreeCAD.Console.PrintMessage(
            f"[bake] grid={nx}x{ny}x{nz} ({n_pts} pts) cell={cell_size:.1f}mm | "
            f"meshgrid={1000*(t1-t0):.1f}ms  eval={1000*(t2-t1):.1f}ms  "
            f"reshape+tobytes={1000*(t3-t2):.1f}ms  total={1000*(t3-t0):.1f}ms\n"
        )

# AFTER:
    from core.dm_object import get_perf_profiler_enabled
    if get_perf_profiler_enabled():
        n_pts = pts.shape[0]
        from core import dm_logger
        dm_logger.info(
            f"[bake] grid={nx}x{ny}x{nz} ({n_pts} pts) cell={cell_size:.1f}mm | "
            f"meshgrid={1000*(t1-t0):.1f}ms  eval={1000*(t2-t1):.1f}ms  "
            f"reshape+tobytes={1000*(t3-t2):.1f}ms  total={1000*(t3-t0):.1f}ms"
        )
```

---

### CQ-006: Fix BUG-1 — get_projected_point() undefined `result` variable

**File:** `core/input_manager.py` — `get_projected_point()` method (starts at line 526)

**What:** Verify the return path in `get_projected_point()`. If any code path returns an
undefined variable named `result`, rename it to the computed value. The fix hint is
"Rename return var" — scan the method body for `return result` where `result` was never
assigned and replace with the correct expression.

**Implementation:**
1. Read `get_projected_point()` in full (lines 526–619).
2. Find any `return result` line where `result` is not previously assigned in that branch.
3. Replace `return result` with `return base_point_3d + normal_3d_copy * world_delta`
   (the expression that should have been assigned to `result`).
4. If no such bug exists (already fixed), mark this task done with a note.

---

### [x] CQ-007: Fix BUG-2 — E-key handler missing SdfEditTool dispatch

**File:** `core/input_manager.py` — E-key handler block (around line 261–278)

**What:** The E-key handler at line 275–277 dispatches `edit_tool.activate()` for `st == "sdf"`,
but the arch report confirms that `SdfEditTool` is unreachable via E-key. Verify the dispatch
actually calls `SdfEditTool` (not the NURBS `EditTool`). If `_et.activate()` is actually
`SdfEditTool`, it is correct. If it activates the wrong tool, fix it.

**Implementation:**
Read lines 261–278. Verify that when `st == "sdf"`, the code calls
`SdfEditTool().activate()` not `EditTool().activate()`. Fix if wrong:
```python
# CORRECT block for st == "sdf":
elif st == "sdf":
    from tools.edit_tool import SdfEditTool
    SdfEditTool().activate(); return True
```

---

### [x] CQ-008: Update README.md folder structure

**File:** `README.md` — lines 74–129 (folder structure section)

**What:** The README lists stale file names and is missing many actual files. Update to match
the real codebase layout.

---

### [x] CQ-009: Update README.md toolbar status table

**File:** `README.md` — toolbar status table

**What:** The status table shows Box, Sphere, Cylinder as "Planned" but they are fully
implemented. Update their status to "Implemented".

---

## [x] Tier 2 — Sparse Octree Infrastructure (Do Before Meshers)

Create `SdfOctreeCache` — the hierarchical evaluator that replaces `np.meshgrid`. This is the
prerequisite for all mesher migration tasks in Tier 3.

### [x] SP-001: Create SdfOctreeCache class with build()

**File:** New file `core/sdf/sdf_octree.py` — create from scratch

**What:** Implement a top-down octree evaluator. Starting from the field's bounding box,
recursively subdivides cells until `leaf_size`, culling cells where the SDF magnitude at the
cell center exceeds half the cell diagonal (guaranteed no surface crossing). Stores leaf cell
corner SDF values in a flat dict keyed by `(level, ix, iy, iz)`.

### [x] SP-002: Implement Octree walk_leaves() and narrow-band logic
**What:** Add traversal method to iterate over populated leaves and extract narrow-band surface points for baker consumption.

### [x] SP-003: Add SdfOctreeCache.query()
**What:** Implement point-in-octree lookup for evaluating the field at arbitrary coordinates using cached data.

### [x] SP-005: Add SdfOctreeCache.narrow_band_points()
**What:** Extract the set of points lying within the narrow band of the SDF for meshing or analysis.

---

## [x] Tier 3 — Mesher & Baker Migration

The returned dict format is unchanged so the baked renderer path is unaffected.

**Implementation:**
```python
def bake_sdf_to_volume(field, cell_size: float, bbox_override=None) -> dict:
    """
    Sample the SDF on a sparse narrow-band grid and pack as a float32 3D volume.
    Out-of-band voxels are set to max_dist (positive = outside).
    """
    import math
    import time
    import numpy as np
    import FreeCAD
    from core.sdf.sdf_octree import SdfOctreeCache

    if bbox_override is not None:
        mn, mx = bbox_override
    else:
        mn, mx = field.bounding_box()

    def _pad(lo, hi):
        if hi - lo < 1e-4:
            mid = (lo + hi) / 2
            return mid - 1.0, mid + 1.0
        return lo - cell_size, hi + cell_size

    x0, x1 = _pad(mn.x, mx.x)
    y0, y1 = _pad(mn.y, mx.y)
    z0, z1 = _pad(mn.z, mx.z)

    nx = max(1, int(math.ceil((x1 - x0) / cell_size)))
    ny = max(1, int(math.ceil((y1 - y0) / cell_size)))
    nz = max(1, int(math.ceil((z1 - z0) / cell_size)))
    max_dist = cell_size * 8.0

    t0 = time.perf_counter()

    # Build octree and collect narrow-band points only
    octree = SdfOctreeCache(field, leaf_size=cell_size)
    octree.build()

    # Allocate output volume filled with max_dist (outside)
    vol = np.full((nx + 1, ny + 1, nz + 1), max_dist, dtype=np.float32)

    # Fill only the narrow-band voxels
    for (ix, iy, iz), corners in octree._leaves.items():
        if ix > nx or iy > ny or iz > nz:
            continue
        # The (0,0,0) corner of this leaf maps to grid index (ix, iy, iz)
        vol[ix, iy, iz] = float(corners[0])

    # OpenGL expects (nz+1, ny+1, nx+1) row-major
    volume_bytes = vol.transpose(2, 1, 0).tobytes()
    t1 = time.perf_counter()

    from core.dm_object import get_perf_profiler_enabled
    if get_perf_profiler_enabled():
        from core import dm_logger
        n_filled = len(octree._leaves)
        dm_logger.info(
            f"[bake] grid={nx}x{ny}x{nz} filled={n_filled} leaf_size={cell_size:.1f}mm "
            f"total={1000*(t1-t0):.1f}ms"
        )

    return {
        "volume_bytes": volume_bytes,
        "nx": nx, "ny": ny, "nz": nz,
        "bbox_min": FreeCAD.Vector(x0, y0, z0),
        "bbox_max": FreeCAD.Vector(x0 + nx * cell_size,
                                    y0 + ny * cell_size,
                                    z0 + nz * cell_size),
        "max_dist": max_dist,
    }
```

**Depends on:** SP-001, SP-004

---

### [x] MG-006: Update cmd_sdf_export.py to pass cell_size through mesher

**File:** `commands/cmd_sdf_export.py` — `Activated()` method (lines 42–100)

**What:** The export command already calls `mesher.mesh(field, **params)` where `params`
includes `cell_size`. After the octree migration the mesher accepts the same signature and
builds its own `SdfOctreeCache` internally. No change to the call site is needed.

**Verification:** Read `cmd_sdf_export.py` lines 57–68 and confirm `mesher.mesh(field, **params)`
passes `cell_size` in `params`. If it does, mark this task done. If `cell_size` is passed
as a positional argument rather than via `**params`, fix it:

```python
result = mesher.mesh(field, cell_size=params['cell_size'],
                     decimate=params.get('decimate', False),
                     deduplicate=params.get('deduplicate', True))
```

**Depends on:** MG-001

---

## Tier 4 — NURBS SDF Fields

Add `SdfNurbsCurveField` and `SdfNurbsSurfaceField` subclasses of `SdfField`. These provide
both CPU `evaluate()` and GPU `to_glsl()` paths for arbitrary NURBS geometry.

This tier can proceed in parallel with Tiers 2–3 as it creates new files only.

See `.agents/skills/dm_nurbs_sdf/SKILL.md` for FreeCAD NURBS API reference and
the `to_glsl()` contract.

### [x] NS-001: Create SdfNurbsCurveField (CPU only)

**File:** New file `core/sdf/sdf/nurbs_curve.py` — create from scratch

**What:** `SdfNurbsCurveField(SdfField)` evaluates signed distance from any point to a 3D
NURBS curve stored as a `Part.BSplineCurve`. Distance is unsigned (tubes around the curve);
sign is negative inside a tube of `tube_radius` and positive outside.

**Implementation:**
```python
"""
core/sdf/sdf/nurbs_curve.py

SDF field for a 3D NURBS (BSpline) curve. Evaluates the distance from
any point to the closest point on the curve.
"""
import numpy as np
import FreeCAD
from core.sdf.sdf_field import SdfField


class SdfNurbsCurveField(SdfField):
    """
    Signed distance to a 3D NURBS curve with a tube radius.

    Negative inside the tube, positive outside.
    f(p) = closest_distance(p, curve) - tube_radius
    """

    def __init__(self, bspline_curve, tube_radius: float = 1.0,
                 placement: FreeCAD.Placement = None):
        """
        bspline_curve: Part.BSplineCurve (FreeCAD)
        tube_radius:   radius of the implicit tube around the curve (mm)
        placement:     optional world transform for the curve
        """
        self.curve = bspline_curve
        self.tube_radius = tube_radius
        self.placement = placement

    def _world_to_local(self, point: FreeCAD.Vector) -> FreeCAD.Vector:
        if self.placement is None:
            return point
        return self.placement.inverse().multVec(point)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        local_pt = self._world_to_local(point)
        try:
            param = self.curve.parameter(local_pt)
            closest = self.curve.value(param)
            dist = (local_pt - closest).Length
        except Exception:
            dist = float('inf')
        return dist - self.tube_radius

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        results = np.empty(len(points), dtype=np.float32)
        for i, pt in enumerate(points):
            fpt = FreeCAD.Vector(float(pt[0]), float(pt[1]), float(pt[2]))
            results[i] = self.evaluate(fpt)
        return results

    def bounding_box(self):
        try:
            bb = self.curve.toBSpline().toShape().BoundBox
            pad = self.tube_radius
            mn = FreeCAD.Vector(bb.XMin - pad, bb.YMin - pad, bb.ZMin - pad)
            mx = FreeCAD.Vector(bb.XMax + pad, bb.YMax + pad, bb.ZMax + pad)
            if self.placement:
                mn = self.placement.multVec(mn)
                mx = self.placement.multVec(mx)
            return mn, mx
        except Exception:
            from core import dm_logger
            dm_logger.debug("SdfNurbsCurveField.bounding_box fallback to unit cube")
            return FreeCAD.Vector(-10, -10, -10), FreeCAD.Vector(10, 10, 10)

    def to_glsl(self, ctx, point_var="p"):
        raise NotImplementedError(
            "SdfNurbsCurveField.to_glsl() not yet implemented — see NS-004"
        )
```

---

### [x] NS-002: Create Sdf2dNurbsCurveField (CPU only)

**File:** New file `core/sdf/sdf2d/nurbs_curve.py` — create from scratch

**What:** `Sdf2dNurbsCurveField(Sdf2dField)` — 2D signed distance from a point to a closed
NURBS profile on a plane. Sign is determined by winding number: negative inside, positive
outside.

**Implementation:**
```python
"""
core/sdf/sdf2d/nurbs_curve.py

2D SDF field for a closed NURBS profile. Used for extrusion profiles.
"""
import numpy as np
import FreeCAD
from core.sdf.sdf2d.sdf2d_field import Sdf2dField


class Sdf2dNurbsCurveField(Sdf2dField):
    """
    2D signed distance to a closed NURBS (BSpline) curve.

    Negative inside, positive outside.
    Distance = closest_point_distance, signed by winding number.
    """

    def __init__(self, bspline_curve_2d, sample_count: int = 64):
        """
        bspline_curve_2d: Part.BSplineCurve in 2D (z=0 plane)
        sample_count:     number of polyline samples for winding number
        """
        self.curve = bspline_curve_2d
        self.sample_count = sample_count
        self._samples = None  # lazy-initialized (N,2) polyline

    def _get_samples(self):
        if self._samples is not None:
            return self._samples
        try:
            u0, u1 = self.curve.FirstParameter, self.curve.LastParameter
            params = np.linspace(u0, u1, self.sample_count + 1)[:-1]
            pts = []
            for u in params:
                v = self.curve.value(u)
                pts.append([v.x, v.y])
            self._samples = np.array(pts, dtype=np.float64)
        except Exception:
            from core import dm_logger
            dm_logger.debug("Sdf2dNurbsCurveField._get_samples failed")
            self._samples = np.zeros((4, 2), dtype=np.float64)
        return self._samples

    def evaluate_2d(self, x: float, y: float) -> float:
        samples = self._get_samples()
        p = np.array([x, y])
        # Closest point distance
        deltas = samples - p
        dists = np.linalg.norm(deltas, axis=1)
        min_dist = float(np.min(dists))
        # Winding number for sign
        winding = _winding_number_2d(p, samples)
        sign = -1.0 if winding != 0 else 1.0
        return sign * min_dist

    def to_glsl_2d(self, ctx, point_var="p2"):
        raise NotImplementedError(
            "Sdf2dNurbsCurveField.to_glsl_2d() not yet implemented — see NS-005"
        )


def _winding_number_2d(point, polygon: np.ndarray) -> int:
    """Winding number test. Returns non-zero if point is inside polygon."""
    wn = 0
    n = len(polygon)
    px, py = point[0], point[1]
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if y1 <= py:
            if y2 > py:
                if (x2 - x1) * (py - y1) - (px - x1) * (y2 - y1) > 0:
                    wn += 1
        else:
            if y2 <= py:
                if (x2 - x1) * (py - y1) - (px - x1) * (y2 - y1) < 0:
                    wn -= 1
    return wn
```

---

### [x] NS-003: Create SdfNurbsSurfaceField (CPU only)

**File:** New file `core/sdf/sdf/nurbs_surface.py` — create from scratch

**What:** `SdfNurbsSurfaceField(SdfField)` evaluates signed distance from any point to a
NURBS surface. Distance uses `Part.BSplineSurface.UVProjPoint()` for closest-point queries;
sign is determined by the surface normal dot product.

**Implementation:**
```python
"""
core/sdf/sdf/nurbs_surface.py

SDF field for a NURBS surface (solid boundary — negative inside, positive outside).
"""
import numpy as np
import FreeCAD
import Part
from core.sdf.sdf_field import SdfField
from core import dm_logger


class SdfNurbsSurfaceField(SdfField):
    """
    Signed distance to a NURBS surface.

    Positive outside the solid bounded by the surface, negative inside.
    Uses UVProjPoint for closest-point query and surface normal for sign.
    """

    def __init__(self, bspline_surface, placement: FreeCAD.Placement = None):
        """
        bspline_surface: Part.BSplineSurface (FreeCAD)
        placement:       optional world transform
        """
        self.surface = bspline_surface
        self.placement = placement

    def _world_to_local(self, point: FreeCAD.Vector) -> FreeCAD.Vector:
        if self.placement is None:
            return point
        return self.placement.inverse().multVec(point)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        local_pt = self._world_to_local(point)
        try:
            u, v = self.surface.UVProjPoint(local_pt)
            closest = self.surface.value(u, v)
            normal = self.surface.normal(u, v)
            diff = local_pt - closest
            dist = diff.Length
            sign = 1.0 if diff.dot(normal) >= 0.0 else -1.0
            return sign * dist
        except Exception as e:
            dm_logger.debug(f"SdfNurbsSurfaceField.evaluate failed: {e}")
            return float('inf')

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        results = np.empty(len(points), dtype=np.float32)
        for i, pt in enumerate(points):
            fpt = FreeCAD.Vector(float(pt[0]), float(pt[1]), float(pt[2]))
            results[i] = self.evaluate(fpt)
        return results

    def bounding_box(self):
        try:
            face = Part.Face(self.surface)
            bb = face.BoundBox
            pad = 1.0
            mn = FreeCAD.Vector(bb.XMin - pad, bb.YMin - pad, bb.ZMin - pad)
            mx = FreeCAD.Vector(bb.XMax + pad, bb.YMax + pad, bb.ZMax + pad)
            if self.placement:
                mn = self.placement.multVec(mn)
                mx = self.placement.multVec(mx)
            return mn, mx
        except Exception as e:
            dm_logger.debug(f"SdfNurbsSurfaceField.bounding_box failed: {e}")
            return FreeCAD.Vector(-50, -50, -50), FreeCAD.Vector(50, 50, 50)

    def to_glsl(self, ctx, point_var="p"):
        raise NotImplementedError(
            "SdfNurbsSurfaceField.to_glsl() not yet implemented — see NS-006"
        )
```

---

### [x] NS-004: Add to_glsl() for SdfNurbsCurveField

**File:** `core/sdf/sdf/nurbs_curve.py` — replace the `to_glsl()` stub in `SdfNurbsCurveField`

**What:** Pass BSpline control points as a uniform array. In the shader, implement Newton-Raphson
iterative closest-point search along the curve (6–8 iterations from an initial parameter
estimate). For curves with ≤ 16 control points, pass the control point array directly as a
`vec3[16]` uniform. Return `length(p - closest) - tube_radius`.

See `.agents/skills/dm_nurbs_sdf/SKILL.md` for the GLSL Newton-Raphson template and
GlslContext usage details.

**Implementation:**
```python
    def to_glsl(self, ctx, point_var="p"):
        try:
            poles = self.curve.getPoles()   # list of FreeCAD.Vector
        except Exception as e:
            from core import dm_logger
            dm_logger.debug(f"SdfNurbsCurveField.to_glsl: getPoles() failed: {e}")
            return f"(length({point_var}) - {self.tube_radius:.6f})"

        n_poles = len(poles)
        if n_poles > 32:
            # Too many control points for inline GLSL uniform array — fall back
            from core import dm_logger
            dm_logger.debug("SdfNurbsCurveField.to_glsl: >32 poles, using evaluate_grid fallback")
            return f"(length({point_var}) - {self.tube_radius:.6f})"

        ctx.need_helper("nurbs_curve_sdf")
        poles_flat = []
        for p in poles:
            poles_flat.extend([p.x, p.y, p.z])
        poles_uniform = ctx.uniform(f"vec3[{n_poles}]", poles_flat)
        r_uniform     = ctx.uniform("float", self.tube_radius)
        n_uniform     = ctx.uniform("int",   n_poles)

        if self.inv_matrix is not None:
            ctx.need_helper("apply_inv_mat")
            m = ctx.uniform("mat4", self.inv_matrix.tolist())
            return (f"nurbs_curve_sdf(apply_inv_mat({m}, {point_var}), "
                    f"{poles_uniform}, {n_uniform}, {r_uniform})")
        return f"nurbs_curve_sdf({point_var}, {poles_uniform}, {n_uniform}, {r_uniform})"
```

Also add the GLSL helper to `GLSL_HELPERS` in `core/sdf/glsl_compiler.py`:
```python
    "nurbs_curve_sdf": """
float nurbs_curve_dist(vec3 p, vec3 poles[32], int n) {
    // De Casteljau closest-point via Newton-Raphson (6 iterations)
    float t = 0.5;
    for (int iter = 0; iter < 6; iter++) {
        // Evaluate curve and tangent at t using de Casteljau on poles[0..n-1]
        // (simplified: treat as Bezier with n poles, t in [0,1])
        vec3 pts[32];
        for (int i = 0; i < n; i++) pts[i] = poles[i];
        for (int k = 1; k < n; k++)
            for (int i = 0; i < n - k; i++)
                pts[i] = mix(pts[i], pts[i+1], t);
        vec3 curve_pt  = pts[0];
        // Tangent: one degree lower
        vec3 tpts[32];
        for (int i = 0; i < n; i++) tpts[i] = poles[i];
        for (int k = 1; k < n - 1; k++)
            for (int i = 0; i < n - 1 - k; i++)
                tpts[i] = mix(tpts[i], tpts[i+1], t);
        vec3 tangent = float(n - 1) * (tpts[1] - tpts[0]);
        // Newton step
        float denom = dot(tangent, tangent);
        if (denom < 1e-10) break;
        t -= dot(curve_pt - p, tangent) / denom;
        t = clamp(t, 0.0, 1.0);
    }
    // Final evaluation at converged t
    vec3 tpts2[32];
    for (int i = 0; i < n; i++) tpts2[i] = poles[i];
    for (int k = 1; k < n; k++)
        for (int i = 0; i < n - k; i++)
            tpts2[i] = mix(tpts2[i], tpts2[i+1], t);
    return length(p - tpts2[0]);
}
float nurbs_curve_sdf(vec3 p, vec3 poles[32], int n, float r) {
    return nurbs_curve_dist(p, poles, n) - r;
}
""",
```

**Depends on:** NS-001

---

### [x] NS-005: Add to_glsl_2d() for Sdf2dNurbsCurveField

**File:** `core/sdf/sdf2d/nurbs_curve.py` — replace the `to_glsl_2d()` stub

**What:** Convert the BSpline to piecewise cubic Bezier segments (at most 16 segments) and
reuse the existing `sd_cubic_bez_2d` helper in `GLSL_HELPERS` if available. Each segment's
four control points are passed as uniforms. The final distance is the min over all segments,
with winding number sign from a polyline winding test.

See `.agents/skills/dm_nurbs_sdf/SKILL.md` for the GLSL winding-number template.

**Implementation:**
```python
    def to_glsl_2d(self, ctx, point_var="p2"):
        samples = self._get_samples()
        n = len(samples)
        # Pass polyline as vec2 array for winding + distance
        if n > 64:
            return f"0.0"  # fallback — too many points
        ctx.need_helper("nurbs_profile_sdf_2d")
        pts_flat = [float(v) for row in samples for v in row]
        pts_u = ctx.uniform(f"vec2[{n}]", pts_flat)
        n_u   = ctx.uniform("int", n)
        return f"nurbs_profile_sdf_2d({point_var}, {pts_u}, {n_u})"
```

Add helper to `GLSL_HELPERS`:
```python
    "nurbs_profile_sdf_2d": """
float nurbs_profile_sdf_2d(vec2 p, vec2 pts[64], int n) {
    float min_d = 1e20;
    int wn = 0;
    for (int i = 0; i < n; i++) {
        vec2 a = pts[i], b = pts[(i + 1) % n];
        // Distance to segment
        vec2 pa = p - a, ba = b - a;
        float h = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0);
        min_d = min(min_d, length(pa - ba * h));
        // Winding
        if (a.y <= p.y) { if (b.y > p.y && cross(vec3(ba, 0.0), vec3(pa, 0.0)).z > 0.0) wn++; }
        else             { if (b.y <= p.y && cross(vec3(ba, 0.0), vec3(pa, 0.0)).z < 0.0) wn--; }
    }
    return min_d * (wn != 0 ? -1.0 : 1.0);
}
""",
```

**Depends on:** NS-002

---

### [x] NS-006: Add to_glsl() for SdfNurbsSurfaceField (triangle proxy approach)

**File:** `core/sdf/sdf/nurbs_surface.py` — replace the `to_glsl()` stub

**What:** Discretize the NURBS surface to a triangle proxy (≤ 64 triangles) and compute
distance to the triangle set in GLSL. Pass triangle vertex positions as a uniform array.
Sign is determined by the per-triangle normal dot product.

**Implementation:**
```python
    def to_glsl(self, ctx, point_var="p"):
        try:
            import Part
            face = Part.Face(self.surface)
            mesh = face.tessellate(1.0)   # coarse tessellation for GLSL proxy
            verts, tris = mesh
        except Exception as e:
            from core import dm_logger
            dm_logger.debug(f"SdfNurbsSurfaceField.to_glsl tessellate failed: {e}")
            return f"length({point_var})"

        if len(tris) > 64:
            tris = tris[:64]

        ctx.need_helper("sdf_tri_proxy")
        flat_verts = []
        for tri in tris:
            for vi in tri:
                v = verts[vi]
                flat_verts.extend([v.x, v.y, v.z])
        n_tris = len(tris)
        tv = ctx.uniform(f"vec3[{n_tris * 3}]", flat_verts)
        nt = ctx.uniform("int", n_tris)
        return f"sdf_tri_proxy({point_var}, {tv}, {nt})"
```

Add helper to `GLSL_HELPERS` in `glsl_compiler.py`:
```python
    "sdf_tri_proxy": """
float sdf_tri(vec3 p, vec3 a, vec3 b, vec3 c) {
    vec3 ba = b-a, pa = p-a, cb = c-b, pb = p-b, ac = a-c, pc = p-c;
    vec3 n = cross(ba, ac);
    return sqrt(
        (sign(dot(cross(ba,n),pa)) + sign(dot(cross(cb,n),pb)) + sign(dot(cross(ac,n),pc)) < 2.0)
        ? min(min(
            dot(ba*clamp(dot(ba,pa)/dot(ba,ba),0.,1.)-pa, ba*clamp(dot(ba,pa)/dot(ba,ba),0.,1.)-pa),
            dot(cb*clamp(dot(cb,pb)/dot(cb,cb),0.,1.)-pb, cb*clamp(dot(cb,pb)/dot(cb,cb),0.,1.)-pb)),
            dot(ac*clamp(dot(ac,pc)/dot(ac,ac),0.,1.)-pc, ac*clamp(dot(ac,pc)/dot(ac,ac),0.,1.)-pc))
        : dot(n,pa)*dot(n,pa)/dot(n,n));
}
float sdf_tri_proxy(vec3 p, vec3 tv[192], int n) {
    float d = 1e20;
    vec3 best_n = vec3(0,1,0);
    for (int i = 0; i < n; i++) {
        vec3 a = tv[i*3], b = tv[i*3+1], c = tv[i*3+2];
        float di = sdf_tri(p, a, b, c);
        if (di < d) { d = di; best_n = normalize(cross(b-a, c-a)); }
    }
    float sign_ = dot(p - tv[0], best_n) >= 0.0 ? 1.0 : -1.0;
    return sign_ * sqrt(d);
}
""",
```

**Depends on:** NS-003

---

### [x] NS-007: Wire NURBS SDF fields to DMObjectProxy

**File:** `core/dm_object.py` — `DMObjectProxy.execute()` method

**What:** When a DMObject has a NURBS curve or surface stored in its `Shape` property, auto-
generate an `SdfNurbsCurveField` or `SdfNurbsSurfaceField` and register it on
`DMSceneRayMarchRenderer`. Look for the section in `execute()` that handles `ShapeType`.

**Implementation:**
1. Read `DMObjectProxy.execute()` to find where `ShapeType` is checked.
2. Add a branch for `ShapeType == "curve"`:
```python
elif shape_type == "curve":
    from core.sdf.sdf.nurbs_curve import SdfNurbsCurveField
    curve = fp.Shape.Edges[0].Curve if fp.Shape.Edges else None
    if curve is not None:
        sdf_field = SdfNurbsCurveField(curve, tube_radius=getattr(fp, "TubeRadius", 1.0))
        proxy.SdfField = sdf_field
```
3. Add a branch for `ShapeType == "surface"`:
```python
elif shape_type == "surface":
    from core.sdf.sdf.nurbs_surface import SdfNurbsSurfaceField
    face = fp.Shape.Faces[0] if fp.Shape.Faces else None
    if face is not None:
        sdf_field = SdfNurbsSurfaceField(face.Surface)
        proxy.SdfField = sdf_field
```

**Depends on:** NS-001, NS-003

---

### [x] NS-008: Add NurbsProfileExtrusion convenience in cmd_curve_sdf.py

**File:** `commands/cmd_curve_sdf.py` — `Activated()` method

**What:** When the user runs this command with a closed NURBS curve selected on a workplane,
create an `SdfExtrusionField` using a `Sdf2dNurbsCurveField` profile. Replace any hardcoded
`Sdf2dBezierCurveField` references with the new NURBS profile type.

**Implementation:**
1. Read `cmd_curve_sdf.py` in full to find where the 2D profile field is created.
2. Replace:
```python
# OLD
from core.sdf.sdf2d.bezier_curve import Sdf2dBezierCurveField
profile = Sdf2dBezierCurveField(control_points)
```
with:
```python
# NEW
from core.sdf.sdf2d.nurbs_curve import Sdf2dNurbsCurveField
profile = Sdf2dNurbsCurveField(curve.Curve, sample_count=64)
```
3. If the command already uses a generic profile type, just add support for
   `Part.BSplineCurve` inputs alongside `Part.BezierCurve`.

**Depends on:** NS-002

---

## Tier 5 — Pipeline Integration & Caching

Wire the octree into all tool paths and add shared caching.

### [x] PI-001: Create SdfCacheManager singleton

**File:** New file `core/sdf/sdf_cache_manager.py` — create from scratch

**What:** Singleton that holds `SdfOctreeCache` instances keyed by `(field_id, leaf_size)`.
Invalidates cache when the field's parameters change. Tools call
`SdfCacheManager.get(field, leaf_size)` to get or build a cache.

**Implementation:**
```python
"""
core/sdf/sdf_cache_manager.py

Singleton cache for SdfOctreeCache instances.
Tools call get() to avoid rebuilding the octree on every operation.
"""
from core import dm_logger


class SdfCacheManager:
    _instance = None
    _cache = {}   # (field_id, leaf_size) → SdfOctreeCache

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get(self, field, leaf_size: float, force_rebuild: bool = False):
        """
        Return a built SdfOctreeCache for field at leaf_size.
        Builds it if not cached. force_rebuild=True ignores the cache.
        """
        from core.sdf.sdf_octree import SdfOctreeCache
        key = (id(field), round(leaf_size, 6))
        if not force_rebuild and key in self._cache:
            return self._cache[key]
        dm_logger.debug(f"SdfCacheManager: building octree leaf_size={leaf_size:.3f}mm")
        cache = SdfOctreeCache(field, leaf_size=leaf_size)
        cache.build()
        self._cache[key] = cache
        return cache

    def invalidate(self, field=None):
        """
        Remove all cached octrees for field (or all if field is None).
        Call this when a field's parameters change.
        """
        if field is None:
            self._cache.clear()
            dm_logger.debug("SdfCacheManager: full invalidation")
        else:
            keys_to_remove = [k for k in self._cache if k[0] == id(field)]
            for k in keys_to_remove:
                del self._cache[k]
            if keys_to_remove:
                dm_logger.debug(f"SdfCacheManager: invalidated {len(keys_to_remove)} entries for field {id(field)}")
```

**Depends on:** SP-001

---

### [x] PI-002: Update sdf_slicer.py to accept SdfOctreeCache

**File:** `core/sdf/sdf_slicer.py` — `slice_sdf()` function (around line 81)

**What:** Add an optional `octree_cache` parameter. When provided, use
`octree_cache.query(pt)` instead of `field.evaluate_grid()` for the 2D grid. The 2D
meshgrid for slicing is acceptable (it is 2D not 3D), but cache lookup can be faster
for repeated slices at the same resolution.

**Implementation:**
```python
def slice_sdf(field, plane_placement, cell_size: float, octree_cache=None):
    """
    ...existing docstring...
    octree_cache: optional SdfOctreeCache — if provided, use for faster lookup.
    """
    # ... existing setup code for plane, grid dimensions ...
    
    if octree_cache is not None:
        # Use cache for evaluation
        vals = np.array([
            octree_cache.query(pt)
            for pt in grid_world_pts  # existing 2D grid points in world space
        ], dtype=np.float32).reshape(nx + 1, ny + 1)
    else:
        # Original dense evaluation
        vals = field.evaluate_grid(grid_world_pts).reshape(nx + 1, ny + 1)
```

Read `sdf_slicer.py` fully first to find the exact variable names (`grid_world_pts`, `nx`, `ny`)
before writing the patch.

**Depends on:** SP-002, PI-001

---

### [x] PI-003: Add SdfOctreeCache acceleration to SdfField.ray_march()

**File:** `core/sdf/sdf_field.py` — `ray_march()` method (starts at line 77)

**What:** Accept an optional `octree_cache` parameter. During the sphere-trace loop, if the
octree cache says `query(current_pos) == +inf` (the current cell is in culled space), advance
the ray by `leaf_size` instead of calling `field.evaluate()`.

**Implementation:**
```python
    def ray_march(self, ray_origin, ray_direction, max_steps=256, surface_eps=0.001,
                  octree_cache=None):
        """
        Sphere-trace a ray. If octree_cache is provided, skip evaluation in empty space.
        """
        t = 0.0
        for _ in range(max_steps):
            pos = ray_origin + ray_direction * t
            if octree_cache is not None:
                cached = octree_cache.query(pos)
                if cached == float('inf'):
                    t += octree_cache.leaf_size
                    continue
                d = cached
            else:
                d = self.evaluate(pos)
            if d < surface_eps:
                # hit — compute normal
                normal = self.gradient(pos)
                return pos, normal
            t += d
        return None
```

Note: this is a partial implementation — read the full existing `ray_march()` for the
complete AABB slab test and step-size logic before writing the patch.

**Depends on:** SP-002

---

### [x] PI-004: Add progress callback to SdfOctreeCache.build()

**File:** `core/sdf/sdf_octree.py` — `build()` method

**What:** The `build()` signature already includes `progress_callback=None` in the SP-001
implementation. This task ensures the callback is called at least every 5,000 leaf cells
and that it forwards to a FreeCAD progress bar.

**Implementation:**
The SP-001 task calls `progress_callback(fraction)` every 1000 leaves. Verify this is
actually wired in the implementation. Additionally, create a helper that callers can use:

```python
def make_freecad_progress_callback(label: str = "Building SDF octree..."):
    """
    Returns a progress_callback(fraction) that updates a FreeCAD progress bar.
    Call the returned function with 1.0 to close the bar.
    """
    try:
        import FreeCADGui
        bar = FreeCADGui.updateGui  # minimal update trigger
        seq = FreeCAD.Base.ProgressIndicator()
        seq.start(label, 100)
        started = [True]
        def callback(fraction):
            if not started[0]: return
            seq.next(True)   # advance — no % arg in FreeCAD's API
            if fraction >= 1.0:
                seq.stop()
                started[0] = False
        return callback
    except Exception:
        return lambda f: None  # no-op if FreeCAD GUI not available
```

Place this function at module level in `core/sdf/sdf_octree.py` after the class definition.

**Depends on:** SP-001

---

## Agent Skills

| Skill | Purpose |
|-------|---------|
| `dm_sdf_octree` | Octree data structure contract, walk_leaves() output format, mesher integration pattern |
| `dm_nurbs_sdf` | FreeCAD BSpline API reference, to_glsl() contract for curve/surface fields |
| `dm_sdf_primitive_pattern` | SdfField subclass template — read before implementing NS-001..NS-003 |
| `dm_opengl33_shader` | GlslContext API — read before implementing NS-004..NS-006 |
