# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.sdf.sdf_field import SdfField, placement_matrix
import numpy as np


class SdfLatticeField(SdfField):
    """Free-form lattice deformation wrapping a source SDF field.

    A regular NxNxN grid of control points is fitted to the source bounding box.
    Each control point can be displaced to warp the enclosed geometry via trilinear
    interpolation. CPU evaluate() and GPU to_glsl() are stubs.
    """

    def __init__(self, source: SdfField, resolution: int = 2,
                 displacements: np.ndarray = None, descriptor: dict = None):
        """
        Args:
            source: wrapped SDF field.
            resolution: number of control points along each axis (minimum 2).
            displacements: (resolution**3, 3) float32 array of per-point offsets.
                           None → all zeros (identity deformation).
            descriptor: optional dictionary containing origin, extent, and placement
                        defining the undeformed lattice.
        """
        super().__init__()
        self.source = source
        self.resolution = max(2, resolution)
        
        # Determine origin, extent, placement of the undeformed lattice
        if descriptor is not None:
            self.origin = descriptor.get('origin', FreeCAD.Vector(0.0, 0.0, 0.0))
            self.extent = descriptor.get('extent', FreeCAD.Vector(1.0, 1.0, 1.0))
            self.placement = descriptor.get('placement', None)
        else:
            try:
                bbox = source.bounding_box()
                min_c, max_c = bbox[0], bbox[1]
                self.origin = min_c
                self.extent = max_c - min_c
            except Exception:
                self.origin = FreeCAD.Vector(-50.0, -50.0, -50.0)
                self.extent = FreeCAD.Vector(100.0, 100.0, 100.0)
            self.placement = None
            
        self.origin_np = np.array([self.origin.x, self.origin.y, self.origin.z], dtype=np.float32)
        self.extent_np = np.array([self.extent.x, self.extent.y, self.extent.z], dtype=np.float32)
        self.inv_matrix = self._compute_inv_matrix(self.placement)

        # Precompute undeformed lattice grid control point positions in local space
        R = self.resolution
        grid_flat = np.zeros((R**3, 3), dtype=np.float32)
        for k in range(R):
            for j in range(R):
                for i in range(R):
                    idx = i + j * R + k * R**2
                    u = i / (R - 1) if R > 1 else 0.0
                    v = j / (R - 1) if R > 1 else 0.0
                    w = k / (R - 1) if R > 1 else 0.0
                    grid_flat[idx] = self.origin_np + np.array([u, v, w], dtype=np.float32) * self.extent_np
        self.grid_local = grid_flat

        n = R ** 3
        if displacements is None:
            self._displacements = np.zeros((n, 3), dtype=np.float32)
        else:
            self._displacements = np.asarray(displacements, dtype=np.float32).reshape(n, 3)
        self.deformed_grid = self.grid_local + self._displacements

    @property
    def displacements(self):
        return self._displacements

    @displacements.setter
    def displacements(self, value):
        self._displacements = np.asarray(value, dtype=np.float32)
        self.deformed_grid = self.grid_local + self._displacements
        self.invalidate_cache()

    # ------------------------------------------------------------------
    # SdfField interface
    # ------------------------------------------------------------------

    def bounding_box(self):
        return self.source.bounding_box()

    def lipschitz(self) -> float:
        if getattr(self, "_g_max", None) is None:
            self._g_max = self._compute_g_max()
        return self.source.lipschitz() * (1.0 + self._g_max)

    def _compute_g_max(self):
        try:
            bbox = self.source.bounding_box()
            min_c, max_c = bbox[0], bbox[1]
            sz = np.array([max_c.x - min_c.x, max_c.y - min_c.y, max_c.z - min_c.z], dtype=np.float32)
        except Exception:
            sz = np.array([100.0, 100.0, 100.0], dtype=np.float32)
            
        import math
        R = self.resolution
        h = sz / max(1.0, R - 1)
        h = np.maximum(h, 1e-6)
        
        disp = self.displacements.reshape((R, R, R, 3))
        dx = np.diff(disp, axis=0) / h[0] if R > 1 else np.zeros((0, R, R, 3))
        dy = np.diff(disp, axis=1) / h[1] if R > 1 else np.zeros((R, 0, R, 3))
        dz = np.diff(disp, axis=2) / h[2] if R > 1 else np.zeros((R, R, 0, 3))
        
        norm_x = np.linalg.norm(dx, axis=-1)
        norm_y = np.linalg.norm(dy, axis=-1)
        norm_z = np.linalg.norm(dz, axis=-1)
        
        g_max = math.sqrt(
            (np.max(norm_x)**2 if norm_x.size > 0 else 0.0) +
            (np.max(norm_y)**2 if norm_y.size > 0 else 0.0) +
            (np.max(norm_z)**2 if norm_z.size > 0 else 0.0)
        )
        return g_max

    def evaluate(self, point: FreeCAD.Vector) -> float:
        pts = np.array([[point.x, point.y, point.z]], dtype=np.float32)
        return float(self.evaluate_grid(pts)[0])

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        if len(points) == 0:
            return np.zeros((0,), dtype=np.float32)
        
        R = self.resolution
        deformed_grid = self.deformed_grid
        
        # Transform points to local coordinates
        points_local = self._to_local_grid(points)
        
        # Map query points into normalized lattice coordinates [0, 1]^3
        extent_safe = np.maximum(self.extent_np, 1e-6)
        u_init = (points_local[:, 0] - self.origin_np[0]) / extent_safe[0]
        v_init = (points_local[:, 1] - self.origin_np[1]) / extent_safe[1]
        w_init = (points_local[:, 2] - self.origin_np[2]) / extent_safe[2]
        
        # Run Newton-Raphson solver to invert the forward trilinear map
        u = np.clip(u_init, 0.0, 1.0)
        v = np.clip(v_init, 0.0, 1.0)
        w = np.clip(w_init, 0.0, 1.0)
        
        for _step in range(5):
            cell_u = np.clip(np.floor(u * (R - 1)).astype(np.int32), 0, R - 2)
            cell_v = np.clip(np.floor(v * (R - 1)).astype(np.int32), 0, R - 2)
            cell_w = np.clip(np.floor(w * (R - 1)).astype(np.int32), 0, R - 2)
            
            # Local coordinates within the cell [0, 1]^3
            uc = u * (R - 1) - cell_u
            vc = v * (R - 1) - cell_v
            wc = w * (R - 1) - cell_w
            
            # Indices of the 8 cell corners
            idx000 = cell_u + cell_v * R + cell_w * R**2
            idx100 = (cell_u + 1) + cell_v * R + cell_w * R**2
            idx010 = cell_u + (cell_v + 1) * R + cell_w * R**2
            idx110 = (cell_u + 1) + (cell_v + 1) * R + cell_w * R**2
            idx001 = cell_u + cell_v * R + (cell_w + 1) * R**2
            idx101 = (cell_u + 1) + cell_v * R + (cell_w + 1) * R**2
            idx011 = cell_u + (cell_v + 1) * R + (cell_w + 1) * R**2
            idx111 = (cell_u + 1) + (cell_v + 1) * R + (cell_w + 1) * R**2
            
            P000 = deformed_grid[idx000]
            P100 = deformed_grid[idx100]
            P010 = deformed_grid[idx010]
            P110 = deformed_grid[idx110]
            P001 = deformed_grid[idx001]
            P101 = deformed_grid[idx101]
            P011 = deformed_grid[idx011]
            P111 = deformed_grid[idx111]
            
            uc_col = uc[:, None]
            vc_col = vc[:, None]
            wc_col = wc[:, None]
            
            w000 = (1.0 - uc_col) * (1.0 - vc_col) * (1.0 - wc_col)
            w100 = uc_col * (1.0 - vc_col) * (1.0 - wc_col)
            w010 = (1.0 - uc_col) * vc_col * (1.0 - wc_col)
            w110 = uc_col * vc_col * (1.0 - wc_col)
            w001 = (1.0 - uc_col) * (1.0 - vc_col) * wc_col
            w101 = uc_col * (1.0 - vc_col) * wc_col
            w011 = (1.0 - uc_col) * vc_col * wc_col
            w111 = uc_col * vc_col * wc_col
            
            # Forward trilinear map evaluation
            f_val = (w000 * P000 + w100 * P100 + w010 * P010 + w110 * P110 +
                     w001 * P001 + w101 * P101 + w011 * P011 + w111 * P111)
            
            err = f_val - points_local
            
            # Analytical derivatives w.r.t cell coordinates
            df_duc = ((1.0 - vc_col) * (1.0 - wc_col) * (P100 - P000) +
                      vc_col * (1.0 - wc_col) * (P110 - P010) +
                      (1.0 - vc_col) * wc_col * (P101 - P001) +
                      vc_col * wc_col * (P111 - P011))
                      
            df_dvc = ((1.0 - uc_col) * (1.0 - wc_col) * (P010 - P000) +
                      uc_col * (1.0 - wc_col) * (P110 - P100) +
                      (1.0 - uc_col) * wc_col * (P011 - P001) +
                      uc_col * wc_col * (P111 - P101))
                      
            df_dwc = ((1.0 - uc_col) * (1.0 - vc_col) * (P001 - P000) +
                      uc_col * (1.0 - vc_col) * (P101 - P100) +
                      (1.0 - uc_col) * vc_col * (P011 - P010) +
                      uc_col * vc_col * (P111 - P110))
                      
            J_u = df_duc * (R - 1)
            J_v = df_dvc * (R - 1)
            J_w = df_dwc * (R - 1)
            
            # Jacobian determinant J_u . (J_v x J_w)
            det = np.sum(J_u * np.cross(J_v, J_w), axis=1)
            det = np.where(np.abs(det) < 1e-9, np.sign(det) * 1e-9, det)
            det = np.where(det == 0.0, 1e-9, det)
            
            # Analytical 3x3 matrix inverse columns
            inv_row_u = np.cross(J_v, J_w) / det[:, None]
            inv_row_v = np.cross(J_w, J_u) / det[:, None]
            inv_row_w = np.cross(J_u, J_v) / det[:, None]
            
            # Solved delta updates
            delta_u = np.sum(inv_row_u * err, axis=1)
            delta_v = np.sum(inv_row_v * err, axis=1)
            delta_w = np.sum(inv_row_w * err, axis=1)
            
            u = np.clip(u - delta_u, 0.0, 1.0)
            v = np.clip(v - delta_v, 0.0, 1.0)
            w = np.clip(w - delta_w, 0.0, 1.0)
            
        err_norm = np.linalg.norm(err, axis=1)
        threshold = 1e-2 * np.linalg.norm(extent_safe)
        inside_mask = (err_norm < threshold)

        p0_inside = self.origin_np + np.column_stack([u, v, w]) * self.extent_np
        p0_local = np.where(inside_mask[:, None], p0_inside, points_local)
        
        # Transform back from local to world space if needed
        if self.placement is not None:
            pts_hom = np.hstack((p0_local, np.ones((len(points), 1), dtype=np.float32)))
            mat_np = placement_matrix(self.placement, dtype=np.float32)
            p0 = (pts_hom @ mat_np.T)[:, :3]
        else:
            p0 = p0_local
            
        return self.source.evaluate_grid(p0).astype(np.float32)

    def to_glsl(self, ctx, point_var="p"):
        R = self.resolution
        
        # Format control points list
        ctrl_flat = []
        for i in range(R**3):
            pt_local = self.deformed_grid[i]
            ctrl_flat.extend([float(pt_local[0]), float(pt_local[1]), float(pt_local[2])])
            
        # Register uniforms
        ctrl_pts_u = ctx.uniform(f"vec3[{R**3}]", ctrl_flat)
        origin_u = ctx.uniform("vec3", [self.origin.x, self.origin.y, self.origin.z])
        extent_u = ctx.uniform("vec3", [self.extent.x, self.extent.y, self.extent.z])
        
        # Add helper function
        ctx.add_custom_helper("fld_ffd_inverse_res2", _GLSL_FFD_INVERSE_RES2)
        
        # Handle placement transformations
        if self.placement is not None:
            mat_val = placement_matrix(self.placement).tolist()
            mat_inv_val = placement_matrix(self.placement, inverse=True).tolist()
            
            mat_u = ctx.uniform("mat4", mat_val)
            mat_inv_u = ctx.uniform("mat4", mat_inv_val)
            
            p_local_expr = f"({mat_inv_u} * vec4({point_var}, 1.0)).xyz"
            p_def_local_expr = f"fld_ffd_inverse_res2({p_local_expr}, {origin_u}, {extent_u}, {ctrl_pts_u})"
            deformed_var = f"({mat_u} * vec4({p_def_local_expr}, 1.0)).xyz"
        else:
            deformed_var = f"fld_ffd_inverse_res2({point_var}, {origin_u}, {extent_u}, {ctrl_pts_u})"
            
        return self.source.to_glsl(ctx, deformed_var)

    def to_glsl_sample(self, ctx, point_var="p"):
        R = self.resolution
        
        # Format control points list
        ctrl_flat = []
        for i in range(R**3):
            pt_local = self.deformed_grid[i]
            ctrl_flat.extend([float(pt_local[0]), float(pt_local[1]), float(pt_local[2])])
            
        # Register uniforms
        ctrl_pts_u = ctx.uniform(f"vec3[{R**3}]", ctrl_flat)
        origin_u = ctx.uniform("vec3", [self.origin.x, self.origin.y, self.origin.z])
        extent_u = ctx.uniform("vec3", [self.extent.x, self.extent.y, self.extent.z])
        
        # Add helper function
        ctx.add_custom_helper("fld_ffd_inverse_res2", _GLSL_FFD_INVERSE_RES2)
        
        # Handle placement transformations
        if self.placement is not None:
            mat_val = placement_matrix(self.placement).tolist()
            mat_inv_val = placement_matrix(self.placement, inverse=True).tolist()
            
            mat_u = ctx.uniform("mat4", mat_val)
            mat_inv_u = ctx.uniform("mat4", mat_inv_val)
            
            p_local_expr = f"({mat_inv_u} * vec4({point_var}, 1.0)).xyz"
            p_def_local_expr = f"fld_ffd_inverse_res2({p_local_expr}, {origin_u}, {extent_u}, {ctrl_pts_u})"
            deformed_var = f"({mat_u} * vec4({p_def_local_expr}, 1.0)).xyz"
        else:
            deformed_var = f"fld_ffd_inverse_res2({point_var}, {origin_u}, {extent_u}, {ctrl_pts_u})"
            
        return self.source.to_glsl_sample(ctx, deformed_var)

    # ------------------------------------------------------------------
    # Injectivity & Clamping checks (SD-003)
    # ------------------------------------------------------------------

    def check_injectivity(self, displacements: np.ndarray) -> bool:
        """Checks if the lattice is injective (no folded cells) for the given displacements."""
        R = self.resolution
        deformed_grid = self.grid_local + displacements
        
        for k in range(R - 1):
            for j in range(R - 1):
                for i in range(R - 1):
                    # Flat indices of the 8 corners of this cell:
                    # idx_c = c_u + c_v * 2 + c_w * 4
                    idx = [
                        (i + c_u) + (j + c_v) * R + (k + c_w) * R**2
                        for c_w in (0, 1)
                        for c_v in (0, 1)
                        for c_u in (0, 1)
                    ]
                    P = [deformed_grid[idx[idx_c]] for idx_c in range(8)]
                    
                    for c_w in (0, 1):
                        for c_v in (0, 1):
                            for c_u in (0, 1):
                                idx_curr = c_u + c_v * 2 + c_w * 4
                                idx_u = (1 - c_u) + c_v * 2 + c_w * 4
                                idx_v = c_u + (1 - c_v) * 2 + c_w * 4
                                idx_w = c_u + c_v * 2 + (1 - c_w) * 4
                                
                                sgn_u = 1.0 if c_u == 0 else -1.0
                                sgn_v = 1.0 if c_v == 0 else -1.0
                                sgn_w = 1.0 if c_w == 0 else -1.0
                                
                                J_u = sgn_u * (P[idx_u] - P[idx_curr])
                                J_v = sgn_v * (P[idx_v] - P[idx_curr])
                                J_w = sgn_w * (P[idx_w] - P[idx_curr])
                                
                                det = np.dot(J_u, np.cross(J_v, J_w))
                                if det <= 1e-5:
                                    return False
        return True

    def clamp_displacements(self, new_displacements: np.ndarray, old_displacements: np.ndarray = None) -> np.ndarray:
        """Keeps displacements within the injective (non-folded) region.
        
        If the new displacements cause any cell Jacobian determinant to be <= 1e-5,
        we scale back the changes towards the previous valid displacements
        (or zero displacements if old_displacements is None) using binary search.
        """
        if old_displacements is None:
            old_displacements = np.zeros_like(new_displacements)
            
        if self.check_injectivity(new_displacements):
            return new_displacements
            
        # Binary search for maximum safe t in [0, 1]
        low = 0.0
        high = 1.0
        safe_disp = old_displacements.copy()
        
        for _ in range(8):
            mid = (low + high) / 2.0
            test_disp = mid * new_displacements + (1.0 - mid) * old_displacements
            if self.check_injectivity(test_disp):
                safe_disp = test_disp
                low = mid
            else:
                high = mid
                
        return safe_disp


_GLSL_FFD_INVERSE_RES2 = """
vec3 fld_ffd_inverse_res2(vec3 p_local, vec3 origin, vec3 extent, vec3 P[8]) {
    vec3 extent_safe = max(extent, vec3(1e-6));
    vec3 uvw = (p_local - origin) / extent_safe;
    uvw = clamp(uvw, vec3(0.0), vec3(1.0));
    
    vec3 u_curr = uvw;
    vec3 err = vec3(0.0);
    
    for (int step = 0; step < 5; ++step) {
        float u = u_curr.x, v = u_curr.y, w = u_curr.z;
        
        float w000 = (1.0 - u) * (1.0 - v) * (1.0 - w);
        float w100 = u * (1.0 - v) * (1.0 - w);
        float w010 = (1.0 - u) * v * (1.0 - w);
        float w110 = u * v * (1.0 - w);
        float w001 = (1.0 - u) * (1.0 - v) * w;
        float w101 = u * (1.0 - v) * w;
        float w011 = (1.0 - u) * v * w;
        float w111 = u * v * w;
        
        vec3 f_val = w000 * P[0] + w100 * P[1] + w010 * P[2] + w110 * P[3] +
                     w001 * P[4] + w101 * P[5] + w011 * P[6] + w111 * P[7];
                     
        err = f_val - p_local;
        
        vec3 df_du = (1.0 - v) * (1.0 - w) * (P[1] - P[0]) +
                     v * (1.0 - w) * (P[3] - P[2]) +
                     (1.0 - v) * w * (P[5] - P[4]) +
                     v * w * (P[7] - P[6]);
                     
        vec3 df_dv = (1.0 - u) * (1.0 - w) * (P[2] - P[0]) +
                     u * (1.0 - w) * (P[3] - P[1]) +
                     (1.0 - u) * w * (P[6] - P[4]) +
                     u * w * (P[7] - P[5]);
                     
        vec3 df_dw = (1.0 - u) * (1.0 - v) * (P[4] - P[0]) +
                     u * (1.0 - v) * (P[5] - P[1]) +
                     (1.0 - u) * v * (P[6] - P[2]) +
                     u * v * (P[7] - P[3]);
                     
        float det = dot(df_du, cross(df_dv, df_dw));
        if (abs(det) < 1e-9) {
            det = sign(det) * 1e-9;
            if (det == 0.0) det = 1e-9;
        }
        
        vec3 inv_u = cross(df_dv, df_dw) / det;
        vec3 inv_v = cross(df_dw, df_du) / det;
        vec3 inv_w = cross(df_du, df_dv) / det;
        
        u_curr.x = clamp(u_curr.x - dot(inv_u, err), 0.0, 1.0);
        u_curr.y = clamp(u_curr.y - dot(inv_v, err), 0.0, 1.0);
        u_curr.z = clamp(u_curr.z - dot(inv_w, err), 0.0, 1.0);
    }
    
    float err_norm = length(err);
    float threshold = 0.01 * length(extent_safe);
    if (err_norm < threshold) {
        return origin + u_curr * extent_safe;
    } else {
        return p_local;
    }
}
"""
