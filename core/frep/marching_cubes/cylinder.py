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
        # Vector from base to point
        pa = point - self.base_center
        # Distance along axis
        h = pa.dot(self.axis)
        
        # Radial vector relative to axis
        radial_vec = pa - self.axis * h
        d_radial = radial_vec.Length - self.radius
        
        # Caps SDF (distance from the [0, height] interval)
        h_center = h - (self.height / 2.0)
        d_axial = abs(h_center) - (self.height / 2.0)
        
        out_dist = FreeCAD.Vector(max(d_radial, 0.0), max(d_axial, 0.0), 0).Length
        in_dist = min(max(d_radial, d_axial), 0.0)
        return out_dist + in_dist

    def bounding_box(self):
        center = self.base_center + self.axis * (self.height / 2.0)
        R = math.sqrt(self.radius**2 + (self.height / 2.0)**2)
        r_vec = FreeCAD.Vector(R, R, R)
        return (center - r_vec, center + r_vec)
