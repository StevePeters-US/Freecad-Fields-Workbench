# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/mesh/mesh_marching_cubes.py

`MarchingCubesMesher`: uniform-grid Marching Cubes, fully vectorized over the
octree's active leaf cells (no Python loop per cube).
"""
import numpy as np

from freecad.fields.core.sdf.sdf_field import SdfField
from freecad.fields.core.sdf.marching_cubes.mc_tables import triTable
from freecad.fields.core.sdf.sdf_constants import CELL_OFFSETS as _CELL_OFFSETS
from freecad.fields.core.mesh.mesh_base import FldMesher
from freecad.fields.core.mesh.mesh_shared import _zero_crossing_t
from freecad.fields.core.mesh.mesh_timer import mesh_timer

# triTable as (256, 16) int8 array - -1 fills unused triangle slots
_TRI_TABLE  = np.array(triTable,  dtype=np.int8)

# Edge endpoint corner indices (constant, vectorized over active cubes)
_EDGE_C1 = np.array([0,1,2,3, 4,5,6,7, 0,1,2,3], dtype=np.int32)
_EDGE_C2 = np.array([1,2,3,0, 5,6,7,4, 4,5,6,7], dtype=np.int32)


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
