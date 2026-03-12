import numpy as np
import sys
import os
import math
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
sys.modules["FreeCAD"] = fc

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.frep.sdf_baker import bake_sdf_to_atlas
from core.frep.sdf.sphere import SdfSphereField

def test_sdf_baker():
    print("Testing SDF Baker...", flush=True)
    
    radius = 10.0
    center = fc.Vector(0, 0, 0)
    field = SdfSphereField(center=center, radius=radius)
    cell_size = 2.0  # Large cell size for small atlas in test
    
    # Run baker
    baked = bake_sdf_to_atlas(field, cell_size)
    
    # 1. Check metadata keys
    required_keys = ["atlas_bytes", "atlas_w", "atlas_h", "nx", "ny", "nz", "atz", "bbox_min", "bbox_max", "max_dist"]
    for key in required_keys:
        assert key in baked, f"Missing key: {key}"
    
    # 2. Check grid dimensions
    # Original bbox for sphere R=10 at (0,0,0) is (-10,-10,-10) to (10,10,10)
    # _pad adds cell_size=2.0 padding: (-12,-12,-12) to (12,12,12)
    # nx = (12 - (-12)) / 2 = 12
    assert baked["nx"] == 12
    assert baked["ny"] == 12
    assert baked["nz"] == 12
    
    # 3. Check atlas dimensions
    # nslices = nz + 1 = 13
    # atz = ceil(sqrt(13)) = 4
    # aty = ceil(13 / 4) = 4
    # atlas_w = atz * (nx + 1) = 4 * 13 = 52
    # atlas_h = aty * (ny + 1) = 4 * 13 = 52
    assert baked["atz"] == 4
    assert baked["atlas_w"] == 52
    assert baked["atlas_h"] == 52
    
    # 4. Check atlas data integrity
    # atlas_bytes is now (H, W, 2) uint8 interleaved
    raw = np.frombuffer(baked["atlas_bytes"], dtype=np.uint8).reshape(baked["atlas_h"], baked["atlas_w"], 2)
    # Reconstruct uint16: high byte << 8 | low byte
    atlas = raw[:, :, 0].astype(np.uint32) << 8 | raw[:, :, 1]
    
    # Center pixel of slice 6 (middle slice)
    # iz = 6
    # col = 6 % 4 = 2
    # row = 6 // 4 = 1
    # x_off = 2 * 13 = 26
    # y_off = 1 * 13 = 13
    # Center of 13x13 slice is relative (6, 6)
    # Grand atlas pixel: (26 + 6, 13 + 6) = (32, 19)
    
    # At (0,0,0), SDF for sphere R=10 is -10.0
    # max_dist = cell_size * 8 = 16.0
    # Clamped to -10.0 (inside range)
    # Normalized: ((-10.0 / 16.0 + 1.0) * 0.5 * 65535) = ((-0.625 + 1.0) * 0.5 * 65535) = 0.1875 * 65535 = 12287.8...
    expected_center = int(round((-10.0 / baked["max_dist"] + 1.0) * 0.5 * 65535))
    center_val = atlas[19, 32]
    print(f"Center value (at origin): {center_val} (Expected: ~{expected_center})", flush=True)
    assert abs(int(center_val) - expected_center) <= 1
    
    # Far corner pixel of first slice (iz=0, ix=0, iy=0)
    # P = (-12, -12, -12). Dist = sqrt(12^2 * 3) = 20.78
    # SDF = 20.78 - 10 = 10.78
    # Clamped to 10.78 (inside range 16.0)
    # Normalized: ((10.78 / 16.0 + 1.0) * 0.5 * 65535)
    expected_corner = int(round((10.7846097 / baked["max_dist"] + 1.0) * 0.5 * 65535))
    corner_val = atlas[0, 0]
    print(f"Corner value (at (-12,-12,-12)): {corner_val} (Expected: ~{expected_corner})", flush=True)
    assert abs(int(corner_val) - expected_corner) <= 2

    print("Test passed: SDF Baker implementation is correct.", flush=True)

if __name__ == "__main__":
    try:
        test_sdf_baker()
    except Exception as e:
        print(f"Test FAILED: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
