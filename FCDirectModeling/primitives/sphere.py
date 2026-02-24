import numpy as np
from FCDirectModeling.sdf_lib import SDFObject

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

    def get_vertices(self):
        # Spheres have no distinct vertices (corners).
        return np.zeros((0, 3))

    def get_edges(self):
        # 3 intersecting orthogonal circles
        theta = np.linspace(0, 2 * np.pi, 64)
        c = np.cos(theta) * self.radius
        s = np.sin(theta) * self.radius
        z = np.zeros_like(theta)
        
        xy_circle = np.column_stack([c, s, z])
        xz_circle = np.column_stack([c, z, s])
        yz_circle = np.column_stack([z, c, s])
        
        return [xy_circle, xz_circle, yz_circle]
