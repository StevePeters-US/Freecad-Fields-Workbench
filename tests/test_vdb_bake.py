"""Test VDB bake roundtrip: field → VDB grid → dense → verify."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock FreeCAD
class _Vec:
    def __init__(self, x=0, y=0, z=0):
        self.x, self.y, self.z = x, y, z
    def __add__(self, o): return _Vec(self.x+o.x, self.y+o.y, self.z+o.z)
    def __sub__(self, o): return _Vec(self.x-o.x, self.y-o.y, self.z-o.z)
    def __mul__(self, s): return _Vec(self.x*s, self.y*s, self.z*s)
    @property
    def Length(self): return (self.x**2+self.y**2+self.z**2)**0.5

class _FC:
    Vector = _Vec
    class Units:
        class Quantity:
            pass
    class Console:
        @staticmethod
        def PrintMessage(s): pass
        @staticmethod
        def PrintWarning(s): pass
        @staticmethod
        def PrintError(s): pass
    class ParamGet:
        def __init__(self, *a): pass
        def GetBool(self, *a): return False
        def GetFloat(self, k, d): return d
    @staticmethod
    def ParamGet(path): return _FC.ParamGet()

sys.modules['FreeCAD'] = _FC

import numpy as np

def test_box_vdb_roundtrip():
    try:
        import openvdb
    except ImportError:
        print("SKIP: pyopenvdb not installed")
        return

    from core.sdf.sdf.box import SdfBoxField
    from core.sdf.sdf_baker import vdb_grid_to_dense

    box = SdfBoxField(_Vec(0,0,0), _Vec(20,20,20))
    grid = box.to_vdb(voxel_size=1.0)

    assert grid.activeVoxelCount() > 0, "Grid has no active voxels"

    result = vdb_grid_to_dense(grid, 1.0)
    assert "volume_bytes" in result
    assert "nx" in result and "ny" in result and "nz" in result
    assert result["nx"] > 0 and result["ny"] > 0 and result["nz"] > 0

    print(f"PASS: box VDB roundtrip — {grid.activeVoxelCount()} active voxels, "
          f"dense={result['nx']}x{result['ny']}x{result['nz']}")

def test_sphere_native_vdb():
    try:
        import openvdb
    except ImportError:
        print("SKIP: pyopenvdb not installed")
        return

    from core.sdf.sdf.sphere import SdfSphereField
    sphere = SdfSphereField(_Vec(0,0,0), 10.0)
    grid = sphere.to_vdb(voxel_size=0.5)

    assert grid.activeVoxelCount() > 0, "Grid has no active voxels"
    print(f"PASS: sphere native VDB — {grid.activeVoxelCount()} active voxels")

if __name__ == "__main__":
    test_box_vdb_roundtrip()
    test_sphere_native_vdb()
    print("All VDB tests passed.")
