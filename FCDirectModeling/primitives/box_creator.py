import FreeCAD
import FreeCADGui
import Part
from PySide import QtCore, QtGui
from .primitive_base import DMPrimitiveCreator
from FCDirectModeling import dm_logger

class BoxCreator(DMPrimitiveCreator):
    def __init__(self):
        super().__init__()
        # Common state (height, panel, is_cutter, locks) initialized by super
        
    def update_material(self):
        pass # Optional: can implement if needed specifically for Box

    def update_from_locks(self):
        if not self.start_point:
            self.start_point = FreeCAD.Vector(0,0,0)
            
        p1 = self.start_point
        p2 = self.current_point if self.current_point else FreeCAD.Vector(0,0,0)
        
        # Calculate raw deltas (signed)
        dx = p2.x - p1.x
        dy = p2.y - p1.y
        
        # Determine signs from mouse (for unlocked dimensions)
        sign_x = 1.0 if dx >= 0 else -1.0
        sign_y = 1.0 if dy >= 0 else -1.0
        
        # Use simple mouse delta magnitude if unlocked
        new_dx = abs(dx) * sign_x
        new_dy = abs(dy) * sign_y
        
        # If locked, OVERRIDE with the lock value directly (Signed Input = Signed Direction)
        if self.locked_length is not None:
             new_dx = self.locked_length
             
        if self.locked_width is not None:
             new_dy = self.locked_width
             
        # Reconstruct point
        new_x = p1.x + new_dx
        new_y = p1.y + new_dy
        
        self.current_point = FreeCAD.Vector(new_x, new_y, 0)
        self.update_preview()
        self.view.redraw()

    def update_ui(self):
        try:
            if not self.start_point or not self.current_point:
                return
                
            p1_local = self.to_local(self.start_point)
            p2_local = self.to_local(self.current_point)
            
            # Send SIGNED deltas to UI so positive = positive local direction
            length = p2_local.x - p1_local.x
            width = p2_local.y - p1_local.y
            height = self.height
            
            if self.panel:
                 self.panel.update_values(length, width, height)
        except Exception:
            pass

    def update_preview(self, debug_pt=None):
        if not self.start_point or not self.current_point:
            return
            
        # Coordinates are now strictly relative to start_point in local space
        # working_plane origin is now p1.
        p1_local = self.to_local(self.start_point) # Should be [0,0,0]
        p2_local = self.to_local(self.current_point)
        h = self.height
        
        dx = p2_local.x - p1_local.x
        dy = p2_local.y - p1_local.y
        
        # Local offset to the minimal corner (where builders start)
        mc = FreeCAD.Vector(min(0.0, dx), min(0.0, dy), min(0.0, h))
        
        # Final World Placement is just the working_plane (which is centered at p1)
        final_placement = self.working_plane
        
        params = {
            "length": abs(dx), 
            "width": abs(dy), 
            "height": abs(h),
            "min_corner_local": mc
        }
        if debug_pt:
            params["debug_pt"] = debug_pt
            
        self.update_dm_preview("box", params, placement=final_placement)
        
        self._last_sdf_params = params
        self._last_placement = final_placement


        





    def handle_click(self, event_dict):
        # Delegate to base for state transitions
        handled = super().handle_click(event_dict)
        if handled and self.state == 1:
            print(f"Pin location 1: {self.start_point.x:.2f}, {self.start_point.y:.2f}, {self.start_point.z:.2f}")
        elif handled and self.state == 2:
            print(f"Pin location 2: {self.current_point.x:.2f}, {self.current_point.y:.2f}, {self.current_point.z:.2f}")
            if self.locked_height is not None:
                self.height = self.locked_height
            else:
                self.height = 0.0
        return handled

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

    def handle_move(self, event_dict):
        # State 0 is handled by base (face detection)
        # State 1 is handled by base (plane projection into self.current_point)
        # State 2 (height) is handled by base (calls apply_height)
        super().handle_move(event_dict)

        if self.state == 1:
            # Sync current_point with locks if needed
            lp = self.to_local(self.current_point)
            p1l = self.to_local(self.start_point)
            
            dx = lp.x - p1l.x
            dy = lp.y - p1l.y
            
            if self.locked_length is not None: dx = self.locked_length
            if self.locked_width is not None: dy = self.locked_width
            
            self.current_point = self.to_global(FreeCAD.Vector(p1l.x + dx, p1l.y + dy, 0))

    def _do_finish(self):
        if not self.start_point or (not self.current_point and self.state == 0):
             # If completely uninitialized, just terminate
            self.terminate()
            return

        from FCDirectModeling.dm_object import create_dm_object
        from FCDirectModeling import dm_logger
        
        # Adjust placement for corner-scaling consistency
        p1_local = self.to_local(self.start_point)
        p2_local = self.to_local(self.current_point)
        h = self.height
        
        dx = p2_local.x - p1_local.x
        dy = p2_local.y - p1_local.y
        
        # Local offset to the minimal corner
        min_corner_local = FreeCAD.Vector(min(0.0, dx), min(0.0, dy), min(0.0, h))
        
        # Final Placement = Start Placement * Local Offset
        final_placement = self.working_plane * FreeCAD.Placement(min_corner_local, FreeCAD.Rotation())
        
        params = {
            "length": abs(dx),
            "width":  abs(dy),
            "height": abs(h),
            "min_corner_local": min_corner_local
        }
        
        create_dm_object("Box", "box", params, placement=final_placement)
        self.terminate()

