import FreeCAD
import Part
from .primitive_base import NURBSPrimitiveCreator
from FCDirectModeling import dm_logger

class PointCreator(NURBSPrimitiveCreator):
    """Tool to create a DMPoint object at a clicked location."""
    
    def __init__(self):
        super().__init__()
        dm_logger.debug("PointCreator initialized")

    def handle_click(self, event_dict):
        try:
            # We only need one click for a point
            if event_dict.get("Button") != "BUTTON1":
                return False

            pt = self.get_mouse_world_pos(event_dict)
            if pt is None:
                return False

            dm_logger.info(f"Placing point at: {pt}")
            
            self.update_active_object("point", {"Position": pt, "debug_pt": pt})
            self._do_finish()
            return True
        except Exception:
            dm_logger.exception("PointCreator.handle_click error")
            return False

    def handle_move(self, event_dict):
        # Update point preview (just the crosshair)
        pt = self.get_mouse_world_pos(event_dict)
        # We don't create/update a DMObject on hover for points, 
        # but we do for the cursor.
        # Actually, let's stick to the rule: unify objects.
        # For a point tool, we maybe don't want to create it until click?
        # User said: "remove the concept of a preview and final mesh. They should be the same."
        # If I create it on move, it will follow the mouse.
        # Let's create it on move if it doesn't exist.
        if pt:
            self.update_active_object("point", {"Position": pt, "debug_pt": pt})

    def _do_finish(self):
        if self._active_obj:
            self._finished = True
        self.terminate()
