
import numpy as np
import FreeCAD
import FreeCADGui

# Remove top-level import to avoid lazy_loader crash
HAS_SKIMAGE = False 

class SDFObject:
    def __init__(self):
        self.inverse_matrix = None
        self.matrix = None # Forward matrix

    def set_placement(self, placement):
        """
        Set the placement (transformation) of the SDF object.
        placement: FreeCAD.Placement or matrix-like object.
        """
        # Get the matrix (4x4)
        mat = placement.Matrix
        # FreeCAD Matrix -> Numpy
        self.matrix = np.array([
            [mat.A11, mat.A12, mat.A13, mat.A14],
            [mat.A21, mat.A22, mat.A23, mat.A24],
            [mat.A31, mat.A32, mat.A33, mat.A34],
            [mat.A41, mat.A42, mat.A43, mat.A44]
        ]).transpose()

        # Invert the matrix for world->local transform
        inv = mat.inverse()
        self.inverse_matrix = np.array([
            [inv.A11, inv.A12, inv.A13, inv.A14],
            [inv.A21, inv.A22, inv.A23, inv.A24],
            [inv.A31, inv.A32, inv.A33, inv.A34],
            [inv.A41, inv.A42, inv.A43, inv.A44]
        ]).transpose()

    def evaluate(self, points):
        """
        Public evaluate method. Transforms points if a placement is set, 
        then calls _evaluate_local.
        """
        if self.inverse_matrix is not None:
            # Add homogeneous coordinate w=1
            ones = np.ones((points.shape[0], 1))
            pts_h = np.hstack((points, ones))
            
            # Transform: (N, 4) dot (4, 4) -> (N, 4)
            # using transposed matrix
            local_pts_h = np.dot(pts_h, self.inverse_matrix)
            
            # Back to 3D
            local_points = local_pts_h[:, :3]
            return self._evaluate_local(local_points)
        else:
            return self._evaluate_local(points)

    def _evaluate_local(self, points):
        """
        Evaluate SDF in local coordinates. Override this.
        """
        raise NotImplementedError

    def bounds(self):
        """
        Return the axis-aligned bounding box of the object.
        If placement is set, returns the AABB of the transformed object.
        Returns: (min_point, max_point) as numpy arrays.
        """
        # Get local bounds from subclass
        local_min, local_max = self._bounds_local()
        
        if self.matrix is None:
            return local_min, local_max
            
        # Transform the 8 corners of the local AABB
        corners = [
            [local_min[0], local_min[1], local_min[2]],
            [local_min[0], local_min[1], local_max[2]],
            [local_min[0], local_max[1], local_min[2]],
            [local_min[0], local_max[1], local_max[2]],
            [local_max[0], local_min[1], local_min[2]],
            [local_max[0], local_min[1], local_max[2]],
            [local_max[0], local_max[1], local_min[2]],
            [local_max[0], local_max[1], local_max[2]]
        ]
        
        corners = np.array(corners)
        ones = np.ones((8, 1))
        corners_h = np.hstack((corners, ones))
        
        # Transform to world
        world_corners_h = np.dot(corners_h, self.matrix)
        world_corners = world_corners_h[:, :3]
        
        # Find new AABB
        min_p = np.min(world_corners, axis=0)
        max_p = np.max(world_corners, axis=0)
        
        return min_p, max_p

    def _bounds_local(self):
        raise NotImplementedError

    def generate_point_cloud(self, resolution=32, margin=0.1, samples=8, iterations=5):
        """
        Generates a dense point cloud using Stochastic Surface Projection.
        1. Identifies active voxels near surface.
        2. Spawns random seeds in those voxels.
        3. Projects seeds to zero-level set using SDF gradients.
        
        Returns points in LOCAL coordinates.
        """
        import time
        t0 = time.time()
        
        bound_min, bound_max = self._bounds_local()
        size = bound_max - bound_min
        size[size < 1e-6] = 1.0 # Safety
        
        margin_vec = size * margin
        grid_min = bound_min - margin_vec
        grid_max = bound_max + margin_vec
        
        # 1. Coarse Grid to find Active Voxels
        # Make resolution slightly coarser? Or use input resolution.
        # User wants "dense" but not regular.
        # Let's use resolution for the coarse grid.
        
        x_vals = np.linspace(grid_min[0], grid_max[0], resolution)
        y_vals = np.linspace(grid_min[1], grid_max[1], resolution)
        z_vals = np.linspace(grid_min[2], grid_max[2], resolution)
        
        step_sizes = np.array([
            x_vals[1] - x_vals[0] if len(x_vals) > 1 else 1.0,
            y_vals[1] - y_vals[0] if len(y_vals) > 1 else 1.0,
            z_vals[1] - z_vals[0] if len(z_vals) > 1 else 1.0
        ])
        voxel_diag = np.linalg.norm(step_sizes)
        
        grid_x, grid_y, grid_z = np.meshgrid(x_vals, y_vals, z_vals, indexing='ij')
        grid_points = np.stack([grid_x, grid_y, grid_z], axis=-1).reshape(-1, 3)
        
        vals = self._evaluate_local(grid_points)
        
        # Active voxels: |val| < diagonal
        # This catches voxels that might contain the surface
        mask = np.abs(vals) < (voxel_diag * 1.5) # slightly generous
        active_indices = np.where(mask)[0]
        
        if len(active_indices) == 0:
            return np.zeros((0, 3))
            
        n_active = len(active_indices)
        # Spawn random seeds
        # Center of active voxels
        centers = grid_points[active_indices]
        
        # Vectors from -0.5 to 0.5 step
        rng = np.random.default_rng() 
        
        all_pts = []
        
        # Vectorized spawning
        # Total points = n_active * samples
        # Repeat centers
        centers_repeated = np.repeat(centers, samples, axis=0)
        n_total = len(centers_repeated)
        
        # Random offsets: -0.5 to 0.5 * step
        jitter = (rng.random((n_total, 3)) - 0.5) * step_sizes
        
        candidates = centers_repeated + jitter
        
        # 2. Projection Loop (Newton-Raphson)
        curr_pts = candidates
        
        for k in range(iterations):
            dists = self._evaluate_local(curr_pts)
            
            # Check convergence/divergence
            # If distance is huge, maybe drop? But keep simple for now.
            
            grads = self.compute_normals(curr_pts, epsilon=1e-4) # Returns normalized normals
            
            # Move towards surface: p_new = p - dist * normal
            # SDF convention: dist > 0 outside, normal points outside.
            # So if dist > 0, we are outside, normal points away from surface.
            # We want to go -normal * dist. 
            # If dist < 0 (inside), normal points outside.
            # We want to go +normal * |dist| = -normal * dist.
            # So formula is consistent.
            
            # Dampen step slightly for stability? 1.0 is Newton.
            curr_pts = curr_pts - grads * dists[:, np.newaxis]
            
            # Clip to bounds to avoid flying off?
            # Optional
            
        # Final filter: Check if actually close to surface
        final_dists = np.abs(self._evaluate_local(curr_pts))
        valid_mask = final_dists < (voxel_diag * 0.1) # Strict tolerance
        
        final_pts = curr_pts[valid_mask]
        
        # Log
        # FreeCAD.Console.PrintMessage(f"Stochastic: {len(final_pts)} points generated. (Init: {n_total})\n")
        
        return final_pts

    def compute_normals(self, points, epsilon=1e-4):
        """
        Compute normalized gradients (normals) for points via central finite difference.
        """
        n_points = len(points)
        if n_points == 0: return np.zeros((0, 3))
        
        sx = np.array([epsilon, 0, 0])
        sy = np.array([0, epsilon, 0])
        sz = np.array([0, 0, epsilon])
        
        # 6 samples per point
        # Efficient batching
        
        # Shapes:
        # P: (N, 3)
        # Q: (N, 6, 3) -> flattened (N*6, 3)
        
        P = points[:, np.newaxis, :] # (N, 1, 3)
        offsets = np.vstack([sx, -sx, sy, -sy, sz, -sz]) # (6, 3)
        
        Q = P + offsets # Broadcasting -> (N, 6, 3)
        Q_flat = Q.reshape(-1, 3)
        
        vals = self._evaluate_local(Q_flat) # (N*6)
        vals = vals.reshape(n_points, 6)
        
        # Gradients
        gx = vals[:, 0] - vals[:, 1]
        gy = vals[:, 2] - vals[:, 3]
        gz = vals[:, 4] - vals[:, 5]
        
        grads = np.stack([gx, gy, gz], axis=1) # (N, 3)
        
        # Normalize
        mags = np.linalg.norm(grads, axis=1, keepdims=True)
        mags[mags < 1e-12] = 1.0 # Avoid div zero
        
        normals = grads / mags
        return normals

    def compute_variances(self, points, radius=None):
        """
        Compute surface normal variance for given points.
        Returns: scores (0.0 to ~1.0)
        """
        n_points = len(points)
        if n_points == 0: return np.array([])
        
        r = radius if radius is not None else 0.2
        
        # Use 6-axis sampling (deterministic)
        offsets = np.array([
            [r, 0, 0], [-r, 0, 0],
            [0, r, 0], [0, -r, 0],
            [0, 0, r], [0, 0, -r]
        ])
        n_samples = len(offsets)
        
        samples = points[:, np.newaxis, :] + offsets[np.newaxis, :, :]
        samples_flat = samples.reshape(-1, 3)
        
        # Compute normals for all samples
        # Reuse separate compute_normals? Yes.
        # But compute_normals uses epsilon for fd. radius here is for variance "search" radius.
        
        norms_flat = self.compute_normals(samples_flat, epsilon=1e-4)
        norms_grouped = norms_flat.reshape(n_points, n_samples, 3)
        
        # Variance = 1 - length(mean_normal)
        mean_norm = np.mean(norms_grouped, axis=1)
        len_mean = np.linalg.norm(mean_norm, axis=1)
        scores = 1.0 - len_mean
        
        return scores

class SDFBox(SDFObject):
    def __init__(self, size):
        super().__init__()
        # size is (width, depth, height)
        if isinstance(size, (int, float)):
             self.half_size = np.array([size, size, size]) / 2.0
        else:
             self.half_size = np.abs(np.array(size)) / 2.0
             
    def _evaluate_local(self, points):
        # sdBox( p, b ) = length( max(abs(p)-b, 0.0) ) + min(max(max(abs(p).x-b.x, abs(p).y-b.y), abs(p).z-b.z), 0.0)
        
        # points shape: (N, 3)
        # self.half_size shape: (3,)
        
        d = np.abs(points) - self.half_size
        
        # Max(d, 0.0)
        max_d = np.maximum(d, 0.0)
        
        # length(max_d)
        len_max_d = np.linalg.norm(max_d, axis=1)
        
        # Min(max(d.x, d.y, d.z), 0.0)
        # We need max across axis 1
        max_d_comp = np.max(d, axis=1)
        min_max_d = np.minimum(max_d_comp, 0.0)
        
        return len_max_d + min_max_d

    def _bounds_local(self):
        return (-self.half_size, self.half_size)


class SDFCapsule(SDFObject):
    def __init__(self, length, radius):
        super().__init__()
        self.length = length
        self.radius = radius
        # Defined along Z axis, centered at origin
        # Point A: (0, 0, -length/2)
        # Point B: (0, 0, length/2)
        self.a = np.array([0, 0, -length/2.0])
        self.b = np.array([0, 0, length/2.0])
        self.ba = self.b - self.a
        self.baba = np.dot(self.ba, self.ba)

    def _evaluate_local(self, points):
        # points: (N, 3)
        
        # pa = p - a
        # shape (N, 3)
        pa = points - self.a
        
        # h = clamp( dot(pa, ba) / dot(ba, ba), 0.0, 1.0 )
        # dot(pa, ba): (N,)
        dot_pa_ba = np.dot(pa, self.ba)
        
        h = np.clip(dot_pa_ba / self.baba, 0.0, 1.0)
        
        # pa - ba * h
        # ba * h: (N, 3) because h is (N,) and ba is (3,)
        # We need to reshape h for broadcasting
        h_vec = h.reshape(-1, 1)
        
        diff = pa - self.ba * h_vec
        
        return np.linalg.norm(diff, axis=1) - self.radius

    def _bounds_local(self):
        # Local bounds
        # Z: -L/2 - R to L/2 + R
        # X, Y: -R to R
        half_z = self.length / 2.0 + self.radius
        r = self.radius
        return (np.array([-r, -r, -half_z]), np.array([r, r, half_z]))

def mesh_from_sdf(sdf_obj, resolution=32, margin=0.1):
    """
    Generate a mesh from an SDF object using Marching Cubes.
    """

    
    # Use local marching cubes implementation
    import FCDirectModeling.marching_cubes as mc
    
    bound_min, bound_max = sdf_obj.bounds()
    
    # Add margin
    size = bound_max - bound_min
    margin_vec = size * margin
    
    grid_min = bound_min - margin_vec
    grid_max = bound_max + margin_vec
    
    # Create grid
    x = np.linspace(grid_min[0], grid_max[0], resolution)
    y = np.linspace(grid_min[1], grid_max[1], resolution)
    z = np.linspace(grid_min[2], grid_max[2], resolution)
    
    # Meshgrid (indexing='ij' for matrix indexing)
    X, Y, Z = np.meshgrid(x, y, z, indexing='ij')
    
    # Flatten to list of points
    points = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)
    
    # Evaluate
    values = sdf_obj.evaluate(points)
    
    # Check for NaNs/Infs
    if np.any(np.isnan(values)) or np.any(np.isinf(values)):
        FreeCAD.Console.PrintWarning("mesh_from_sdf: NaN or Inf detected in SDF evaluation!\n")
        # Replace bad values with a large positive distance (outside)
        values = np.nan_to_num(values, nan=100.0, posinf=100.0, neginf=100.0)
    
    # Reshape back to grid (x, y, z)
    vol = values.reshape((resolution, resolution, resolution))
    
    # Transpose to (z, y, x) for marching_cubes which expects Axis 0 as Z
    vol = vol.transpose((2, 1, 0))
    
    # Marching Cubes
    # level=0.0 is the surface
    try:
        # returns verts, faces, normals, values
        # Note: custom implementation might return None for normals
        verts, faces, normals, values = mc.marching_cubes(vol, level=0.0)
        
        if verts.shape[0] == 0:
            return None, None, None
            
        # Transform verts from grid indices to World coordinates
        step = (grid_max - grid_min) / (resolution - 1)
        world_verts = verts * step + grid_min
        
        # Transform verts from grid indices to World coordinates
        step = (grid_max - grid_min) / (resolution - 1)
        world_verts = verts * step + grid_min
        
        # Transform World -> Local
        # Because FreeCAD ViewProvider already applies the object's Placement,
        # we need the mesh to be in local coordinates (centered at origin, etc.)
        # otherwise we get double transformation.
        
        if sdf_obj.inverse_matrix is not None:
             # Add homogeneous w=1
             ones = np.ones((world_verts.shape[0], 1))
             w_verts_h = np.hstack((world_verts, ones))
             
             # Apply inverse matrix: World -> Local
             # Dot with Transpose of Inverse (which is just 'inverse_matrix' stored in SDFObject)
             # SDFObject stores:
             # self.matrix = np.array([mat.A...]).transpose() <-- Column-Major logic?
             # self.inverse_matrix = np.array([inv.A...]).transpose()
             # evaluate() uses: np.dot(pts_h, self.inverse_matrix)
             
             l_verts_h = np.dot(w_verts_h, sdf_obj.inverse_matrix) 
             real_verts = l_verts_h[:, :3]
        else:
             real_verts = world_verts
             
        return real_verts, faces, normals
        
    except ValueError:
        return None, None, None

def contours_from_sdf(sdf_obj, resolution=32, margin=0.1, axis='z', slices=5):
    """
    Generate contours (slices) from an SDF object.
    """
    import FCDirectModeling.marching_cubes as mc

    bound_min, bound_max = sdf_obj.bounds()
    size = bound_max - bound_min
    margin_vec = size * margin
    grid_min = bound_min - margin_vec
    grid_max = bound_max + margin_vec
    
    lines = [] # List of polylines (Nx3 arrays)

    # Determine slice positions
    if isinstance(slices, int):
        idx = {'x':0, 'y':1, 'z':2}[axis]
        slice_vals = np.linspace(grid_min[idx], grid_max[idx], slices)
    else:
        slice_vals = slices
        
    other_axes = [i for i in range(3) if i != {'x':0, 'y':1, 'z':2}[axis]]
    u_idx, v_idx = other_axes[0], other_axes[1]
    
    # Grid for the slice plane (U, V)
    u = np.linspace(grid_min[u_idx], grid_max[u_idx], resolution)
    v = np.linspace(grid_min[v_idx], grid_max[v_idx], resolution)
    U, V = np.meshgrid(u, v, indexing='ij')
    
    # Common points array buffer
    slice_points = np.zeros((resolution*resolution, 3))
    
    for val in slice_vals:
        # Populate points
        slice_points[:, u_idx] = U.ravel()
        slice_points[:, v_idx] = V.ravel()
        slice_points[:, {'x':0, 'y':1, 'z':2}[axis]] = val
        
        # Evaluate
        d_vals = sdf_obj.evaluate(slice_points)
        d_grid = d_vals.reshape((resolution, resolution))
        
        # Find contours
        try:
            contours = mc.find_contours(d_grid, level=0.0)
            
            for contour in contours:
                # contour is (N, 2) -> (row, col) -> (u_idx, v_idx)
                # We need to map back to world coordinates
                
                # U coordinate
                # row is float index in u array (if implementation uses index)
                # Our mc.find_contours returns index coordinates
                
                # u_coords = contour[:, 0] * (u[1] - u[0]) + u[0]
                # But u is linspace, so index * step + min
                step_u = u[1] - u[0]
                step_v = v[1] - v[0]
                
                u_coords = contour[:, 0] * step_u + u[0]
                v_coords = contour[:, 1] * step_v + v[0]
                
                # Create 3D points
                polyline = np.zeros((len(contour), 3))
                polyline[:, u_idx] = u_coords
                polyline[:, v_idx] = v_coords
                polyline[:, {'x':0, 'y':1, 'z':2}[axis]] = val
                
                lines.append(polyline)
                
        except ValueError:
            continue
            
    return lines


class SDFOperation(SDFObject):
    def __init__(self, sdf_a, sdf_b):
        super().__init__()
        self.sdf_a = sdf_a
        self.sdf_b = sdf_b
        
    def _evaluate_local(self, points):
        # points are in Local coordinates of this Boolean object.
        # But sdf_a and sdf_b are independent objects with their own transforms,
        # so they expect World coordinates.
        # We must transform points: Local -> World
        
        world_points = points
        if self.matrix is not None:
            # Add homogeneous coord
            ones = np.ones((points.shape[0], 1))
            pts_h = np.hstack((points, ones))
            # Local -> World: dot(pts, matrix) (since matrix is transposed storage of FreeCAD matrix?)
            # Wait, set_placement stores:
            # self.matrix = np.array(...).transpose()
            # self.inverse_matrix = np.array(...).transpose()
            # evaluate() uses: np.dot(pts_h, self.inverse_matrix)
            # This implies row-vector * matrix. (1x4 * 4x4)
            # So self.inverse_matrix is indeed the transpose of the standard column-major matrix?
            # FreeCAD Matrix is Row-Major? No, usually Column-Major in OpenGL, but logic here says:
            # v' = v * M. This is row-vector convention.
            # So we use self.matrix for Local->World.
            
            w_pts_h = np.dot(pts_h, self.matrix)
            world_points = w_pts_h[:, :3]

        d1 = self.sdf_a.evaluate(world_points)
        d2 = self.sdf_b.evaluate(world_points)
        return self._combine(d1, d2)

class SDFSphere(SDFObject):
    def __init__(self, radius):
        super().__init__()
        self.radius = abs(radius)

    def _evaluate_local(self, points):
        # length(p) - r
        return np.linalg.norm(points, axis=1) - self.radius

    def _bounds_local(self):
        r = self.radius
        return (np.array([-r, -r, -r]), np.array([r, r, r]))

class SDFCone(SDFObject):
    def __init__(self, radius, height):
        super().__init__()
        self.radius = abs(radius) # Base radius
        self.height = height # Total height
        
        # Avoid division by zero
        h_safe = height if abs(height) > 1e-6 else 1e-6
        r_safe = self.radius
        
        self.q = np.array([r_safe/h_safe, -1.0]) 
        
        hyp = np.sqrt(r_safe*r_safe + h_safe*h_safe)
        if hyp < 1e-9: hyp = 1e-9
        
        self.sin_a = r_safe / hyp
        self.cos_a = h_safe / hyp
        
    def _evaluate_local(self, points):
        # Capped Cone (Inigo Quilez sdCappedCone)
        # Adapted for Z-up: p.z is height, p.xy is radius.
        # We treat base at Z=0 (r1=Radius) and tip at Z=Height (r2=0).
        
        p = points
        h = self.height
        r1 = self.radius
        r2 = 0.0 # Tip
        
        # In IQ formula:
        # p is vec3, h is height (center to tip?? No, usually full height if defined by 2 points)
        # But standard function: float sdCappedCone( vec3 p, float h, float r1, float r2 )
        # usually centered at 0.
        
        # Let's use the Vectorized version of:
        # float sdCappedCone(vec3 p, vec3 a, vec3 b, float ra, float rb)
        # a=(0,0,0), b=(0,0,h)
        
        ba = np.array([0, 0, h])
        pa = p # since a is 0
        
        # l2 = dot(ba,ba)
        l2 = h*h
        
        # rr = ra - rb (r1 - 0 = r1)
        rr = r1
        
        # a2 = l2 - rr*rr  <-- This was the crash source in RoundCone if l2 < rr*rr
        # But we are NOT using RoundCone anymore. We use exact CappedCone.
        
        # Vectorized implementation of IQ sdCappedCone (exact):
        # https://www.shadertoy.com/view/tsq3ft
        
        # float sdCappedCone(vec3 p, vec3 a, vec3 b, float ra, float rb)
        # {
        #     float rba  = rb-ra;
        #     float baba = dot(b-a,b-a);
        #     float papa = dot(p-a,p-a);
        #     float paba = dot(p-a,b-a)/baba;
        #     float x = sqrt( papa - paba*paba*baba );
        #     float cax = max(0.0,x-((paba<0.5)?ra:rb));
        #     float cay = abs(paba-0.5)-0.5;
        #     float k = rba*rba + baba;
        #     float f = clamp( (rba*(x-ra)+paba*baba)/k, 0.0, 1.0 );
        #     float cbx = x-ra - f*rba;
        #     float cby = paba - f;
        #     float s = (cbx < 0.0 && cby < 0.0) ? -1.0 : 1.0;
        #     return s*sqrt( min(cax*cax + cay*cay*baba, cbx*cbx*k + cby*cby*baba) );
        # }
        
        # Mapping to our variables:
        # a = (0,0,0), b = (0,0,h)
        # ra = r1, rb = 0
        
        rba = r2 - r1 # -r1
        baba = l2 # h^2
        papa = np.sum(pa*pa, axis=1) # dot(p, p)
        
        paba = np.dot(pa, ba) / (baba + 1e-9) # height projection ratio
        
        # x = sqrt( papa - paba*paba*baba ) -> Replace with numerical safe version
        # Dist from axis
        # x is actually length(p - projection_on_axis)
        # simplified: length(p.xy) if axis is Z
        x = np.linalg.norm(pa[:, :2], axis=1)
        
        # cax = max(0.0, x - ((paba<0.5)?ra:rb))
        # ternary vectorization
        target_r = np.where(paba < 0.5, r1, r2)
        cax = np.maximum(0.0, x - target_r)
        
        cay = np.abs(paba - 0.5) - 0.5
        
        k = rba*rba + baba 
        
        if k < 1e-9: k = 1e-9
        
        f = np.clip( (rba*(x - r1) + paba * baba) / k, 0.0, 1.0 )
        
        cbx = x - r1 - f * rba
        cby = paba - f
        
        s = np.where( (cbx < 0.0) & (cby < 0.0), -1.0, 1.0 )
        
        term1 = cax*cax + cay*cay*baba
        term2 = cbx*cbx*k + cby*cby*baba # (paba-f)^2 * h^2
        
        res = s * np.sqrt( np.minimum(term1, term2) )
        return res

    def _bounds_local(self):
        r = self.radius
        h = self.height
        # Height goes from 0 to h. 
        # If h is negative, range is [h, 0].
        z_min = min(0, h)
        z_max = max(0, h)
        return (np.array([-r, -r, z_min]), np.array([r, r, z_max]))

class SDFTorus(SDFObject):
    def __init__(self, major_radius, minor_radius):
        super().__init__()
        self.R = major_radius
        self.r = minor_radius
        
    def _evaluate_local(self, points):
        # sdTorus(p, vec2(t.x, t.y)) = length( vec2(length(p.xz)-t.x, p.y) ) - t.y
        # FreeCAD Torus is usually in XY plane (Axis Z).
        # So Major Ring is in XY. Cross section in Z.
        # SDF formula `length(p.xz)-t.x` assumes ring in XZ plane.
        
        # Adapted for Ring in XY:
        # length(p.xy) - R
        
        xy = points[:, :2] # Shape (N, 2)
        len_xy = np.linalg.norm(xy, axis=1)
        
        q_x = len_xy - self.R
        q_y = points[:, 2] # Z component
        
        q = np.stack([q_x, q_y], axis=1)
        return np.linalg.norm(q, axis=1) - self.r

    def _bounds_local(self):
        # Box containing torus
        # XY: -(R+r) to (R+r)
        # Z: -r to r
        lim = self.R + self.r
        return (np.array([-lim, -lim, -self.r]), np.array([lim, lim, self.r]))

class SDFOperation(SDFObject):
    def __init__(self, sdf_a, sdf_b):
        super().__init__()
        self.sdf_a = sdf_a
        self.sdf_b = sdf_b
        
    def _combine(self, d1, d2):
        raise NotImplementedError
        
    def _evaluate_local(self, points):
        # points are in Local coordinates of this Boolean object.
        # But sdf_a and sdf_b are independent objects with their own transforms,
        # so they expect World coordinates.
        # We must transform points: Local -> World
        
        world_points = points
        if self.matrix is not None:
             # Add homogeneous w=1
             ones = np.ones((points.shape[0], 1))
             pts_h = np.hstack((points, ones))
             # Local -> World: dot(pts, matrix)
             w_pts_h = np.dot(pts_h, self.matrix)
             world_points = w_pts_h[:, :3]

        d1 = self.sdf_a.evaluate(world_points)
        d2 = self.sdf_b.evaluate(world_points)
        return self._combine(d1, d2)

    def _get_child_bounds_in_local(self, child):
        """
        Helper to get child's world bounds and transform them into 
        this operation's local coordinate system.
        """
        c_min, c_max = child.bounds() # World Bounds
        
        if self.inverse_matrix is None:
            return c_min, c_max
            
        # Transform World Bounds -> Local
        # Note: Bounds are AABB. Rotating AABB is complex.
        # We transform the 8 corners of the World AABB and find new Local AABB.
        
        corners = [
            [c_min[0], c_min[1], c_min[2]],
            [c_min[0], c_min[1], c_max[2]],
            [c_min[0], c_max[1], c_min[2]],
            [c_min[0], c_max[1], c_max[2]],
            [c_max[0], c_min[1], c_min[2]],
            [c_max[0], c_min[1], c_max[2]],
            [c_max[0], c_max[1], c_min[2]],
            [c_max[0], c_max[1], c_max[2]]
        ]
        
        corners = np.array(corners)
        ones = np.ones((8, 1))
        corners_h = np.hstack((corners, ones))
        
        # World -> Local
        l_corners_h = np.dot(corners_h, self.inverse_matrix)
        l_corners = l_corners_h[:, :3]
        
        l_min = np.min(l_corners, axis=0)
        l_max = np.max(l_corners, axis=0)
        
        return l_min, l_max

class SDFUnion(SDFOperation):
    def _combine(self, d1, d2):
        return np.minimum(d1, d2)
        
    def _bounds_local(self):
        min_a, max_a = self._get_child_bounds_in_local(self.sdf_a)
        min_b, max_b = self._get_child_bounds_in_local(self.sdf_b)
        return np.minimum(min_a, min_b), np.maximum(max_a, max_b)

class SDFDifference(SDFOperation):
    def _combine(self, d1, d2):
        return np.maximum(d1, -d2)
        
    def _bounds_local(self):
        # Difference bounds is at most A
        return self._get_child_bounds_in_local(self.sdf_a)

class SDFIntersection(SDFOperation):
    def _combine(self, d1, d2):
        return np.maximum(d1, d2)
        
    def _bounds_local(self):
        min_a, max_a = self._get_child_bounds_in_local(self.sdf_a)
        min_b, max_b = self._get_child_bounds_in_local(self.sdf_b)
        return np.maximum(min_a, min_b), np.minimum(max_a, max_b)

def generate_point_cloud(sdf_obj, resolution=32, margin=0.1):
    """
    Generates a dense point cloud on the zero-isosurface of the SDF object.
    Finds zero-crossings along grid edges directly.
    Returns points in LOCAL coordinates of the object.
    """
    import time
    t0 = time.time()
    
    # We want LOCAL coordinates because SDFRenderer applies the placement transform.
    # So we must generate grid in Local Space.
    bound_min, bound_max = sdf_obj._bounds_local()
    size = bound_max - bound_min
    margin_vec = size * margin
    grid_min = bound_min - margin_vec
    grid_max = bound_max + margin_vec
    
    # Create grid
    x_vals = np.linspace(grid_min[0], grid_max[0], resolution)
    y_vals = np.linspace(grid_min[1], grid_max[1], resolution)
    z_vals = np.linspace(grid_min[2], grid_max[2], resolution)
    
    # 3D Grid
    grid_x, grid_y, grid_z = np.meshgrid(x_vals, y_vals, z_vals, indexing='ij')
    
    # Reshape grid points for evaluation
    grid_points = np.stack([grid_x, grid_y, grid_z], axis=-1).reshape(-1, 3)
    
    # Evaluate SDF (Local Coords)
    vals = sdf_obj._evaluate_local(grid_points)
    
    vals = vals.reshape(resolution, resolution, resolution)
    
    points = []
    
    # Find zero crossings along X edges
    # grid[i, j, k] vs grid[i+1, j, k]
    # Indices: (x, y, z)
    
    # X-edges: (x, y, z) -> (x+1, y, z)
    # v1 = vals[:-1, :, :]
    # v2 = vals[1:, :, :]
    # crossings: v1 * v2 < 0
    
    def get_crossings(v1, v2, p1_idx, p2_idx, axis):
        mask = (v1 * v2) <= 0
        if not np.any(mask):
            return np.zeros((0, 3))
            
        # Get indices
        # ix, iy, iz are indices into v1
        ix, iy, iz = np.where(mask)
        
        # Values
        val1 = v1[ix, iy, iz]
        val2 = v2[ix, iy, iz]
        
        # Interpolate t (fraction from p1 to p2)
        # p = p1 + t * (p2 - p1)
        # val = val1 + t * (val2 - val1) = 0
        # t = -val1 / (val2 - val1)
        # Avoid div zero
        denom = (val2 - val1)
        denom[denom == 0] = 1.0 # Should be covered by mask (val1*val2 <= 0 so if both 0, t=0?)
        t = -val1 / denom
        
        # Coords
        # Grid coords
        
        # Axis 0 (X): p1=(ix, iy, iz), p2=(ix+1, iy, iz)
        # Axis 1 (Y): p1=(ix, iy, iz), p2=(ix, iy+1, iz)
        # Axis 2 (Z): p1=(ix, iy, iz), p2=(ix, iy, iz+1)
        
        # Basic coords:
        c_x = x_vals[ix]
        c_y = y_vals[iy]
        c_z = z_vals[iz]
        
        # Result coords
        pts = np.zeros((len(t), 3))
        pts[:, 0] = c_x
        pts[:, 1] = c_y
        pts[:, 2] = c_z
        
        # Add offset along axis
        # Step size along axis
        if axis == 0: # X
            dx = x_vals[1] - x_vals[0]
            pts[:, 0] += t * dx
        elif axis == 1: # Y
            dy = y_vals[1] - y_vals[0]
            pts[:, 1] += t * dy
        elif axis == 2: # Z
            dz = z_vals[1] - z_vals[0]
            pts[:, 2] += t * dz
            
        return pts

    # X-edges: varies in dim 0
def detect_features(sdf_obj, resolution=32, threshold=0.5, algorithm="Laplacian"):

    """
    Detects features (high curvature points) on the SDF surface.
    Returns: (points, scores)
    """
    # 1. Generate mesh vertices to get candidate surface points
    verts, faces, normals = mesh_from_sdf(sdf_obj, resolution=resolution, margin=0.1)
    
    if verts is None or len(verts) == 0:
        print("detect_features: No vertices found.")
        return None, None
        
    FreeCAD.Console.PrintMessage(f"detect_features: {len(verts)} vertices. Algo: {algorithm}, Thresh: {threshold}\n")
    if len(verts) > 0:
        FreeCAD.Console.PrintMessage(f"detect_features: First vert: {verts[0]}\n")
        
    # Calculate dynamic radius based on resolution
    # Matches mesh_from_sdf logic roughly
    bound_min, bound_max = sdf_obj.bounds()
    size = bound_max - bound_min
    # Margin is 0.1 in mesh_from_sdf
    margin_vec = size * 0.1
    grid_min = bound_min - margin_vec
    grid_max = bound_max + margin_vec
    
    # max step size
    step = np.max((grid_max - grid_min) / (resolution - 1))
    
    # Radius should be comparable to step size. 
    # If radius < step/2, we might miss things between grid points?
    # Actually verts ARE on grid lines/edges (marching cubes).
    # But normal variance needs to sample *around* the point.
    # Radius = step * 0.8 seemed to be the heuristic in the plan.
    radius = step * 0.8
    
    if algorithm == "Laplacian":
        return _detect_laplacian(sdf_obj, verts, threshold)
    elif algorithm == "Normal Variance":
        # Pass normals if available? We can compute them locally or use mesh normals.
        # Ideally we sample fresh.
        return _detect_variance(sdf_obj, verts, threshold, radius=radius)
    else:
        FreeCAD.Console.PrintError(f"Unknown Algorithm: {algorithm}\n")
        return None, None

def _detect_laplacian(sdf_obj, points, threshold):
    # We use numerical Laplacian of SDF: L = d_xx + d_yy + d_zz
    eps = 1e-4
    n = len(points)
    
    dx = np.array([eps, 0, 0])
    dy = np.array([0, eps, 0])
    dz = np.array([0, 0, eps])
    
    p = points
    
    # 7 evaluations per point
    # Center, X+, X-, Y+, Y-, Z+, Z-
    
    batch = np.vstack([
        p + dx, p - dx,
        p + dy, p - dy,
        p + dz, p - dz,
        p
    ])
    
    vals = sdf_obj._evaluate_local(batch)
    
    v_xp = vals[0:n]
    v_xm = vals[n:2*n]
    v_yp = vals[2*n:3*n]
    v_ym = vals[3*n:4*n]
    v_zp = vals[4*n:5*n]
    v_zm = vals[5*n:6*n]
    v_c = vals[6*n:7*n]
    
    d_xx = (v_xp - 2*v_c + v_xm) / (eps*eps)
    d_yy = (v_yp - 2*v_c + v_ym) / (eps*eps)
    d_zz = (v_zp - 2*v_c + v_zm) / (eps*eps)
    
    laplacian = d_xx + d_yy + d_zz
    scores = np.abs(laplacians)
    
    # Debug
    msg = f"Laplacian Scores: Min={np.min(scores):.4f}, Max={np.max(scores):.4f}, Mean={np.mean(scores):.4f}"
    # print(msg)
    FreeCAD.Console.PrintMessage(msg + "\n")
    
    mask = scores > threshold
    return points[mask], scores[mask]

def _detect_variance(sdf_obj, points, threshold, radius=None):
    """
    Sample points in a small radius around each candidate.
    Compute normals at samples.
    Variance of normals (1 - length of mean vector) indicates curvature.
    """
    n_points = len(points)
    
    # Use dynamic radius if provided, else fallback to 0.2
    r = radius if radius is not None else 0.2
    
    # Generate random offsets in sphere
    # Better: 6 neighbors at radius R.
    
    offsets = np.array([
        [r, 0, 0], [-r, 0, 0],
        [0, r, 0], [0, -r, 0],
        [0, 0, r], [0, 0, -r]
    ])
    
    # Shape: (6, 3)
    n_samples = len(offsets)
    
    # We need to evaluate gradients (normals) at p + offset
    # Gradient = finite difference
    eps = 1e-4
    
    # Total evaluations: n_points * n_samples * 6 (for grad) -> Expensive.
    # 1000 points * 6 samples * 6 evals = 36k evals. Doable.
    
    # 1. Batch all sample points
    # (n_points, n_samples, 3)
    # p[:, None, :] + offsets[None, :, :]
    
    samples = points[:, np.newaxis, :] + offsets[np.newaxis, :, :]
    samples_flat = samples.reshape(-1, 3) # (N*6, 3)
    total_samples = len(samples_flat)
    
    # 2. Compute Normals at all samples
    # We need SDF at (samples +/- eps)
    
    sx = np.array([eps, 0, 0])
    sy = np.array([0, eps, 0])
    sz = np.array([0, 0, eps])
    
    # 6 evals per sample for central difference gradient
    # Or 4 if we use forward/backward? Central is better.
    
    grad_batch = np.vstack([
        samples_flat + sx, samples_flat - sx,
        samples_flat + sy, samples_flat - sy,
        samples_flat + sz, samples_flat - sz
    ])
    
    g_vals = sdf_obj._evaluate_local(grad_batch)
    
    gx_p = g_vals[0:total_samples]
    gx_m = g_vals[total_samples:2*total_samples]
    gy_p = g_vals[2*total_samples:3*total_samples]
    gy_m = g_vals[3*total_samples:4*total_samples]
    gz_p = g_vals[4*total_samples:5*total_samples]
    gz_m = g_vals[5*total_samples:6*total_samples]
    
    nx = (gx_p - gx_m)
    ny = (gy_p - gy_m)
    nz = (gz_p - gz_m)
    
    # Stack normals
    norms = np.stack([nx, ny, nz], axis=1) # (N*6, 3)
    
    # Normalize
    mag = np.linalg.norm(norms, axis=1, keepdims=True)
    norms = norms / (mag + 1e-9)
    
    # Reshape back to (N, 6, 3)
    norms_grouped = norms.reshape(n_points, n_samples, 3)
    
    # 3. Compute Variance
    # Mean normal vector
    mean_norm = np.mean(norms_grouped, axis=1) # (N, 3)
    
    # Length of mean normal
    # If all normals align, length is 1.0.
    # If they scatter, length < 1.0.
    # If they oppose (sharp edge), length is much less.
    
    len_mean = np.linalg.norm(mean_norm, axis=1)
    
    # Score = 1 - length
    # Flat = 0
    # Edge (90 deg) -> normals (1,0,0) and (0,1,0). Mean (0.5, 0.5, 0). Len 0.707. Score ~0.3
    scor = 1.0 - len_mean
    
    # Boost score for thresholding convenience? 
    # Scores are usually 0.0 to 1.0
    # Threshold 0.1 is usually good for edges.
    
    # Map to similar range as Laplacian? 
    # Laplacian was high (e.g. 5, 10).
    # Let's multiply by 10 to make 0.5 roughly significant.
    
    scores = scor * 100.0 # Scale up so threshold 5.0 works
    
    # Debug
    msg = f"Variance Scores: Min={np.min(scores):.4f}, Max={np.max(scores):.4f}, Mean={np.mean(scores):.4f}"
    # FreeCAD.Console.PrintMessage(msg + "\n")
    
    count_over = np.sum(scores > threshold)
    # FreeCAD.Console.PrintMessage(f"Points over Threshold ({threshold}): {count_over} / {len(scores)}\n")
    
    mask = scores > threshold
    return points[mask], scores[mask]
