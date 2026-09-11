# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/sdf/sdf_baked.py

Bakes a 3D static signed distance field volume over a regular grid.
Evaluated on the GPU when compute shaders are supported.
"""
import numpy as np
import FreeCAD
from freecad.fields.core.sdf.field_eval import eval_grid
from freecad.fields.core.sdf.sdf_field import placement_matrix
from freecad.fields.core.sdf.gpu_field_eval import GpuFieldEvaluator

def compute_bake_grid_bounds(bmin_local, bmax_local, resolution=96, pad=0.05):
    """Compute local grid coordinates (bmin_grid, bmax_grid) for the bake.
    Shared between shader code emission and texture baking to guarantee alignment.
    """
    extents = bmax_local - bmin_local
    extents = np.maximum(extents, 1e-5)
    
    pad_val = extents * pad
    bmin_padded = bmin_local - pad_val
    bmax_padded = bmax_local + pad_val
    
    longest_axis = np.argmax(extents)
    longest_extent = bmax_padded[longest_axis] - bmin_padded[longest_axis]
    cell_size = longest_extent / resolution
    
    bmin_grid = bmin_padded - cell_size
    bmax_grid = bmax_padded + cell_size
    
    nx = max(8, int(np.round((bmax_grid[0] - bmin_grid[0]) / cell_size)))
    ny = max(8, int(np.round((bmax_grid[1] - bmin_grid[1]) / cell_size)))
    nz = max(8, int(np.round((bmax_grid[2] - bmin_grid[2]) / cell_size)))
    
    bmax_grid = np.array([
        bmin_grid[0] + nx * cell_size,
        bmin_grid[1] + ny * cell_size,
        bmin_grid[2] + nz * cell_size
    ], dtype=np.float32)
    
    return bmin_grid, bmax_grid, (nx, ny, nz)

def bake_field_grid(field, resolution=96, pad=0.05):
    """Sample field.evaluate-compatible signed distance on a regular grid.

    Grid covers the field's bounding_box() padded by `pad` (fraction) plus one
    voxel margin. `resolution` is the cell count along the longest axis; other
    axes scale by extent (min 8). Returns (values (nz+1,ny+1,nx+1) float32, bmin (3,),
    bmax (3,)) or None when no GL/GPU evaluator is available.
    """
    if not GpuFieldEvaluator.is_available():
        return None

    # Get local bounding box. For SdfCageField, use cached _cage_aabb.
    if hasattr(field, "_cage_aabb") and field._cage_aabb is not None:
        bmin_local, bmax_local = field._cage_aabb
    elif hasattr(field, "vertices") and hasattr(field, "handles"):
        all_pts = np.vstack([field.vertices, field.handles])
        bmin_local = np.min(all_pts, axis=0)
        bmax_local = np.max(all_pts, axis=0)
    else:
        mn, mx = field.bounding_box()
        bmin_local = np.array([mn.x, mn.y, mn.z], dtype=np.float32)
        bmax_local = np.array([mx.x, mx.y, mx.z], dtype=np.float32)
        if field.placement is not None:
            corners = []
            for dx in [0, 1]:
                for dy in [0, 1]:
                    for dz in [0, 1]:
                        p = FreeCAD.Vector(
                            mn.x + dx * (mx.x - mn.x),
                            mn.y + dy * (mx.y - mn.y),
                            mn.z + dz * (mx.z - mn.z)
                        )
                        lp = field._to_local_point(p)
                        corners.append([lp.x, lp.y, lp.z])
            corners = np.array(corners)
            bmin_local = np.min(corners, axis=0)
            bmax_local = np.max(corners, axis=0)

    # Compute grid coordinates and counts using the shared helper
    bmin_grid, bmax_grid, (nx, ny, nz) = compute_bake_grid_bounds(
        bmin_local, bmax_local, resolution=resolution, pad=pad
    )

    x = np.linspace(bmin_grid[0], bmax_grid[0], nx + 1)
    y = np.linspace(bmin_grid[1], bmax_grid[1], ny + 1)
    z = np.linspace(bmin_grid[2], bmax_grid[2], nz + 1)

    Z, Y, X = np.meshgrid(z, y, x, indexing='ij')
    local_pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]).astype(np.float32)

    # Transform local points to world points
    if field.placement is not None:
        m = placement_matrix(field.placement, dtype=np.float32)
        N = local_pts.shape[0]
        pts_hom = np.hstack((local_pts, np.ones((N, 1), dtype=np.float32)))
        world_pts = (pts_hom @ m.T)[:, :3]
    else:
        world_pts = local_pts

    try:
        # GPU evaluated batch points
        values_flat = eval_grid(field, world_pts)
        values = values_flat.reshape((nz + 1, ny + 1, nx + 1)).astype(np.float32)
        return values, bmin_grid, bmax_grid
    except Exception as e:
        from freecad.fields.core import fld_logger
        fld_logger.error(f"bake_field_grid: Failed to evaluate grid on GPU: {e}")
        return None
