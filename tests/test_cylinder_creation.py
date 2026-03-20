import numpy as np
import math
import sys
import os

# Add repo root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Mock FreeCAD and necessary parts of DM
class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        if isinstance(x, (tuple, list)): self.x, self.y, self.z = map(float, x)
        else: self.x, self.y, self.z = float(x), float(y), float(z)
    def __sub__(self, other): return Vector(self.x-other.x, self.y-other.y, self.z-other.z)
    def __add__(self, other): return Vector(self.x+other.x, self.y+other.y, self.z+other.z)
    def __mul__(self, other):
        if isinstance(other, (int, float, np.float32, np.float64)): return Vector(self.x*other, self.y*other, self.z*other)
        return Vector(self.x*other.x, self.y*other.y, self.z*other.z)
    def dot(self, other): return self.x*other.x + self.y*other.y + self.z*other.z
    @property
    def Length(self): return math.sqrt(self.x**2 + self.y**2 + self.z**2)
    def normalize(self):
        l = self.Length
        if l > 1e-9: self.x /= l; self.y /= l; self.z /= l
    def __repr__(self): return f"Vector({self.x}, {self.y}, {self.z})"
    def __getitem__(self, i):
        if i == 0: return self.x
        if i == 1: return self.y
        if i == 2: return self.z
        raise IndexError()

class Matrix:
    def __init__(self, values=None):
        if values is None: values = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]
        self.A11, self.A12, self.A13, self.A14 = values[0:4]
        self.A21, self.A22, self.A23, self.A24 = values[4:8]
        self.A31, self.A32, self.A33, self.A34 = values[8:12]
        self.A41, self.A42, self.A43, self.A44 = values[12:16]
    def invert(self):
        # 90deg X inverse for this test specifically
        if self.A23 == -1 and self.A32 == 1: # Original 90deg X
            self.A23, self.A32 = 1, -1
        elif self.A23 == 1 and self.A32 == -1: # Already inverted
            self.A23, self.A32 = -1, 1
    def multVec(self, v):
        x = self.A11*v.x + self.A12*v.y + self.A13*v.z + self.A14
        y = self.A21*v.x + self.A22*v.y + self.A23*v.z + self.A24
        z = self.A31*v.x + self.A32*v.y + self.A33*v.z + self.A34
        return Vector(x, y, z)

class Rotation:
    def __init__(self, axis=Vector(0,0,1), angle=0.0):
        self.axis, self.angle = axis, angle
    def multVec(self, v):
        if self.axis.x == 1 and abs(self.angle - 90) < 1e-3: return Vector(v.x, -v.z, v.y)
        if self.axis.x == 1 and abs(self.angle + 90) < 1e-3: return Vector(v.x, v.z, -v.y)
        return v

class Placement:
    def __init__(self, pos=Vector(), rot=Rotation()):
        self.Base, self.Rotation = pos, rot
    def inverse(self):
        inv_rot = Rotation(self.Rotation.axis, -self.Rotation.angle)
        inv_pos = inv_rot.multVec(Vector(-self.Base.x, -self.Base.y, -self.Base.z))
        return Placement(inv_pos, inv_rot)
    def multVec(self, v):
        rv = self.Rotation.multVec(v); return Vector(rv.x + self.Base.x, rv.y + self.Base.y, rv.z + self.Base.z)
    def toMatrix(self):
        if self.Rotation.axis.x == 1 and abs(self.Rotation.angle - 90) < 1e-3:
            return Matrix([1,0,0,self.Base.x, 0,0,-1,self.Base.y, 0,1,0,self.Base.z, 0,0,0,1])
        return Matrix([1,0,0,self.Base.x, 0,1,0,self.Base.y, 0,0,1,self.Base.z, 0,0,0,1])

sys.modules['FreeCAD'] = type('FreeCAD', (), {'Vector': Vector, 'Placement': Placement, 'Rotation': Rotation, 'Matrix': Matrix, 'Console': type('Console', (), {'PrintMessage': print, 'PrintWarning': print, 'PrintError': print})})
sys.modules['FreeCADGui'] = type('FreeCADGui', (), {'Selection': type('Selection', (), {'clearSelection': lambda: None, 'addSelection': lambda x: None}), 'updateGui': lambda: None})
sys.modules['Part'] = type('Part', (), {'Shape': lambda: type('Shape', (), {'isNull': lambda: False})})

from core.sdf.sdf.cylinder import SdfCylinderField

def test_cylinder_flow():
    print("--- Simulating CylinderCreator workflow on Front plane (90deg X) ---")
    
    # Workplane: 90 deg X rotation (Local Z is World -Y)
    wp = Placement(Vector(10, 20, 30), Rotation(Vector(1, 0, 0), 90))
    
    # Mock to_local and to_global logic from DMBase
    def to_local(p):
        mat = wp.toMatrix()
        mat.invert()
        return mat.multVec(p)
    
    # 1. User clicks base point at World (10, 20, 30) (on workplane origin)
    world_p0 = Vector(10, 20, 30)
    loc_base = to_local(world_p0)
    print(f"Click 1 (Base): World{world_p0} -> Local{loc_base} (Expected Vector(0,0,0))")
    
    # 2. User clicks radius point at World (20, 20, 30) (shifted in World X)
    world_p1 = Vector(20, 20, 30)
    loc_p1 = to_local(world_p1)
    radius = math.sqrt((loc_p1.x - loc_base.x)**2 + (loc_p1.y - loc_base.y)**2)
    print(f"Click 2 (Radius): World{world_p1} -> Local{loc_p1}, radius={radius} (Expected 10.0)")
    
    # 3. User clicks height point. Normal for Front plane is (0, -1, 0).
    # Drag from radius point (20, 20, 30) by 50mm along normal -> (20, 20 - 50, 30) = (20, -30, 30)
    world_p2 = Vector(20, -30, 30)
    loc_p2 = to_local(world_p2)
    height = (loc_p2 - loc_base).z
    print(f"Click 3 (Height): World{world_p2} -> Local{loc_p2}, height={height} (Expected 50.0)")
    
    # 4. Resulting SdfCylinderField
    loc_axis = Vector(0, 0, 1)
    field = SdfCylinderField(loc_base, loc_axis, radius, height, placement=wp)
    
    print("\n--- Verifying resulting SdfCylinderField ---")
    # Point at "top" of cylinder: World (10, -30, 30)
    # This is Local (0, 0, 50). pa = (0,0,50). h = 50. d_axial = 0. d_radial = -10. SDF = -10.
    world_top = Vector(10, -30, 30)
    d = field.evaluate(world_top)
    print(f"evaluate(World Top {world_top}) = {d} (Expected -10.0)")
    
    # World Z point: Vector(10, 20, 80)
    # Local: wp_inv * (10, 20, 80 - 30) = ??
    # Let's check Local distance for World Z point
    world_z_offset = Vector(10, 20, 80)
    d_z = field.evaluate(world_z_offset)
    print(f"evaluate(World Z point {world_z_offset}) = {d_z} (Expected > 0, actually 15.0?)")

if __name__ == "__main__":
    test_cylinder_flow()
