import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Mock FreeCAD and other dependencies before importing DM modules
mock_freecad = MagicMock()
mock_freecad_gui = MagicMock()
mock_pyside_core = MagicMock()
mock_pyside_gui = MagicMock()
mock_pivy_coin = MagicMock()

# Set up some basic FreeCAD mocks
class MockVector:
    def __init__(self, x=0, y=0, z=0):
        self.x, self.y, self.z = x, y, z
    def multVec(self, v): return v
    def __sub__(self, other): return MockVector(self.x-other.x, self.y-other.y, self.z-other.z)
    def __add__(self, other): return MockVector(self.x+other.x, self.y+other.y, self.z+other.z)
    @property
    def Length(self): return 0.0

class MockPlacement:
    def __init__(self, base=MockVector()):
        self.Base = base
        self.Rotation = MagicMock()
    def multVec(self, v): return v
    def inverse(self): return self

mock_freecad.Vector = MockVector
mock_freecad.Placement = MockPlacement
mock_freecad.ActiveDocument = MagicMock()

mock_pyside = MagicMock()
mock_pyside.QtCore = mock_pyside_core
mock_pyside.QtGui = mock_pyside_gui

sys.modules["FreeCAD"] = mock_freecad
sys.modules["FreeCADGui"] = mock_freecad_gui
sys.modules["PySide"] = mock_pyside
sys.modules["PySide.QtCore"] = mock_pyside_core
sys.modules["PySide.QtGui"] = mock_pyside_gui
sys.modules["pivy"] = MagicMock()
sys.modules["pivy.coin"] = mock_pivy_coin
sys.modules["Part"] = MagicMock()

# Add repo root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import the tool after mocking
from tools.work_plane_tool import WorkPlaneCreator
from core.dm_tool_manager import DMToolManager

class TestWorkPlaneEdit(unittest.TestCase):
    def setUp(self):
        DMToolManager.get_instance().set_active_tool(None)
        mock_freecad_gui.activeView.return_value = MagicMock()
        mock_freecad.ActiveDocument = MagicMock()
        mock_freecad_gui.Selection.getSelection.return_value = []
        mock_pyside_core.QTimer.singleShot.reset_mock()
        mock_freecad_gui.Control.closeDialog.reset_mock()

    def test_workplane_finish_in_edit_mode(self):
        """Verify that finishing an edit terminates the tool without creating a new preview."""
        
        # 1. Create a mock workplane object
        mock_wp = MagicMock()
        mock_wp.Label = "MyWorkPlane"
        mock_wp.Proxy.__class__.__name__ = "DMWorkPlane"
        mock_wp.Placement = MockPlacement()
        mock_wp.Document.Name = "TestDoc"
        mock_wp.Name = "DM_WorkPlane"
        
        # 2. Mock selection to include this workplane
        mock_freecad_gui.Selection.getSelection.return_value = [mock_wp]
        
        # 3. Instantiate the tool
        tool = WorkPlaneCreator()
        
        # Capture _post_init
        post_init_call = None
        for args, kwargs in mock_pyside_core.QTimer.singleShot.call_args_list:
            if len(args) >= 2 and callable(args[1]) and args[1].__name__ == "_post_init":
                post_init_call = args[1]
                break
        self.assertIsNotNone(post_init_call)
        post_init_call()
        
        # We are now in edit mode
        self.assertEqual(tool.state, 1)
        self.assertEqual(tool._editing_obj, mock_wp)
        self.assertFalse(tool._is_new)
        
        # 4. Call finish()
        # Reset mocks to track termination
        mock_pyside_core.QTimer.singleShot.reset_mock()
        tool.finish()
        
        # 5. Verify behavior
        self.assertIsNone(tool._preview_obj, "Should not have created a new preview object")
        self.assertIsNone(tool._editing_obj, "Should have cleared editing reference")
        
        # Check if terminate was called (it usually schedules _do_terminate via singleShot)
        terminate_scheduled = False
        for args, kwargs in mock_pyside_core.QTimer.singleShot.call_args_list:
            if len(args) >= 2 and callable(args[1]) and args[1].__name__ == "_do_terminate":
                terminate_scheduled = True
                break
        self.assertTrue(terminate_scheduled, "Tool should have scheduled termination after finishing edit")

    def test_workplane_finish_in_creation_mode(self):
        """Verify that finishing a NEW creation starts a repeat cycle (new preview)."""
        
        # 1. Instantiate tool with no selection
        tool = WorkPlaneCreator()
        self.assertTrue(tool._is_new)
        self.assertIsNotNone(tool._preview_obj)
        
        # 2. Mock dropping the plane (state 0 -> state 1)
        tool.state = 1
        tool._active_obj = tool._preview_obj
        tool._preview_obj = None
        
        # 3. Call finish()
        tool.finish()
        
        # 4. Verify behavior
        self.assertIsNotNone(tool._preview_obj, "Should have created a NEW preview object for repeat")
        self.assertEqual(tool._preview_obj.Label, "Work Plane Preview")
        self.assertTrue(tool._is_new, "Repeat should be a new creation cycle")
        
        # Verify it didn't terminate yet
        terminate_scheduled = False
        for args, kwargs in mock_pyside_core.QTimer.singleShot.call_args_list:
            if len(args) >= 2 and callable(args[1]) and args[1].__name__ == "_do_terminate":
                terminate_scheduled = True
                break
        self.assertFalse(terminate_scheduled, "Tool should NOT terminate after new creation (Accept & Repeat)")

if __name__ == "__main__":
    unittest.main()
