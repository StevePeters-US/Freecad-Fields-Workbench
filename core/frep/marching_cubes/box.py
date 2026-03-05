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

    def bounding_box(self):
        return (self.center - self.half_size, self.center + self.half_size)
