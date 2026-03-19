# Mock FreeCAD and Coin
from types import ModuleType
import sys

fc = ModuleType("FreeCAD")
class Vector:
    def __init__(self, x=0, y=0, z=0):
        if isinstance(x, (list, tuple)):
            self.x, self.y, self.z = x
        elif hasattr(x, "x"):
            self.x, self.y, self.z = x.x, x.y, x.z
        else:
            self.x, self.y, self.z = float(x), float(y), float(z)
    def __mul__(self, other): return Vector(self.x*other, self.y*other, self.z*other)
    def __add__(self, other): return Vector(self.x+other.x, self.y+other.y, self.z+other.z)
    def __sub__(self, other): return Vector(self.x-other.x, self.y-other.y, self.z-other.z)
    def __truediv__(self, other): return Vector(self.x/other, self.y/other, self.z/other)
fc.Vector = Vector
def ParamGet(path):
    class Params:
        _store = {}
        def GetFloat(self, k, default): return self._store.get(k, default)
        def SetFloat(self, k, v): self._store[k] = v
        def GetBool(self, k, default): return self._store.get(k, default)
        def SetBool(self, k, v): self._store[k] = v
        def GetInt(self, k, default): return self._store.get(k, default)
        def SetInt(self, k, v): self._store[k] = v
    if not hasattr(ParamGet, "inst"): ParamGet.inst = Params()
    return ParamGet.inst
fc.ParamGet = ParamGet
sys.modules["FreeCAD"] = fc

fc_gui = ModuleType("FreeCADGui")
sys.modules["FreeCADGui"] = fc_gui

part = ModuleType("Part")
part.Point = lambda x: None
part.Shape = lambda: None
sys.modules["Part"] = part

class MockField:
    def __init__(self, *args, **kwargs): pass
    def setValue(self, *args, **kwargs): pass
    def set1Value(self, *args, **kwargs): pass
    def setValues(self, *args, **kwargs): pass
    def setNum(self, *args, **kwargs): pass
    def __getattr__(self, name): return MockField()

class MockNode:
    def __init__(self, *args, **kwargs):
        self.whichChild = 0
    def __getattr__(self, name):
        return MockField()
    def setCallback(self, cb): pass
    def addChild(self, child): pass
    def removeChild(self, child): pass
    def getClassTypeId(self): return 1
    def isOfType(self, t): return True

coin = ModuleType("pivy.coin")
coin.SoSeparator = MockNode
coin.SoSwitch = MockNode
coin.SoCallback = MockNode
coin.SoMaterial = MockNode
coin.SoPickStyle = MockNode
coin.SoCoordinate3 = MockNode
coin.SoIndexedLineSet = MockNode
coin.SoShaderProgram = MockNode
coin.SoVertexShader = MockNode
coin.SoFragmentShader = MockNode
coin.SoShaderParameter1i = MockNode
coin.SoShaderParameter3f = MockNode
coin.SoShapeHints = MockNode
coin.SoShapeHints.UNKNOWN_ORDERING = 1
coin.SoIndexedFaceSet = MockNode
coin.SoDepthBuffer = MockNode
coin.SoShaderObject = MockNode
coin.SoShaderObject.GLSL_PROGRAM = 1
coin.SoPickStyle.UNPICKABLE = 1
coin.SoSeparator.OFF = 1
coin.SoOrthographicCamera = MockNode
coin.SoPerspectiveCamera = MockNode

class SbVec3f:
    def __init__(self, x=0, y=0, z=0):
        if isinstance(x, (list, tuple)):
            self.x, self.y, self.z = x
        elif hasattr(x, "x"):
            self.x, self.y, self.z = x.x, x.y, x.z
        else:
            self.x, self.y, self.z = float(x), float(y), float(z)
    def length(self): return (self.x**2 + self.y**2 + self.z**2)**0.5
    def getValue(self): return [self.x, self.y, self.z]
coin.SbVec3f = SbVec3f
coin.SbViewportRegion = lambda *args: None

sys.modules["pivy.coin"] = coin
sys.modules["pivy"] = ModuleType("pivy")

# Add project root to sys.path
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import FreeCAD
from core.dm_object import set_max_sdf_render_size, get_max_sdf_render_size
from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer

class TestField:
    def __init__(self, bb_min, bb_max):
        self._bb_min = bb_min
        self._bb_max = bb_max
    def bounding_box(self):
        return (FreeCAD.Vector(self._bb_min), FreeCAD.Vector(self._bb_max))

def test_sdf_size_limit():
    # 1. Test setting and getting
    set_max_sdf_render_size(500.0)
    assert get_max_sdf_render_size() == 500.0
    print("Setting/Getting MaxSdfRenderSize: PASS")

    # 2. Test clamping logic in renderer
    renderer = DMSceneRayMarchRenderer.get_instance()
    
    # Create a field that is 1000x1000x1000
    field = TestField((-500, -500, -500), (500, 500, 500))
    
    # Compute params with 500.0 limit
    params = renderer._compute_grid_params(field, cell_size=2.0)
    
    bmin = params["bbox_min"]
    bmax = params["bbox_max"]
    
    size_x = bmax.x - bmin.x
    size_y = bmax.y - bmin.y
    size_z = bmax.z - bmin.z
    
    print(f"Clamped sizes: {size_x}, {size_y}, {size_z}")
    
    # Allowing for padding (which adds 2 * cell_size = 4.0)
    # The actual clamped mn, mx should be 500.0. After _pad, it becomes 504.0.
    limit_with_pad = 500.0 + 4.0
    
    assert size_x <= limit_with_pad + 0.1
    assert size_y <= limit_with_pad + 0.1
    assert size_z <= limit_with_pad + 0.1
    
    print("SDF Size Clamping logic: PASS")

if __name__ == "__main__":
    try:
        test_sdf_size_limit()
        print("\nALL TESTS PASSED")
    except Exception as e:
        print(f"\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
