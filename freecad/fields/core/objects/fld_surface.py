# SPDX-License-Identifier: CC-BY-NC-SA-4.0
from freecad.fields.core import fld_logger


def _grid_to_preview_shape(grid):
    """Flatten a 2D grid of points into a quad-split triangle preview Part.Shape."""
    import FreeCAD
    import numpy as np
    res_v = len(grid)
    res_u = len(grid[0])
    verts_list = []
    for j in range(res_v):
        for i in range(res_u):
            p = grid[j][i]
            v = p if isinstance(p, FreeCAD.Vector) else FreeCAD.Vector(*p)
            verts_list.append([v.x, v.y, v.z])
    verts_arr = np.array(verts_list, dtype=np.float32)

    tris_idx = []
    for j in range(res_v - 1):
        for i in range(res_u - 1):
            idx00 = j * res_u + i
            idx01 = j * res_u + (i + 1)
            idx10 = (j + 1) * res_u + i
            idx11 = (j + 1) * res_u + (i + 1)
            tris_idx.append([idx00, idx10, idx01, -1])
            tris_idx.append([idx01, idx10, idx11, -1])

    idx_arr = np.array(tris_idx, dtype=np.int32)
    from freecad.fields.core.objects.fld_curve_fill_geometry import _triangles_to_preview_shape
    return _triangles_to_preview_shape(verts_arr, idx_arr)


def _compute_ring_point(B_i, T_i, centroid, normal, R_outer, k, extra_v, fraction, blend_factor):
    """One point of the extension ring: tangent extrapolation blended with a
    linear planar transition toward the outer radius."""
    T_len = T_i.Length
    if T_len < 1e-4:
        T_i = B_i - centroid
        T_len = T_i.Length

    dir_i = B_i - centroid
    dir_plane = dir_i - normal * dir_i.dot(normal)
    if dir_plane.Length > 1e-4:
        dir_plane.normalize()
    else:
        dir_plane = T_i.normalize()

    outer_pt = centroid + dir_plane * R_outer

    # Tangent extrapolation
    P_tangent = B_i + T_i * (k * (R_outer - dir_i.Length) / (extra_v * T_len))
    # Linear planar transition
    P_linear = B_i * (1.0 - fraction) + outer_pt * fraction

    # Blend
    return P_tangent * (1.0 - blend_factor) + P_linear * blend_factor


class FldSurface:
    """NURBS surface built from a grid of control points or 4 boundary curves."""

    def __init__(self, grid):
        # grid: list of rows, each row a list of FreeCAD.Vector
        self.grid = grid

    def to_shape(self):
        import Part
        if not self.grid or not self.grid[0]:
            return Part.Shape()
        try:
            return _grid_to_preview_shape(self.grid)
        except Exception as e:
            fld_logger.debug(f"FldSurface to_shape failed: {e}")
            return Part.Shape()

    def to_extended_shape(self, extra_u=8, extra_v=8, is_u_periodic=False, R_outer=800.0):
        import Part
        import FreeCAD
        if not self.grid or not self.grid[0]:
            return Part.Shape()

        v_res = len(self.grid)
        u_res = len(self.grid[0])

        # Collect boundary points to find centroid and normal
        boundary_pts = []
        if is_u_periodic:
            boundary_pts = [self.grid[-1][i] for i in range(u_res - 1)] if u_res > 1 else list(self.grid[-1])
        else:
            boundary_pts.extend(self.grid[0])
            boundary_pts.extend(self.grid[-1])
            boundary_pts.extend([row[0] for row in self.grid])
            boundary_pts.extend([row[-1] for row in self.grid])
            
            # Deduplicate points keeping order
            seen = set()
            dedup = []
            for p in boundary_pts:
                k = (round(p.x, 3), round(p.y, 3), round(p.z, 3))
                if k not in seen:
                    seen.add(k)
                    dedup.append(p)
            boundary_pts = dedup

        if not boundary_pts:
            return Part.Shape()

        centroid = FreeCAD.Vector(0.0, 0.0, 0.0)
        for p in boundary_pts:
            centroid += p
        centroid /= len(boundary_pts)

        normal = FreeCAD.Vector(0.0, 0.0, 0.0)
        for i in range(len(boundary_pts)):
            v1 = boundary_pts[i] - centroid
            v2 = boundary_pts[(i + 1) % len(boundary_pts)] - centroid
            normal += v1.cross(v2)
        if normal.Length > 1e-4:
            normal.normalize()
        else:
            normal = FreeCAD.Vector(0.0, 0.0, 1.0)

        # For Disk Laplacian (periodic):
        if is_u_periodic:
            # We construct a periodic ring: inner edge is self.grid[-1], outer edge is a circle at R_outer
            ring_grid = []
            ring_grid.append(list(self.grid[-1]))
            
            num_cols = len(self.grid[-1])
            for k in range(1, extra_v + 1):
                fraction = k / extra_v
                blend_factor = fraction * fraction # quadratic blend to plane
                row = []
                for i in range(num_cols):
                    B_i = self.grid[-1][i]
                    T_i = self.grid[-1][i] - self.grid[-2][i]
                    row.append(_compute_ring_point(B_i, T_i, centroid, normal, R_outer, k, extra_v, fraction, blend_factor))
                ring_grid.append(row)

            try:
                return _grid_to_preview_shape(ring_grid)
            except Exception as e:
                fld_logger.warn(f"FldSurface: failed to construct periodic extension mesh: {e}")
                return Part.Shape()
        else:
            # For Coons Patch (non-periodic):
            # We build a periodic ring extension around the outer rectangular loop
            boundary_loop = []
            # Top edge
            for i in range(u_res):
                boundary_loop.append((self.grid[0][i], self.grid[0][i] - self.grid[1][i]))
            # Right edge
            for j in range(1, v_res):
                boundary_loop.append((self.grid[j][-1], self.grid[j][-1] - self.grid[j][-2]))
            # Bottom edge (reversed)
            for i in range(u_res - 2, -1, -1):
                boundary_loop.append((self.grid[-1][i], self.grid[-1][i] - self.grid[-2][i]))
            # Left edge (reversed)
            for j in range(v_res - 2, 0, -1):
                boundary_loop.append((self.grid[j][0], self.grid[j][0] - self.grid[j][1]))
                
            # Close the loop
            boundary_loop.append(boundary_loop[0])
            
            ring_grid = []
            ring_grid.append([item[0] for item in boundary_loop])
            
            num_cols = len(boundary_loop)
            for k in range(1, extra_v + 1):
                fraction = k / extra_v
                blend_factor = fraction * fraction
                row = []
                for i in range(num_cols):
                    B_i, T_i = boundary_loop[i]
                    row.append(_compute_ring_point(B_i, T_i, centroid, normal, R_outer, k, extra_v, fraction, blend_factor))
                ring_grid.append(row)

            try:
                return _grid_to_preview_shape(ring_grid)
            except Exception as e:
                fld_logger.warn(f"FldSurface: failed to construct non-periodic extension mesh: {e}")
                return Part.Shape()
