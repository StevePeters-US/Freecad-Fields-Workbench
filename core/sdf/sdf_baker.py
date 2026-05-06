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

    xs = np.linspace(x0, x0 + nx * cell_size, nx + 1)
    ys = np.linspace(y0, y0 + ny * cell_size, ny + 1)
    zs = np.linspace(z0, z0 + nz * cell_size, nz + 1)

    X, Y, Z = np.meshgrid(xs, ys, zs, indexing='ij')
    pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]).astype(np.float32)

    t1 = time.perf_counter()
    vals = field.evaluate_grid(pts).astype(np.float32)
    t2 = time.perf_counter()

    # Reshape to (nx+1, ny+1, nz+1), transpose to (nz+1, ny+1, nx+1) for OpenGL row-major.
    vol = vals.reshape(nx + 1, ny + 1, nz + 1).transpose(2, 1, 0)
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

