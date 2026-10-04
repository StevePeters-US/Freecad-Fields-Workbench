# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/mesh/mesh_shared.py

Vectorized helpers shared by more than one mesher in fld_mesher.py.

`_pack_cell_keys` / `_lookup_cell_keys` give sparse (ix,iy,iz) -> row-index
lookups without a dense 3D array (Surface Nets / Dual Contouring neighbor
queries). `_zero_crossing_t` is the edge-interpolation parameter shared by
MarchingCubesMesher, SurfaceNetsMesher and DualContouringMesher. `_DUAL_EDGE_SPECS`
and `_assemble_dual_quads` are the dual-vertex quad assembly shared by
SurfaceNetsMesher and DualContouringMesher (CR-038) -- each used to
reimplement these identically.
"""
import numpy as np

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
