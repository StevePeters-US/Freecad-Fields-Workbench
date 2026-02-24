import numpy as np
from FCDirectModeling.sdf_lib import SDFObject

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

    def get_vertices(self):
        b = self.half_size
        return np.array([
            [b[0], b[1], b[2]],
            [-b[0], b[1], b[2]],
            [b[0], -b[1], b[2]],
            [-b[0], -b[1], b[2]],
            [b[0], b[1], -b[2]],
            [-b[0], b[1], -b[2]],
            [b[0], -b[1], -b[2]],
            [-b[0], -b[1], -b[2]]
        ])

    def get_edges(self):
        b = self.half_size
        edges = []
        # Top face edges
        edges.append(np.array([[b[0], b[1], b[2]], [-b[0], b[1], b[2]]]))
        edges.append(np.array([[-b[0], b[1], b[2]], [-b[0], -b[1], b[2]]]))
        edges.append(np.array([[-b[0], -b[1], b[2]], [b[0], -b[1], b[2]]]))
        edges.append(np.array([[b[0], -b[1], b[2]], [b[0], b[1], b[2]]]))
        
        # Bottom face edges
        edges.append(np.array([[b[0], b[1], -b[2]], [-b[0], b[1], -b[2]]]))
        edges.append(np.array([[-b[0], b[1], -b[2]], [-b[0], -b[1], -b[2]]]))
        edges.append(np.array([[-b[0], -b[1], -b[2]], [b[0], -b[1], -b[2]]]))
        edges.append(np.array([[b[0], -b[1], -b[2]], [b[0], b[1], -b[2]]]))
        
        # Pillars
        edges.append(np.array([[b[0], b[1], b[2]], [b[0], b[1], -b[2]]]))
        edges.append(np.array([[-b[0], b[1], b[2]], [-b[0], b[1], -b[2]]]))
        edges.append(np.array([[-b[0], -b[1], b[2]], [-b[0], -b[1], -b[2]]]))
        edges.append(np.array([[b[0], -b[1], b[2]], [b[0], -b[1], -b[2]]]))
        
        return edges
