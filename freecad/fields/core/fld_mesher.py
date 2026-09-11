# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import Part
import FreeCAD

from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf_field import SdfField
from freecad.fields.core.sdf.marching_cubes.mc_tables import edgeTable, triTable
from freecad.fields.core.objects.fld_object import get_perf_profiler_enabled
from freecad.fields.core.sdf.sdf_constants import CELL_OFFSETS as _CELL_OFFSETS

import time
import numpy as np

# Pre-convert lookup tables to numpy arrays for fast indexing
_EDGE_TABLE = np.array(edgeTable, dtype=np.int32)

# triTable as (256, 16) int8 array - -1 fills unused triangle slots
_TRI_TABLE  = np.array(triTable,  dtype=np.int8)

# Edge endpoint corner indices (constant, vectorized over active cubes)
_EDGE_C1 = np.array([0,1,2,3, 4,5,6,7, 0,1,2,3], dtype=np.int32)
_EDGE_C2 = np.array([1,2,3,0, 5,6,7,4, 4,5,6,7], dtype=np.int32)

# Integer-coordinate hashing for sparse (ix,iy,iz) -> row-index lookups (Surface Nets / Dual
# Contouring neighbor queries). Avoids a dense 3D array, which would violate the no-dense-grid
# invariant for large/fine fields. 20 bits per axis covers +-524288 cells, far beyond what
# 0.05mm-over-1m (max ~20000 cells/axis) ever needs, with no risk of int64 overflow.
_KEY_SHIFT = 1 << 20
_KEY_BIAS = 1 << 19


def _pack_cell_keys(coords):
    """coords: (N,3) int — returns (N,) int64 unique keys for sparse hashing."""
    c = coords.astype(np.int64) + _KEY_BIAS
    return (c[:, 0] * _KEY_SHIFT + c[:, 1]) * _KEY_SHIFT + c[:, 2]


def _lookup_cell_keys(query_coords, sorted_keys, sorted_vidx):
    """Vectorized lookup of query_coords against a pre-sorted (key, vidx) table.
    Returns (N,) int64 of vidx, or -1 where not found."""
    qkeys = _pack_cell_keys(query_coords)
    pos = np.searchsorted(sorted_keys, qkeys)
    pos_clipped = np.clip(pos, 0, len(sorted_keys) - 1)
    found = (pos < len(sorted_keys)) & (sorted_keys[pos_clipped] == qkeys)
    result = np.full(len(query_coords), -1, dtype=np.int64)
    result[found] = sorted_vidx[pos_clipped[found]]
    return result


def _zero_crossing_t(v1, v2, eps=1e-8, fallback=0.5):
    """Newton-style interpolation parameter `t` where the line from `v1` to `v2`
    crosses zero: `v1 + t*(v2-v1) == 0`. Falls back to `fallback` (not 0/0)
    wherever the edge is degenerate (`|v2-v1| <= eps`).

    Shared by MarchingCubesMesher, SurfaceNetsMesher and DualContouringMesher
    (CR-038), which each reimplemented this identically except for the fallback:
    MarchingCubesMesher wants 0.0 (its own table lookup already excludes any
    edge where a degenerate fallback would show up), the other two want 0.5
    (the cell midpoint) -- pass `fallback` explicitly rather than relying on
    the default at that one call site.
    """
    dv = v2 - v1
    safe = np.abs(dv) > eps
    return np.where(safe, -v1 / np.where(safe, dv, 1.0), fallback)


# (c1, c2, [4 neighbor cell offsets sharing that edge]) for the 3 canonical
# dual-cell edges starting at corner (0,0,0): (0,1) X, (0,3) Y, (0,4) Z.
# Shared by SurfaceNetsMesher and DualContouringMesher (CR-038), which each
# retyped this identical literal (DualContouringMesher twice, once per pass).
_DUAL_EDGE_SPECS = [
    (0, 1, [(0, 0, 0), (0, -1, 0), (0, -1, -1), (0, 0, -1)]),   # X-edge
    (0, 3, [(0, 0, 0), (0, 0, -1), (-1, 0, -1), (-1, 0, 0)]),   # Y-edge
    (0, 4, [(0, 0, 0), (-1, 0, 0), (-1, -1, 0), (0, -1, 0)]),   # Z-edge
]


def _assemble_dual_quads(corners, coords, edge_specs, sorted_keys, sorted_vidx):
    """Build quads from dual vertices: for every edge with a sign change,
    connect the (up to 4) neighbor cells sharing it, flipping winding where the
    crossing direction requires it.

    Shared by SurfaceNetsMesher and DualContouringMesher (CR-038) -- their
    "one vertex per cell" computation differs (averaged edge crossings vs a
    solved QEF), but this assembly step, once each active cell's dual-vertex
    index is known, does not. Returns a list of (Q,4) int arrays, one per
    edge family in `edge_specs`, in the same concatenation order both meshers
    already used -- callers still do their own `if not quad_list: ...` /
    `np.concatenate(...)`, since that part isn't identical between them
    (DualContouringMesher stashes `self._debug_quads` first).
    """
    quad_list = []
    for c1, c2, deltas in edge_specs:
        sc = (corners[:, c1] < 0) != (corners[:, c2] < 0)
        if not np.any(sc):
            continue
        sel_coords = coords[sc]
        flip = corners[sc, c2] < corners[sc, c1]

        neighbor_vidx = []
        for dx, dy, dz in deltas:
            nb = sel_coords + np.array([dx, dy, dz], dtype=np.int64)
            neighbor_vidx.append(_lookup_cell_keys(nb, sorted_keys, sorted_vidx))
        quad = np.stack(neighbor_vidx, axis=1)  # (Q, 4)

        valid = np.all(quad >= 0, axis=1)
        quad = quad[valid]
        flip = flip[valid]
        if len(quad) == 0:
            continue
        quad[flip] = quad[flip][:, ::-1]
        quad_list.append(quad)
    return quad_list


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

    def summary(self, label: str = "Fields Mesh"):
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
        fld_logger.info("\n".join(lines))
        self.reset()


# Module-level singleton - shared across all mesher calls for the same tool session
mesh_timer = MeshTimer()


class FldMesher:
    """Abstract base class for all Fields SDF meshers.

    Implements the shared octree-build / timing / post-processing pipeline
    (Template Method pattern); subclasses provide the isosurface extraction
    logic via `_extract_mesh_geometry`.
    """
    def mesh(self, field: SdfField, cell_size: float, decimate=False, deduplicate=True, bounds=None, **kwargs) -> tuple:
        from freecad.fields.core.sdf.sdf_octree import SdfOctreeCache
        mesh_timer.tick()

        proxy = kwargs.get("proxy", None)
        octree = None

        if proxy is not None:
            cached_octree = getattr(proxy, "_octree_cache", None)
            if cached_octree is not None and abs(cached_octree.leaf_size - cell_size) < 1e-6:
                from freecad.fields.core.sdf.sdf.cage import SdfCageField
                if isinstance(field, SdfCageField):
                    current_sig = (len(field.vertices), tuple(tuple(f) for f in field._face_verts))
                else:
                    current_sig = None
                
                cached_sig = getattr(proxy, "_octree_topo_sig", None)
                if cached_sig == current_sig:
                    octree = cached_octree
                    octree.field = field
                    if bounds is not None:
                        mesh_timer.start("field_eval")
                        octree.update_region(bounds)
                        mesh_timer.stop("field_eval")
                    else:
                        mesh_timer.start("field_eval")
                        octree.build()
                        mesh_timer.stop("field_eval")

        if octree is None:
            octree = SdfOctreeCache(field, leaf_size=cell_size)
            mesh_timer.start("field_eval")
            octree.build(bounds=bounds)
            mesh_timer.stop("field_eval")
            if proxy is not None:
                proxy._octree_cache = octree
                from freecad.fields.core.sdf.sdf.cage import SdfCageField
                if isinstance(field, SdfCageField):
                    proxy._octree_topo_sig = (len(field.vertices), tuple(tuple(f) for f in field._face_verts))
                else:
                    proxy._octree_topo_sig = None

        if not octree._leaves:
            return np.zeros((0, 3), dtype=np.float32), np.zeros(0, dtype=np.int32)

        flat_verts, flat_idx = self._extract_mesh_geometry(octree, field, cell_size, **kwargs)

        if decimate:
            mesh_timer.start("decimate")
            flat_verts, flat_idx = decimate_flat_tris(flat_verts, flat_idx)
            mesh_timer.stop("decimate")

        if deduplicate:
            mesh_timer.start("deduplicate")
            flat_verts, flat_idx = deduplicate_verts(flat_verts, flat_idx)
            mesh_timer.stop("deduplicate")

        return flat_verts, flat_idx

    def _extract_mesh_geometry(self, octree, field: SdfField, cell_size: float, **kwargs) -> tuple:
        raise NotImplementedError("mesher must implement _extract_mesh_geometry()")


class MarchingCubesMesher(FldMesher):
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
    def _extract_mesh_geometry(self, octree, field: SdfField, cell_size: float, **kwargs) -> tuple:
        # Collect surface-crossing leaf cells using the fast vectorized method
        origins, sizes_arr, cv = octree.get_active_leaves()

        if len(origins) == 0:
            return np.zeros((0, 3), dtype=np.float32), np.zeros(0, dtype=np.int32)

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
            return np.zeros((0, 3), dtype=np.float32), np.zeros(0, dtype=np.int32)

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

        t = _zero_crossing_t(v1e, v2e, fallback=0.0)
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
            return np.zeros((0, 3), dtype=np.float32), np.zeros(0, dtype=np.int32)

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

        return flat_verts, flat_idx




class SurfaceNetsMesher(FldMesher):
    """
    Naive Surface Nets (Gibson 1998).
    
    One vertex per active cell (average of edge crossing points).
    Produces quad-dominant meshes (triangulated for Coin3D).
    
    Vectorized implementation:
    1. Grid sampling & field eval (same as MC)
    2. Edge sign-change detection for all 12 edge families
    3. Net cell assembly
    """
    def _extract_mesh_geometry(self, octree, field: SdfField, cell_size: float, **kwargs) -> tuple:
        mesh_timer.start("sn_vertex_compute")
        # Surface Nets: one vertex per active cell (average of edge crossing points).
        # Vectorized over all leaf cells at once (no Python loop per cell).
        coords  = octree._leaves_coords    # (M, 3)
        corners = octree._leaves_corners.astype(np.float64)  # (M, 8)
        M = len(coords)
        cell_origins = octree._origin + coords.astype(np.float64) * cell_size  # (M, 3)

        # All 12 edges (Lorensen-Cline corner pairs), matching the original per-cell loop order.
        _SN_C1 = np.array([0,3,4,7, 0,1,4,5, 0,1,2,3], dtype=np.int32)
        _SN_C2 = np.array([1,2,5,6, 3,2,7,6, 4,5,6,7], dtype=np.int32)

        v1e = corners[:, _SN_C1]  # (M, 12)
        v2e = corners[:, _SN_C2]
        sign_change = (v1e < 0) != (v2e < 0)  # (M, 12)
        t = _zero_crossing_t(v1e, v2e)

        _OFFS = _CELL_OFFSETS * cell_size  # (8, 3)
        p1 = _OFFS[_SN_C1]  # (12, 3)
        p2 = _OFFS[_SN_C2]
        local_edge_pts = p1[None, :, :] + t[:, :, None] * (p2[None, :, :] - p1[None, :, :])  # (M, 12, 3)

        sum_local = np.where(sign_change[:, :, None], local_edge_pts, 0.0).sum(axis=1)  # (M, 3)
        edge_count = sign_change.sum(axis=1)  # (M,)

        active_mask = edge_count > 0
        if not np.any(active_mask):
            return np.zeros((0, 3), dtype=np.float32), np.zeros(0, dtype=np.int32)

        active_coords = coords[active_mask]
        cell_verts = cell_origins[active_mask] + sum_local[active_mask] / edge_count[active_mask, None]
        M = len(cell_verts)
        mesh_timer.stop("sn_vertex_compute")

        mesh_timer.start("sn_quad_assembly")
        # For every edge with a sign change, connect the 4 cells that share it.
        # We only check each edge ONCE, via the 3 edges starting at corner (0,0,0): (0,1), (0,3), (0,4).
        sorted_order = np.argsort(_pack_cell_keys(active_coords))
        sorted_keys  = _pack_cell_keys(active_coords)[sorted_order]
        sorted_vidx  = sorted_order.astype(np.int64)

        quad_list = _assemble_dual_quads(corners, coords, _DUAL_EDGE_SPECS, sorted_keys, sorted_vidx)

        if not quad_list:
            return np.zeros((0, 3), dtype=np.float32), np.zeros(0, dtype=np.int32)

        all_quads = np.concatenate(quad_list, axis=0).astype(np.int32)
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
            from freecad.fields.core.sdf.field_eval import eval_grid
            sdf_vals = eval_grid(field, new_verts.astype(np.float32))
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

        return flat_verts, flat_idx


class DualContouringMesher(FldMesher):
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
    def _extract_mesh_geometry(self, octree, field: SdfField, cell_size: float, **kwargs) -> tuple:
        mesh_timer.start("dc_qef_collect")
        # Vectorized over all leaf cells at once (no Python loop per cell/edge).
        coords  = octree._leaves_coords    # (N, 3)
        corners = octree._leaves_corners.astype(np.float64)  # (N, 8)
        cell_origins = octree._origin + coords.astype(np.float64) * cell_size  # (N, 3)

        # A cell is active for DC if it straddles the surface (any corner sign change possible).
        active_mask = (corners.min(axis=1) < 0.0) & (corners.max(axis=1) >= 0.0)
        active_coords = coords[active_mask]
        M = len(active_coords)
        if M == 0:
            return np.zeros((0, 3), dtype=np.float32), np.zeros(0, dtype=np.int32)

        active_origin = cell_origins[active_mask]
        ata = np.zeros((M, 3, 3), dtype=np.float64)
        atb = np.zeros((M, 3), dtype=np.float64)
        mass_point = np.zeros((M, 3), dtype=np.float64)
        count = np.zeros(M, dtype=np.int64)

        sorted_order = np.argsort(_pack_cell_keys(active_coords))
        sorted_keys  = _pack_cell_keys(active_coords)[sorted_order]
        sorted_vidx  = sorted_order.astype(np.int64)

        _OFFS = _CELL_OFFSETS * cell_size  # (8, 3) local corner offsets

        # For every edge with a sign change, compute the crossing point + normal and
        # scatter-accumulate the QEF contribution into the (up to 4) cells sharing it.
        edge_specs = _DUAL_EDGE_SPECS
        for c1, c2, deltas in edge_specs:
            sc = (corners[:, c1] < 0) != (corners[:, c2] < 0)  # (N,)
            if not np.any(sc):
                continue
            sel_coords = coords[sc]
            v1c, v2c = corners[sc, c1], corners[sc, c2]
            t = _zero_crossing_t(v1c, v2c)
            p = cell_origins[sc] + _OFFS[c1] + t[:, None] * (_OFFS[c2] - _OFFS[c1])  # (Q, 3)

            grads = field.gradient_grid(p)
            norm = np.linalg.norm(grads, axis=1)
            valid = norm > 1e-12
            if not np.any(valid):
                continue
            sel_coords, p, grads, norm = sel_coords[valid], p[valid], grads[valid], norm[valid]

            n = grads / norm[:, None]
            ata_c = n[:, :, None] * n[:, None, :]            # (Q, 3, 3)
            atb_c = n * np.sum(n * p, axis=1, keepdims=True)  # (Q, 3)

            for dx, dy, dz in deltas:
                nb = sel_coords + np.array([dx, dy, dz], dtype=np.int64)
                vidx = _lookup_cell_keys(nb, sorted_keys, sorted_vidx)
                m = vidx >= 0
                if not np.any(m):
                    continue
                idxs = vidx[m]
                np.add.at(ata, idxs, ata_c[m])
                np.add.at(atb, idxs, atb_c[m])
                np.add.at(mass_point, idxs, p[m])
                np.add.at(count, idxs, 1)

        mesh_timer.stop("dc_qef_collect")

        mesh_timer.start("dc_qef_solve")
        cell_verts = np.zeros((M, 3), dtype=np.float64)
        has_count = count > 0
        U, S, Vh = np.linalg.svd(ata)
        max_s = np.max(S, axis=1)
        threshold = 0.1 * max_s

        s_inv = np.where(S > threshold[:, None], 1.0 / np.where(S > 0, S, 1.0), 0.0)  # (M, 3)
        V = np.transpose(Vh, (0, 2, 1))
        pinv = np.einsum('mij,mj,mkj->mik', V, s_inv, U)
        v = np.einsum('mik,mk->mi', pinv, atb)

        eps = 1e-4
        out_of_bounds = np.any(
            (v < active_origin - eps) | (v > active_origin + cell_size + eps), axis=1
        )
        bad = out_of_bounds | (threshold < 1e-6)
        safe_count = np.maximum(count, 1)[:, None]
        fallback = mass_point / safe_count
        final = np.where(bad[:, None], fallback, v)
        cell_verts[has_count] = final[has_count]
        self._debug = (active_coords.copy(), cell_verts.copy())
        mesh_timer.stop("dc_qef_solve")

        mesh_timer.start("dc_quad_assembly")
        quad_list = _assemble_dual_quads(corners, coords, edge_specs, sorted_keys, sorted_vidx)

        if not quad_list:
            return np.zeros((0, 3), dtype=np.float32), np.zeros(0, dtype=np.int32)
        all_quads = np.concatenate(quad_list, axis=0).astype(np.int32)
        self._debug_quads = all_quads.copy()
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

        def _ear_clip_triangulate(loop_ids):
            """Ear-clipping triangulation of a flat (possibly concave) polygon.
            Returns a list of (a, b, c) index triples into loop_ids."""
            pts = [hash_to_pos[h] for h in loop_ids]
            n = len(pts)
            if n < 3:
                return []
            # Best-fit normal via Newell's method (robust for near-planar loops)
            normal = np.zeros(3)
            for i in range(n):
                p0, p1 = pts[i], pts[(i + 1) % n]
                normal += np.cross(p0, p1)
            norm_len = np.linalg.norm(normal)
            if norm_len < 1e-12:
                return []
            normal /= norm_len
            # Project to 2D on the dominant plane
            axis = np.argmax(np.abs(normal))
            u_axis, v_axis = [i for i in range(3) if i != axis]
            pts2d = [(p[u_axis], p[v_axis]) for p in pts]
            if normal[axis] < 0:
                pts2d = pts2d[::-1]
                loop_ids = loop_ids[::-1]

            def cross2(o, a, b):
                return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

            def point_in_tri(p, a, b, c):
                d1 = cross2(a, b, p)
                d2 = cross2(b, c, p)
                d3 = cross2(c, a, p)
                has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
                has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
                return not (has_neg and has_pos)

            indices = list(range(n))
            tris = []
            guard = 0
            while len(indices) > 3 and guard < 10 * n:
                guard += 1
                ear_found = False
                for k in range(len(indices)):
                    i_prev = indices[k - 1]
                    i_curr = indices[k]
                    i_next = indices[(k + 1) % len(indices)]
                    a, b, c = pts2d[i_prev], pts2d[i_curr], pts2d[i_next]
                    if cross2(a, b, c) <= 1e-12:
                        continue  # reflex or degenerate, not an ear
                    if any(
                        point_in_tri(pts2d[idx], a, b, c)
                        for idx in indices
                        if idx not in (i_prev, i_curr, i_next)
                    ):
                        continue
                    tris.append((loop_ids[i_prev], loop_ids[i_curr], loop_ids[i_next]))
                    del indices[k]
                    ear_found = True
                    break
                if not ear_found:
                    break  # degenerate polygon, stop clipping what's left
            if len(indices) == 3:
                tris.append((loop_ids[indices[0]], loop_ids[indices[1]], loop_ids[indices[2]]))
            return tris

        # 6. Triangulate (ear-clipping, handles concave loops)
        for loop in simplified_loops:
            if len(loop) < 3: continue
            for a, b, c in _ear_clip_triangulate(loop):
                final_verts.append([hash_to_pos[a], hash_to_pos[b], hash_to_pos[c]])
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


def get_active_mesher(type_override=None) -> FldMesher:
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

