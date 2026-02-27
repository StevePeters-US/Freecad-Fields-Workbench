import FreeCAD
import FreeCADGui
import Part
from PySide import QtCore, QtGui
from .primitive_base import DMPrimitiveCreator
from FCDirectModeling import dm_logger

class BoxCreator(DMPrimitiveCreator):
    def __init__(self):
        super().__init__()
        
    def update_material(self):
        # Preview object is managed by base
        if self._preview_obj and hasattr(self._preview_obj, "ViewObject"):
            if self.is_cutter:
                self._preview_obj.ViewObject.ShapeColor = (0.8, 0.2, 0.2) # Red
            else:
                self._preview_obj.ViewObject.ShapeColor = (0.2, 0.6, 0.85) # Blue

    def update_from_locks(self):
        if not self.start_point:
            return
            
        p1 = self.start_point
        p2 = self.current_point if self.current_point else p1
        
        # Calculate world deltas
        dx = p2.x - p1.x
        dy = p2.y - p1.y
        dz = p2.z - p1.z
        
        # Determine signs from mouse
        sign_x = 1.0 if dx >= 0 else -1.0
        sign_y = 1.0 if dy >= 0 else -1.0
        
        # Override with locks if active
        new_dx = abs(dx) * sign_x
        new_dy = abs(dy) * sign_y
        
        if self.locked_length is not None:
             new_dx = self.locked_length
             
        if self.locked_width is not None:
             new_dy = self.locked_width
             
        # Reconstruct in world space. Use p1's Z if we are just doing plane-drag (dz=0)
        # Actually, in stage 1, p2.z is already p1.z usually.
        self.current_point = FreeCAD.Vector(p1.x + new_dx, p1.y + new_dy, p2.z)
        
        dm_logger.debug(f"DEBUG: Box Lock world delta dx/dy: {new_dx:.2f}, {new_dy:.2f}")
        dm_logger.debug(f"DEBUG: Box Result World Corner: {self.current_point.x:.2f}, {self.current_point.y:.2f}, {self.current_point.z:.2f}")
        
        self.update_preview()
        self.view.redraw()

    def update_ui(self):
        try:
            if not self.start_point or not self.current_point:
                return
                
            p1_local = self.to_local(self.start_point)
            p2_local = self.to_local(self.current_point)
            
            # Send SIGNED local deltas to UI for consistency with Task Panel logic
            length = p2_local.x - p1_local.x
            width = p2_local.y - p1_local.y
            height = self.height
            
            if self.panel:
                 self.panel.update_values(length, width, height)
        except Exception:
            pass

    def handle_click(self, event_dict):
        # Delegate to base for state transitions
        old_state = self.state
        handled = super().handle_click(event_dict)
        if handled:
            print(f"Pin location {old_state+1}: {self.current_point.x:.2f}, {self.current_point.y:.2f}, {self.current_point.z:.2f}")
        return handled

    def on_state_change(self, new_state):
        if new_state == 2:
            if self.locked_height is not None:
                self.height = self.locked_height
            else:
                self.height = 0.001
        self.update_ui()

    def apply_height(self, height):
        if self.locked_height is not None:
            self.height = self.locked_height
        else:
            self.height = height
            
        if abs(self.height) < 0.001:
            self.height = 0.001 if self.height >= 0 else -0.001

        # Auto-Cutter / Fuse Logic
        if not self.manual_mode_override and self.snap_face:
            if self.height < -1e-4:
                 if not self.is_cutter:
                     self.is_cutter = True
                     self.update_material()
            else:
                 if self.is_cutter:
                     self.is_cutter = False
                     self.update_material()

    def update_preview(self, debug_pt=None):
        if not self.start_point or not self.current_point:
            return
            
        lp1 = self.to_local(self.start_point)
        lp2 = self.to_local(self.current_point)
        h = self.height
        
        dx = lp2.x - lp1.x
        dy = lp2.y - lp1.y
        
        # Builder handles (dx, dy, h) as signed dimensions relative to (0,0,0)
        params = {
            "length": dx, 
            "width": dy, 
            "height": h
        }
        if debug_pt:
            params["debug_pt"] = debug_pt
            
        self.update_dm_preview("box", params, placement=self.working_plane)

    def _do_finish(self):
        if not self.start_point or (not self.current_point and self.state == 1):
            self.terminate()
            return

        lp1 = self.to_local(self.start_point)
        lp2 = self.to_local(self.current_point)
        h = self.height
        
        dx = lp2.x - lp1.x
        dy = lp2.y - lp1.y
        
        params = {
            "length": dx,
            "width":  dy,
            "height": h
        }
        
        from FCDirectModeling.dm_object import create_dm_object
        create_dm_object("Box", "box", params, placement=self.working_plane)
        self.terminate()
