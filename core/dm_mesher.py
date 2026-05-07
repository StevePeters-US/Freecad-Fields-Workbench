import time
import Part
import FreeCAD
import numpy as np

from core import dm_logger
from core.sdf.sdf_field import SdfField
from core.sdf.marching_cubes.mc_tables import edgeTable, triTable
from core.dm_object import get_perf_profiler_enabled

# Pre-convert lookup tables to numpy arrays for fast indexing
_EDGE_TABLE = np.array(edgeTable, dtype=np.int32)

# triTable as (256, 16) int8 array - -1 fills unused triangle slots
_TRI_TABLE  = np.array(triTable,  dtype=np.int8)

# Edge endpoint corner indices (constant, vectorized over active cubes)
_EDGE_C1 = np.array([0,1,2,3, 4,5,6,7, 0,1,2,3], dtype=np.int32)
_EDGE_C2 = np.array([1,2,3,0, 5,6,7,4, 4,5,6,7], dtype=np.int32)

# Corner (dx, dy, dz) offsets in Lorensen-Cline order
_OFF_X = np.array([0,1,1,0, 0,1,1,0], dtype=np.float64)
_OFF_Y = np.array([0,0,1,1, 0,0,1,1], dtype=np.float64)
_OFF_Z = np.array([0,0,0,0, 1,1,1,1], dtype=np.float64)
_CELL_OFFSETS = np.stack([_OFF_X, _OFF_Y, _OFF_Z], axis=1)  # (8, 3) corner offsets in unit-cell coords


class MeshTimer:
    """Accumulates timing across multiple mesh() calls.
    Call summary() once (on tool commit) to print a single INFO log.
    """
    _STAGES = [
        "field_eval", "cube_index", "active_filter",
        "corner_extract", "edge_interp", "tri_extract", "mesh_build",
        "decimate", "deduplicate",
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
        if not get_perf_profiler_enabled():
            self.reset()
            return
            
        if self._calls == 0:
            return
        n = self._calls
        total_ms = sum(self._totals.values()) * 1000
        lines = [f"[PERF] {label} - {n} call(s), {total_ms:.1f} ms total"]
        
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


# Module-level singleton - shared across all mesher calls for the same tool session
mesh_timer = MeshTimer()


class DMMesher:
    """Abstract base class for all Direct Modeling SDF meshers."""
    def mesh(self, field: SdfField, cell_size: float, decimate=False, deduplicate=True, **kwargs) -> tuple:
        raise NotImplementedError("mesher must implement mesh()")


class MarchingCubesMesher(DMMesher):
    """
    Uniform-grid Marching Cubes.

    Uses an absolute cell_size (in mm) to ensure consistent triangle density
    regardless of the object's overall dimensions.

    Vectorization levels:
      1. Field evaluation      - NumPy batch (per-field override)
      2. cube_index            - 8 shifted array views, no Python loop
      3. Active-cube filter    - np.argwhere, skips empty/full cubes
      4. Corner extraction     - (M,8) broadcast, no loop
      5. Edge interpolation    - (M,12,3) batch, no loop
      6. Triangle extraction   - (M,5,3) reshape + np.where, no loop
      7. Mesh build            - flat (N,3,3) ndarray -> Coin3D arrays
    """
    def mesh(self, field: SdfField, cell_size: float, decimate=False, deduplicate=True, **kwargs) -> tuple:
        from core.sdf.sdf_octree import SdfOctreeCache
        mesh_timer.tick()

        octree = SdfOctreeCache(field, leaf_size=cell_size)
        mesh_timer.start("field_eval")
        octree.build()
        mesh_timer.stop("field_eval")

        # Collect surface-crossing leaf cells
        origins, sizes, corner_vals = [], [], []
        for origin, size, corners in octree.walk_leaves():
            origins.append(origin)
            sizes.append(size)
            corner_vals.append(corners)

        if not origins:
            return None

        # Convert to arrays - shape (M, 3), (M,), (M, 8)
        origins     = np.array(origins,     dtype=np.float64)  # (M, 3)
        sizes_arr   = np.array(sizes,       dtype=np.float64)  # (M,)
        cv          = np.array(corner_vals, dtype=np.float64)  # (M, 8)
        M           = len(origins)
        s           = sizes_arr[0]  # all leaves have the same size

        mesh_timer.start("cube_index")
        neg = cv < 0  # (M, 8)
        cube_idx = np.zeros(M, dtype=np.uint8)
        for bit in range(8):
            cube_idx |= (neg[:, bit].astype(np.uint8) << bit)
        mesh_timer.stop("cube_index")

        mesh_timer.start("active_filter")
        active_mask = (cube_idx != 0) & (cube_idx != 255)
        ai_origins = origins[active_mask]
        ai_cv      = cv[active_mask]
        ci         = cube_idx[active_mask]
        M2         = len(ci)
        mesh_timer.stop("active_filter")

        if M2 == 0:
            return None

        # Corner world positions: (M2, 8, 3)
        mesh_timer.start("corner_extract")
        cp = ai_origins[:, None, :] + _CELL_OFFSETS[None, :, :] * s
        mesh_timer.stop("corner_extract")

        # Update cv and M to use the filtered (active) subset for downstream logic
        cv = ai_cv
        M = M2

        # ── 5. Vectorised edge interpolation - (M, 12, 3) ─────────────────────
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

        # ── 6. Triangle extraction - fully vectorised ──────────────────────────
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
        #   verts:    (N_tris*3, 3) float32  - one vertex per triangle corner
        #   flat_idx: (N_tris*4,) int32     - flat [0,1,2,-1, ...] for SoIndexedFaceSet
        mesh_timer.start("mesh_build")

        flat_verts = tri_verts.reshape(-1, 3).astype(np.float32)

        n_tris = len(mi)
        base = np.arange(n_tris, dtype=np.int32) * 3
        tri_idx = np.stack([base, base+1, base+2], axis=1)
        sentinel = np.full((n_tris, 1), -1, dtype=np.int32)
        flat_idx = np.hstack([tri_idx, sentinel]).ravel()

        mesh_timer.stop("mesh_build")

        if decimate:
            mesh_timer.start("decimate")
            flat_verts, flat_idx = decimate_flat_tris(flat_verts, flat_idx)
            mesh_timer.stop("decimate")

        if deduplicate:
            mesh_timer.start("deduplicate")
            flat_verts, flat_idx = deduplicate_verts(flat_verts, flat_idx)
            mesh_timer.stop("deduplicate")

        return flat_verts, flat_idx




class SurfaceNetsMesher(DMMesher):
    """
    Naive Surface Nets (Gibson 1998).
    
    One vertex per active cell (average of edge crossing points).
    Produces quad-dominant meshes (triangulated for Coin3D).
    
    Vectorized implementation:
    1. Grid sampling & field eval (same as MC)
    2. Edge sign-change detection for all 12 edge families
    3. Net cell assembly
    """
    def mesh(self, field: SdfField, cell_size: float, decimate=False, deduplicate=True, **kwargs) -> tuple:
        from core.sdf.sdf_octree import SdfOctreeCache
        mesh_timer.tick()

        octree = SdfOctreeCache(field, leaf_size=cell_size)
        mesh_timer.start("field_eval")
        octree.build()
        mesh_timer.stop("field_eval")

        mesh_timer.start("sn_vertex_compute")
        # Surface Nets: one vertex per active cell (average of edge crossing points).
        # We'll map (ix, iy, iz) -> vertex_index
        active_cells = [] # List of ((ix,iy,iz), vertex_pos)
        cell_to_vidx = {}

        # Helper for edge interpolation
        def get_t(v1, v2):
            dv = v2 - v1
            return -v1 / dv if abs(dv) > 1e-8 else 0.5

        # Local corner offsets (Lorensen-Cline order)
        _OFFS = _CELL_OFFSETS * cell_size

        for (ix, iy, iz), corners in octree._leaves.items():
            # Check all 12 edges of this cell for sign changes
            # Corner indices for 12 edges:
            # X-edges: (0,1), (3,2), (4,5), (7,6)
            # Y-edges: (0,3), (1,2), (4,7), (5,6)
            # Z-edges: (0,4), (1,5), (2,6), (3,7)
            edges = [
                (0,1), (3,2), (4,5), (7,6), # X
                (0,3), (1,2), (4,7), (5,6), # Y
                (0,4), (1,5), (2,6), (3,7)  # Z
            ]
            
            sum_p = np.zeros(3, dtype=np.float64)
            count = 0
            base_p = octree._origin + np.array([ix, iy, iz]) * cell_size
            
            for c1, c2 in edges:
                if (corners[c1] < 0) != (corners[c2] < 0):
                    t = get_t(corners[c1], corners[c2])
                    sum_p += base_p + _OFFS[c1] + t * (_OFFS[c2] - _OFFS[c1])
                    count += 1
            
            if count > 0:
                vidx = len(active_cells)
                active_cells.append(sum_p / count)
                cell_to_vidx[(ix, iy, iz)] = vidx

        if not active_cells:
            return None
            
        cell_verts = np.array(active_cells, dtype=np.float64)
        M = len(cell_verts)
        mesh_timer.stop("sn_vertex_compute")

        mesh_timer.start("sn_quad_assembly")
        # For every edge with a sign change, connect the 4 cells that share it.
        # Since we use a dict for cells, we need to find which cells exist.
        
        quad_vindices = []
        
        # We only need to check each edge ONCE. 
        # A simple way is to iterate over cells and only check the 3 edges 
        # starting at (0,0,0) corner: (0,1), (0,3), (0,4).
        # These edges are shared by 4 cells.
        
        for (ix, iy, iz), corners in octree._leaves.items():
            # X-edge (0,1)
            if (corners[0] < 0) != (corners[1] < 0):
                # Cells sharing X-edge: (ix, iy, iz), (ix, iy-1, iz), (ix, iy-1, iz-1), (ix, iy, iz-1)
                v0 = cell_to_vidx.get((ix, iy, iz), -1)
                v1 = cell_to_vidx.get((ix, iy-1, iz), -1)
                v2 = cell_to_vidx.get((ix, iy-1, iz-1), -1)
                v3 = cell_to_vidx.get((ix, iy, iz-1), -1)
                if v0 >= 0 and v1 >= 0 and v2 >= 0 and v3 >= 0:
                    q = [v0, v1, v2, v3]
                    if corners[1] < corners[0]: q.reverse() # Winding check
                    quad_vindices.append(q)

            # Y-edge (0,3)
            if (corners[0] < 0) != (corners[3] < 0):
                # Cells sharing Y-edge: (ix, iy, iz), (ix, iy, iz-1), (ix-1, iy, iz-1), (ix-1, iy, iz)
                v0 = cell_to_vidx.get((ix, iy, iz), -1)
                v1 = cell_to_vidx.get((ix, iy, iz-1), -1)
                v2 = cell_to_vidx.get((ix-1, iy, iz-1), -1)
                v3 = cell_to_vidx.get((ix-1, iy, iz), -1)
                if v0 >= 0 and v1 >= 0 and v2 >= 0 and v3 >= 0:
                    q = [v0, v1, v2, v3]
                    if corners[3] < corners[0]: q.reverse()
                    quad_vindices.append(q)

            # Z-edge (0,4)
            if (corners[0] < 0) != (corners[4] < 0):
                # Cells sharing Z-edge: (ix, iy, iz), (ix-1, iy, iz), (ix-1, iy-1, iz), (ix, iy-1, iz)
                v0 = cell_to_vidx.get((ix, iy, iz), -1)
                v1 = cell_to_vidx.get((ix-1, iy, iz), -1)
                v2 = cell_to_vidx.get((ix-1, iy-1, iz), -1)
                v3 = cell_to_vidx.get((ix, iy-1, iz), -1)
                if v0 >= 0 and v1 >= 0 and v2 >= 0 and v3 >= 0:
                    q = [v0, v1, v2, v3]
                    if corners[4] < corners[0]: q.reverse()
                    quad_vindices.append(q)

        if not quad_vindices:
            return None
            
        all_quads = np.array(quad_vindices, dtype=np.int32)
        mesh_timer.stop("sn_quad_assembly")

        # Relaxation Loop (Constrained Laplacian Smoothing)
        mesh_timer.start("sn_relax")
        n_iters = 3
        curr_verts = cell_verts.copy()
        
        edges = np.vstack([
            all_quads[:, [0, 1]], all_quads[:, [1, 2]],
            all_quads[:, [2, 3]], all_quads[:, [3, 0]]
        ])
        edges = np.sort(edges, axis=1)
        unique_edges = np.unique(edges, axis=0)
        
        v_idx, counts = np.unique(unique_edges.ravel(), return_counts=True)
        max_valence = np.max(counts) if counts.size > 0 else 0
        adj_table = np.full((M, max_valence), -1, dtype=np.int32)
        
        all_dir_edges = np.vstack([unique_edges, unique_edges[:, [1, 0]]])
        sort_idx = np.argsort(all_dir_edges[:, 0])
        sorted_edges = all_dir_edges[sort_idx]
        
        v_from = sorted_edges[:, 0]
        v_to = sorted_edges[:, 1]
        v_unique, v_start, v_counts = np.unique(v_from, return_index=True, return_counts=True)
        
        for i in range(max_valence):
            mask = i < v_counts
            if not np.any(mask): break
            adj_table[v_unique[mask], i] = v_to[v_start[mask] + i]
            
        for it in range(n_iters):
            mask = adj_table != -1
            neighbor_pos = curr_verts[adj_table]
            neighbor_pos[~mask] = 0
            neighbor_sum = np.sum(neighbor_pos, axis=1)
            neighbor_count = np.maximum(np.sum(mask, axis=1, keepdims=True), 1)
            new_verts = neighbor_sum / neighbor_count
            
            sdf_vals = field.evaluate_grid(new_verts.astype(np.float32))
            grads = field.gradient_grid(new_verts.astype(np.float32))
            grad_sq_mags = np.sum(grads**2, axis=1)
            safe = grad_sq_mags > 1e-12
            new_verts[safe] -= (sdf_vals[safe] / grad_sq_mags[safe])[:, None] * grads[safe]
            curr_verts = new_verts
            
        cell_verts = curr_verts
        mesh_timer.stop("sn_relax")

        mesh_timer.start("mesh_build")
        tri1 = all_quads[:, [0, 1, 2]]
        tri2 = all_quads[:, [0, 2, 3]]
        all_tris = np.vstack([tri1, tri2])
        flat_verts = cell_verts[all_tris.ravel()].astype(np.float32)
        n_tris = len(all_tris)
        base = np.arange(n_tris, dtype=np.int32) * 3
        tri_idx = np.stack([base, base+1, base+2], axis=1)
        flat_idx = np.hstack([tri_idx, np.full((n_tris, 1), -1, dtype=np.int32)]).ravel()
        mesh_timer.stop("mesh_build")

        if decimate:
            mesh_timer.start("decimate")
            flat_verts, flat_idx = decimate_flat_tris(flat_verts, flat_idx)
            mesh_timer.stop("decimate")

        if deduplicate:
            mesh_timer.start("deduplicate")
            flat_verts, flat_idx = deduplicate_verts(flat_verts, flat_idx)
            mesh_timer.stop("deduplicate")

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
    def mesh(self, field: SdfField, cell_size: float, decimate=False, deduplicate=True, **kwargs) -> tuple:
        from core.sdf.sdf_octree import SdfOctreeCache
        mesh_timer.tick()

        octree = SdfOctreeCache(field, leaf_size=cell_size)
        mesh_timer.start("field_eval")
        octree.build()
        mesh_timer.stop("field_eval")

        mesh_timer.start("dc_qef_collect")
        # Identify active cells (those possessing at least one crossing edge)
        # For DC, a cell is active if it shares an edge with a sign change.
        active_cells = [] # List of (ix,iy,iz)
        cell_to_active = {} # (ix,iy,iz) -> index in active_cells

        for (ix, iy, iz), corners in octree._leaves.items():
            if corners.min() < 0.0 and corners.max() >= 0.0:
                cell_to_active[(ix, iy, iz)] = len(active_cells)
                active_cells.append((ix, iy, iz))
        
        if not active_cells:
            return None
            
        M = len(active_cells)
        ata = np.zeros((M, 3, 3), dtype=np.float64)
        atb = np.zeros((M, 3), dtype=np.float64)
        mass_point = np.zeros((M, 3), dtype=np.float64)
        count = np.zeros(M, dtype=np.int32)

        def get_t(v1, v2):
            dv = v2 - v1
            return -v1 / dv if abs(dv) > 1e-8 else 0.5

        # Local corner offsets (Lorensen-Cline order)
        _OFFS = _CELL_OFFSETS * cell_size

        # For every edge with a sign change, compute crossing and accumulate to 4 sharing cells
        for (ix, iy, iz), corners in octree._leaves.items():
            base_p = octree._origin + np.array([ix, iy, iz]) * cell_size
            
            # Helper to process one crossing edge
            def process_edge(c1, c2, offset_tuples):
                if (corners[c1] < 0) != (corners[c2] < 0):
                    t = get_t(corners[c1], corners[c2])
                    p = base_p + _OFFS[c1] + t * (_OFFS[c2] - _OFFS[c1])
                    grad = field.gradient(FreeCAD.Vector(*p))
                    n = np.array([grad.x, grad.y, grad.z])
                    norm = np.linalg.norm(n)
                    if norm > 1e-12:
                        n /= norm
                        # Outer products for QEF: (3,1) * (1,3) -> (3,3)
                        ata_c = n[:, None] * n[None, :]
                        atb_c = n * np.dot(n, p)
                        
                        # Add to the 4 sharing cells
                        for dx, dy, dz in offset_tuples:
                            target = (ix + dx, iy + dy, iz + dz)
                            vidx = cell_to_active.get(target, -1)
                            if vidx >= 0:
                                ata[vidx] += ata_c
                                atb[vidx] += atb_c
                                mass_point[vidx] += p
                                count[vidx] += 1

            # X-edge (0,1): shared by cells (ix, iy, iz), (ix, iy-1, iz), (ix, iy-1, iz-1), (ix, iy, iz-1)
            process_edge(0, 1, [(0,0,0), (0,-1,0), (0,-1,-1), (0,0,-1)])
            # Y-edge (0,3): shared by (ix, iy, iz), (ix, iy, iz-1), (ix-1, iy, iz-1), (ix-1, iy, iz)
            process_edge(0, 3, [(0,0,0), (0,0,-1), (-1,0,-1), (-1,0,0)])
            # Z-edge (0,4): shared by (ix, iy, iz), (ix-1, iy, iz), (ix-1, iy-1, iz), (ix, iy-1, iz)
            process_edge(0, 4, [(0,0,0), (-1,0,0), (-1,-1,0), (0,-1,0)])

        mesh_timer.stop("dc_qef_collect")

        mesh_timer.start("dc_qef_solve")
        cell_verts = np.zeros((M, 3), dtype=np.float64)
        U, S, Vh = np.linalg.svd(ata)
        max_s = np.max(S, axis=1)
        threshold = 0.1 * max_s
        
        for i in range(M):
            if count[i] == 0: continue
            s_inv = np.zeros(3)
            for j in range(3):
                if S[i, j] > threshold[i]: s_inv[j] = 1.0 / S[i, j]
            pinv = Vh[i].T @ np.diag(s_inv) @ U[i].T
            v = pinv @ atb[i]
            
            # Clamp to cell bounds
            ix, iy, iz = active_cells[i]
            cx, cy, cz = octree._origin + np.array([ix, iy, iz]) * cell_size
            eps = 1e-4
            if (v[0] < cx - eps or v[0] > cx + cell_size + eps or
                v[1] < cy - eps or v[1] > cy + cell_size + eps or
                v[2] < cz - eps or v[2] > cz + cell_size + eps or
                threshold[i] < 1e-6):
                cell_verts[i] = mass_point[i] / count[i]
            else:
                cell_verts[i] = v
        mesh_timer.stop("dc_qef_solve")

        mesh_timer.start("dc_quad_assembly")
        quad_vindices = []
        for (ix, iy, iz), corners in octree._leaves.items():
            # Emit quads for edges that we "own" (starting at corner 0)
            # X-edge (0,1)
            if (corners[0] < 0) != (corners[1] < 0):
                v0 = cell_to_active.get((ix, iy, iz), -1)
                v1 = cell_to_active.get((ix, iy-1, iz), -1)
                v2 = cell_to_active.get((ix, iy-1, iz-1), -1)
                v3 = cell_to_active.get((ix, iy, iz-1), -1)
                if v0 >= 0 and v1 >= 0 and v2 >= 0 and v3 >= 0:
                    q = [v0, v1, v2, v3]
                    if corners[1] < corners[0]: q.reverse()
                    quad_vindices.append(q)
            # Y-edge (0,3)
            if (corners[0] < 0) != (corners[3] < 0):
                v0 = cell_to_active.get((ix, iy, iz), -1)
                v1 = cell_to_active.get((ix, iy, iz-1), -1)
                v2 = cell_to_active.get((ix-1, iy, iz-1), -1)
                v3 = cell_to_active.get((ix-1, iy, iz), -1)
                if v0 >= 0 and v1 >= 0 and v2 >= 0 and v3 >= 0:
                    q = [v0, v1, v2, v3]
                    if corners[3] < corners[0]: q.reverse()
                    quad_vindices.append(q)
            # Z-edge (0,4)
            if (corners[0] < 0) != (corners[4] < 0):
                v0 = cell_to_active.get((ix, iy, iz), -1)
                v1 = cell_to_active.get((ix-1, iy, iz), -1)
                v2 = cell_to_active.get((ix-1, iy-1, iz), -1)
                v3 = cell_to_active.get((ix, iy-1, iz), -1)
                if v0 >= 0 and v1 >= 0 and v2 >= 0 and v3 >= 0:
                    q = [v0, v1, v2, v3]
                    if corners[4] < corners[0]: q.reverse()
                    quad_vindices.append(q)

        if not quad_vindices:
            return None
        all_quads = np.array(quad_vindices, dtype=np.int32)
        mesh_timer.stop("dc_quad_assembly")

        mesh_timer.start("mesh_build")
        tri1, tri2 = all_quads[:, [0, 1, 2]], all_quads[:, [0, 2, 3]]
        all_tris = np.vstack([tri1, tri2])
        flat_verts = cell_verts[all_tris.ravel()].astype(np.float32)
        n_tris = len(all_tris)
        base = np.arange(n_tris, dtype=np.int32) * 3
        tri_idx = np.stack([base, base+1, base+2], axis=1)
        flat_idx = np.hstack([tri_idx, np.full((n_tris, 1), -1, dtype=np.int32)]).ravel()
        mesh_timer.stop("mesh_build")

        if decimate:
            mesh_timer.start("decimate")
            flat_verts, flat_idx = decimate_flat_tris(flat_verts, flat_idx)
            mesh_timer.stop("decimate")

        if deduplicate:
            mesh_timer.start("deduplicate")
            flat_verts, flat_idx = deduplicate_verts(flat_verts, flat_idx)
            mesh_timer.stop("deduplicate")

        return flat_verts, flat_idx


def decimate_flat_tris(verts, indices, angle_tol=5.0):
    """
    Merges coplanar adjacent triangles to reduce triangle count.
    
    verts: (N*3, 3) float32 - flat vertex array
    indices: (N*4,) int32 - flat index array with -1 sentinels
    angle_tol: float - maximum angle in degrees between triangle normals to be considered coplanar
    
    Returns: (new_verts, new_indices) in the same format.
    """
    if len(indices) == 0:
        return verts, indices

    n_tris = len(indices) // 4
    tri_verts = verts.reshape(n_tris, 3, 3).astype(np.float64)
    
    # 1. Calculate Face Normals
    v0 = tri_verts[:, 0, :]
    v1 = tri_verts[:, 1, :]
    v2 = tri_verts[:, 2, :]
    
    raw_normals = np.cross(v1 - v0, v2 - v0)
    norms = np.linalg.norm(raw_normals, axis=1)
    
    valid_mask = norms > 1e-12
    normals = np.zeros_like(raw_normals)
    normals[valid_mask] = raw_normals[valid_mask] / norms[valid_mask][:, None]
    
    # 2. Build Face Adjacency
    # We use rounded vertex positions as keys since vertices are not shared.
    def get_v_hash(v):
        return tuple((v / 1e-5).round().astype(np.int64))
        
    edge_to_faces = {} # edge_hash -> list of face_indices
    
    for i in range(n_tris):
        if not valid_mask[i]:
            continue
        v_hashes = [get_v_hash(tri_verts[i, j]) for j in range(3)]
        # Triangle edges
        edges = [
            frozenset([v_hashes[0], v_hashes[1]]),
            frozenset([v_hashes[1], v_hashes[2]]),
            frozenset([v_hashes[2], v_hashes[0]])
        ]
        for e in edges:
            if e not in edge_to_faces:
                edge_to_faces[e] = []
            edge_to_faces[e].append(i)
            
    # Adjacency graph
    adj = [[] for _ in range(n_tris)]
    for faces in edge_to_faces.values():
        if len(faces) == 2:
            f1, f2 = faces
            adj[f1].append(f2)
            adj[f2].append(f1)
            
    # 3. Group Coplanar Adjacent Triangles
    cos_tol = np.cos(np.radians(angle_tol))
    visited = np.zeros(n_tris, dtype=bool)
    groups = []
    
    for i in range(n_tris):
        if visited[i] or not valid_mask[i]:
            continue
        
        group = []
        stack = [i]
        visited[i] = True
        n0 = normals[i]
        
        while stack:
            curr = stack.pop()
            group.append(curr)
            
            for neighbor in adj[curr]:
                if not visited[neighbor]:
                    # Planarity check
                    if np.dot(n0, normals[neighbor]) > cos_tol:
                        visited[neighbor] = True
                        stack.append(neighbor)
        groups.append(group)
        
    # 4. Extract Boundaries and Re-triangulate
    final_verts = []
    final_idx = []
    curr_v_count = 0
    
    # Pre-mapping of hashes to representative positions for boundary reconstruction
    # (Using the very first encounter of a rounded vertex to keep it consistent)
    hash_to_pos = {}
    for i in range(n_tris):
        if not valid_mask[i]: continue
        for j in range(3):
            h = get_v_hash(tri_verts[i, j])
            if h not in hash_to_pos:
                hash_to_pos[h] = tri_verts[i, j]

    for group in groups:
        if len(group) == 1:
            # No decimation possible for single triangle
            f_idx = group[0]
            final_verts.append(tri_verts[f_idx])
            final_idx.extend([curr_v_count, curr_v_count+1, curr_v_count+2, -1])
            curr_v_count += 3
            continue
            
        # Count edge occurrences within the group
        group_edge_counts = {}
        for f_idx in group:
            v_hashes = [get_v_hash(tri_verts[f_idx, j]) for j in range(3)]
            # Use directed edges for boundary tracing: (v1, v2)
            edges = [(v_hashes[0], v_hashes[1]), (v_hashes[1], v_hashes[2]), (v_hashes[2], v_hashes[0])]
            for e in edges:
                # Store as sorted tuple for existence check, but we need direction for loops
                rev_e = (e[1], e[0])
                canonical = tuple(sorted(e))
                group_edge_counts[canonical] = group_edge_counts.get(canonical, 0) + 1

        # Boundary edges are those that appear only once in the group
        boundary_edges = []
        for f_idx in group:
            v_hashes = [get_v_hash(tri_verts[f_idx, j]) for j in range(3)]
            edges = [(v_hashes[0], v_hashes[1]), (v_hashes[1], v_hashes[2]), (v_hashes[2], v_hashes[0])]
            for e in edges:
                if group_edge_counts[tuple(sorted(e))] == 1:
                    boundary_edges.append(e)
                    
        if not boundary_edges:
            continue
            
        # 5. Assemble and Simplify Boundary Loops
        # Group boundary edges into contiguous loops
        edge_map = {e[0]: e[1] for e in boundary_edges}
        loops = []
        while edge_map:
            start_v = next(iter(edge_map))
            loop = [start_v]
            curr_v = edge_map.pop(start_v)
            while curr_v != start_v and curr_v in edge_map:
                loop.append(curr_v)
                next_v = edge_map.pop(curr_v)
                curr_v = next_v
            loops.append(loop)

        # Simplify loops by removing collinear vertices
        simplified_loops = []
        for loop in loops:
            if len(loop) < 3: continue
            simple = []
            for i in range(len(loop)):
                p0 = hash_to_pos[loop[i-1]]
                p1 = hash_to_pos[loop[i]]
                p2 = hash_to_pos[loop[(i+1)%len(loop)]]
                
                v1 = p1 - p0
                v2 = p2 - p1
                v1_n = np.linalg.norm(v1)
                v2_n = np.linalg.norm(v2)
                
                if v1_n > 1e-8 and v2_n > 1e-8:
                    cos_a = np.dot(v1, v2) / (v1_n * v2_n)
                    if cos_a > 1.0 - 1e-6: # Collinear
                        continue
                simple.append(loop[i])
            simplified_loops.append(simple)

        # 6. Triangulate (Simple Centroid Fan for primary loop)
        # TODO: A more robust triangulator (ear-clipping) for complex/holey polygons.
        # For now, we use a simple fan which works for most convex/simple CAD faces.
        for loop in simplified_loops:
            if len(loop) < 3: continue
            
            # Use the first vertex as the fan center
            v_root = hash_to_pos[loop[0]]
            for i in range(1, len(loop) - 1):
                v_a = hash_to_pos[loop[i]]
                v_b = hash_to_pos[loop[i+1]]
                
                final_verts.append([v_root, v_a, v_b])
                final_idx.extend([curr_v_count, curr_v_count+1, curr_v_count+2, -1])
                curr_v_count += 3

    if not final_verts:
        return np.zeros((0, 3), dtype=np.float32), np.zeros(0, dtype=np.int32)
        
    return np.array(final_verts).reshape(-1, 3).astype(np.float32), np.array(final_idx, dtype=np.int32)


def deduplicate_verts(flat_verts, flat_idx, tol=1e-5):
    """
    Deduplicates vertices and updates the index buffer.
    
    flat_verts: (N, 3) float32
    flat_idx: (M,) int32 with -1 sentinels
    tol: float - rounding tolerance for vertex matching
    
    Returns: (unique_verts, new_idx)
    """
    if len(flat_verts) == 0:
        return flat_verts, flat_idx
        
    # 1. Round vertices to tolerance
    rounded = np.round(flat_verts / tol) * tol
    
    # 2. Find unique vertices
    # np.unique returns unique rows and indices to reconstruct the original array
    unique_verts, inverse = np.unique(rounded, axis=0, return_inverse=True)
    
    # 3. Remap index buffer
    # flat_idx contains indices into flat_verts. We need to remap them using 'inverse'.
    # -1 sentinels must remain unchanged.
    
    # Create a mapping for all indices in flat_verts
    # inverse[i] is the index in unique_verts corresponding to flat_verts[i]
    
    # Work on a copy of flat_idx
    new_idx = flat_idx.copy()
    
    # Mask for non-sentinel indices
    mask = new_idx != -1
    
    # Replace mesh indices with their unique vertex counterparts
    # Since flat_verts were emitted 3-per-triangle, flat_idx [0, 1, 2, -1, 3, 4, 5, -1]
    # maps exactly to inverse indices [inverse[0], inverse[1], inverse[2], -1, ...]
    
    # If the meshers emitted Shared Index buffers this would be different, 
    # but currently they emit unique vertices per triangle.
    # The valid indices in flat_idx are always 0..len(flat_verts)-1 in order.
    # Wait, SN and DC might actually use indices differently?
    # No, look at mesh_build in MC/SN/DC:
    # MC: tri_idx = np.stack([base, base+1, base+2], axis=1) -> flat_idx
    # SN: flat_verts = cell_verts[all_tris.ravel()] ... tri_idx = np.stack([base, base+1, base+2], axis=1)
    # DC: (in the portion not seen, but likely similar)
    
    # So new_idx[mask] are currently 0, 1, 2, 3, 4, 5...
    # We replace them with inverse[new_idx[mask]]
    new_idx[mask] = inverse[new_idx[mask]]
    
    return unique_verts.astype(np.float32), new_idx.astype(np.int32)


def get_active_mesher(type_override=None) -> DMMesher:
    """Return the active mesher based on type override, defaulting to Marching Cubes (0).

    Handles both integer indices and string labels from FreeCAD's PropertyEnumeration.
    """
    st = type_override if type_override is not None else 0

    # 0: MarchingCubes, 1: SurfaceNets, 2: DualContouring
    label = str(st)
    if st == 1 or "Surface Nets" in label:
        return SurfaceNetsMesher()
    if st == 2 or "Dual Contouring" in label:
        return DualContouringMesher()
    return MarchingCubesMesher()

