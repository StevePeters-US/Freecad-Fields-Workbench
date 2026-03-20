"""Unit test for SDF Slicer logic — runs without FreeCAD."""
import sys, os
from types import ModuleType
import numpy as np

# Minimal FreeCAD stubs
fc = ModuleType("FreeCAD")
class _Vec:
    def __init__(self, x=0, y=0, z=0):
        if isinstance(x, _Vec):
            self.x, self.y, self.z = x.x, x.y, x.z
        else:
            self.x=x; self.y=y; self.z=z
    def __add__(self, o): return _Vec(self.x+o.x, self.y+o.y, self.z+o.z)
    def __sub__(self, o): return _Vec(self.x-o.x, self.y-o.y, self.z-o.z)
    def __mul__(self, s): return _Vec(self.x*s, self.y*s, self.z*s)
    def __neg__(self): return _Vec(-self.x, -self.y, -self.z)
    def normalize(self):
        l = self.Length
        if l > 1e-6:
            self.x/=l; self.y/=l; self.z/=l
    def cross(self, o):
        return _Vec(self.y*o.z-self.z*o.y, self.z*o.x-self.x*o.z, self.x*o.y-self.y*o.x)
    def dot(self, o):
        return self.x*o.x + self.y*o.y + self.z*o.z
    @property
    def Length(self): return (self.x**2+self.y**2+self.z**2)**0.5
fc.Vector = _Vec
sys.modules["FreeCAD"] = fc

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.sdf.sdf_slicer import _chain_segments, slice_sdf, fit_dm_curve
from core.sdf.sdf.sphere import SdfSphereField

def test_chain_segments():
    """Test chaining unordered segments into a polyline."""
    # A simple square: (0,0)-(1,0), (1,0)-(1,1), (1,1)-(0,1), (0,1)-(0,0)
    # Shuffled
    segments = [
        ((1, 0), (1, 1)),
        ((0, 1), (0, 0)),
        ((0, 0), (1, 0)),
        ((1, 1), (0, 1)),
    ]
    contours = _chain_segments(segments)
    assert len(contours) == 1
    assert len(contours[0]) == 5 # 4 segments, 5 points
    # First and last should be same (closed)
    assert contours[0][0] == contours[0][4]
    print("PASS: test_chain_segments")

def test_slice_sphere():
    """Slice a sphere and verify some basic properties."""
    sphere = SdfSphereField(center=fc.Vector(0,0,0), radius=10.0)
    origin = fc.Vector(0,0,0) # Center slice
    normal = fc.Vector(0,0,1) # Z-plane
    
    contours = slice_sdf(sphere, origin, normal, resolution=1.0)
    assert len(contours) == 1
    contour = contours[0]
    
    # Should be roughly circular with radius 10
    for p in contour:
        assert abs(p.Length - 10.0) < 1.0 # 1mm res error tolerance
        assert abs(p.z) < 1e-6 # Must be on the plane
    
    # Check closure
    assert (contour[0] - contour[-1]).Length < 0.1
    print("PASS: test_slice_sphere")

def test_fit_dm_curve():
    """Test fitting DM curve parameters."""
    pts = [fc.Vector(0,0,0), fc.Vector(10,0,0), fc.Vector(10,10,0), fc.Vector(0,10,0)]
    # closed
    pts.append(fc.Vector(0,0,0))
    
    params = fit_dm_curve(pts, closed=True)
    assert "Points" in params
    assert "HandleIn" in params
    assert "HandleOut" in params
    assert params["Closed"] == True
    assert params["is_closed"] == True
    assert len(params["Points"]) == 4 # One point removed due to closure
    
    # Check tangents exist
    for i in range(len(params["Points"])):
        assert (params["HandleIn"][i] - params["Points"][i]).Length > 0
        assert (params["HandleOut"][i] - params["Points"][i]).Length > 0
    print("PASS: test_fit_dm_curve")

if __name__ == "__main__":
    try:
        test_chain_segments()
        test_slice_sphere()
        test_fit_dm_curve()
        print("\nAll SDF Slicer tests passed.")
    except Exception as e:
        print(f"Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
