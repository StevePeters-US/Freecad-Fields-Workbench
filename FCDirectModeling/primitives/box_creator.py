import FreeCAD
import FreeCADGui
import Part
from PySide import QtCore, QtGui
from .base import SDFMeshPrimitiveCreator
from FCDirectModeling import dm_logger

class BoxCreator(SDFMeshPrimitiveCreator):
    def __init__(self):
        super().__init__()
        
        # State is initialized by super: self.start_point, self.current_point, self.state=0
        
        self.height = 0.0
        
        self.active_axis = None
        self.locked_length = None
        self.locked_width = None
        self.locked_height = None
        
        self.manual_mode_override = False
        
        self.working_plane = None # FreeCAD.Placement
        self.snap_face = None # (obj, face_name)
        
        self.panel = None

        self.is_cutter = False
        
        # Base initializes: self.sg, self.material, self.preview_sep, self.sdf_sep
        
        # Add Box-specific nodes
        
    def update_material(self):
        pass

    def toggle_cutter_mode(self):
         self.is_cutter = not self.is_cutter
         self.manual_mode_override = True
         self.view.redraw()

    def terminate(self):
        super().terminate()
        self.panel = None

    def set_panel(self, panel):
        self.panel = panel

    def set_length_lock(self, length):
        self.locked_length = length
        self.update_from_locks()
        
    def set_width_lock(self, width):
        self.locked_width = width
        self.update_from_locks()
        
    def set_height_lock(self, height):
        self.locked_height = height
        self.height = height
        self.view.redraw()

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
                
            p1 = self.start_point
            p2 = self.current_point
            
            # Send SIGNED deltas to UI so positive = positive direction
            length = p2.x - p1.x
            width = p2.y - p1.y
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
        
        # Local relative bounds (start point is 0,0,0)
        # Note: we use min/max to allow dragging in any direction
        min_x = min(0.0, dx); max_x = max(0.0, dx)
        min_y = min(0.0, dy); max_y = max(0.0, dy)
        min_z = min(0.0, h);  max_z = max(0.0, h)
        
        # We must ensure we don't have 0-size bounds
        max_x = max(max_x, min_x + 1e-3)
        max_y = max(max_y, min_y + 1e-3)
        max_z = max(max_z, min_z + 1e-3)
        
        bounds_min = [min_x, min_y, min_z]
        bounds_max = [max_x, max_y, max_z]
        
        # Final World Placement is just the working_plane (which is centered at p1)
        final_placement = self.working_plane
        
        dm_logger.debug(f"BoxCreator.update_preview: bounds={bounds_min}/{bounds_max}")
        
        params = {"bounds_min": bounds_min, "bounds_max": bounds_max}
        if debug_pt:
            params["debug_pt"] = debug_pt
            
        self.update_sdf_preview("box", params, placement=final_placement)
        
        self._last_sdf_params = params
        self._last_placement = final_placement

    def event_cb(self, event_dict):
        event_type = event_dict["Type"]
        
        if event_type == "SoMouseButtonEvent":
            if event_dict["State"] == "DOWN":
                button = event_dict["Button"]
                if button == "BUTTON1":
                    return self.handle_click(event_dict)
                
        elif event_type == "SoLocation2Event":
            self.handle_move(event_dict)
            
        elif event_type == "SoKeyboardEvent":
            if event_dict["State"] == "DOWN":
                handled = self.handle_keyboard(event_dict)
                if handled:
                    return True
            
        return False
        

            
    def handle_keyboard(self, event_dict):
        key = str(event_dict["Key"]).upper()
        
        # ESC to cancel
        if key == "ESCAPE":
            if self.panel:
                import FreeCADGui
                FreeCADGui.Control.closeDialog()
            else:
                QtCore.QTimer.singleShot(0, self.terminate)
            return True
            
        # Toggle Cutter Mode (C)
        if key == "C":
             self.is_cutter = not self.is_cutter
             self.update_material()
             self.view.redraw()
             return

        # Axis Toggles -> Focus Panel
        target_axis = None
        if key == "X": target_axis = "x"
        elif key == "Y": target_axis = "y"
        elif key == "Z": target_axis = "z"
        
        if target_axis:
            self.toggle_axis(target_axis)

    def toggle_axis(self, target_axis):
        if not self.panel:
            return
            
        if self.active_axis == target_axis:
            # Toggle OFF
            self.active_axis = None
            if target_axis == 'x': self.locked_length = None
            if target_axis == 'y': self.locked_width = None
            if target_axis == 'z': self.locked_height = None
            
            # Clear focus from panel fields
            if self.panel:
                self.panel.clear_focus()
                
            # Trigger update to snap back to mouse
            self.update_from_locks()
        else:
            # Focus Field
            self.active_axis = target_axis
            self.panel.focus_field(target_axis)

    def handle_click(self, event_dict):
        dm_logger.debug(f"DEBUG: handle_click, state={self.state}")
        n = FreeCAD.Vector(0,0,1)
        o = FreeCAD.Vector(0,0,0)
        if self.working_plane:
            n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
            o = self.working_plane.Base
            
        pt = self.get_point_on_plane(event_dict, n, o)
        print(f"[DEBUG] BoxClick: pt={pt}, state={self.state}")
        
        if self.state == 0: # Start
            # We fix the working plane origin to exactly the click point
            # This makes all local coords relative to start_point = [0,0,0]
            rot = self.working_plane.Rotation if self.working_plane else FreeCAD.Rotation()
            self.working_plane = FreeCAD.Placement(pt, rot)
            print(f"[DEBUG] BoxStart: working_plane.Base={self.working_plane.Base}")

            self.start_point = pt
            self.current_point = pt
            self.state = 1
            self.update_ui()
            
        elif self.state == 1: # End base -> Start Height
            self.state = 2
            self.drag_start_screen_y = event_dict["Position"][1]
            # Height lock handling?
            if self.locked_height is not None:
                self.height = self.locked_height
            else:
                self.height = 0.0
            
        elif self.state == 2: # Finish
            self.finish()
            return True
        
        return False

    def handle_move(self, event_dict):
        dm_logger.debug(f"DEBUG: BoxCreator.handle_move, state={self.state}")
        if self.state == 0:
            # Detect face under mouse
            obj, subname = self.get_face_under_mouse(event_dict)
            if obj and subname and "Face" in subname:
                try:
                    face = obj.Shape.getElement(subname)
                    # Support all faces by using their local placement
                    # For planes, Surface.Position is reliable.
                    if hasattr(face, "Surface") and "GeomPlane" in face.Surface.TypeId:
                        self.working_plane = face.Surface.Position
                        self.snap_face = (obj, subname)
                    else:
                        # Fallback for non-planar (though we mostly want planes for now)
                        self.working_plane = None # Will default to Z=0 in base
                        self.snap_face = None
                except Exception:
                    self.working_plane = None
                    self.snap_face = None
            else:
                self.working_plane = None
                self.snap_face = None
            return
            
        elif self.state == 1:
            n = FreeCAD.Vector(0,0,1)
            o = FreeCAD.Vector(0,0,0)
            if self.working_plane:
                n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
                o = self.working_plane.Base
            
            raw_pt = self.get_point_on_plane(event_dict, n, o)
            # Log delta from start to help alignment verification
            delta = (raw_pt - self.start_point)
            print(f"[DEBUG] BoxMove: dist={delta.Length:.2f}, raw_pt={raw_pt.x:.2f},{raw_pt.y:.2f},{raw_pt.z:.2f}")
            
            # Work in Local Coords for standard delta logic
            local_raw_pt = self.to_local(raw_pt)
            local_p1 = self.to_local(self.start_point)
            
            # Raw Signed Deltas from Mouse (Local)
            dx_mouse = local_raw_pt.x - local_p1.x
            dy_mouse = local_raw_pt.y - local_p1.y
            
            # Final Deltas (Lock Overrides)
            new_dx = dx_mouse
            new_dy = dy_mouse
            
            if self.locked_length is not None:
                 new_dx = self.locked_length
                 
            if self.locked_width is not None:
                 new_dy = self.locked_width
            
            # Reconstruct (Local)
            new_local_x = local_p1.x + new_dx
            new_local_y = local_p1.y + new_dy
            
            # Back to Global
            new_local_pt = FreeCAD.Vector(new_local_x, new_local_y, 0)
            self.current_point = self.to_global(new_local_pt)
            
            self.update_preview(debug_pt=raw_pt)
            self.update_ui()
            
        elif self.state == 2:
            # Handle height
            current_screen_y = event_dict["Position"][1]
            
            if self.locked_height is not None:
                self.height = self.locked_height
            else:
                if hasattr(self, 'drag_start_screen_y'):
                    delta = current_screen_y - self.drag_start_screen_y
                    self.height = delta / 4.0 
            
            # Auto-Cutter / Fuse Logic
            # If manual override is OFF, and we have a snap face:
            # Height < 0 (into face) -> Cut
            # Height > 0 (out of face) -> Fuse (Create)
            if not self.manual_mode_override and self.snap_face:
                if self.height < -1e-4:
                     if not self.is_cutter:
                         self.is_cutter = True
                         self.update_material()
                else:
                     if self.is_cutter:
                         self.is_cutter = False
                         self.update_material()
            
            # We still want the mouse dot during height mode
            n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
            o = self.working_plane.Base
            raw_pt = self.get_point_on_plane(event_dict, n, o)

            self.update_preview(debug_pt=raw_pt)
            self.update_ui()

    def _do_finish(self):
        if not self.start_point or (not self.current_point and self.state == 0):
             # If completely uninitialized, just terminate
            self.terminate()
            return

        from FCDirectModeling.sdf_object import create_sdf_object
        from FCDirectModeling import dm_logger
        
        # Adjust placement for corner-scaling consistency
        # SDFObject.build_sdf uses [0,0,0] -> [L,W,H]. 
        # So we MUST set Placement to the MINIMAL corner.
        p1_local = self.to_local(self.start_point)
        p2_local = self.to_local(self.current_point)
        h = self.height
        
        dx = p2_local.x - p1_local.x
        dy = p2_local.y - p1_local.y
        
        # Local offset to the minimal corner
        min_corner_local = FreeCAD.Vector(min(0.0, dx), min(0.0, dy), min(0.0, h))
        
        # Final Placement = Start Placement * Local Offset
        # This keeps the box exactly where it was during preview
        final_placement = self.working_plane * FreeCAD.Placement(min_corner_local, FreeCAD.Rotation())
        
        params = {
            "length": abs(dx),
            "width":  abs(dy),
            "height": abs(h)
        }
        
        # Pass bounds for initialization
        params["bounds_min"] = [0.0, 0.0, 0.0]
        params["bounds_max"] = [abs(dx), abs(dy), abs(h)]
        
        dm_logger.debug(f"BoxCreator._do_finish: final_placement={final_placement.Base}, dims={params['length']}/{params['width']}/{params['height']}")
        
        create_sdf_object("Box", "box", params, placement=final_placement)
        self.terminate()

