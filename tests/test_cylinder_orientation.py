import numpy as np
import math
import sys
import os

# Add repo root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Mock FreeCAD
class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)
    def __sub__(self, other):
        return Vector(self.x-other.x, self.y-other.y, self.z-other.z)
    def __add__(self, other):
        return Vector(self.x+other.x, self.y+other.y, self.z+other.z)
    def __mul__(self, other):
        if isinstance(other, (int, float, np.float32, np.float64)):
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
    def __init__(self, values=None):
        if values is None:
            values = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]
        self.A11, self.A12, self.A13, self.A14 = values[0:4]
        self.A21, self.A22, self.A23, self.A24 = values[4:8]
        self.A31, self.A32, self.A33, self.A34 = values[8:12]
        self.A41, self.A42, self.A43, self.A44 = values[12:16]
    def invert(self):
        # Full 4x4 matrix inversion for 90deg X rotation with translation
        # m = [ 1 0  0 tx ]
        #     [ 0 0 -1 ty ]
        #     [ 0 1  0 tz ]
        #     [ 0 0  0 1  ]
        # In this test, we only care about the rotation part being inverted correctly.
        # R = [ 1 0  0 ]  R^T = [ 1 0 0 ]
        #     [ 0 0 -1 ]        [ 0 0 1 ]
        #     [ 0 1  0 ]        [ 0 -1 0 ]
        if self.A23 == -1 and self.A32 == 1:
            self.A23, self.A32 = 1, -1
        elif self.A23 == 1 and self.A32 == -1:
            self.A23, self.A32 = -1, 1
        
        # Invert translation: T' = -R^T * T
        tx, ty, tz = self.A14, self.A24, self.A34
        if self.A32 == 1: # R^T for 90deg X
            self.A14 = -tx
            self.A24 = -tz
            self.A34 = ty
        else: # R^T for -90deg X
            self.A14 = -tx
            self.A24 = tz
            self.A34 = -ty
    def multVec(self, v):
        x = self.A11*v.x + self.A12*v.y + self.A13*v.z + self.A14
        y = self.A21*v.x + self.A22*v.y + self.A23*v.z + self.A24
        z = self.A31*v.x + self.A32*v.y + self.A33*v.z + self.A34
        return Vector(x, y, z)

class Rotation:
    def __init__(self, axis=Vector(0,0,1), angle=0.0):
        self.axis = axis
        self.angle = angle
    def multVec(self, v):
        # Very limited rotation mock for 90deg X
        if self.axis.x == 1 and abs(self.angle - 90) < 1e-3:
            return Vector(v.x, -v.z, v.y)
        return v

class Placement:
    def __init__(self, pos=Vector(), rot=Rotation()):
        self.Base = pos
        self.Rotation = rot
    def inverse(self):
        # Inverse of (T, R) is (R^-1 * -T, R^-1)
        # For 90deg X: R inverse is -90deg X
        inv_rot = Rotation(self.Rotation.axis, -self.Rotation.angle)
        inv_pos = inv_rot.multVec(Vector(-self.Base.x, -self.Base.y, -self.Base.z))
        return Placement(inv_pos, inv_rot)
    def multVec(self, v):
        rv = self.Rotation.multVec(v)
        return Vector(rv.x + self.Base.x, rv.y + self.Base.y, rv.z + self.Base.z)
    def toMatrix(self):
        # Only support 90deg X rotation for this test
        if self.Rotation.axis.x == 1 and abs(self.Rotation.angle - 90) < 1e-3:
            return Matrix([1,0,0,self.Base.x, 0,0,-1,self.Base.y, 0,1,0,self.Base.z, 0,0,0,1])
        return Matrix([1,0,0,self.Base.x, 0,1,0,self.Base.y, 0,0,1,self.Base.z, 0,0,0,1])

sys.modules['FreeCAD'] = type('FreeCAD', (), {'Vector': Vector, 'Placement': Placement, 'Rotation': Rotation, 'Matrix': Matrix})
import FreeCAD

from core.frep.sdf.cylinder import SdfCylinderField

def test_orientation():
    # 1. Identity Placement (standard Z-up)
    print("--- Testing Identity Placement (Normal = Z) ---")
    field_id = SdfCylinderField(Vector(0,0,0), Vector(0,0,1), 10.0, 50.0)
    # Point at height 25 on the axis should have SDF -10
    d = field_id.evaluate(Vector(0, 0, 25))
    print(f"evaluate(0,0,25) = {d} (Expected -10.0)")
    
    # 2. Rotated Placement (90deg X, so Local Z is World Y)
    print("\n--- Testing 90deg X Rotation (Local Z = World Y) ---")
    # Front plane workplane: Normal is World -Y or Y depending on convention.
    # Let's say Local Z maps to World Y.
    rot = Rotation(Vector(1,0,0), 90) 
    placement = Placement(Vector(0,0,0), rot)
    
    # In CylinderCreator (accepted state):
    # loc_base = to_local(world_base) = (0,0,0)
    # loc_axis = (0,0,1)
    field_rot = SdfCylinderField(Vector(0,0,0), Vector(0,0,1), 10.0, 50.0, placement=placement)
    
    # Point at world (0, 25, 0) should be local (0, 0, 25)
    # Because Placement maps local (0,0,25) -> world (0, 25, 0) if Z maps to Y.
    # verify multVec: local(0,0,25) -> rot.multVec(0,0,25) = (0, -25, 0) ... wait.
    # If 90deg X: Local Y maps to -Z, Local Z maps to Y.
    # Verify: rot.multVec(0,0,1) = (0, -1, 0)? No, FreeCAD 90deg X rotation:
    # Right-hand rule around X: Y -> Z, Z -> -Y. Angle is +90.
    # Vector(0,1,0) rotated 90 around X: x stays 0, (y,z) rotated 90: (0,1).
    # Vector(0,0,1) rotated 90 around X: x stays 0, (y,z) rotated 90: (-1,0).
    # So Local Z (0,0,1) maps to World (0, -1, 0).
    
    d_rot = field_rot.evaluate(Vector(0, -25, 0))
    print(f"evaluate_rot(0, -25, 0) = {d_rot} (Expected -10.0)")
    
    # Point at world (0, 0, 25) should NOT be on the axis now.
    d_rot_off = field_rot.evaluate(Vector(0, 0, 25))
    print(f"evaluate_rot(0, 0, 25) = {d_rot_off} (Expected > 0, actually 15.0)")

    # 3. Test evaluate_grid
    print("\n--- Testing evaluate_grid (Vectorized) ---")
    pts = np.array([
        [0, 0, 25],   # World Z point (off axis if rotated 90deg X)
        [0, -25, 0],  # World -Y point (on axis if rotated 90deg X)
    ], dtype=np.float32)
    d_grid = field_rot.evaluate_grid(pts)
    print(f"evaluate_grid values: {d_grid}")
    # d_grid[0] should match d_rot_off, d_grid[1] should match d_rot

    # 4. Test to_glsl
    print("\n--- Testing to_glsl ---")
    class MockCtx:
        def __init__(self): self.uniforms = []
        def uniform(self, t, v):
            self.uniforms.append((t, v))
            return f"U{len(self.uniforms)-1}"
        def need_helper(self, n): pass
    
    ctx = MockCtx()
    glsl = field_rot.to_glsl(ctx)
    print(f"GLSL: {glsl}")
    for i, (t, v) in enumerate(ctx.uniforms):
        print(f"  U{i} ({t}): {v}")

if __name__ == "__main__":
    test_orientation()
