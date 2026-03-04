import FreeCAD
import Part
from .dm_base import NURBSPrimitiveCreator
from core import dm_logger

class PointCreator(NURBSPrimitiveCreator):
    """Tool to create a DMPoint object at a clicked location."""
    
    def __init__(self):
        super().__init__()
        self.created_points = []
        dm_logger.debug("PointCreator initialized")

    def handle_click(self, event_dict):
        try:
            if event_dict.get("Button") != "BUTTON1":
                return False

            pt = self.get_mouse_plane_pt(event_dict)
            if pt is None:
                return False

            dm_logger.info(f"Placing point at: {pt}")
            
            self.update_active_object("point", {"Position": pt, "debug_pt": pt})
            
            if self._active_obj:
                self._active_obj.Label = "Point"
                self.created_points.append(self._active_obj.Name)
                # clear active object so a new one is spawned on next move
                self._active_obj = None
                
            return True
        except Exception:
            dm_logger.exception("PointCreator.handle_click error")
            return False

    def handle_move(self, event_dict):
        # Update point preview (just the crosshair)
        pt = self.get_mouse_plane_pt(event_dict)
        if pt:
            self.update_active_object("point", {"Position": pt, "debug_pt": pt})

    def _do_finish(self):
        # Called when right clicking (i.e. accept and finish)
        # We want to keep all points dropped by L-click, but discard the preview point floating at the cursor
        if self._active_obj:
            try:
                doc = self.doc or FreeCAD.ActiveDocument
                if doc:
                    doc.removeObject(self._active_obj.Name)
                    doc.recompute()
            except Exception as e:
                dm_logger.debug(f"PointCreator._do_finish: Failed to remove preview object: {e}")
            self._active_obj = None
            
        self._finished = True
        self.terminate()

    def handle_keyboard(self, event_dict):
        key = str(event_dict.get("Key", "None")).upper()
        # ESC to cancel and remove all points created in this session
        if key in ["ESCAPE", "ESC"]:
            self.cancel_points()
            return True
        return super().handle_keyboard(event_dict)

    def cancel_points(self):
        doc = self.doc or FreeCAD.ActiveDocument
        if doc:
            for pt_name in self.created_points:
                try:
                    doc.removeObject(pt_name)
                except Exception as e:
                    dm_logger.error(f"Failed to remove point {pt_name}: {e}")
            
            if self._active_obj:
                try:
                    doc.removeObject(self._active_obj.Name)
                except Exception as e:
                    dm_logger.debug(f"PointCreator.cancel_points: Failed to remove active object: {e}")
            self._active_obj = None
            
            doc.recompute()
            
        self.created_points.clear()
        self._finished = True # prevent terminate from trying to remove again
        self.terminate()

    def on_tool_option_0(self):
        dm_logger.info("snapping point tool")
        
    def on_tool_option_1(self):
        dm_logger.info("snapping point tool")

    def get_context_menu(self, event_dict=None):
        return [
            ("Point Option 1", lambda: dm_logger.info("Selected Point Option 1")),
            ("Point Option 2", lambda: dm_logger.info("Selected Point Option 2"))
        ]
