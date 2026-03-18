import numpy as np
import sys
import os
from types import ModuleType

# Minimal FreeCAD stubs
fc = ModuleType("FreeCAD")
class _Vec:
    def __init__(self, x=0, y=0, z=0): self.x=x; self.y=y; self.z=z
    def __repr__(self): return f"Vector({self.x}, {self.y}, {self.z})"
    def __add__(self, other): return _Vec(self.x+other.x, self.y+other.y, self.z+other.z)
    def __sub__(self, other): return _Vec(self.x-other.x, self.y-other.y, self.z-other.z)
    def __mul__(self, s): return _Vec(self.x*s, self.y*s, self.z*s)
    def __rmul__(self, s): return self * s
    def __truediv__(self, s): return _Vec(self.x/s, self.y/s, self.z/s)
    def __neg__(self): return _Vec(-self.x, -self.y, -self.z)
    @property
    def Length(self): return np.sqrt(self.x**2 + self.y**2 + self.z**2)
fc.Vector = _Vec
class _Param:
    def GetBool(self, name, default): return default
    def GetFloat(self, name, default): return default
    def GetInt(self, name, default): return default
fc.ParamGet = lambda path: _Param()
sys.modules["FreeCAD"] = fc

part = ModuleType("Part")
sys.modules["Part"] = part

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.dm_mesher import SurfaceNetsMesher
from core.frep.sdf.sphere import SdfSphereField

def test_sn_relaxation_quality():
    print("Testing SurfaceNets relaxation on a sphere...", flush=True)
    
    radius = 10.0
    center = fc.Vector(0, 0, 0)
    field = SdfSphereField(center=center, radius=radius)
    mesher = SurfaceNetsMesher()
    cell_size = 1.0
    
    # Run meshing (decimate/deduplicate are False by default in this test)
    verts, idx = mesher.mesh(field, cell_size, decimate=False, deduplicate=False)
    
    print(f"Generated {len(verts)} vertices, {len(idx)//4} triangles", flush=True)
    
    # Calculate distance of each vertex from origin
    dist = np.linalg.norm(verts, axis=1)
    mean_dist = np.mean(dist)
    std_dist = np.std(dist)
    
    print(f"Mean distance from origin: {mean_dist:.4f} (Ideal: {radius})", flush=True)
    print(f"Std dev of distance: {std_dist:.4f}", flush=True)
    
    # With relaxation + projection, the radius should be very accurate
    # and the surface should be smooth (low variance in distance).
    assert abs(mean_dist - radius) < 0.1, f"Mesh radius {mean_dist} too far from {radius}"
    assert std_dist < 0.05, f"Mesh vertices not smooth (std dev {std_dist})"
    
    print("Test passed: SurfaceNets produced a high quality sphere mesh via relaxation.", flush=True)

if __name__ == "__main__":
    try:
        # (Monkeypatching removed as mesher now takes local arguments)
        
        test_sn_relaxation_quality()
        print("\nSurfaceNets relaxation test passed!", flush=True)
    except Exception as e:
        print(f"\nTest FAILED: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
