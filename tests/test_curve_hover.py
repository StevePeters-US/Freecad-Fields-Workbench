import sys
import os
from types import ModuleType

# Setup sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE_DIR)

# Flexible mock helper
class Mock(object):
    def __init__(self, *args, **kwargs): pass
    def __call__(self, *args, **kwargs): return Mock()
    def __getattr__(self, name): return Mock()

def mock_module(name):
    m = ModuleType(name)
    m.__path__ = []
    sys.modules[name] = m
    return m

# Create a master Mock instance that returns Mocks for everything
master_mock = Mock()

# Mock EVERY dependency found in imports
module_names = [
    "FreeCAD", "FreeCADGui", "PySide", "PySide.QtCore", "PySide.QtGui", 
    "PySide.QtCore.Qt", "PySide.QtCore.QTimer", "PySide.QtGui.QApplication",
    "pivy", "pivy.coin", "Part", "core", "core.dm_logger", "core.work_plane", 
    "core.view_projector", "core.input_manager", "core.dm_tool_manager", 
    "core.dm_point", "core.dm_object", "core.dm_workplane", "core.dm_menu"
]

for mname in module_names:
    m = mock_module(mname)
    # Add a __getattr__ to the module to return Mock for anything
    m.__class__ = type(mname, (ModuleType, Mock), {})

# Special constants needed by the code
import PySide.QtCore
sys.modules["PySide.QtCore"].Qt = Mock()
sys.modules["PySide.QtCore"].Qt.CrossCursor = 1
sys.modules["PySide.QtCore"].Qt.PointingHandCursor = 2
sys.modules["PySide.QtCore"].Qt.SizeAllCursor = 3

sys.modules["core.dm_logger"].debug = print
sys.modules["core.dm_logger"].info = print
sys.modules["core.dm_logger"].warn = print
sys.modules["core.dm_logger"].error = print
sys.modules["core.dm_logger"].exception = print

# Now import the tool
from tools.curve_tool import CurveCreator

def test_hover_none_fix():
    print("Testing CurveCreator._set_hover fix...")
    
    # Bypass __init__ completely
    class DummyCurveCreator(CurveCreator):
        def __init__(self):
            self._hovered_idx = -1
            self.dm_points = []
            self.view = Mock()
    
    # Mock _restore_cursor and _set_cursor
    DummyCurveCreator._restore_cursor = lambda self: None
    DummyCurveCreator._set_cursor = lambda self, c: None
    
    tool = DummyCurveCreator()
    
    # 1. Hover over first point (idx=0)
    print("1. Hovering over point 0")
    tool._set_hover(0)
    assert tool._hovered_idx == 0
    
    # 2. Hover over nothing (idx=None)
    print("2. Hovering over nothing (None)")
    tool._set_hover(None)
    assert tool._hovered_idx is None
    
    # 3. Hover over first point again (idx=0)
    # This is where it used to crash: None < len(dm_points)
    print("3. Hovering over point 0 again (should NOT crash)")
    tool._set_hover(0)
    assert tool._hovered_idx == 0
    
    print("Test passed!")

if __name__ == "__main__":
    try:
        test_hover_none_fix()
    except Exception as e:
        print(f"Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
