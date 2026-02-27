import unittest
import sys
from unittest.mock import MagicMock, patch

# --- Mocking FreeCAD/FreeCADGui before importing any project code ---
mock_freecad = MagicMock()
mock_freecad.Vector = MagicMock
mock_freecad.Rotation = MagicMock
mock_freecad.Placement = MagicMock
sys.modules['FreeCAD'] = mock_freecad

mock_freecad_gui = MagicMock()
sys.modules['FreeCADGui'] = mock_freecad_gui

mock_part = MagicMock()
sys.modules['Part'] = mock_part

mock_pivy = MagicMock()
sys.modules['pivy'] = mock_pivy
sys.modules['pivy.coin'] = mock_pivy.coin

# Mock PySide
mock_pyside = MagicMock()
sys.modules['PySide'] = mock_pyside
sys.modules['PySide.QtCore'] = mock_pyside.QtCore
sys.modules['PySide.QtGui'] = mock_pyside.QtGui

# Now we can import our code
from FCDirectModeling.primitives.primitive_base import PrimitiveBase

class TestKeyboardInput(unittest.TestCase):
    
    def setUp(self):
        # Reset mocks
        mock_freecad.reset_mock()
        mock_freecad_gui.reset_mock()
        
        # Setup view mock
        self.mock_view = MagicMock()
        self.mock_view.addEventCallback.return_value = 123
        mock_freecad_gui.ActiveDocument.ActiveView = self.mock_view
        
        # Instantiate PrimitiveBase
        with patch('FCDirectModeling.work_plane.WorkPlaneManager'):
            self.creator = PrimitiveBase()

    def test_esc_key_terminates(self):
        """Verify ESC key triggers termination."""
        with patch('PySide.QtCore.QTimer.singleShot') as mock_timer:
            event = {"Type": "SoKeyboardEvent", "Key": "ESCAPE", "State": "DOWN"}
            self.creator.event_cb(event)
            mock_timer.assert_called_once()
            args, _ = mock_timer.call_args
            self.assertEqual(args[1], self.creator.terminate)

    def test_c_key_toggles_cutter(self):
        """Verify C key toggles cutter mode."""
        self.creator.update_material = MagicMock()
        self.creator.is_cutter = False
        
        event = {"Type": "SoKeyboardEvent", "Key": "C", "State": "DOWN"}
        self.creator.event_cb(event)
        
        self.assertTrue(self.creator.is_cutter)
        self.assertTrue(self.creator.manual_mode_override)
        self.creator.update_material.assert_called_once()

    def test_r_key_resets_state(self):
        """Verify R key resets state to 0."""
        self.creator.state = 2
        
        # We need a real-ish Vector for some operations if they aren't mocked
        mock_vec = MagicMock()
        self.creator.start_point = mock_vec
        
        # Patch reset_state to check it's called
        with patch.object(self.creator, 'reset_state', wraps=self.creator.reset_state) as mock_reset:
            event = {"Type": "SoKeyboardEvent", "Key": "R", "State": "DOWN"}
            self.creator.event_cb(event)
            
            mock_reset.assert_called_once()
            self.assertEqual(self.creator.state, 0)
            self.assertIsNone(self.creator.start_point)

    def test_axis_keys_focus_panel(self):
        """Verify X, Y, Z keys focus panel fields."""
        self.creator.panel = MagicMock()
        
        for key in ["X", "Y", "Z"]:
            event = {"Type": "SoKeyboardEvent", "Key": key, "State": "DOWN"}
            self.creator.event_cb(event)
            self.creator.panel.focus_field.assert_called_with(key.lower())

if __name__ == "__main__":
    unittest.main()
