"""
core/sdf/sdf_octree.py

Hierarchical SDF evaluator. Replaces np.meshgrid for CPU meshing/export.
Only evaluates cells near the zero-crossing surface.
"""
import math
import numpy as np
import FreeCAD
from core import dm_logger


class SdfOctreeCache:
    """
    Top-down octree over a single SdfField.

    build() subdivides from the bounding box down to leaf_size,
    culling subtrees where abs(sdf_center) > cell_half_diagonal.
    Leaf cells that straddle the surface store all 8 corner values.

    Attributes:
        field:       the SdfField being cached
        leaf_size:   smallest cell edge length (mm)
        _leaves:     dict keyed by (ix, iy, iz) at the leaf level →
                     np.ndarray shape (8,) of corner SDF values
        _leaf_step:  leaf_size (stored for mesher convenience)
        _origin:     np.ndarray (3,) — bounding box min corner
    """

    def __init__(self, field, leaf_size: float):
        self.field = field
        self.leaf_size = leaf_size
        self._leaves = {}       # (ix, iy, iz) → float32 (8,) corner values
        self._origin = None
        self._leaf_step = leaf_size

    def build(self, progress_callback=None):
        """
        Build the octree. Evaluates field near the surface only.

        progress_callback: optional callable(fraction: float) called periodically
                           with 0.0..1.0 as the build progresses.
        """
        mn, mx = self.field.bounding_box()
        pad = self.leaf_size
        x0 = mn.x - pad;  x1 = mx.x + pad
        y0 = mn.y - pad;  y1 = mx.y + pad
        z0 = mn.z - pad;  z1 = mx.z + pad

        self._origin = np.array([x0, y0, z0], dtype=np.float64)

        # Top-level cell covering the whole bounding box.
        # We start with one cell and subdivide recursively.
        span_x = x1 - x0
        span_y = y1 - y0
        span_z = z1 - z0
        top_size = max(span_x, span_y, span_z)

        # Round top_size up to nearest power-of-two multiple of leaf_size
        # so that every subdivision lands exactly on the leaf grid.
        n = math.ceil(top_size / self.leaf_size)
        n = 1 << math.ceil(math.log2(max(n, 1)))  # next power of 2
        top_size = n * self.leaf_size

        self._leaves.clear()
        processed = [0]

        # Use the authoritative offsets from dm_mesher.py
        _OFF_X = np.array([0,1,1,0, 0,1,1,0], dtype=np.float64)
        _OFF_Y = np.array([0,0,1,1, 0,0,1,1], dtype=np.float64)
        _OFF_Z = np.array([0,0,0,0, 1,1,1,1], dtype=np.float64)
        _CELL_OFFSETS = np.stack([_OFF_X, _OFF_Y, _OFF_Z], axis=1)

        def _subdivide(ox, oy, oz, size):
            """Recursively subdivide the axis-aligned cube at (ox,oy,oz) with edge=size."""
            # Cull: if abs(SDF at center) > half diagonal, no surface here.
            cx, cy, cz = ox + size * 0.5, oy + size * 0.5, oz + size * 0.5
            center_pts = np.array([[cx, cy, cz]], dtype=np.float32)
            d = float(self.field.evaluate_grid(center_pts)[0])
            half_diag = size * math.sqrt(3.0) * 0.5
            if abs(d) > half_diag:
                return  # guaranteed no zero-crossing

            if size <= self.leaf_size * 1.001:
                # Leaf cell — evaluate 8 corners and store
                corners = np.array([ox, oy, oz], dtype=np.float64) + _CELL_OFFSETS * size
                vals = self.field.evaluate_grid(corners.astype(np.float32))
                # Store under leaf grid index
                ix = round((ox - x0) / self.leaf_size)
                iy = round((oy - y0) / self.leaf_size)
                iz = round((oz - z0) / self.leaf_size)
                self._leaves[(ix, iy, iz)] = vals.astype(np.float32)
                processed[0] += 1
                if progress_callback and processed[0] % 1000 == 0:
                    progress_callback(min(processed[0] / max(len(self._leaves) * 2, 1), 0.99))
                return

            # Subdivide into 8 children
            half = size * 0.5
            for dx in (0.0, half):
                for dy in (0.0, half):
                    for dz in (0.0, half):
                        _subdivide(ox + dx, oy + dy, oz + dz, half)

        _subdivide(x0, y0, z0, top_size)
        if progress_callback:
            progress_callback(1.0)
        dm_logger.debug(
            f"SdfOctreeCache.build: {len(self._leaves)} leaf cells "
            f"at leaf_size={self.leaf_size:.3f}mm"
        )

    def query(self, point) -> float:
        """
        Trilinear interpolation of cached SDF at world-space point.
        Returns +inf if the point is in culled (empty) space.

        point: FreeCAD.Vector or (3,) array-like
        """
        if self._origin is None:
            return float('inf')
        x0, y0, z0 = self._origin
        s = self._leaf_step
        px = float(point[0] if hasattr(point, '__getitem__') else point.x)
        py = float(point[1] if hasattr(point, '__getitem__') else point.y)
        pz = float(point[2] if hasattr(point, '__getitem__') else point.z)
        fx = (px - x0) / s
        fy = (py - y0) / s
        fz = (pz - z0) / s
        ix, iy, iz = int(math.floor(fx)), int(math.floor(fy)), int(math.floor(fz))
        if (ix, iy, iz) not in self._leaves:
            return float('inf')
        vals = self._leaves[(ix, iy, iz)]  # (8,) Lorensen-Cline order
        tx, ty, tz = fx - ix, fy - iy, fz - iz
        # Trilinear interpolation using Lorensen-Cline order
        # index (dx,dy,dz): 0:(0,0,0), 1:(1,0,0), 2:(1,1,0), 3:(0,1,0), 4:(0,0,1), 5:(1,0,1), 6:(1,1,1), 7:(0,1,1)
        c00 = vals[0] * (1 - tx) + vals[1] * tx
        c01 = vals[3] * (1 - tx) + vals[2] * tx
        c10 = vals[4] * (1 - tx) + vals[5] * tx
        c11 = vals[7] * (1 - tx) + vals[6] * tx
        c0  = c00 * (1 - ty) + c01 * ty
        c1  = c10 * (1 - ty) + c11 * ty
        return float(c0 * (1 - tz) + c1 * tz)

    def walk_leaves(self):
        """
        Iterate over surface-crossing leaf cells.

        Yields tuples of:
            origin  — np.ndarray (3,) world-space position of the (0,0,0) corner
            size    — float, leaf cell edge length (== self.leaf_size)
            corners — np.ndarray (8,) of SDF corner values in Lorensen-Cline order
        """
        if self._origin is None:
            return
        x0, y0, z0 = self._origin
        s = self._leaf_step
        for (ix, iy, iz), corners in self._leaves.items():
            # Only yield cells that straddle the surface
            if corners.min() < 0.0 and corners.max() >= 0.0:
                origin = np.array([x0 + ix * s, y0 + iy * s, z0 + iz * s],
                                  dtype=np.float64)
                yield origin, s, corners

    def narrow_band_points(self, band_width: float = None):
        """
        Return (N,3) float64 array of all cached corner points within band_width of surface.

        band_width: max abs(SDF) to include. Defaults to 2 * leaf_size.
        """
        if self._origin is None:
            return np.empty((0, 3), dtype=np.float64)
        if band_width is None:
            band_width = self._leaf_step * 2.0
        x0, y0, z0 = self._origin
        s = self._leaf_step
        pts_list = []
        seen = set()
        # Authoritative offsets
        _OFF_X = np.array([0,1,1,0, 0,1,1,0], dtype=np.float64)
        _OFF_Y = np.array([0,0,1,1, 0,0,1,1], dtype=np.float64)
        _OFF_Z = np.array([0,0,0,0, 1,1,1,1], dtype=np.float64)
        offsets = np.stack([_OFF_X, _OFF_Y, _OFF_Z], axis=1) * s
        
        for (ix, iy, iz), corners in self._leaves.items():
            base = np.array([x0 + ix * s, y0 + iy * s, z0 + iz * s])
            for ci, (val, off) in enumerate(zip(corners, offsets)):
                if abs(val) <= band_width:
                    key = (ix + int(off[0]/s+0.5), iy + int(off[1]/s+0.5), iz + int(off[2]/s+0.5))
                    if key not in seen:
                        seen.add(key)
                        pts_list.append(base + off)
        if not pts_list:
            return np.empty((0, 3), dtype=np.float64)
        return np.array(pts_list, dtype=np.float64)


def make_freecad_progress_callback(label: str = "Building SDF octree..."):
    """
    Returns a progress_callback(fraction) that updates a FreeCAD progress bar.
    Call the returned function with 1.0 to close the bar.
    """
    try:
        import FreeCADGui
        seq = FreeCAD.Base.ProgressIndicator()
        seq.start(label, 100)
        started = [True]
        last_val = [0]
        def callback(fraction):
            if not started[0]: return
            val = int(fraction * 100)
            if val > last_val[0]:
                for _ in range(val - last_val[0]):
                    seq.next(True)
                last_val[0] = val
            if fraction >= 1.0:
                seq.stop()
                started[0] = False
        return callback
    except Exception:
        return lambda f: None  # no-op if FreeCAD GUI not available
