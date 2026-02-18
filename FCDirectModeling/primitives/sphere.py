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
