import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Mock FreeCAD and other dependencies
from types import ModuleType
from unittest.mock import MagicMock

def mock_module(name):
    m = ModuleType(name)
    sys.modules[name] = m
    # To make it a package if it has dots
    if '.' in name:
        parent = name.rsplit('.', 1)[0]
        if parent in sys.modules:
            setattr(sys.modules[parent], name.split('.')[-1], m)
    return m

fc = mock_module('FreeCAD')
fc.Vector = MagicMock
fc.Placement = MagicMock
fc.Rotation = MagicMock

mock_module('FreeCADGui')
mock_module('Part')
pivy = mock_module('pivy')
pivy.coin = mock_module('pivy.coin')

pyside = mock_module('PySide')
qtcore = mock_module('PySide.QtCore')
qtgui = mock_module('PySide.QtGui')

qtcore.QObject = MagicMock
qtcore.Qt = MagicMock
qtgui.QApplication = MagicMock

# Create mocks for core submodules
mock_module('core')
sys.modules['core'].__path__ = [] # treat as package
mock_module('core.input_manager')
sys.modules['core.input_manager'].DMInputManager = MagicMock
mock_module('core.work_plane')
sys.modules['core.work_plane'].WorkPlaneManager = MagicMock
mock_module('core.view_projector')
sys.modules['core.view_projector'].ViewProjector = MagicMock
mock_module('core.dm_point')
sys.modules['core.dm_point'].DMPoint = MagicMock
mock_module('core.dm_line')
sys.modules['core.dm_line'].DMLineSet = MagicMock
mock_module('core.dm_object')
sys.modules['core.dm_object'].create_dm_object = MagicMock
sys.modules['core.dm_object'].get_interactive_throttle_interval = MagicMock
mock_module('core.dm_mesher')
sys.modules['core.dm_mesher'].mesh_timer = MagicMock()
mock_module('core.sdf')
sys.modules['core.sdf'].__path__ = []
mock_module('core.sdf.sdf')
sys.modules['core.sdf.sdf'].__path__ = []
mock_module('core.sdf.sdf.box')
sys.modules['core.sdf.sdf.box'].SdfBoxField = MagicMock
mock_module('core.sdf.sdf.sphere')
sys.modules['core.sdf.sdf.sphere'].SdfSphereField = MagicMock
mock_module('core.sdf.sdf.cylinder')
sys.modules['core.sdf.sdf.cylinder'].SdfCylinderField = MagicMock
mock_module('core.dm_scene_ray_march_renderer')
sys.modules['core.dm_scene_ray_march_renderer'].DMSceneRayMarchRenderer = MagicMock

sys.modules['core'].dm_logger = MagicMock()

from tools.primitive_tool import PrimitiveCreatorBase

class TestTier1PrimitiveFlow(unittest.TestCase):
    def setUp(self):
        self.tool = PrimitiveCreatorBase()
        self.tool.view = MagicMock()

    def test_compute_default_size_ortho(self):
        from pivy import coin
        mock_cam = MagicMock(spec=coin.SoOrthographicCamera)
        mock_cam.height.getValue.return_value = 1000.0
        self.tool.view.getCameraNode.return_value = mock_cam
        
        # We need to make sure isinstance check works. since we mocked coin, we need to handle it.
        # But in the code it's: if isinstance(cam, coin.SoOrthographicCamera)
        # So we patch isinstance or just make coin.SoOrthographicCamera a real class for the test.
        
        with patch('isinstance', side_effect=lambda obj, cls: True if cls == coin.SoOrthographicCamera else isinstance(obj, cls)):
            size = self.tool._compute_default_size()
            # 1000 * 0.15 = 150
            self.assertEqual(size, 150.0)

    def test_compute_default_size_perspective(self):
        from pivy import coin
        import math
        mock_cam = MagicMock() # Not SoOrthographicCamera
        mock_cam.focalDistance.getValue.return_value = 500.0
        mock_cam.heightAngle.getValue.return_value = math.radians(45.0)
        self.tool.view.getCameraNode.return_value = mock_cam
        
        with patch('isinstance', return_value=False):
            size = self.tool._compute_default_size()
            # h = 2 * 500 * tan(22.5 deg) = 1000 * 0.4142 = 414.2
            # size = 414.2 * 0.15 = 62.13
            expected_h = 2.0 * 500.0 * math.tan(math.radians(22.5))
            expected_size = expected_h * 0.15
            self.assertAlmostEqual(size, expected_size, places=2)

    def test_compute_default_size_exception(self):
        self.tool.view.getCameraNode.side_effect = Exception("Cam not found")
        size = self.tool._compute_default_size()
        self.assertEqual(size, 200.0)

    def test_compute_default_size_clamping(self):
        from pivy import coin
        mock_cam = MagicMock(spec=coin.SoOrthographicCamera)
        
        # Test lower clamp
        mock_cam.height.getValue.return_value = 1.0 # 1 * 0.15 = 0.15 < 5.0
        self.tool.view.getCameraNode.return_value = mock_cam
        with patch('isinstance', return_value=True):
            size = self.tool._compute_default_size()
            self.assertEqual(size, 5.0)
            
        # Test upper clamp
        mock_cam.height.getValue.return_value = 10000.0 # 10000 * 0.15 = 1500 > 500.0
        self.tool.view.getCameraNode.return_value = mock_cam
        with patch('isinstance', return_value=True):
            size = self.tool._compute_default_size()
            self.assertEqual(size, 500.0)

if __name__ == '__main__':
    unittest.main()
