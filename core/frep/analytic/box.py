import numpy as np
import FreeCAD
from core.frep.analytic.analytic_field import AnalyticField

class AnalyticBoxField(AnalyticField):
    """An axis-aligned box SDF, optionally placed arbitrarily in space."""
    def __init__(self, center: FreeCAD.Vector, size: FreeCAD.Vector, placement: FreeCAD.Placement = None):
        self.center = center
        self.half_size = FreeCAD.Vector(abs(size.x)/2, abs(size.y)/2, abs(size.z)/2)
        self.placement = placement
        
        self.inv_matrix = None
        if self.placement is not None:
             m = self.placement.toMatrix()
             m.invert()
             self.inv_matrix = np.array([
                 [m.A11, m.A12, m.A13, m.A14],
                 [m.A21, m.A22, m.A23, m.A24],
                 [m.A31, m.A32, m.A33, m.A34],
                 [m.A41, m.A42, m.A43, m.A44]
             ], dtype=np.float32)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        local_pt = point
        if self.placement is not None:
            local_pt = self.placement.inverse().multVec(point)
            
        dx = abs(local_pt.x - self.center.x) - self.half_size.x
        dy = abs(local_pt.y - self.center.y) - self.half_size.y
        dz = abs(local_pt.z - self.center.z) - self.half_size.z

        out_dist = FreeCAD.Vector(max(dx, 0), max(dy, 0), max(dz, 0)).Length
        in_dist = min(max(dx, max(dy, dz)), 0.0)
        return out_dist + in_dist

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        """Vectorized SDF evaluation over (N,3) array of points."""
        if self.inv_matrix is not None:
            N = points.shape[0]
            pts_hom = np.hstack((points, np.ones((N, 1), dtype=np.float32)))
            local_pts = (pts_hom @ self.inv_matrix.T)[:, :3]
        else:
            local_pts = points

        c = np.array([self.center.x, self.center.y, self.center.z])
        h = np.array([self.half_size.x, self.half_size.y, self.half_size.z])
        # d[i] = |pts[i] - center| - half_size
        d = np.abs(local_pts - c) - h
        # Outside distance: length of positive components
        out_dist = np.linalg.norm(np.maximum(d, 0.0), axis=1)
        # Inside distance: negative if fully inside
        in_dist = np.minimum(np.max(d, axis=1), 0.0)
        return (out_dist + in_dist).astype(np.float32)

    def bounding_box(self):
        c = self.center
        h = self.half_size
        corners_local = [
            c + FreeCAD.Vector(-h.x, -h.y, -h.z),
            c + FreeCAD.Vector( h.x, -h.y, -h.z),
            c + FreeCAD.Vector(-h.x,  h.y, -h.z),
            c + FreeCAD.Vector( h.x,  h.y, -h.z),
            c + FreeCAD.Vector(-h.x, -h.y,  h.z),
            c + FreeCAD.Vector( h.x, -h.y,  h.z),
            c + FreeCAD.Vector(-h.x,  h.y,  h.z),
            c + FreeCAD.Vector( h.x,  h.y,  h.z)
        ]
        
        if self.placement is not None:
            corners = [self.placement.multVec(pt) for pt in corners_local]
        else:
            corners = corners_local
            
        min_x = min(pt.x for pt in corners)
        min_y = min(pt.y for pt in corners)
        min_z = min(pt.z for pt in corners)
        
        max_x = max(pt.x for pt in corners)
        max_y = max(pt.y for pt in corners)
        max_z = max(pt.z for pt in corners)
        
        return (FreeCAD.Vector(min_x, min_y, min_z), FreeCAD.Vector(max_x, max_y, max_z))
