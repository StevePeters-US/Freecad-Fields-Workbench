# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/mesh/mesh_surface_nets.py

`SurfaceNetsMesher`: naive Surface Nets (Gibson 1998), one vertex per active
cell (average of edge crossing points), with a constrained-Laplacian
relaxation pass. Produces quad-dominant meshes, triangulated for Coin3D.
"""
import numpy as np

from freecad.fields.core.sdf.sdf_field import SdfField
from freecad.fields.core.sdf.sdf_constants import CELL_OFFSETS as _CELL_OFFSETS
from freecad.fields.core.mesh.mesh_base import FldMesher
from freecad.fields.core.mesh.mesh_shared import _zero_crossing_t, _pack_cell_keys, _DUAL_EDGE_SPECS, _assemble_dual_quads
from freecad.fields.core.mesh.mesh_timer import mesh_timer


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
            safe_adj = np.where(mask, adj_table, 0)
            neighbor_pos = curr_verts[safe_adj]
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
