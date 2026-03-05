import FreeCAD
from core.frep.marching_cubes.mc_field import MarchingCubesField

class MCPlaneField(MarchingCubesField):
    """A half-space field divided by an infinite plane."""
    def __init__(self, normal: FreeCAD.Vector, origin: FreeCAD.Vector):
        self.normal = normal
        self.normal.normalize()
        self.origin = origin

    def evaluate(self, point: FreeCAD.Vector) -> float:
        return (point - self.origin).dot(self.normal)

    def gradient(self, point: FreeCAD.Vector, h: float = 1e-4) -> FreeCAD.Vector:
        return self.normal
