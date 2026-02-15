
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
            
        # Transform verts from grid indices to world coordinates
        # Grid step
        step = (grid_max - grid_min) / (resolution - 1)
        
        real_verts = verts * step + grid_min
        
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

