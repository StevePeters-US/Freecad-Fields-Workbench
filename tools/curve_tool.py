import FreeCAD
import FreeCADGui
import Part
from PySide import QtCore, QtGui
from .primitive_base import NURBSPrimitiveCreator
from core import dm_logger

class CurveCreator(NURBSPrimitiveCreator):
    """Tool to create a DMCurve object from clicked points."""
    def __init__(self):
        super().__init__()
        self.points = []
        self.is_closed = False
        self.current_point = None

        # Point dragging state (REMOVED - EditTool handles this now)
        self.dragged_index = -1 
        self._drag_start_pos = None 

    def handle_click(self, event_dict):
        try:
            btn = event_dict.get("Button")
            state = event_dict.get("State")
            dm_logger.debug(f"DEBUG: curve handle_click: State={self.state}, btn={btn}, state={state}")
            
            # Point picking/dragging logic removed from Curve Tool.
            # Control points are now manipulated exclusively via the Edit Tool (click-to-select/drop).

            if btn == "BUTTON3":
                if len(self.points) >= 2:
                    self.finish()
                else:
                    self.terminate()
                return True

            if btn != "BUTTON1":
                return False

            pt = self.get_mouse_plane_pt(event_dict)
            if pt is None:
                return False
            
            # In state 0, first click sets the working plane (if not already set via selection)
            if self.state == 0:
                if not self.working_plane:
                    # Establish a new working plane at the first click
                    # get_base_plane() will return face-tangent or camera-facing normal
                    n, _ = self.get_base_plane()
                    rot = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), n)
                    self.working_plane = FreeCAD.Placement(pt, rot)
                    # Re-project pt onto the newly established plane to be safe
                    pt = self.get_mouse_plane_pt(event_dict)
                    
                self.start_point = pt
                self.points.append(pt)
                
                # Debug logging
                n, o = self.get_base_plane()
                dist = (pt - o).dot(n)
                dm_logger.info(f"Curve Point 0: {pt} (dist to plane: {dist:.6f})")
                
                self.state = 1
                self.update_preview()
                self.update_ui()
                return True
            
            # In state 1, subsequent clicks add points
            elif self.state == 1:
                # Check for click on start point (close the curve)
                if len(self.points) >= 2:
                    from core.dm_object import get_picking_radius
                    dist = (pt - self.points[0]).Length
                    if dist < get_picking_radius(): 
                        self.is_closed = True
                        self.current_point = None # Avoid double point on closure
                        self.finish()
                        return True

                self.points.append(pt)
                
                # Debug logging
                n, o = self.get_base_plane()
                dist = (pt - o).dot(n)
                dm_logger.info(f"Curve Point {len(self.points)-1}: {pt} (dist to plane: {dist:.6f})")
                
                self.update_preview()
                self.update_ui()
                return True
                
            return False
        except Exception:
            dm_logger.exception("Curve handle_click error")
            return False

    def handle_move(self, event_dict):
        if self.state == 0:
            # Snap to faces etc.
            super().handle_move(event_dict)
        elif self.state == 1:
            # Point Dragging removed from creator tool
            pass

            # Update temporary "current_point" for preview
            pt = self.get_mouse_plane_pt(event_dict)
            if pt is None:
                return
            
            # Snapping to start point
            if len(self.points) >= 2:
                from core.dm_object import get_picking_radius
                dist = (pt - self.points[0]).Length
                if dist < get_picking_radius(): 
                    pt = self.points[0]
            self.current_point = pt
            self.update_preview()
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
            self.terminate()
            return True
            
        return super().handle_keyboard(event_dict)

    def _get_auto_handles(self, points):
        """Compute automatic smooth handles for each point in the list."""
        n = len(points)
        if n < 2:
            return [], []
            
        h_in = [p for p in points]
        h_out = [p for p in points]
        
        # Simple Catmull-Rom like tangent: T_i = (P_{i+1} - P_{i-1}) / 2
        # Handle distance = 1/3 of segment length
        for i in range(n):
            p = points[i]
            
            # If both are manual, skip expensive tangent math for this point
            # (Reserved for future manual handle control via context menu)
            # if is_in_manual and is_out_manual: continue

            if self.is_closed:
                # Wrap indices for periodic curve
                prev_p = points[(i - 1) % n]
                next_p = points[(i + 1) % n]
            else:
                prev_p = points[i-1] if i > 0 else (points[1] - (points[1]-points[0]) if n > 1 else p)
                next_p = points[i+1] if i < n-1 else (points[-1] + (points[-1]-points[-2]) if n > 1 else p)
            
            tangent = (next_p - prev_p) * 0.5
            dist = tangent.Length
            if dist > 0.0001:
                # Limit handles to roughly 1/3 of segment length
                h_out[i] = p + (tangent * 0.33)
                h_in[i] = p - (tangent * 0.33)
            
            # For open curves, suppress the "outbound" handle of the final point
            # and the "inbound" handle of the first point to avoid sticking out.
            if not self.is_closed:
                if i == 0:
                    h_in[i] = p
                if i == n - 1:
                    h_out[i] = p
        
        return h_in, h_out

    def update_preview(self, drag_pt=None):
        if not self.points:
            return
        pts = list(self.points)
        # Handle preview for unfinalized point
        if self.current_point and self.dragged_index == -1:
            pts.append(self.current_point)
        
        params = {
            "Points": pts,
            "Closed": self.is_closed
        }
        
        # Calculate auto-handles
        if len(pts) >= 2:
            hi, ho = self._get_auto_handles(pts)
            params["HandleIn"] = hi
            params["HandleOut"] = ho
            params["PointTypes"] = [0] * len(pts)
            params["HandleTypes"] = [0] * (2 * len(pts))
        else:
            params["HandleIn"] = pts
            params["HandleOut"] = pts
            params["PointTypes"] = [0] * len(pts)
            params["HandleTypes"] = [0] * (2 * len(pts))
                
        self.update_active_object("curve", params)

    def update_ui(self):
        # Optional: update panel with point count or last segment length
        pass

    def _do_finish(self):
        """Finalize the curve."""
        if len(self.points) < 2:
            self.terminate()
            return

        # Explicitly drop the un-clicked trailing mouse point
        self.current_point = None

        # Ensure active object is fully updated one last time
        self.update_preview()
        
        self._finished = True
        # dm_logger.debug(f"Curve finalized: {self._active_obj.Name if self._active_obj else 'None'}")
        
        # Reset but keep object
        self._active_obj = None
        self.terminate()
