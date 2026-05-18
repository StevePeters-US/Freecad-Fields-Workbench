"""
core/sdf/sdf_baker.py

Bakes any SdfField to a 3D float32 volume (dense grid).
Used by mesh export tools. Rendering uses the analytical GLSL path instead.
"""
import math
import time
import numpy as np
import FreeCAD


def bake_sdf_to_volume(field, cell_size: float, bbox_override=None) -> dict:
    """
    Sample the SDF on a uniform grid and pack as a float32 3D volume.

    Args:
        field:         Any SdfField subclass.
        cell_size:     Grid spacing in mm.
        bbox_override: Optional (min_vec, max_vec) to bake a sub-region.
                       If None, uses field.bounding_box().

    Returns dict with keys:
        volume_bytes : bytes          — float32 as bytes, (nz+1)*(ny+1)*(nx+1)*4
        nx, ny, nz   : int            — grid cell counts
        bbox_min     : FreeCAD.Vector
        bbox_max     : FreeCAD.Vector
        max_dist     : float          — informational (= cell_size * 8.0)
    """
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

    t0 = time.perf_counter()

    from core.sdf.sdf_octree import SdfOctreeCache
    octree = SdfOctreeCache(field, leaf_size=cell_size)
    octree._origin = np.array([x0, y0, z0], dtype=np.float64) # Force alignment
    octree.build()

    t1 = time.perf_counter()
    
    # Initialize dense grid with max_dist
    max_dist = cell_size * 8.0
    vol = np.full((nx + 1, ny + 1, nz + 1), max_dist, dtype=np.float32)

    # Fill voxels from octree leaves
    # Each leaf (ix, iy, iz) owns 8 corners: (ix+dx, iy+dy, iz+dz) where d in {0,1}
    # Using the same indexing as dm_mesher
    for (ix, iy, iz), corners in octree._leaves.items():
        if ix < 0 or iy < 0 or iz < 0 or ix >= nx or iy >= ny or iz >= nz:
            continue
        
        # Corners in Lorensen-Cline order (dm_mesher.py):
        # 0:(0,0,0), 1:(1,0,0), 2:(1,1,0), 3:(0,1,0), 4:(0,0,1), 5:(1,0,1), 6:(1,1,1), 7:(0,1,1)
        vol[ix,   iy,   iz]   = corners[0]
        vol[ix+1, iy,   iz]   = corners[1]
        vol[ix+1, iy+1, iz]   = corners[2]
        vol[ix,   iy+1, iz]   = corners[3]
        vol[ix,   iy,   iz+1] = corners[4]
        vol[ix+1, iy,   iz+1] = corners[5]
        vol[ix+1, iy+1, iz+1] = corners[6]
        vol[ix,   iy+1, iz+1] = corners[7]

    t2 = time.perf_counter()
    # Transpose to (nz+1, ny+1, nx+1) for OpenGL row-major (Z-outermost)
    vol_out = vol.transpose(2, 1, 0)
    volume_bytes = vol_out.tobytes()
    t3 = time.perf_counter()

    from core.dm_object import get_perf_profiler_enabled
    if get_perf_profiler_enabled():
        n_leaves = len(octree._leaves)
        from core import dm_logger
        dm_logger.info(
            f"[bake] grid={nx}x{ny}x{nz} ({n_leaves} active cells) cell={cell_size:.1f}mm | "
            f"octree_build={1000*(t1-t0):.1f}ms  fill_voxels={1000*(t2-t1):.1f}ms  "
            f"transpose+bytes={1000*(t3-t2):.1f}ms  total={1000*(t3-t0):.1f}ms"
        )

    return {
        "volume_bytes": volume_bytes,
        "nx": nx, "ny": ny, "nz": nz,
        "bbox_min": FreeCAD.Vector(x0, y0, z0),
        "bbox_max": FreeCAD.Vector(x0 + nx * cell_size,
                                    y0 + ny * cell_size,
                                    z0 + nz * cell_size),
        "max_dist": cell_size * 8.0,
    }

