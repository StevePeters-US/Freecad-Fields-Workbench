import numpy as np
from FCDirectModeling.sdf_lib import SDFObject

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
