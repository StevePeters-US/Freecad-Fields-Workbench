
import numpy as np
try:
    from skimage import measure
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False

class SDFObject:
    def __init__(self):
        pass

    def evaluate(self, points):
        """
        Evaluate the signed distance function at the given points.
        points: (N, 3) numpy array of points.
        Returns: (N,) numpy array of signed distances.
        """
        raise NotImplementedError

    def bounds(self):
        """
        Return the axis-aligned bounding box of the object.
        Returns: ((min_x, min_y, min_z), (max_x, max_y, max_z))
        """
        raise NotImplementedError

class SDFBox(SDFObject):
    def __init__(self, size):
        super().__init__()
        # size is (width, depth, height) - half extents for calculation?
        # Usually box SDF is defined by half-sizes (radii)
        if isinstance(size, (int, float)):
             self.half_size = np.array([size, size, size]) / 2.0
        else:
             self.half_size = np.array(size) / 2.0
             
    def evaluate(self, points):
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
    if not HAS_SKIMAGE:
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
