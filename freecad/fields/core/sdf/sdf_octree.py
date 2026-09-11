# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/sdf/sdf_octree.py

Hierarchical SDF evaluator. Replaces np.meshgrid for CPU meshing/export.
Only evaluates cells near the zero-crossing surface.
"""
import FreeCAD
from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf_constants import CELL_OFFSETS as _CELL_OFFSETS
import math
import numpy as np


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
        self._leaves_coords = None
        self._leaves_corners = None

    def build(self, bounds=None, progress_callback=None):
        """
        Build the octree. Evaluates field near the surface only.

        Cells are processed breadth-first, one tree level at a time, so every
        level does a single batched `evaluate_grid()` call across all surviving
        cells instead of one Python call per cell. For composed fields (booleans,
        multi-primitive trees) the per-call overhead of evaluating a single point
        dwarfs the actual math, so batching is the dominant lever here.

        progress_callback: optional callable(fraction: float) called periodically
                           with 0.0..1.0 as the build progresses.
        """
        # Deferred like the eval_grid import below it: field_eval pulls in
        # fld_object and the GPU evaluator, which must not load at import time.
        import time
        from freecad.fields.core.sdf import field_eval

        if bounds is not None:
            mn, mx = bounds
        else:
            mn, mx = self.field.bounding_box()
        pad = self.leaf_size
        x0 = mn.x - pad;  x1 = mx.x + pad
        y0 = mn.y - pad;  y1 = mx.y + pad
        z0 = mn.z - pad;  z1 = mx.z + pad

        self._origin = np.array([x0, y0, z0], dtype=np.float64)

        # Top-level cell covering the whole bounding box.
        span_x = x1 - x0
        span_y = y1 - y0
        span_z = z1 - z0
        top_size = max(span_x, span_y, span_z)

        # Round top_size up to nearest power-of-two multiple of leaf_size
        # so that every subdivision lands exactly on the leaf grid.
        n = math.ceil(top_size / self.leaf_size)
        n = 1 << math.ceil(math.log2(max(n, 1)))  # next power of 2
        top_size = n * self.leaf_size
        total_levels = int(round(math.log2(n))) + 1

        self._leaves.clear()

        # Attribute the build's cost: how much of it is the field's own
        # evaluation, and how much is this loop. A stacked-extrusion field can
        # turn a 2 mm build into seconds, and the leaf count alone does not say
        # whether that came from more cells or a costlier field.
        t_build_start = time.perf_counter()
        _eval_before = field_eval.eval_stats_snapshot()

        # Frontier: (K, 3) origins of cells still awaiting the cull test, all the
        # same size. Starts with the single top-level cell.
        frontier = np.array([[x0, y0, z0]], dtype=np.float64)
        size = top_size
        level = 0

        L = self.field.lipschitz()
        while frontier.shape[0] > 0:
            centers = frontier + size * 0.5
            from freecad.fields.core.sdf.field_eval import eval_grid
            d = eval_grid(self.field, centers.astype(np.float32))
            half_diag = size * math.sqrt(3.0) * 0.5
            keep_mask = np.abs(d) <= half_diag * L
            kept = frontier[keep_mask]

            if kept.shape[0] == 0:
                break

            if size <= self.leaf_size * 1.001:
                # Leaf level — deduplicate corner coordinates to avoid redundant SDF evaluations
                K = kept.shape[0]
                
                ix = np.round((kept[:, 0] - x0) / self.leaf_size).astype(np.int64)
                iy = np.round((kept[:, 1] - y0) / self.leaf_size).astype(np.int64)
                iz = np.round((kept[:, 2] - z0) / self.leaf_size).astype(np.int64)
                
                cell_coords = np.stack([ix, iy, iz], axis=1) # (K, 3)
                
                # Expand to 8 corners for each cell
                corner_grid_coords = cell_coords[:, None, :] + _CELL_OFFSETS[None, :, :].astype(np.int64) # (K, 8, 3)
                flat_grid_coords = corner_grid_coords.reshape(-1, 3) # (8*K, 3)
                
                # Find unique corners
                unique_grid_coords, inverse_indices = np.unique(flat_grid_coords, axis=0, return_inverse=True)
                
                # Convert unique grid coords back to world space points and evaluate in a single batch
                unique_world_pts = self._origin[None, :] + unique_grid_coords * self.leaf_size
                from freecad.fields.core.sdf.field_eval import eval_grid
                unique_vals = eval_grid(
                    self.field, unique_world_pts.astype(np.float32)
                ).astype(np.float32)
                
                # Reconstruct corner values back into (K, 8) shape
                vals = unique_vals[inverse_indices].reshape(K, 8)
                
                # Cache as NumPy arrays for fast vectorized meshing
                self._leaves_coords = cell_coords
                self._leaves_corners = vals
                
                for k in range(K):
                    self._leaves[(int(ix[k]), int(iy[k]), int(iz[k]))] = vals[k]
                break

            # Subdivide every surviving cell into 8 children for the next level.
            half = size * 0.5
            children = kept[:, None, :] + _CELL_OFFSETS[None, :, :] * half  # (K, 8, 3)
            frontier = children.reshape(-1, 3)
            size = half
            level += 1
            if progress_callback:
                progress_callback(min(level / max(total_levels, 1), 0.99))

        if progress_callback:
            progress_callback(1.0)
        t_total = time.perf_counter() - t_build_start
        ev = field_eval.eval_stats_delta(_eval_before)
        fld_logger.debug(
            f"SdfOctreeCache.build: {len(self._leaves)} leaf cells "
            f"at leaf_size={self.leaf_size:.3f}mm — {t_total*1000:.1f}ms "
            f"({type(self.field).__name__}, {level + 1} level(s), "
            f"{ev['points']} pts in {ev['seconds']*1000:.1f}ms, "
            f"overhead {(t_total - ev['seconds'])*1000:.1f}ms)"
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

    def get_active_leaves(self):
        """
        Returns (origins, sizes, corners) of active leaf cells as numpy arrays.
        Bypasses dictionary and generator lookup.
        """
        if self._origin is None or self._leaves_coords is None:
            return (
                np.empty((0, 3), dtype=np.float64),
                np.empty(0, dtype=np.float64),
                np.empty((0, 8), dtype=np.float64)
            )
        corners = self._leaves_corners
        active_mask = (corners.min(axis=1) < 0.0) & (corners.max(axis=1) >= 0.0)
        active_coords = self._leaves_coords[active_mask]
        active_corners = corners[active_mask]
        origins = self._origin[None, :] + active_coords.astype(np.float64) * self.leaf_size
        sizes = np.full(len(active_coords), self.leaf_size, dtype=np.float64)
        return origins, sizes, active_corners

    def update_region(self, dirty_aabb):
        """
        Incrementally rebuilds the octree within the dirty_aabb bounds.
        """
        if self._origin is None:
            self.build()
            return

        x0, y0, z0 = self._origin
        pad = self.leaf_size
        
        # Padded dirty bounds
        d_min = np.array([dirty_aabb[0].x - pad, dirty_aabb[0].y - pad, dirty_aabb[0].z - pad])
        d_max = np.array([dirty_aabb[1].x + pad, dirty_aabb[1].y + pad, dirty_aabb[1].z + pad])
        
        # 1. Clear existing leaves inside the dirty region
        ix_min = int(math.floor((d_min[0] - x0) / self.leaf_size))
        ix_max = int(math.ceil((d_max[0] - x0) / self.leaf_size))
        iy_min = int(math.floor((d_min[1] - y0) / self.leaf_size))
        iy_max = int(math.ceil((d_max[1] - y0) / self.leaf_size))
        iz_min = int(math.floor((d_min[2] - z0) / self.leaf_size))
        iz_max = int(math.ceil((d_max[2] - z0) / self.leaf_size))
        
        keys_to_remove = []
        for key in self._leaves.keys():
            ix, iy, iz = key
            if ix_min <= ix <= ix_max and iy_min <= iy <= iy_max and iz_min <= iz <= iz_max:
                keys_to_remove.append(key)
        for key in keys_to_remove:
            del self._leaves[key]
            
        # 2. Run top-down subdivision starting from the top-level cell,
        # but restricted to cells that intersect the padded dirty region.
        mn, mx = self.field.bounding_box()
        span_x = (mx.x + pad) - (mn.x - pad)
        span_y = (mx.y + pad) - (mn.y - pad)
        span_z = (mx.z + pad) - (mn.z - pad)
        top_size = max(span_x, span_y, span_z)
        n = math.ceil(top_size / self.leaf_size)
        n = 1 << math.ceil(math.log2(max(n, 1)))  # next power of 2
        top_size = n * self.leaf_size
        
        frontier = np.array([[x0, y0, z0]], dtype=np.float64)
        size = top_size
        L = self.field.lipschitz()
        
        while frontier.shape[0] > 0:
            # Filter frontier: keep only cells that intersect [d_min, d_max]
            c_min = frontier
            c_max = frontier + size
            intersect = np.all(c_min <= d_max[None, :], axis=-1) & np.all(c_max >= d_min[None, :], axis=-1)
            frontier = frontier[intersect]
            if frontier.shape[0] == 0:
                break
                
            centers = frontier + size * 0.5
            from freecad.fields.core.sdf.field_eval import eval_grid
            d = eval_grid(self.field, centers.astype(np.float32))
            half_diag = size * math.sqrt(3.0) * 0.5
            keep_mask = np.abs(d) <= half_diag * L
            kept = frontier[keep_mask]
            
            if kept.shape[0] == 0:
                break
                
            if size <= self.leaf_size * 1.001:
                # Leaf level — evaluate corners
                K = kept.shape[0]
                ix = np.round((kept[:, 0] - x0) / self.leaf_size).astype(np.int64)
                iy = np.round((kept[:, 1] - y0) / self.leaf_size).astype(np.int64)
                iz = np.round((kept[:, 2] - z0) / self.leaf_size).astype(np.int64)
                
                cell_coords = np.stack([ix, iy, iz], axis=1) # (K, 3)
                corner_grid_coords = cell_coords[:, None, :] + _CELL_OFFSETS[None, :, :].astype(np.int64)
                flat_grid_coords = corner_grid_coords.reshape(-1, 3)
                
                unique_grid_coords, inverse_indices = np.unique(flat_grid_coords, axis=0, return_inverse=True)
                unique_world_pts = self._origin[None, :] + unique_grid_coords * self.leaf_size
                
                unique_vals = eval_grid(
                    self.field, unique_world_pts.astype(np.float32)
                ).astype(np.float32)
                
                vals = unique_vals[inverse_indices].reshape(K, 8)
                
                for k in range(K):
                    self._leaves[(int(ix[k]), int(iy[k]), int(iz[k]))] = vals[k]
                break
                
            half = size * 0.5
            children = kept[:, None, :] + _CELL_OFFSETS[None, :, :] * half
            frontier = children.reshape(-1, 3)
            size = half
            
        # 3. Rebuild self._leaves_coords and self._leaves_corners
        if self._leaves:
            coords = []
            corners = []
            for (ix, iy, iz), vals in self._leaves.items():
                coords.append([ix, iy, iz])
                corners.append(vals)
            self._leaves_coords = np.array(coords, dtype=np.int64)
            self._leaves_corners = np.array(corners, dtype=np.float32)
        else:
            self._leaves_coords = None
            self._leaves_corners = None


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
    except Exception as e:
        fld_logger.debug(f"make_freecad_progress_callback fallback: {e}")
        return lambda f: None  # no-op if FreeCAD GUI not available
