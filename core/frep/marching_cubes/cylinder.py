import numpy as np
import FreeCAD
import math
from core.frep.marching_cubes.mc_field import MarchingCubesField

class MCCylinderField(MarchingCubesField):
    """A finite cylinder exact SDF."""
    def __init__(self, base_center: FreeCAD.Vector, axis: FreeCAD.Vector, radius: float, height: float):
        self.base_center = base_center
        self.axis = axis
        self.axis.normalize()
        self.radius = radius
        self.height = height

    def evaluate(self, point: FreeCAD.Vector) -> float:
        pa = point - self.base_center
        h = pa.dot(self.axis)
        radial_vec = pa - self.axis * h
        d_radial = radial_vec.Length - self.radius
        h_center = h - (self.height / 2.0)
        d_axial = abs(h_center) - (self.height / 2.0)
        out_dist = FreeCAD.Vector(max(d_radial, 0.0), max(d_axial, 0.0), 0).Length
        in_dist = min(max(d_radial, d_axial), 0.0)
        return out_dist + in_dist

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        """Vectorized cylinder SDF over (N, 3) points."""
        c = np.array([self.base_center.x, self.base_center.y, self.base_center.z])
        ax = np.array([self.axis.x, self.axis.y, self.axis.z])

        pa = points - c                             # (N, 3) vectors from base center
        h = pa @ ax                                 # (N,) projection along axis
        radial = pa - np.outer(h, ax)              # (N, 3) radial component
        d_radial = np.linalg.norm(radial, axis=1) - self.radius  # (N,)

        h_center = h - self.height / 2.0
        d_axial = np.abs(h_center) - self.height / 2.0            # (N,)

        d_r_pos = np.maximum(d_radial, 0.0)
        d_a_pos = np.maximum(d_axial, 0.0)
        out_dist = np.sqrt(d_r_pos**2 + d_a_pos**2)
        in_dist = np.minimum(np.maximum(d_radial, d_axial), 0.0)
        return (out_dist + in_dist).astype(np.float32)

    def bounding_box(self):
        center = self.base_center + self.axis * (self.height / 2.0)
        R = math.sqrt(self.radius**2 + (self.height / 2.0)**2)
        r_vec = FreeCAD.Vector(R, R, R)
        return (center - r_vec, center + r_vec)
