# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/mesh/mesh_dual_contouring.py

`DualContouringMesher`: Dual Contouring (Ju et al. 2002). Preserves sharp
features by solving a Quadratic Error Function (QEF) per cell to find the
optimal vertex position.
"""
import numpy as np

from freecad.fields.core.sdf.sdf_field import SdfField
from freecad.fields.core.sdf.sdf_constants import CELL_OFFSETS as _CELL_OFFSETS
from freecad.fields.core.mesh.mesh_base import FldMesher
from freecad.fields.core.mesh.mesh_shared import (
    _zero_crossing_t, _pack_cell_keys, _lookup_cell_keys, _DUAL_EDGE_SPECS, _assemble_dual_quads,
)
from freecad.fields.core.mesh.mesh_timer import mesh_timer


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
