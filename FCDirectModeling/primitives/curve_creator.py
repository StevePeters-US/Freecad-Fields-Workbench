import FreeCAD
import FreeCADGui
import Part
from PySide import QtCore, QtGui
from .primitive_base import NURBSPrimitiveCreator
from FCDirectModeling import dm_logger

class CurveCreator(NURBSPrimitiveCreator):
    """Tool to create a DMCurve object from clicked points."""
    def __init__(self):
        super().__init__()
        self.points = []
        self.is_closed = False
        self.current_point = None

    def handle_click(self, event_dict):
        try:
            btn = event_dict.get("Button")
            dm_logger.debug(f"DEBUG: Curve handle_click: State={self.state}, Button={btn}")

            # Right-click (BUTTON3) to finish
            if btn == "BUTTON3":
                if len(self.points) >= 2:
                    self.finish()
                else:
                    self.terminate()
                return True

            if btn != "BUTTON1":
                return False

            pt = self.get_mouse_world_pos(event_dict)
            if pt is None:
                return False
            
            # In state 0, first click sets the working plane
            if self.state == 0:
                if self.wp_manager:
                    self.working_plane = self.wp_manager.get_placement()
                    # Ensure start_point is EXACTLY on this plane
                    n, o = self.get_base_plane()
                    pt = self.get_mouse_world_pos(event_dict, n, o)
                else:
                    rot = self.working_plane.Rotation if self.working_plane else FreeCAD.Rotation()
                    self.working_plane = FreeCAD.Placement(pt, rot)
                    
                self.start_point = pt
                self.points.append(pt)
                self.state = 1
                dm_logger.debug(f"DEBUG: Curve State 0 -> 1. Working plane origin: {self.working_plane.Base}")
                self.update_preview()
                self.update_ui()
                return True
            
            # In state 1, subsequent clicks add points
            elif self.state == 1:
                # Ensure point is on the established plane
                n, o = self.get_base_plane()
                pt = self.get_mouse_world_pos(event_dict, n, o)
                if pt is None:
                    return False

                # Check for click on start point (close the curve)
                if len(self.points) >= 2:
                    dist = (pt - self.points[0]).Length
                    if dist < 1.0: # Snapping distance for click
                        self.is_closed = True
                        self.finish()
                        return True

                self.points.append(pt)
                dm_logger.debug(f"DEBUG: Curve point added. Total: {len(self.points)} at {pt}")
                self.update_preview()
                self.update_ui()
                return True
                
            return False
        except Exception:
            dm_logger.exception("Curve handle_click error")
            return False

    def handle_move(self, event_dict):
        if self.state == 0:
            # Face snapping logic from base
            super().handle_move(event_dict)
        elif self.state == 1:
            # Update temporary "current_point" for preview
            # Ensure point is on the established plane
            n, o = self.get_base_plane()
            pt = self.get_mouse_world_pos(event_dict, n, o)
            if pt is None:
                return
            
            # Snapping to start point
            if len(self.points) >= 2:
                dist = (pt - self.points[0]).Length
                if dist < 1.0: # Snapping distance for move
                    pt = self.points[0]

            self.current_point = pt
            self.update_preview(debug_pt=pt)
            self.update_ui()

    def handle_keyboard(self, event_dict):
        key = str(event_dict.get("Key", "None")).upper()
        
        if key == "RETURN" or key == "ENTER":
            if len(self.points) >= 2:
                self.finish()
            else:
                self.terminate()
            return True
            
        if key == "ESCAPE":
            if len(self.points) >= 2:
                # Remove the current trailing mouse point before finishing
                self.current_point = None
                self.finish()
            else:
                self.terminate()
            return True
            
        return super().handle_keyboard(event_dict)

    def _get_auto_handles(self):
        """Compute automatic smooth handles for each point."""
        if len(self.points) < 2:
            return [], []
            
        h_in = [None] * len(self.points)
        h_out = [None] * len(self.points)
        
        # Simple Catmull-Rom like tangent: T_i = (P_{i+1} - P_{i-1}) / 2
        # Handle distance = 1/3 of segment length
        for i in range(len(self.points)):
            p = self.points[i]
            prev_p = self.points[i-1] if i > 0 else (self.points[1] - (self.points[1]-self.points[0]) if len(self.points) > 1 else p)
            next_p = self.points[i+1] if i < len(self.points)-1 else (self.points[-1] + (self.points[-1]-self.points[-2]) if len(self.points) > 1 else p)
            
            tangent = (next_p - prev_p) * 0.5
            h_in[i] = p - tangent * 0.33
            h_out[i] = p + tangent * 0.33
            
        return h_in, h_out

    def update_preview(self, debug_pt=None):
        if not self.points:
            return
        pts = list(self.points)
        if self.current_point:
            pts.append(self.current_point)
        
        # Prepare parameters for DMObject
        params = {
            "Points": pts,
            "is_closed": self.is_closed
        }
        
        # Calculate auto-handles for preview if we have enough points
        if len(pts) >= 2:
            hi, ho = self._get_auto_handles()
            params["HandleIn"] = hi
            params["HandleOut"] = ho
        
        if debug_pt:
            params["debug_pt"] = debug_pt
            
        self.update_active_object("curve", params)

    def update_ui(self):
        # Optional: update panel with point count or last segment length
        pass

    def _do_finish(self):
        """Finalize the curve."""
        if len(self.points) < 2:
            self.terminate()
            return

        # Ensure active object is fully updated one last time
        self.update_preview()
        
        self._finished = True
        dm_logger.debug(f"Curve finalized: {self._active_obj.Name if self._active_obj else 'None'}")
        
        # Reset but keep object
        self._active_obj = None
        self.terminate()
