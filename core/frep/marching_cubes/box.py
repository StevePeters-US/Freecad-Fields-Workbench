import numpy as np
import FreeCAD
from core.frep.marching_cubes.mc_field import MarchingCubesField

class MCBoxField(MarchingCubesField):
    """An exact axis-aligned box SDF."""
    def __init__(self, center: FreeCAD.Vector, size: FreeCAD.Vector):
        self.center = center
        self.half_size = FreeCAD.Vector(abs(size.x)/2, abs(size.y)/2, abs(size.z)/2)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        dx = abs(point.x - self.center.x) - self.half_size.x
        dy = abs(point.y - self.center.y) - self.half_size.y
        dz = abs(point.z - self.center.z) - self.half_size.z

        out_dist = FreeCAD.Vector(max(dx, 0), max(dy, 0), max(dz, 0)).Length
        in_dist = min(max(dx, max(dy, dz)), 0.0)
        return out_dist + in_dist

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        """Vectorized SDF evaluation over (N,3) array of points."""
        c = np.array([self.center.x, self.center.y, self.center.z])
        h = np.array([self.half_size.x, self.half_size.y, self.half_size.z])
        # d[i] = |pts[i] - center| - half_size
        d = np.abs(points - c) - h
        # Outside distance: length of positive components
        out_dist = np.linalg.norm(np.maximum(d, 0.0), axis=1)
        # Inside distance: negative if fully inside
        in_dist = np.minimum(np.max(d, axis=1), 0.0)
        return (out_dist + in_dist).astype(np.float32)

    def bounding_box(self):
        return (self.center - self.half_size, self.center + self.half_size)
