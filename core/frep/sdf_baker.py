"""
core/frep/sdf_baker.py

Bakes any FRepField to a 2D luminance atlas for GPU ray marching.
Only uses field.evaluate_grid() and field.bounding_box() — no primitives.
"""
import math
import numpy as np


def bake_sdf_to_atlas(field, cell_size: float) -> dict:
    """
    Sample the SDF on a uniform grid and pack it as a 2D uint8 atlas.

    Args:
        field:     Any FRepField subclass.
        cell_size: Grid spacing in mm. Controls resolution and clamp range.

    Returns dict with keys:
        atlas_bytes : bytes        — uint8 luminance pixels, row-major
        atlas_w     : int          — atlas pixel width  (= atz * (nx+1))
        atlas_h     : int          — atlas pixel height (= aty * (ny+1))
        nx, ny, nz  : int          — grid cell counts
        atz         : int          — atlas tile columns
        bbox_min    : FreeCAD.Vector
        bbox_max    : FreeCAD.Vector
        max_dist    : float        — SDF clamp range in mm (= 4 * cell_size)
    """
    mn, mx = field.bounding_box()

    # Identical to dm_mesher.py:117–143
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
    vals = field.evaluate_grid(pts).reshape(nx + 1, ny + 1, nz + 1)  # (nx+1,ny+1,nz+1)

    # Clamp and normalise to [0, 65535] (uint16 for smooth gradients)
    max_dist = cell_size * 8.0
    clamped  = np.clip(vals, -max_dist, max_dist)
    norm     = ((clamped / max_dist + 1.0) * 0.5 * 65535.0).astype(np.uint16)

    # Tile z-slices into 2D atlas
    nslices = nz + 1
    atz = max(1, int(math.ceil(math.sqrt(nslices))))
    aty = int(math.ceil(nslices / atz))

    atlas_w = atz * (nx + 1)
    atlas_h = aty * (ny + 1)
    atlas   = np.zeros((atlas_h, atlas_w), dtype=np.uint16)

    for iz in range(nslices):
        col = iz % atz
        row = iz // atz
        x_off = col * (nx + 1)
        y_off = row * (ny + 1)
        # norm is (nx+1, ny+1, nz+1), indexing='ij' → axis 0=x, 1=y, 2=z
        # Atlas rows = y axis, cols = x axis
        slice_xy = norm[:, :, iz]          # shape (nx+1, ny+1)
        atlas[y_off:y_off + ny + 1, x_off:x_off + nx + 1] = slice_xy.T  # transpose: row=y, col=x

    # Pack uint16 as two-channel uint8 (high byte, low byte) for GL_LUMINANCE_ALPHA
    high = (atlas >> 8).astype(np.uint8)
    low  = (atlas & 0xFF).astype(np.uint8)
    # Interleave: [h0, l0, h1, l1, ...] for LUMINANCE_ALPHA format
    packed = np.empty((atlas_h, atlas_w, 2), dtype=np.uint8)
    packed[:, :, 0] = high
    packed[:, :, 1] = low

    import FreeCAD
    return {
        "atlas_bytes": packed.tobytes(),
        "atlas_w":     atlas_w,
        "atlas_h":     atlas_h,
        "nx": nx, "ny": ny, "nz": nz,
        "atz":         atz,
        "bbox_min":    FreeCAD.Vector(x0, y0, z0),
        "bbox_max":    FreeCAD.Vector(x0 + nx * cell_size,
                                      y0 + ny * cell_size,
                                      z0 + nz * cell_size),
        "max_dist":    max_dist,
    }
