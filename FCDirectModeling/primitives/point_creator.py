import FreeCAD
import Part
from .primitive_base import NURBSPrimitiveCreator
from FCDirectModeling import dm_logger

class PointCreator(NURBSPrimitiveCreator):
    """Tool to create a NurbsPoint object at a clicked location."""
    
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
            
            # Finalize immediately
            from FCDirectModeling.dm_object import create_dm_object
            create_dm_object("Point", "point", {"position": pt})
            
            self.terminate()
            return True
        except Exception:
            dm_logger.exception("PointCreator.handle_click error")
            return False

    def handle_move(self, event_dict):
        # Update point preview (just the crosshair)
        raw_pt = self.get_mouse_world_pos(event_dict)
        self.update_preview(debug_pt=raw_pt)
        
    def update_preview(self, debug_pt=None):
        if self._terminated:
            return
            
        params = {}
        if debug_pt:
            params["debug_pt"] = debug_pt
            
        # Point tool doesn't have a shape-body preview, just the cursor
        self.update_nurbs_preview("point", params)
