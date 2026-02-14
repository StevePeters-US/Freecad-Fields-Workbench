
import numpy as np

# Remove top-level import to avoid lazy_loader crash
HAS_SKIMAGE = False 

class SDFObject:
    def __init__(self):
        self.inverse_matrix = None

    def set_placement(self, placement):
        """
        Set the placement (transformation) of the SDF object.
        placement: FreeCAD.Placement or matrix-like object.
        """
        # Get the matrix (4x4)
        # We need the inverse to transform world points to local points
        mat = placement.Matrix
        # FreeCAD Matrix is row-major or column-major? 
        # API: mat.A11, mat.A12... 
        # We can extract values to numpy
        
        # Invert the matrix for world->local transform
        inv = mat.inverse()
        
        # Create numpy 4x4 matrix
        self.inverse_matrix = np.array([
            [inv.A11, inv.A12, inv.A13, inv.A14],
            [inv.A21, inv.A22, inv.A23, inv.A24],
            [inv.A31, inv.A32, inv.A33, inv.A34],
            [inv.A41, inv.A42, inv.A43, inv.A44]
        ]).transpose() # Transpose because numpy multiplies (N,4) x (4,4) 

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
        Returns: ((min_x, min_y, min_y), (max_x, max_y, max_z))
        """
        # Note: If rotated, AABB should likely be larger or transformed.
        # For now we return untransformed bounds, caller might need to handle OBB.
        # Or we implement a method to get transformed AABB.
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

    def bounds(self):
        return (-self.half_size, self.half_size)

def mesh_from_sdf(sdf_obj, resolution=32, margin=0.1):
    """
    Generate a mesh from an SDF object using Marching Cubes.
    """
    try:
        from skimage import measure
    except (ImportError, AttributeError, Exception) as e:
        print(f"SDF Warning: Skimage import failed (meshing disabled): {e}")
        return None, None
        
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
    # For large grids, might need chunking
    values = sdf_obj.evaluate(points)
    
    # Reshape back to grid
    vol = values.reshape((resolution, resolution, resolution))
    
    # Marching Cubes
    # level=0.0 is the surface
    try:
        verts, faces, normals, values = measure.marching_cubes(vol, level=0.0)
        
        # Transform verts from grid indices to world coordinates
        # Grid step
        step = (grid_max - grid_min) / (resolution - 1)
        
        real_verts = verts * step + grid_min
        
        return real_verts, faces
        
    except ValueError:
        # Surface level not found within volume
        return None, None
