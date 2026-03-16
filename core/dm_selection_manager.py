import FreeCAD
import FreeCADGui
from core import dm_logger


class DMSelectionManager:
    """Handles SDF object selection on left-click when no tool is active."""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = DMSelectionManager()
        return cls._instance

    def __init__(self):
        self._last_selection_time = 0.0

    def try_sdf_selection(self, qt_pos):
        """Attempt to select an SDF object at the given Qt screen position.

        Args:
            qt_pos: tuple (x, y) in Qt coordinates (Y=0 at top)

        Returns:
            True if an SDF object was selected, False otherwise.
        """
        import time
        now = time.monotonic()
        if now - self._last_selection_time < 0.1:  # 100ms debounce
            return False
        self._last_selection_time = now

        try:
            view = FreeCADGui.ActiveDocument.ActiveView if FreeCADGui.ActiveDocument else None
            if not view:
                return False

            from core.view_projector import ViewProjector
            proj = ViewProjector(view)
            sdf_result = proj.get_sdf_hit({"QtPosition": qt_pos})
            if sdf_result:
                _, _, sdf_obj = sdf_result
                FreeCADGui.Selection.clearSelection()
                FreeCADGui.Selection.addSelection(sdf_obj)
                return True
        except Exception as e:
            dm_logger.debug(f"SDF selection failed: {e}")
        return False
