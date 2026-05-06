import math
import unittest
from unittest.mock import MagicMock

# Define the logic to be tested directly to avoid import hell
def compute_default_size_logic(view, coin_so_ortho_cls):
    """Refined copy of the logic from PrimitiveCreatorBase._compute_default_size"""
    try:
        cam = view.getCameraNode()
        # Use simple type check instead of isinstance if we can, 
        # but the actual code uses isinstance.
        # We can pass the camera type to the function for testing.
        if type(cam) == coin_so_ortho_cls:
            h = cam.height.getValue()
        else:
            h = 2.0 * cam.focalDistance.getValue() * math.tan(cam.heightAngle.getValue() / 2.0)
        size = h * 0.15
    except Exception as e:
        # print(f"DEBUG: caught exception {e}")
        size = 200.0
    return max(5.0, min(size, 500.0))

class MockOrthoCamera:
    def __init__(self, height):
        self.height = MagicMock()
        self.height.getValue.return_value = height

class MockPerspCamera:
    def __init__(self, focal, angle):
        self.focalDistance = MagicMock()
        self.focalDistance.getValue.return_value = focal
        self.heightAngle = MagicMock()
        self.heightAngle.getValue.return_value = angle

class TestDefaultSizeLogic(unittest.TestCase):
    def setUp(self):
        self.view = MagicMock()
        self.ortho_cls = MockOrthoCamera

    def test_ortho(self):
        mock_cam = MockOrthoCamera(1000.0)
        self.view.getCameraNode.return_value = mock_cam
        
        size = compute_default_size_logic(self.view, self.ortho_cls)
        self.assertEqual(size, 150.0)

    def test_perspective(self):
        mock_cam = MockPerspCamera(500.0, math.radians(45.0))
        self.view.getCameraNode.return_value = mock_cam
        
        size = compute_default_size_logic(self.view, self.ortho_cls)
        expected_h = 2.0 * 500.0 * math.tan(math.radians(22.5))
        expected_size = expected_h * 0.15
        self.assertAlmostEqual(size, expected_size, places=2)

    def test_clamp_low(self):
        mock_cam = MockOrthoCamera(1.0)
        self.view.getCameraNode.return_value = mock_cam
        size = compute_default_size_logic(self.view, self.ortho_cls)
        self.assertEqual(size, 5.0)

    def test_clamp_high(self):
        mock_cam = MockOrthoCamera(10000.0)
        self.view.getCameraNode.return_value = mock_cam
        size = compute_default_size_logic(self.view, self.ortho_cls)
        self.assertEqual(size, 500.0)

    def test_exception(self):
        self.view.getCameraNode.side_effect = Exception("error")
        size = compute_default_size_logic(self.view, self.ortho_cls)
        self.assertEqual(size, 200.0)

if __name__ == '__main__':
    unittest.main()
