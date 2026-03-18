"""
core/frep/sdf_baker.py

Bakes any SdfField to a 3D float32 volume for GPU ray marching.
Uses field.evaluate_grid() and field.bounding_box() — no primitives.
"""
import math
import numpy as np


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

    xs = np.linspace(x0, x0 + nx * cell_size, nx + 1)
    ys = np.linspace(y0, y0 + ny * cell_size, ny + 1)
    zs = np.linspace(z0, z0 + nz * cell_size, nz + 1)

    X, Y, Z = np.meshgrid(xs, ys, zs, indexing='ij')
    pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]).astype(np.float32)
    vals = field.evaluate_grid(pts).astype(np.float32)

    # Reshape to (nx+1, ny+1, nz+1), then transpose to (nz+1, ny+1, nx+1)
    # for row-major upload where x varies fastest (OpenGL convention).
    vol = vals.reshape(nx + 1, ny + 1, nz + 1).transpose(2, 1, 0)

    # Reinterpret float32 as 4×uint8 (RGBA)
    volume_bytes = vol.astype(np.float32).tobytes()

    import FreeCAD
    return {
        "volume_bytes": volume_bytes,
        "nx": nx, "ny": ny, "nz": nz,
        "bbox_min": FreeCAD.Vector(x0, y0, z0),
        "bbox_max": FreeCAD.Vector(x0 + nx * cell_size,
                                    y0 + ny * cell_size,
                                    z0 + nz * cell_size),
        "max_dist": cell_size * 8.0,
    }
