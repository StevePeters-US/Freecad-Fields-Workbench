
import sys
import os
import numpy as np
import time

# Mock FreeCAD for standalone testing
class MockVector:
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z
    def __repr__(self): return f"Vector({self.x}, {self.y}, {self.z})"

class MockMatrix:
    def __init__(self):
        self.A11 = 1; self.A12 = 0; self.A13 = 0; self.A14 = 0
        self.A21 = 0; self.A22 = 1; self.A23 = 0; self.A24 = 0
        self.A31 = 0; self.A32 = 0; self.A33 = 1; self.A34 = 0
        self.A41 = 0; self.A42 = 0; self.A43 = 0; self.A44 = 1
    def inverse(self): return self

class MockPlacement:
    def __init__(self):
        self.Matrix = MockMatrix()

import builtins
class MockFreeCADModule:
    Vector = MockVector
    Placement = MockPlacement
    class Console:
        @staticmethod
        def PrintError(msg): print("ERROR:", msg)
        @staticmethod
        def PrintWarning(msg): print("WARN:", msg)
        @staticmethod
        def PrintMessage(msg): print("MSG:", msg)

builtins.FreeCAD = MockFreeCADModule()

# Add path
sys.path.append(os.getcwd())

import FCDirectModeling.sdf_lib as sdf_lib

def test_cone_bounds():
    print("--- Testing Cone Bounds ---")
    r = 5.0
    h = 10.0
    cone = sdf_lib.SDFCone(r, h)
    
    b_min, b_max = cone.bounds()
    print(f"Cone (r={r}, h={h}) Bounds:")
    print(f"  Min: {b_min}")
    print(f"  Max: {b_max}")
    
    size = b_max - b_min
    print(f"  Size: {size}")
    
    # Check if bounds effectively cover the cone
    # Cone goes from z=0 to z=10. X/Y from -5 to 5.
    # Bounds should be close to (-5,-5,0) to (5,5,10).
    
    expected_size = np.array([10.0, 10.0, 10.0])
    if np.allclose(size, expected_size):
        print("Bounds Look CORRECT.")
    else:
        print("Bounds Look WRONG.")

    # Mesh Generation
    print("\n--- Generating Mesh (Res=32) ---")
    start = time.time()
    verts, faces, normals = sdf_lib.mesh_from_sdf(cone, resolution=32, margin=0.1)
    end = time.time()
    
    if verts is not None:
        print(f"Mesh Generated in {end-start:.4f}s")
        print(f"  Verts: {len(verts)}")
        print(f"  Faces: {len(faces)}")
        
        # Approximate step size
        # Grid size with margin
        margin_vec = size * 0.1
        grid_size = size + 2 * margin_vec
        step = grid_size / 31.0
        print(f"  Grid Step Size: {step}")
    else:
        print("FAIL: No mesh generated.")

def test_sphere_bounds():
    print("\n--- Testing Sphere Bounds ---")
    r = 5.0
    sphere = sdf_lib.SDFSphere(r)
    
    b_min, b_max = sphere.bounds()
    print(f"Sphere (r={r}) Bounds:")
    print(f"  Min: {b_min}")
    print(f"  Max: {b_max}")
    
    size = b_max - b_min
    print(f"  Size: {size}")
    
    print("\n--- Generating Mesh (Res=32) ---")
    start = time.time()
    verts, faces, normals = sdf_lib.mesh_from_sdf(sphere, resolution=32, margin=0.1)
    end = time.time()
    
    if verts is not None:
        print(f"Mesh Generated in {end-start:.4f}s")
        print(f"  Verts: {len(verts)}")
        print(f"  Faces: {len(faces)}")
        
        margin_vec = size * 0.1
        grid_size = size + 2 * margin_vec
        step = grid_size / 31.0
        print(f"  Grid Step Size: {step}")

if __name__ == "__main__":
    test_cone_bounds()
    test_sphere_bounds()
