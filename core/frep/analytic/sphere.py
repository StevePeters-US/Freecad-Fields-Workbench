import numpy as np
import FreeCAD
from core.frep.marching_cubes.mc_field import MarchingCubesField

class MCSphereField(MarchingCubesField):
    """An exact analytical sphere SDF."""
    def __init__(self, center: FreeCAD.Vector, radius: float):
        self.center = center
        self.radius = radius

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return (point - self.center).Length - self.radius

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        """Vectorized SDF: distance from center minus radius."""
        c = np.array([self.center.x, self.center.y, self.center.z])
        return (np.linalg.norm(points - c, axis=1) - self.radius).astype(np.float32)

    def gradient(self, point: FreeCAD.Vector, h: float = 1e-4) -> FreeCAD.Vector:
        v = point - self.center
        L = v.Length
        if L < 1e-6:
            return FreeCAD.Vector(0, 0, 1)
        return v * (1.0 / L)

    def bounding_box(self):
        r_vec = FreeCAD.Vector(self.radius, self.radius, self.radius)
        return (self.center - r_vec, self.center + r_vec)
