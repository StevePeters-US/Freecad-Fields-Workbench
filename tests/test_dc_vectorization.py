import numpy as np
import sys
import os
import time
from types import ModuleType

# Minimal FreeCAD stubs
fc = ModuleType("FreeCAD")
class _Vec:
    def __init__(self, x=0, y=0, z=0): self.x=float(x); self.y=float(y); self.z=float(z)
    def __repr__(self): return f"Vector({self.x}, {self.y}, {self.z})"
    def __add__(self, other): return _Vec(self.x+other.x, self.y+other.y, self.z+other.z)
    def __sub__(self, other): return _Vec(self.x-other.x, self.y-other.y, self.z-other.z)
    def __mul__(self, scalar): return _Vec(self.x*scalar, self.y*scalar, self.z*scalar)
    def __rmul__(self, scalar): return self.__mul__(scalar)
    @property
    def Length(self): return np.sqrt(self.x**2 + self.y**2 + self.z**2)
    def normalize(self):
        l = self.Length
        if l > 1e-12: self.x /= l; self.y /= l; self.z /= l
fc.Vector = _Vec
fc.ParamGet = lambda path: ModuleType("Param")
sys.modules["FreeCAD"] = fc

part = ModuleType("Part")
sys.modules["Part"] = part

# Add the project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock preferences
import core.dm_object
core.dm_object.get_decimate_enabled = lambda: False
core.dm_object.get_deduplicate_enabled = lambda: False

from core.dm_mesher import DualContouringMesher, mesh_timer
from core.frep.frep_field import FRepField

class SphereField(FRepField):
    def __init__(self, radius=10.0):
        self.radius = radius
    def evaluate(self, point):
        return np.sqrt(point.x**2 + point.y**2 + point.z**2) - self.radius
    def evaluate_grid(self, points):
        return np.linalg.norm(points, axis=1) - self.radius
    def bounding_box(self):
        return fc.Vector(-11, -11, -11), fc.Vector(11, 11, 11)

def run_benchmark():
    field = SphereField(radius=10.0)
    mesher = DualContouringMesher()
    
    print("Running DC Benchmark (Vectorized Implementation)...")
    
    resolutions = [2.0, 1.0, 0.5]
    for res in resolutions:
        print(f"\nResolution: {res}")
        start_time = time.time()
        mesh_timer.tick()
        result = mesher.mesh(field, res)
        end_time = time.time()
        
        if result:
            verts, idx = result
            print(f"  Triangles: {len(idx)//4}")
            print(f"  Vertices: {len(verts)}")
            print(f"  Total Time: {end_time - start_time:.4f}s")
            # The mesh_timer timings aren't easily accessible from outside unless we print them
            # Let's hope it just works for now.
        else:
            print("  Mesh failed!")

if __name__ == "__main__":
    run_benchmark()
