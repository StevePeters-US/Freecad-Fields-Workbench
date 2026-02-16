
import numpy as np

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

class SDFBox(SDFObject):
    def __init__(self, size):
        super().__init__()
        # size is (width, depth, height)
        if isinstance(size, (int, float)):
             self.half_size = np.array([size, size, size]) / 2.0
        else:
             self.half_size = np.array(size) / 2.0
             
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
        
        # ---------------------------------------------------------
        # VERTEX PROJECTION (Sharp Edges Improvement)
        # ---------------------------------------------------------
        # Project vertices onto the exact zero-isosurface to improve
        # representation of sharp features and flat surfaces.
        # Iterative Gradient Descent: v = v - d * gradient(v)
        
        # We need a gradient function
        def calc_gradients(pts, epsilon=1e-5):
            # Central difference
            # x
            pts_x0 = pts - [epsilon, 0, 0]
            pts_x1 = pts + [epsilon, 0, 0]
            dx = sdf_obj.evaluate(pts_x1) - sdf_obj.evaluate(pts_x0)
            
            # y
            pts_y0 = pts - [0, epsilon, 0]
            pts_y1 = pts + [0, epsilon, 0]
            dy = sdf_obj.evaluate(pts_y1) - sdf_obj.evaluate(pts_y0)
            
            # z
            pts_z0 = pts - [0, 0, epsilon]
            pts_z1 = pts + [0, 0, epsilon]
            dz = sdf_obj.evaluate(pts_z1) - sdf_obj.evaluate(pts_z0)
            
            grads = np.stack([dx, dy, dz], axis=1)
            # Normalize
            norms = np.linalg.norm(grads, axis=1, keepdims=True)
            # Avoid div by zero
            return grads / (norms + 1e-9)

        # Iterate (2-3 times is usually enough)
        for _ in range(3):
            dists = sdf_obj.evaluate(world_verts)
            grads = calc_gradients(world_verts)
            # Move towards surface: v - d * grad
            # Note: SDF is positive outside. Gradient points outside.
            # To go to 0: v - d * grad
            world_verts = world_verts - grads * dists.reshape(-1, 1)

        # ---------------------------------------------------------

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
        self.radius = radius

    def _evaluate_local(self, points):
        # length(p) - r
        return np.linalg.norm(points, axis=1) - self.radius

    def _bounds_local(self):
        r = self.radius
        return (np.array([-r, -r, -r]), np.array([r, r, r]))

class SDFCone(SDFObject):
    def __init__(self, radius, height):
        super().__init__()
        self.radius = radius # Base radius
        self.height = height # Total height
        
        # Avoid division by zero
        h_safe = height if abs(height) > 1e-6 else 1e-6
        r_safe = radius
        
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

