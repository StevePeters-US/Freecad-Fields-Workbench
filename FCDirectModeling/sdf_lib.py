
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
    
    # Reshape back to grid
    vol = values.reshape((resolution, resolution, resolution))
    
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
        # Defined with base at z=0? Or centered?
        # Standard IQ cone is infinite? No, capped cone.
        # Let's assume standard Cone primitive: Base at Z=0, Tip at Z=H?
        # Or centered at Z=0 (from -H/2 to H/2).
        # FreeCAD Cone usually allows placement.
        # Let's define it: Tip at (0,0,H), Base at (0,0,0) radius R.
        # Or Tip at (0,0,0)...
        # In SDF logic (Inigo Quilez), a cone is usually p.xy vs p.z.
        
        # solid cone
        # float sdCone( vec3 p, vec2 c, float h )
        # c is (sin/cos) of angle, but we have R, H.
        # angle alpha: tan(alpha) = r/h.
        
        # Let's use a simpler Cylinder approximation if Cone is hard?
        # No, implementing capped cone.
        
        self.q = np.array([radius/height, -1.0]) # Gradient?
        # Precompute sin/cos?
        hyp = np.sqrt(radius*radius + height*height)
        self.sin_a = radius / hyp
        self.cos_a = height / hyp
        
    def _evaluate_local(self, points):
        # Capped Cone: Base at Z=0, Tip at Z=H, Radius R at Base, 0 at Tip.
        # IQ sdCone adapted for Z-up
        
        # p = points (N, 3)
        # q = vec2( length(p.xy), p.z )
        xy = points[:, :2] # N, 2
        q_x = np.linalg.norm(xy, axis=1) # Radial dist
        q_y = points[:, 2] # Height (Z)
        
        # We want to shift it so it's centered for the standard formula? 
        # Standard formula usually has center at 0.
        # Let's shift Z by -h/2 so it's from -h/2 to h/2
        # h in formula is half-height?
        # IQ: "h is height" (likely half dimension if using abs?)
        # Let's use the explicit logic from a reliable source or derivation.
        
        # Capped Cone (Inigo Quilez) defined by 2 points? 
        # sdRoundCone(vec3 p, vec3 a, vec3 b, float r1, float r2)
        # Point A: (0,0,0), R1: radius
        # Point B: (0,0,height), R2: 0.0
        
        # Vectorized RoundCone:
        a = np.array([0.0, 0.0, 0.0])
        b = np.array([0.0, 0.0, self.height])
        r1 = self.radius
        r2 = 0.0
        
        # pa = p - a
        pa = points - a
        # ba = b - a
        ba = b - a # (0,0,h)
        
        # l2 = dot(ba,ba)
        l2 = np.dot(ba, ba) # h^2
        
        # rr = r1 - r2
        rr = r1 - r2 # r1
        
        # a2 = l2 - rr*rr
        a2 = l2 - rr*rr
        
        # il2 = 1.0/l2
        il2 = 1.0 / (l2 + 1e-9)
        
        # pa * ba (dot product per row)
        pa_ba = np.dot(pa, ba) # (N,)
        
        # y = pa_ba
        y = pa_ba
        
        # x = sqrt( dot(pa,pa)*l2 - y*y )
        dot_pa_pa = np.sum(pa*pa, axis=1)
        x = np.sqrt(np.maximum(dot_pa_pa * l2 - y*y, 0.0))
        
        # y = y - l2 * clamp( (y*l2 + x*rr*sqrt(a2)) / (l2*a2), 0.0, 1.0 )
        if a2 < 1e-6:
             # Cylinder case or error
             k = 0.0
        else:
             k = np.clip((y * l2 + x * rr * np.sqrt(a2)) / (l2 * a2), 0.0, 1.0)
             
        # New p = pa - ba * k
        # ba * k needs shape (N, 3)
        k_vec = k.reshape(-1, 1)
        p_new = pa - ba * k_vec
        
        # d = length(p_new) - mix(r1, r2, k)
        d = np.linalg.norm(p_new, axis=1) - (r1 * (1.0 - k) + r2 * k)
        
        return d

    def _bounds_local(self):
        r = self.radius
        h = self.height
        return (np.array([-r, -r, 0]), np.array([r, r, h]))

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

        
    def _bounds_local(self):
        raise NotImplementedError

class SDFUnion(SDFOperation):
    def _combine(self, d1, d2):
        return np.minimum(d1, d2)
        
    def _bounds_local(self):
        min_a, max_a = self.sdf_a.bounds()
        min_b, max_b = self.sdf_b.bounds()
        return np.minimum(min_a, min_b), np.maximum(max_a, max_b)

class SDFDifference(SDFOperation):
    def _combine(self, d1, d2):
        return np.maximum(d1, -d2)
        
    def _bounds_local(self):
        # Difference bounds is at most A logic (conservative)
        return self.sdf_a.bounds()

class SDFIntersection(SDFOperation):
    def _combine(self, d1, d2):
        return np.maximum(d1, d2)
        
    def _bounds_local(self):
        min_a, max_a = self.sdf_a.bounds()
        min_b, max_b = self.sdf_b.bounds()
        return np.maximum(min_a, min_b), np.minimum(max_a, max_b)

