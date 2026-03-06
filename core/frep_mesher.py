import time
import FreeCAD
import Mesh
import Part
import numpy as np

from core import dm_logger
from core.frep.frep_field import FRepField
from core.frep.marching_cubes.mc_tables import edgeTable, triTable
from core.dm_object import get_meshing_type

# Pre-convert lookup tables to numpy arrays for fast indexing
_EDGE_TABLE = np.array(edgeTable, dtype=np.int32)

# triTable as (256, 16) int8 array — -1 fills unused triangle slots
_TRI_TABLE  = np.array(triTable,  dtype=np.int8)

# Edge endpoint corner indices (constant, vectorized over active cubes)
_EDGE_C1 = np.array([0,1,2,3, 4,5,6,7, 0,1,2,3], dtype=np.int32)
_EDGE_C2 = np.array([1,2,3,0, 5,6,7,4, 4,5,6,7], dtype=np.int32)

# Corner (dx, dy, dz) offsets in Lorensen-Cline order
_OFF_X = np.array([0,1,1,0, 0,1,1,0], dtype=np.float64)
_OFF_Y = np.array([0,0,1,1, 0,0,1,1], dtype=np.float64)
_OFF_Z = np.array([0,0,0,0, 1,1,1,1], dtype=np.float64)


class MeshTimer:
    """Accumulates timing across multiple mesh() calls.
    Call summary() once (on tool commit) to print a single INFO log.
    """
    _STAGES = [
        "field_eval", "cube_index", "active_filter",
        "corner_extract", "edge_interp", "tri_extract", "mesh_build",
        "mb_list_conv", "mb_mesh_obj", "mb_make_shape", "mb_make_solid"
    ]
    # Sub-stages to indent in summary output
    _SUB_STAGES = {"mb_list_conv", "mb_mesh_obj", "mb_make_shape", "mb_make_solid"}

    def __init__(self):
        self.reset()

    def reset(self):
        self._totals  = {s: 0.0 for s in self._STAGES}
        self._calls   = 0
        self._start   = {}

    def start(self, stage: str):
        self._start[stage] = time.perf_counter()

    def stop(self, stage: str):
        if stage in self._start:
            self._totals[stage] += time.perf_counter() - self._start.pop(stage)

    def tick(self):
        """Call once per mesh() invocation so we can compute averages."""
        self._calls += 1

    def summary(self, label: str = "FRep Mesh"):
        """Emit one INFO log with totals and per-call averages, then reset."""
        from core.dm_object import get_perf_profiler_enabled
        if not get_perf_profiler_enabled():
            self.reset()
            return
            
        if self._calls == 0:
            return
        n = self._calls
        total_ms = sum(self._totals.values()) * 1000
        lines = [f"[PERF] {label} — {n} call(s), {total_ms:.1f} ms total"]
        for s in self._STAGES:
            t = self._totals[s] * 1000
            indent = "    " if s in self._SUB_STAGES else "  "
            lines.append(f"{indent}{s:<18} {t:6.1f} ms  ({t/n:5.2f} ms/call)")
        dm_logger.info("\n".join(lines))
        self.reset()


# Module-level singleton — shared across all mesher calls for the same tool session
mesh_timer = MeshTimer()


class FRepMesher:
    """Abstract base class for all F-Rep meshing protocols."""
    def mesh(self, field: FRepField, resolution: int) -> Part.Shape:
        raise NotImplementedError("mesher must implement mesh()")


class MarchingCubesMesher(FRepMesher):
    """
    Uniform-grid Marching Cubes.

    Vectorization levels:
      1. Field evaluation      — NumPy batch (per-field override)
      2. cube_index            — 8 shifted array views, no Python loop
      3. Active-cube filter    — np.argwhere, skips empty/full cubes
      4. Corner extraction     — (M,8) broadcast, no loop
      5. Edge interpolation    — (M,12,3) batch, no loop
      6. Triangle extraction   — (M,5,3) reshape + np.where, no loop
      7. Mesh build            — flat (N,3,3) ndarray -> Mesh.Mesh
    """
    def mesh(self, field: FRepField, resolution: int) -> Part.Shape:
        min_b, max_b = field.bounding_box()

        def _pad(lo, hi):
            if hi - lo < 1e-4:
                mid = (lo + hi) / 2
                return mid - 1.0, mid + 1.0
            return lo, hi

        x0, x1 = _pad(min_b.x, max_b.x)
        y0, y1 = _pad(min_b.y, max_b.y)
        z0, z1 = _pad(min_b.z, max_b.z)

        R = int(resolution)
        mesh_timer.tick()

        # ── 1. Sample the scalar field on a regular grid ──────────────────────
        x = np.linspace(x0, x1, R + 1)
        y = np.linspace(y0, y1, R + 1)
        z = np.linspace(z0, z1, R + 1)

        X, Y, Z = np.meshgrid(x, y, z, indexing='ij')
        pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])

        mesh_timer.start("field_eval")
        vals = field.evaluate_grid(pts).reshape(R + 1, R + 1, R + 1)
        mesh_timer.stop("field_eval")

        # ── 2. Vectorised cube classification ─────────────────────────────────
        mesh_timer.start("cube_index")
        neg = vals < 0
        v0 = neg[:-1,:-1,:-1]; v1 = neg[1:, :-1,:-1]
        v2 = neg[1:, 1:, :-1]; v3 = neg[:-1,1:, :-1]
        v4 = neg[:-1,:-1,1:];  v5 = neg[1:, :-1,1:]
        v6 = neg[1:, 1:, 1:];  v7 = neg[:-1,1:, 1:]

        cube_idx = (
            v0.astype(np.uint8)        | (v1.astype(np.uint8) << 1) |
            (v2.astype(np.uint8) << 2) | (v3.astype(np.uint8) << 3) |
            (v4.astype(np.uint8) << 4) | (v5.astype(np.uint8) << 5) |
            (v6.astype(np.uint8) << 6) | (v7.astype(np.uint8) << 7)
        )
        mesh_timer.stop("cube_index")

        # ── 3. Filter to active cubes only ─────────────────────────────────────
        mesh_timer.start("active_filter")
        active = np.argwhere((cube_idx != 0) & (cube_idx != 255))
        mesh_timer.stop("active_filter")

        if active.size == 0:
            return Part.Shape()
        ai, aj, ak = active[:, 0], active[:, 1], active[:, 2]
        ci = cube_idx[ai, aj, ak]
        M  = len(ai)

        # ── 4. Corner values and positions, batched ────────────────────────────
        mesh_timer.start("corner_extract")
        dx = x[1] - x[0];  dy = y[1] - y[0];  dz = z[1] - z[0]

        val_views = [vals[:-1,:-1,:-1], vals[1:,:-1,:-1],
                     vals[1:,1:,:-1],   vals[:-1,1:,:-1],
                     vals[:-1,:-1,1:],  vals[1:,:-1,1:],
                     vals[1:,1:,1:],    vals[:-1,1:,1:]]
        cv = np.stack([vv[ai, aj, ak] for vv in val_views], axis=1)  # (M,8)

        cx = x[ai]; cy = y[aj]; cz = z[ak]
        cp = np.empty((M, 8, 3), dtype=np.float64)
        cp[:, :, 0] = cx[:, None] + _OFF_X[None, :] * dx
        cp[:, :, 1] = cy[:, None] + _OFF_Y[None, :] * dy
        cp[:, :, 2] = cz[:, None] + _OFF_Z[None, :] * dz
        mesh_timer.stop("corner_extract")

        # ── 5. Vectorised edge interpolation — (M, 12, 3) ─────────────────────
        mesh_timer.start("edge_interp")
        v1e = cv[:, _EDGE_C1]           # (M, 12)
        v2e = cv[:, _EDGE_C2]           # (M, 12)
        p1e = cp[:, _EDGE_C1, :]        # (M, 12, 3)
        p2e = cp[:, _EDGE_C2, :]        # (M, 12, 3)

        dv   = v2e - v1e
        safe = np.abs(dv) > 1e-8
        t    = np.where(safe, -v1e / np.where(safe, dv, 1.0), 0.0)
        edge_pts = p1e + t[:, :, None] * (p2e - p1e)  # (M, 12, 3)
        mesh_timer.stop("edge_interp")

        # ── 6. Triangle extraction — fully vectorised ──────────────────────────
        mesh_timer.start("tri_extract")
        tri_arr = _TRI_TABLE[ci].astype(np.int32)          # (M, 16)
        tri5    = tri_arr[:, :15].reshape(M, 5, 3)          # (M, 5, 3)
        valid   = np.all(tri5 != -1, axis=2)               # (M, 5)
        mi, ti  = np.where(valid)
        mesh_timer.stop("tri_extract")

        if mi.size == 0:
            return Part.Shape()

        ei   = tri5[mi, ti]                  # (N_tris, 3) edge indices
        # Reversed winding [0,2,1] for correct outward normals with SDF negative-inside
        pts1 = edge_pts[mi, ei[:, 0]]
        pts2 = edge_pts[mi, ei[:, 2]]
        pts3 = edge_pts[mi, ei[:, 1]]
        tri_verts = np.stack([pts1, pts2, pts3], axis=1)   # (N_tris, 3, 3)

        # ── 7. Return raw triangle arrays for Coin3D rendering ────────────────
        # No Part.Shape / makeShapeFromMesh needed.
        # Returns:
        #   verts:    (N_tris*3, 3) float32  — one vertex per triangle corner (no dedup needed for rendering)
        #   flat_idx: (N_tris*4,) int32     — flat [0,1,2,-1, 3,4,5,-1, ...] for SoIndexedFaceSet
        mesh_timer.start("mesh_build")

        # Flat vertex array: each of the N_tris triangles has 3 unique vertices
        flat_verts = tri_verts.reshape(-1, 3).astype(np.float32)  # (N_tris*3, 3)

        # Sequential indices: tri i uses vertices [3i, 3i+1, 3i+2]
        n_tris = len(mi)
        base = np.arange(n_tris, dtype=np.int32) * 3   # [0, 3, 6, ...]
        tri_idx = np.stack([base, base+1, base+2], axis=1)  # (N_tris, 3)
        sentinel = np.full((n_tris, 1), -1, dtype=np.int32)
        flat_idx = np.hstack([tri_idx, sentinel]).ravel()   # (N_tris*4,)

        mesh_timer.stop("mesh_build")
        return flat_verts, flat_idx





class AdaptiveMCMesher(FRepMesher):
    def mesh(self, field: FRepField, resolution: int) -> Part.Shape:
        dm_logger.warn("AdaptiveMCMesher not fully implemented. Falling back.")
        return MarchingCubesMesher().mesh(field, resolution)


class NurbsFRepMesher(FRepMesher):
    def mesh(self, field: FRepField, resolution: int) -> Part.Shape:
        dm_logger.warn("NurbsFRepMesher not fully implemented. Falling back.")
        return MarchingCubesMesher().mesh(field, resolution)


def get_active_mesher(type_override=None) -> FRepMesher:
    """Return the active mesher based on global settings or a specific type override."""
    st = type_override if type_override is not None else get_meshing_type()
    if st == 1:   return AdaptiveMCMesher()
    elif st == 2: return NurbsFRepMesher()
    else:         return MarchingCubesMesher()
