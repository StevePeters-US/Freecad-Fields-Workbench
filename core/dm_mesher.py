import time
import Part
import FreeCAD
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
            if stage not in self._totals:
                self._totals[stage] = 0.0
            self._totals[stage] += time.perf_counter() - self._start.pop(stage)

    def tick(self):
        """Call once per mesh() invocation so we can compute averages."""
        self._calls += 1

    def summary(self, label: str = "DM Mesh"):
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
        
        # Display stages in _STAGES order first, then any other timed stages
        dynamic_stages = sorted([s for s in self._totals if s not in self._STAGES and self._totals[s] > 0])
        all_ordered = self._STAGES + dynamic_stages
        
        for s in all_ordered:
            if s not in self._totals: continue
            t = self._totals[s] * 1000
            if t == 0 and s not in self._STAGES: continue
            indent = "    " if s in self._SUB_STAGES else "  "
            lines.append(f"{indent}{s:<18} {t:6.1f} ms  ({t/n:5.2f} ms/call)")
        dm_logger.info("\n".join(lines))
        self.reset()


# Module-level singleton — shared across all mesher calls for the same tool session
mesh_timer = MeshTimer()


class DMMesher:
    """Abstract base class for all Direct Modeling SDF meshers."""
    def mesh(self, field: FRepField, cell_size: float) -> tuple:
        raise NotImplementedError("mesher must implement mesh()")


class MarchingCubesMesher(DMMesher):
    """
    Uniform-grid Marching Cubes.

    Uses an absolute cell_size (in mm) to ensure consistent triangle density
    regardless of the object's overall dimensions.

    Vectorization levels:
      1. Field evaluation      — NumPy batch (per-field override)
      2. cube_index            — 8 shifted array views, no Python loop
      3. Active-cube filter    — np.argwhere, skips empty/full cubes
      4. Corner extraction     — (M,8) broadcast, no loop
      5. Edge interpolation    — (M,12,3) batch, no loop
      6. Triangle extraction   — (M,5,3) reshape + np.where, no loop
      7. Mesh build            — flat (N,3,3) ndarray -> Coin3D arrays
    """
    def mesh(self, field: FRepField, cell_size: float) -> tuple:
        min_b, max_b = field.bounding_box()

        def _pad(lo, hi):
            if hi - lo < 1e-4:
                mid = (lo + hi) / 2
                return mid - 1.0, mid + 1.0
            # Add one cell size of padding to all sides to ensure solid encasement
            return lo - cell_size, hi + cell_size

        x0, x1 = _pad(min_b.x, max_b.x)
        y0, y1 = _pad(min_b.y, max_b.y)
        z0, z1 = _pad(min_b.z, max_b.z)

        mesh_timer.tick()

        # ── 1. Dimension-Independent Grid Sampling ──────────────────────────
        # cell_size is the physical distance between samples in any direction.
        # This ensures uniform, non-stretched triangles regardless of model shape.
        dx, dy, dz = (x1 - x0), (y1 - y0), (z1 - z0)
        nx = max(1, int(np.ceil(dx / cell_size)))
        ny = max(1, int(np.ceil(dy / cell_size)))
        nz = max(1, int(np.ceil(dz / cell_size)))

        x = np.linspace(x0, x0 + nx * cell_size, nx + 1)
        y = np.linspace(y0, y0 + ny * cell_size, ny + 1)
        z = np.linspace(z0, z0 + nz * cell_size, nz + 1)

        X, Y, Z = np.meshgrid(x, y, z, indexing='ij')
        pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])

        mesh_timer.start("field_eval")
        vals = field.evaluate_grid(pts).reshape(nx + 1, ny + 1, nz + 1)
        mesh_timer.stop("field_eval")

        # ── 2. Vectorised cube classification ─────────────────────────────────
        mesh_timer.start("cube_index")
        neg = vals < 0
        v0 = neg[:-1, :-1, :-1]; v1 = neg[1:,  :-1, :-1]
        v2 = neg[1:,  1:,  :-1]; v3 = neg[:-1, 1:,  :-1]
        v4 = neg[:-1, :-1, 1:];  v5 = neg[1:,  :-1, 1:]
        v6 = neg[1:,  1:,  1:];  v7 = neg[:-1, 1:,  1:]

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
            return None
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
            return None

        ei   = tri5[mi, ti]                  # (N_tris, 3) edge indices
        # Reversed winding [0,2,1] for correct outward normals with SDF negative-inside
        pts1 = edge_pts[mi, ei[:, 0]]
        pts2 = edge_pts[mi, ei[:, 2]]
        pts3 = edge_pts[mi, ei[:, 1]]
        tri_verts = np.stack([pts1, pts2, pts3], axis=1)   # (N_tris, 3, 3)

        # ── 7. Return raw triangle arrays for Coin3D rendering ────────────────
        # Returns:
        #   verts:    (N_tris*3, 3) float32  — one vertex per triangle corner
        #   flat_idx: (N_tris*4,) int32     — flat [0,1,2,-1, ...] for SoIndexedFaceSet
        mesh_timer.start("mesh_build")

        flat_verts = tri_verts.reshape(-1, 3).astype(np.float32)

        n_tris = len(mi)
        base = np.arange(n_tris, dtype=np.int32) * 3
        tri_idx = np.stack([base, base+1, base+2], axis=1)
        sentinel = np.full((n_tris, 1), -1, dtype=np.int32)
        flat_idx = np.hstack([tri_idx, sentinel]).ravel()

        mesh_timer.stop("mesh_build")
        return flat_verts, flat_idx


class AdaptiveMCMesher(DMMesher):
    """Adaptive Marching Cubes placeholder — currently falls back to MarchingCubesMesher."""
    def mesh(self, field: FRepField, cell_size: float) -> tuple:
        dm_logger.warn("AdaptiveMCMesher is not yet implemented (it is currently identical to Marching Cubes).")
        return MarchingCubesMesher().mesh(field, cell_size)


class SurfaceNetsMesher(DMMesher):
    """
    Naive Surface Nets (Gibson 1998).
    
    One vertex per active cell (average of edge crossing points).
    Produces quad-dominant meshes (triangulated for Coin3D).
    
    Vectorized implementation:
    1. Grid sampling & field eval (same as MC)
    2. Edge sign-change detection for all 12 edge families
    3. Net cell detection (cells owning at least one sign-change edge)
    4. Crossing point computation & averaging per cell
    5. Quad assembly for each sign-change edge
    """
    def mesh(self, field: FRepField, cell_size: float) -> tuple:
        min_b, max_b = field.bounding_box()

        def _pad(lo, hi):
            if hi - lo < 1e-4:
                mid = (lo + hi) / 2
                return mid - 1.0, mid + 1.0
            # Add one cell size of padding to all sides to ensure solid encasement
            return lo - cell_size, hi + cell_size

        x0, x1 = _pad(min_b.x, max_b.x)
        y0, y1 = _pad(min_b.y, max_b.y)
        z0, z1 = _pad(min_b.z, max_b.z)

        mesh_timer.tick()

        dx_phys, dy_phys, dz_phys = (x1 - x0), (y1 - y0), (z1 - z0)
        nx = max(1, int(np.ceil(dx_phys / cell_size)))
        ny = max(1, int(np.ceil(dy_phys / cell_size)))
        nz = max(1, int(np.ceil(dz_phys / cell_size)))

        x = np.linspace(x0, x0 + nx * cell_size, nx + 1)
        y = np.linspace(y0, y0 + ny * cell_size, ny + 1)
        z = np.linspace(z0, z0 + nz * cell_size, nz + 1)

        X, Y, Z = np.meshgrid(x, y, z, indexing='ij')
        pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])

        mesh_timer.start("field_eval")
        vals = field.evaluate_grid(pts).reshape(nx + 1, ny + 1, nz + 1)
        mesh_timer.stop("field_eval")

        mesh_timer.start("sn_detect_edges")
        # Identify edges with sign changes. 
        # Inside < 0, Outside >= 0.
        is_inside = vals < 0
        
        # Edge families (indices of the low-end grid point)
        # X-edges: (i,j,k) -> (i+1,j,k)
        ex = is_inside[:-1, :, :] != is_inside[1:, :, :]
        # Y-edges: (i,j,k) -> (i,j+1,k)
        ey = is_inside[:, :-1, :] != is_inside[:, 1:, :]
        # Z-edges: (i,j,k) -> (i,j,k+1)
        ez = is_inside[:, :, :-1] != is_inside[:, :, 1:]
        mesh_timer.stop("sn_detect_edges")

        mesh_timer.start("sn_net_cells")
        # A cell (i,j,k) [0..nx-1, 0..ny-1, 0..nz-1] is a "net cell" 
        # if any of its 12 edges has a sign change.
        # Edges of cell (i,j,k):
        # X: (i,j,k), (i,j+1,k), (i,j,k+1), (i,j+1,k+1)
        # Y: (i,j,k), (i+1,j,k), (i,j,k+1), (i+1,j,k+1)
        # Z: (i,j,k), (i+1,j,k), (i,j+1,k), (i+1,j+1,k)
        
        cell_active = (
            ex[:, :-1, :-1] | ex[:, 1:, :-1] | ex[:, :-1, 1:] | ex[:, 1:, 1:] |
            ey[:-1, :, :-1] | ey[1:, :, :-1] | ey[:-1, :, 1:] | ey[1:, :, 1:] |
            ez[:-1, :-1, :] | ez[1:, :-1, :] | ez[:-1, 1:, :] | ez[1:, 1:, :]
        )
        
        active_idx = np.argwhere(cell_active)
        if active_idx.size == 0:
            return None
            
        ai, aj, ak = active_idx[:, 0], active_idx[:, 1], active_idx[:, 2]
        M = len(ai)
        mesh_timer.stop("sn_net_cells")

        mesh_timer.start("sn_vertex_compute")
        # For each active cell, compute the average of all its crossing points.
        # We need crossing points for every edge of every active cell.
        
        # Helper to compute interpolation factor t for v1 -> v2
        def get_t(v1, v2):
            dv = v2 - v1
            safe = np.abs(dv) > 1e-8
            return np.where(safe, -v1 / np.where(safe, dv, 1.0), 0.5)

        # Grid spacing
        dx, dy, dz = x[1]-x[0], y[1]-y[0], z[1]-z[0]
        
        # Accumulators for average
        sum_p = np.zeros((M, 3), dtype=np.float64)
        count = np.zeros(M, dtype=np.int32)
        
        # We check all 12 edges. This is slightly redundant but robust for vectorization.
        # Offset map to edges: (axis, d_off)
        # X-edges: (i,j,k), (i,j+1,k), (i,j,k+1), (i,j+1,k+1)
        x_edge_offsets = [(0,0,0), (0,1,0), (0,0,1), (0,1,1)]
        for dj, dk in [(0,0), (1,0), (0,1), (1,1)]:
            mask = ex[ai, aj+dj, ak+dk]
            if np.any(mask):
                v1 = vals[ai, aj+dj, ak+dk]
                v2 = vals[ai+1, aj+dj, ak+dk]
                t = get_t(v1, v2)
                sum_p[mask, 0] += x[ai[mask]] + t[mask] * dx
                sum_p[mask, 1] += y[aj[mask] + dj]
                sum_p[mask, 2] += z[ak[mask] + dk]
                count[mask] += 1
                
        # Y-edges
        for di, dk in [(0,0), (1,0), (0,1), (1,1)]:
            mask = ey[ai+di, aj, ak+dk]
            if np.any(mask):
                v1 = vals[ai+di, aj, ak+dk]
                v2 = vals[ai+di, aj+1, ak+dk]
                t = get_t(v1, v2)
                sum_p[mask, 0] += x[ai[mask] + di]
                sum_p[mask, 1] += y[aj[mask]] + t[mask] * dy
                sum_p[mask, 2] += z[ak[mask] + dk]
                count[mask] += 1
                
        # Z-edges
        for di, dj in [(0,0), (1,0), (0,1), (1,1)]:
            mask = ez[ai+di, aj+dj, ak]
            if np.any(mask):
                v1 = vals[ai+di, aj+dj, ak]
                v2 = vals[ai+di, aj+dj, ak+1]
                t = get_t(v1, v2)
                sum_p[mask, 0] += x[ai[mask] + di]
                sum_p[mask, 1] += y[aj[mask] + dj]
                sum_p[mask, 2] += z[ak[mask]] + t[mask] * dz
                count[mask] += 1

        cell_verts = sum_p / count[:, None]
        
        # Map active cell index to vertex index
        cell_to_vidx = -np.ones((nx, ny, nz), dtype=np.int32)
        cell_to_vidx[ai, aj, ak] = np.arange(M, dtype=np.int32)
        mesh_timer.stop("sn_vertex_compute")

        mesh_timer.start("sn_quad_assembly")
        # Every sign-change edge is shared by 4 cells. Connect their vertices into a quad.
        # The quad should be wound so normal matches gradient (v2 - v1).
        
        quad_vindices = []
        
        # X-axis edges: shared by cells (ai, aj, ak), (ai, aj-1, ak), (ai, aj-1, ak-1), (ai, aj, ak-1)
        ei, ej, ek = np.where(ex[:, 1:-1, 1:-1])
        if ei.size > 0:
            # Shift to cell indices (ej, ek correspond to j,k in ex)
            ej_c, ek_c = ej + 1, ek + 1
            v0 = cell_to_vidx[ei, ej_c,   ek_c]
            v1 = cell_to_vidx[ei, ej_c-1, ek_c]
            v2 = cell_to_vidx[ei, ej_c-1, ek_c-1]
            v3 = cell_to_vidx[ei, ej_c,   ek_c-1]
            valid = (v0 >= 0) & (v1 >= 0) & (v2 >= 0) & (v3 >= 0)
            if np.any(valid):
                # Gradient check for winding
                # For X-edge, if v1 < 0 and v2 > 0, gradient is +X.
                # v1 is at (ei, ej_c, ek_c), v2 is at (ei+1, ej_c, ek_c)
                v1_val = vals[ei[valid], ej_c[valid], ek_c[valid]]
                v2_val = vals[ei[valid]+1, ej_c[valid], ek_c[valid]]
                flip = v2_val < v1_val
                q = np.stack([v0[valid], v1[valid], v2[valid], v3[valid]], axis=1)
                q[flip] = q[flip, ::-1]
                quad_vindices.append(q)

        # Y-axis edges: shared by cells (ai, aj, ak), (ai-1, aj, ak), (ai-1, aj, ak-1), (ai, aj, ak-1)
        ei, ej, ek = np.where(ey[1:-1, :, 1:-1])
        if ej.size > 0:
            ei_c, ek_c = ei + 1, ek + 1
            v0 = cell_to_vidx[ei_c,   ej, ek_c]
            v1 = cell_to_vidx[ei_c,   ej, ek_c-1]
            v2 = cell_to_vidx[ei_c-1, ej, ek_c-1]
            v3 = cell_to_vidx[ei_c-1, ej, ek_c]
            valid = (v0 >= 0) & (v1 >= 0) & (v2 >= 0) & (v3 >= 0)
            if np.any(valid):
                v1_val = vals[ei_c[valid], ej[valid], ek_c[valid]]
                v2_val = vals[ei_c[valid], ej[valid]+1, ek_c[valid]]
                flip = v2_val < v1_val  # Consistent with X-axis
                q = np.stack([v0[valid], v1[valid], v2[valid], v3[valid]], axis=1)
                q[flip] = q[flip, ::-1]
                quad_vindices.append(q)

        # Z-axis edges: shared by (ai, aj, ak), (ai-1, aj, ak), (ai-1, aj-1, ak), (ai, aj-1, ak)
        ei, ej, ek = np.where(ez[1:-1, 1:-1, :])
        if ek.size > 0:
            ei_c, ej_c = ei + 1, ej + 1
            v0 = cell_to_vidx[ei_c,   ej_c,   ek]
            v1 = cell_to_vidx[ei_c-1, ej_c,   ek]
            v2 = cell_to_vidx[ei_c-1, ej_c-1, ek]
            v3 = cell_to_vidx[ei_c,   ej_c-1, ek]
            valid = (v0 >= 0) & (v1 >= 0) & (v2 >= 0) & (v3 >= 0)
            if np.any(valid):
                v1_val = vals[ei_c[valid], ej_c[valid], ek[valid]]
                v2_val = vals[ei_c[valid], ej_c[valid], ek[valid]+1]
                flip = v2_val < v1_val
                q = np.stack([v0[valid], v1[valid], v2[valid], v3[valid]], axis=1)
                q[flip] = q[flip, ::-1]
                quad_vindices.append(q)

        if not quad_vindices:
            return None
            
        all_quads = np.vstack(quad_vindices)
        mesh_timer.stop("sn_quad_assembly")

        mesh_timer.start("mesh_build")
        # Triangulate and build flat arrays
        # Each quad (0,1,2,3) -> (0,1,2) and (0,2,3)
        tri1 = all_quads[:, [0, 1, 2]]
        tri2 = all_quads[:, [0, 2, 3]]
        all_tris = np.vstack([tri1, tri2])
        
        flat_verts = cell_verts[all_tris.ravel()].astype(np.float32)

        n_tris = len(all_tris)
        base = np.arange(n_tris, dtype=np.int32) * 3
        tri_idx = np.stack([base, base+1, base+2], axis=1)
        sentinel = np.full((n_tris, 1), -1, dtype=np.int32)
        flat_idx = np.hstack([tri_idx, sentinel]).ravel()
        mesh_timer.stop("mesh_build")

        return flat_verts, flat_idx


class DualContouringMesher(DMMesher):
    """
    Dual Contouring (Ju et al. 2002).
    
    Preserves sharp features by solving a Quadratic Error Function (QEF) 
    per cell to find the optimal vertex position.
    
    Implementation:
    1. Grid sampling (same as MC)
    2. Detect sign-change edges
    3. For each crossing edge: compute p (crossing) and n (normal)
    4. Accumulate QEF (AtA, Atb) for the 4 cells sharing each edge
    5. Batch solve QEFs using SVD
    6. Assemble quads from dual vertices
    """
    def mesh(self, field: FRepField, cell_size: float) -> tuple:
        min_b, max_b = field.bounding_box()

        def _pad(lo, hi):
            if hi - lo < 1e-4:
                mid = (lo + hi) / 2
                return mid - 1.0, mid + 1.0
            # Add one cell size of padding to all sides to ensure solid encasement
            return lo - cell_size, hi + cell_size

        x0, x1 = _pad(min_b.x, max_b.x)
        y0, y1 = _pad(min_b.y, max_b.y)
        z0, z1 = _pad(min_b.z, max_b.z)

        mesh_timer.tick()

        nx = max(1, int(np.ceil((x1 - x0) / cell_size)))
        ny = max(1, int(np.ceil((y1 - y0) / cell_size)))
        nz = max(1, int(np.ceil((z1 - z0) / cell_size)))

        x = np.linspace(x0, x0 + nx * cell_size, nx + 1)
        y = np.linspace(y0, y0 + ny * cell_size, ny + 1)
        z = np.linspace(z0, z0 + nz * cell_size, nz + 1)

        X, Y, Z = np.meshgrid(x, y, z, indexing='ij')
        pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])

        mesh_timer.start("field_eval")
        vals = field.evaluate_grid(pts).reshape(nx + 1, ny + 1, nz + 1)
        mesh_timer.stop("field_eval")

        mesh_timer.start("dc_detect_edges")
        is_inside = vals < 0
        ex = is_inside[:-1, :, :] != is_inside[1:, :, :]
        ey = is_inside[:, :-1, :] != is_inside[:, 1:, :]
        ez = is_inside[:, :, :-1] != is_inside[:, :, 1:]
        mesh_timer.stop("dc_detect_edges")

        mesh_timer.start("dc_qef_collect")
        # Identify active cells (those possessing at least one crossing edge)
        # For DC, a cell is active if it shares an edge with a sign change.
        active_cells = (
            ex[:, :-1, :-1] | ex[:, 1:, :-1] | ex[:, :-1, 1:] | ex[:, 1:, 1:] |
            ey[:-1, :, :-1] | ey[1:, :, :-1] | ey[:-1, :, 1:] | ey[1:, :, 1:] |
            ez[:-1, :-1, :] | ez[1:, :-1, :] | ez[:-1, 1:, :] | ez[1:, 1:, :]
        )
        active_idx = np.argwhere(active_cells)
        if active_idx.size == 0:
            return None
        
        ai, aj, ak = active_idx[:, 0], active_idx[:, 1], active_idx[:, 2]
        M = len(ai)
        
        # Map (i,j,k) -> active index 0..M-1
        cell_to_active = -np.ones((nx, ny, nz), dtype=np.int32)
        cell_to_active[ai, aj, ak] = np.arange(M, dtype=np.int32)

        # Pre-allocate QEF data
        # AtA @ v = Atb
        ata = np.zeros((M, 3, 3), dtype=np.float64)
        atb = np.zeros((M, 3), dtype=np.float64)
        mass_point = np.zeros((M, 3), dtype=np.float64)
        count = np.zeros(M, dtype=np.int32)

        def get_t(v1, v2):
            dv = v2 - v1
            safe = np.abs(dv) > 1e-8
            return np.where(safe, -v1 / np.where(safe, dv, 1.0), 0.5)

        dx_s, dy_s, dz_s = x[1]-x[0], y[1]-y[0], z[1]-z[0]

        # X-Edge crossings: (i,j,k) -> (i+1,j,k)
        # Shared by 4 cells: (i,j,k), (i,j-1,k), (i,j-1,k-1), (i,j,k-1)
        ei_x, ej_x, ek_x = np.where(ex)
        if ei_x.size > 0:
            v1, v2 = vals[ei_x, ej_x, ek_x], vals[ei_x+1, ej_x, ek_x]
            t = get_t(v1, v2)
            px = x[ei_x] + t * dx_s
            py = y[ej_x]
            pz = z[ek_x]
            pts_edge = np.column_stack([px, py, pz])
            # (Removal of redundant loop)
            
        # Refined vectorized QEF collection
        def accumulate_qef(edges, axis, offset_tuples):
            # edges: (nx, ny, nz) bool mask for sign changes on this axis
            # offset_tuples: list of (di, dj, dk) to find the 4 sharing cells
            ei, ej, ek = np.where(edges)
            if ei.size == 0: return
            
            v1 = vals[ei, ej, ek]
            if axis == 0: v2 = vals[ei+1, ej, ek]
            elif axis == 1: v2 = vals[ei, ej+1, ek]
            else: v2 = vals[ei, ej, ek+1]
            
            t = get_t(v1, v2)
            px = x[ei] + (t * dx_s if axis==0 else 0)
            py = y[ej] + (t * dy_s if axis==1 else 0)
            pz = z[ek] + (t * dz_s if axis==2 else 0)
            pts_edge = np.column_stack([px, py, pz])
            
            # Gradients must be evaluated at the crossing points
            # This is the slow part (Python loop over edges)
            for m in range(len(ei)):
                p = pts_edge[m]
                n_vec = field.gradient(FreeCAD.Vector(*p))
                if n_vec.Length < 1e-12: continue
                n_vec.normalize()
                n = np.array([n_vec.x, n_vec.y, n_vec.z])
                
                # Accumulate for each of the 4 sharing cells
                ii, jj, kk = ei[m], ej[m], ek[m]
                for di, dj, dk in offset_tuples:
                    ci, cj, ck = ii+di, jj+dj, kk+dk
                    if 0 <= ci < nx and 0 <= cj < ny and 0 <= ck < nz:
                        v_idx = cell_to_active[ci, cj, ck]
                        if v_idx >= 0:
                            ata[v_idx] += np.outer(n, n)
                            atb[v_idx] += n * np.dot(n, p)
                            mass_point[v_idx] += p
                            count[v_idx] += 1

        accumulate_qef(ex, 0, [(0, 0, 0), (0, -1, 0), (0, -1, -1), (0, 0, -1)])
        accumulate_qef(ey, 1, [(0, 0, 0), (-1, 0, 0), (-1, 0, -1), (0, 0, -1)])
        accumulate_qef(ez, 2, [(0, 0, 0), (-1, 0, 0), (-1, -1, 0), (0, -1, 0)])
        mesh_timer.stop("dc_qef_collect")

        mesh_timer.start("dc_qef_solve")
        # Solve per-cell QEF
        cell_verts = np.zeros((M, 3), dtype=np.float64)
        
        # Batch SVD
        U, S, Vh = np.linalg.svd(ata) # (M, 3, 3)
        # Threshold for pseudo-inverse
        max_s = np.max(S, axis=1)
        threshold = 0.1 * max_s
        
        # Use a hybrid approach: if rank < 3, or if solve yields point outside cell,
        # use mass point.
        for i in range(M):
            if count[i] == 0: continue
            
            # SVD Solve
            s_inv = np.zeros(3)
            for j in range(3):
                if S[i, j] > threshold[i]:
                    s_inv[j] = 1.0 / S[i, j]
            
            # v = V @ Sinv @ Ut @ atb
            pinv = Vh[i].T @ np.diag(s_inv) @ U[i].T
            v = pinv @ atb[i]
            
            # Clamp to cell bounds
            cx, cy, cz = x[ai[i]], y[aj[i]], z[ak[i]]
            eps = 1e-4
            if (v[0] < cx - eps or v[0] > cx + dx_s + eps or
                v[1] < cy - eps or v[1] > cy + dy_s + eps or
                v[2] < cz - eps or v[2] > cz + dz_s + eps or
                threshold[i] < 1e-6):
                # Fallback to mass point if solution is outside or unstable
                cell_verts[i] = mass_point[i] / count[i]
            else:
                cell_verts[i] = v
        mesh_timer.stop("dc_qef_solve")

        mesh_timer.start("dc_quad_assembly")
        quad_vindices = []
        
        # Emit quads for each sign-change edge
        # X-edges (ei, ej, ek) shared by cells (ai, aj, ak), (ai, aj-1, ak), (ai, aj-1, ak-1), (ai, aj, ak-1)
        ei, ej, ek = np.where(ex[:, 1:-1, 1:-1])
        if ei.size > 0:
            ej_c, ek_c = ej + 1, ek + 1
            v0 = cell_to_active[ei, ej_c,   ek_c]
            v1 = cell_to_active[ei, ej_c-1, ek_c]
            v2 = cell_to_active[ei, ej_c-1, ek_c-1]
            v3 = cell_to_active[ei, ej_c,   ek_c-1]
            valid = (v0 >= 0) & (v1 >= 0) & (v2 >= 0) & (v3 >= 0)
            if np.any(valid):
                v1_val, v2_val = vals[ei[valid], ej_c[valid], ek_c[valid]], vals[ei[valid]+1, ej_c[valid], ek_c[valid]]
                flip = v2_val < v1_val
                q = np.stack([v0[valid], v1[valid], v2[valid], v3[valid]], axis=1)
                q[flip] = q[flip, ::-1]
                quad_vindices.append(q)

        # Y-edges
        ei, ej, ek = np.where(ey[1:-1, :, 1:-1])
        if ej.size > 0:
            ei_c, ek_c = ei + 1, ek + 1
            v0 = cell_to_active[ei_c,   ej, ek_c]
            v1 = cell_to_active[ei_c,   ej, ek_c-1]
            v2 = cell_to_active[ei_c-1, ej, ek_c-1]
            v3 = cell_to_active[ei_c-1, ej, ek_c]
            valid = (v0 >= 0) & (v1 >= 0) & (v2 >= 0) & (v3 >= 0)
            if np.any(valid):
                v1_val, v2_val = vals[ei_c[valid], ej[valid], ek_c[valid]], vals[ei_c[valid], ej[valid]+1, ek_c[valid]]
                flip = v2_val < v1_val
                q = np.stack([v0[valid], v1[valid], v2[valid], v3[valid]], axis=1)
                q[flip] = q[flip, ::-1]
                quad_vindices.append(q)

        # Z-edges
        ei, ej, ek = np.where(ez[1:-1, 1:-1, :])
        if ek.size > 0:
            ei_c, ej_c = ei + 1, ej + 1
            v0 = cell_to_active[ei_c,   ej_c,   ek]
            v1 = cell_to_active[ei_c-1, ej_c,   ek]
            v2 = cell_to_active[ei_c-1, ej_c-1, ek]
            v3 = cell_to_active[ei_c,   ej_c-1, ek]
            valid = (v0 >= 0) & (v1 >= 0) & (v2 >= 0) & (v3 >= 0)
            if np.any(valid):
                v1_val, v2_val = vals[ei_c[valid], ej_c[valid], ek[valid]], vals[ei_c[valid], ej_c[valid], ek[valid]+1]
                flip = v2_val < v1_val
                q = np.stack([v0[valid], v1[valid], v2[valid], v3[valid]], axis=1)
                q[flip] = q[flip, ::-1]
                quad_vindices.append(q)

        if not quad_vindices:
            return None
        all_quads = np.vstack(quad_vindices)
        mesh_timer.stop("dc_quad_assembly")

        mesh_timer.start("mesh_build")
        tri1, tri2 = all_quads[:, [0, 1, 2]], all_quads[:, [0, 2, 3]]
        all_tris = np.vstack([tri1, tri2])
        flat_verts = cell_verts[all_tris.ravel()].astype(np.float32)

        n_tris = len(all_tris)
        base = np.arange(n_tris, dtype=np.int32) * 3
        tri_idx = np.stack([base, base+1, base+2], axis=1)
        sentinel = np.full((n_tris, 1), -1, dtype=np.int32)
        flat_idx = np.hstack([tri_idx, sentinel]).ravel()
        mesh_timer.stop("mesh_build")

        return flat_verts, flat_idx


def get_active_mesher(type_override=None) -> DMMesher:
    """Return the active mesher based on global settings or a specific type override.
    
    Handles both integer indices and string labels from FreeCAD's PropertyEnumeration.
    """
    st = type_override if type_override is not None else get_meshing_type()
    
    # Map index or label to worker class
    # Order: 0: MC, 1: AMC, 2: SN, 3: DC
    mesher = MarchingCubesMesher()
    label = str(st)
    
    if st == 1 or "Adaptive Marching Cubes" in label:
        mesher = AdaptiveMCMesher()
    elif st == 2 or "Surface Nets" in label:
        mesher = SurfaceNetsMesher()
    elif st == 3 or "Dual Contouring" in label:
        mesher = DualContouringMesher()
    
    # dm_logger.debug(f"DirectModeling: Using mesher {mesher.__class__.__name__} (selection: {st})")
    return mesher
