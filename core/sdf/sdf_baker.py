"""
core/sdf/sdf_baker.py

Bakes any SdfField to a 3D float32 volume for GPU ray marching.
Uses field.evaluate_grid() and field.bounding_box() — no primitives.
"""
import math
import time
import numpy as np
import FreeCAD

_openvdb_available = None

def has_openvdb():
    """Return True if pyopenvdb is importable. Caches the result."""
    global _openvdb_available
    if _openvdb_available is None:
        try:
            import openvdb
            _openvdb_available = True
        except ImportError:
            _openvdb_available = False
    return _openvdb_available


def bake_sdf_to_volume(field, cell_size: float, bbox_override=None) -> dict:
    """
    Sample the SDF on a uniform grid and pack as a float32 RGBA8 3D volume.

    Args:
        field:         Any SdfField subclass.
        cell_size:     Grid spacing in mm.
        bbox_override: Optional (min_vec, max_vec) to bake a sub-region of the field.
                       If None, uses field.bounding_box(). Must be within field bounds.

    Returns dict with keys:
        volume_bytes : bytes          — float32 as RGBA8 bytes, (nz+1)*(ny+1)*(nx+1)*4
        nx, ny, nz   : int           — grid cell counts
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

    xs = np.linspace(x0, x0 + nx * cell_size, nx + 1)
    ys = np.linspace(y0, y0 + ny * cell_size, ny + 1)
    zs = np.linspace(z0, z0 + nz * cell_size, nz + 1)

    X, Y, Z = np.meshgrid(xs, ys, zs, indexing='ij')
    pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]).astype(np.float32)

    t1 = time.perf_counter()
    vals = field.evaluate_grid(pts).astype(np.float32)
    t2 = time.perf_counter()

    # Reshape to (nx+1, ny+1, nz+1), then transpose to (nz+1, ny+1, nx+1)
    # for row-major upload where x varies fastest (OpenGL convention).
    vol = vals.reshape(nx + 1, ny + 1, nz + 1).transpose(2, 1, 0)

    # Reinterpret float32 as 4×uint8 (RGBA)
    volume_bytes = vol.tobytes()
    t3 = time.perf_counter()

    from core.dm_object import get_perf_profiler_enabled
    if get_perf_profiler_enabled():
        n_pts = pts.shape[0]
        FreeCAD.Console.PrintMessage(
            f"[bake] grid={nx}x{ny}x{nz} ({n_pts} pts) cell={cell_size:.1f}mm | "
            f"meshgrid={1000*(t1-t0):.1f}ms  eval={1000*(t2-t1):.1f}ms  "
            f"reshape+tobytes={1000*(t3-t2):.1f}ms  total={1000*(t3-t0):.1f}ms\n"
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

def vdb_grid_to_dense(grid, voxel_size):
    """Extract VDB narrow band to dense numpy array for GL_R32F 3D texture upload.

    Returns dict with same keys as bake_sdf_to_volume():
        volume_bytes, nx, ny, nz, bbox_min, bbox_max, max_dist, cell_size
    """
    import math
    bbox = grid.evalActiveVoxelBoundingBox()
    imin, imax = bbox
    # Pad by 1 voxel on each side for interpolation safety
    imin = tuple(i - 1 for i in imin)
    imax = tuple(i + 2 for i in imax)
    shape = tuple(mx - mn for mn, mx in zip(imin, imax))

    dense = np.full(shape, grid.background, dtype=np.float32)
    grid.copyToArray(dense, imin)

    world_min = grid.transform.indexToWorld(imin)
    world_max = grid.transform.indexToWorld(imax)

    # OpenGL row-major convention: transpose (x,y,z) → (z,y,x)
    vol = dense.transpose(2, 1, 0)

    # nx, ny, nz are cell counts (one less than sample counts)
    nx = shape[0] - 1
    ny = shape[1] - 1
    nz = shape[2] - 1

    return {
        "volume_bytes": vol.tobytes(),
        "nx": nx, "ny": ny, "nz": nz,
        "bbox_min": FreeCAD.Vector(float(world_min[0]), float(world_min[1]), float(world_min[2])),
        "bbox_max": FreeCAD.Vector(float(world_max[0]), float(world_max[1]), float(world_max[2])),
        "max_dist": voxel_size * 8.0,
        "cell_size": voxel_size,
    }

def bake_sdf_vdb(field, cell_size, bbox_override=None):
    """Bake an SDF field via VDB sparse level set.

    Same return format as bake_sdf_to_volume(). Raises ImportError if
    pyopenvdb is not installed.

    Args:
        field:         Any SdfField subclass with to_vdb().
        cell_size:     Grid spacing in mm.
        bbox_override: Ignored for VDB path (VDB auto-clips to active region).

    Returns:
        dict with volume_bytes, nx, ny, nz, bbox_min, bbox_max, max_dist, cell_size
    """
    import time
    t0 = time.perf_counter()
    grid = field.to_vdb(voxel_size=cell_size)
    t1 = time.perf_counter()
    result = vdb_grid_to_dense(grid, cell_size)
    t2 = time.perf_counter()

    from core.dm_object import get_perf_profiler_enabled
    if get_perf_profiler_enabled():
        n_active = grid.activeVoxelCount()
        FreeCAD.Console.PrintMessage(
            f"[bake_vdb] grid={result['nx']}x{result['ny']}x{result['nz']} "
            f"active={n_active} cell={cell_size:.1f}mm | "
            f"to_vdb={1000*(t1-t0):.1f}ms  to_dense={1000*(t2-t1):.1f}ms  "
            f"total={1000*(t2-t0):.1f}ms\n"
        )
    return result
