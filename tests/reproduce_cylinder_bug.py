import numpy as np
import math
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Mock FreeCAD
class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z
    def __sub__(self, other):
        return Vector(self.x-other.x, self.y-other.y, self.z-other.z)
    def __add__(self, other):
        return Vector(self.x+other.x, self.y+other.y, self.z+other.z)
    def __mul__(self, other):
        if isinstance(other, (int, float)):
            return Vector(self.x*other, self.y*other, self.z*other)
        return Vector(self.x*other.x, self.y*other.y, self.z*other.z)
    def dot(self, other):
        return self.x*other.x + self.y*other.y + self.z*other.z
    @property
    def Length(self):
        return math.sqrt(self.x**2 + self.y**2 + self.z**2)
    def normalize(self):
        l = self.Length
        if l > 0:
            self.x /= l; self.y /= l; self.z /= l
    def __repr__(self):
        return f"Vector({self.x}, {self.y}, {self.z})"

class Matrix:
    def __init__(self, values):
        self.A11, self.A12, self.A13, self.A14 = values[0:4]
        self.A21, self.A22, self.A23, self.A24 = values[4:8]
        self.A31, self.A32, self.A33, self.A34 = values[8:12]
        self.A41, self.A42, self.A43, self.A44 = values[12:16]
    def invert(self):
        # Extremely simplified for this test (only supports translation/rotation if we are lucky)
        pass

class Placement:
    def __init__(self, pos=Vector(), rot=(0,0,0,1)):
        self.Base = pos
        self.Rotation = rot
    def inverse(self):
        return Placement(Vector(-self.Base.x, -self.Base.y, -self.Base.z))
    def multVec(self, v):
        return Vector(v.x + self.Base.x, v.y + self.Base.y, v.z + self.Base.z)
    def toMatrix(self):
        return Matrix([1,0,0,self.Base.x, 0,1,0,self.Base.y, 0,0,1,self.Base.z, 0,0,0,1])

sys.modules['FreeCAD'] = type('FreeCAD', (), {'Vector': Vector, 'Placement': Placement})
import FreeCAD

from core.frep.sdf.cylinder import SdfCylinderField

def test():
    # Base center at world (10, 0, 0). 
    # Workplane rotated 90 deg around X (Z becomes Y).
    # Rotation (axis-angle): X is (1, 0, 0), 90 deg.
    # New axes: X' = (1, 0, 0), Y' = (0, 0, 1), Z' = (0, -1, 0)
    
    # Simple Mock of a 90 deg X rotation Placement
    class RotPlacement(Placement):
        def inverse(self):
            # Invert: -90 deg around X.
            # Z' = (0, 1, 0)
            return RotPlacement(Vector(-self.Base.x, -self.Base.y, -self.Base.z), "inv")
        def multVec(self, v):
            # World to Local (using inverse) or Local to World
            if self.Rotation == "inv": # World to Local
                # p_local.x = p_world.x - 10
                # p_local.y = p_world.z
                # p_local.z = -p_world.y
                return Vector(v.x - self.Base.x, v.z, -v.y)
            else: # Local to World
                # p_world.x = p_local.x + 10
                # p_world.y = -p_local.z
                # p_world.z = p_local.y
                return Vector(v.x + self.Base.x, -v.z, v.y)
        def toMatrix(self):
            # X row: [1 0 0 Tx]
            # Y row: [0 0 -1 Ty]
            # Z row: [0 1 0 Tz]
            return Matrix([1,0,0,self.Base.x, 0,0,-1,self.Base.y, 0,1,0,self.Base.z, 0,0,0,1])

    placement = RotPlacement(Vector(10, 0, 0))
    
    # In CylinderCreator, we calculate loc_base by calling to_local(world_pt)
    # If I click at (10, 0, 0) in world:
    # loc_base = placement.inverse().multVec((10, 0, 0)) = (0, 0, 0)
    loc_base = Vector(0, 0, 0)
    loc_axis = Vector(0, 0, 1)
    radius = 5.0
    height = 20.0
    
    field = SdfCylinderField(loc_base, loc_axis, radius, height, placement=placement)
    
    print(f"Testing SdfCylinderField with 90 deg X rotation (Local Z is World Y)")
    
    # Mid-cylinder point in local space is (0, 0, 10)
    # Corresponding world point: x=10, y=-10, z=0
    pt_world = Vector(10, -10, 0)
    d = field.evaluate(pt_world)
    print(f"evaluate({pt_world}) = {d} (Expected ~ -5.0)")
    
    # Vectorized
    pts = np.array([
        [10, -10, 0],
        [10, 0, 0],   # Local (0,0,0) -> base center
        [10, -20, 0],  # Local (0,0,20) -> top center
        [10, -10, 5],  # Local (0,5,10) -> side edge
    ], dtype=np.float32)
    
    vals = field.evaluate_grid(pts)
    print(f"evaluate_grid values: {vals}")
    
    for i in range(len(pts)):
        v = Vector(pts[i][0], pts[i][1], pts[i][2])
        d_scalar = field.evaluate(v)
        print(f"Pt {pts[i]} scalar={d_scalar} grid={vals[i]}")

if __name__ == "__main__":
    test()
