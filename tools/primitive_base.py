"""Base classes for DM primitive creators."""

import FreeCAD
import FreeCADGui
from PySide import QtCore
import traceback
import math

from core import dm_logger
from core.work_plane import WorkPlaneManager



# ─────────────────────────────────────────────────────────────────────────────
# Camera helpers (no Coin3D)
# ─────────────────────────────────────────────────────────────────────────────

def _is_orthographic(view):
    """True if the active camera is orthographic (not perspective)."""
    try:
        cam = view.getCameraNode()
        return "Orthographic" in cam.getTypeId().getName()
    except Exception:
        return False

def _cam_pos(view):
    """Camera world position as a FreeCAD.Vector (perspective only)."""
    try:
        cam = view.getCameraNode()
        p = cam.position.getValue()
        return FreeCAD.Vector(p[0], p[1], p[2])
    except Exception:
        return FreeCAD.Vector(0,0,100)


# ─────────────────────────────────────────────────────────────────────────────
# PrimitiveCreatorBase
# ─────────────────────────────────────────────────────────────────────────────

class PrimitiveBase:
    def __init__(self):
        self._terminated = False
        self.view = FreeCADGui.activeView()
        self.doc = FreeCAD.ActiveDocument
        if not self.view:
            # Try to get it from ActiveDocument as fallback
            try:
                self.view = FreeCADGui.ActiveDocument.ActiveView
            except Exception:
                pass
        
        if not self.view:
            dm_logger.error("DEBUG: PrimitiveBase: Could not find active view!")
            return


        dm_logger.debug(f"{self.__class__.__name__} initialized")
        self.callback = self.view.addEventCallback("SoEvent", self.event_cb)

        self.start_point   = None
        self.current_point = None
        self.center        = None # Legacy, use start_point
        self.state         = 0
        
        self.working_plane = None
        self.snap_face = None
        self.drag_start_screen_y = None

        # Shared UX state
        self.height = 0.0
        self.is_cutter = False
        self.manual_mode_override = False
        self.panel = None

        # Shared constraint state
        self.active_axis = None
        self.locked_length = None
        self.locked_width = None
        self.locked_height = None

        # Check for selected WorkPlane
        self._detect_selected_workplane()

    def _detect_selected_workplane(self):
        """Checks if a DM_WorkPlane is selected and sets it as the active working plane."""
        try:
            selection = FreeCADGui.Selection.getSelection()
            if not selection:
                # dm_logger.debug("DEBUG: Selection is empty")
                return
                
            for obj in selection:
                is_wp = False
                proxy_name = "None"
                if hasattr(obj, "Proxy") and obj.Proxy:
                    proxy_name = obj.Proxy.__class__.__name__
                    if proxy_name == "DMWorkPlane":
                        is_wp = True
                
                # dm_logger.debug(f"DEBUG: Checking selection: {obj.Label}, Proxy: {proxy_name}")
                
                # 2. Check for specific properties if proxy check is brittle
                if not is_wp and hasattr(obj, "Proxy") and hasattr(obj.Proxy, "execute") and hasattr(obj, "Length") and hasattr(obj, "Width"):
                    # This looks like one of our workplanes
                    is_wp = True
                
                if is_wp:
                    # Use getGlobalPlacement to handle nested objects
                    if hasattr(obj, "getGlobalPlacement"):
                        self.working_plane = obj.getGlobalPlacement()
                    else:
                        self.working_plane = obj.Placement
                    dm_logger.info(f"Using selected workplane: {obj.Label}")
                    break
        except Exception as e:
            dm_logger.debug(f"Error detecting selected workplane: {e}")

    def terminate(self):
        self._terminated = True
        try:
            if self.callback:
                self.view.removeEventCallback("SoEvent", self.callback)
                self.callback = None
            
            # Close task panel if open
            import FreeCADGui
            FreeCADGui.Control.closeDialog()
        except Exception:
            pass

    def finish(self):
        pass

    def set_panel(self, panel):
        self.panel = panel

    def toggle_cutter_mode(self):
         self.is_cutter = not self.is_cutter
         self.manual_mode_override = True
         self.update_material()
         self.view.redraw()

    def update_material(self):
        pass

    def update_ui(self):
        pass

    # ------------------------------------------------------------------
    # Geometry helpers — use FreeCAD view API, not Coin3D directly
    # ------------------------------------------------------------------

    def get_mouse_world_pos(self, event_dict, plane_normal=None, plane_point=None):
        """
        Unified Ray-Plane intersection. 
        If plane_normal and plane_point are provided, intersects the mouse ray with that plane.
        Otherwise, uses the default view.getPoint().
        """
        if not self.view:
            dm_logger.error("DEBUG: get_mouse_world_pos: No active view!")
            return None

        pos = event_dict.get("Position", (0, 0))
        x, y = pos[0], pos[1]
        
        if plane_normal is not None and plane_point is not None:
            # Ray-Plane intersection
            ray = None
            try:
                # Diagnostics: View size
                viewer = self.view.getViewer()
                sz = viewer.getSize()
                h = sz[1]
                
                # Attempt ray acquisition
                def try_get_ray(cur_y):
                    if hasattr(self.view, "getRay"):
                        return self.view.getRay(int(x), int(cur_y))
                    if hasattr(viewer, "getRay"):
                        return viewer.getRay(int(x), int(cur_y))
                    return None

                # 1. Try raw (Coin3D style, 0 at bottom)
                ray = try_get_ray(y)
                
                # 2. Try flipped (Qt style, 0 at top) if raw fails
                if not ray:
                    ray = try_get_ray(h - y)
                    if ray:
                        # If flipped works, continue using it
                        pass 

            except Exception as e:
                dm_logger.debug(f"DEBUG: Ray acquisition failed: {e}")

            if ray:
                ray_p = ray[0]
                ray_d = ray[1]
                
                denom = ray_d.dot(plane_normal)
                if abs(denom) > 1e-6:
                    t = (plane_point - ray_p).dot(plane_normal) / denom
                    pt = ray_p + ray_d * t
                    return pt
                # dm_logger.debug("DEBUG: Ray is parallel to plane")
                pass
            else:
                 # dm_logger.warn(f"DEBUG: getRay returned None for {x},{y} (flipped: {h-y if 'h' in locals() else 'N/A'})")
                 pass
        
        # Fallback to depth-buffered point on surface
        try:
            pt = self.view.getPoint(x, y)
            # if plane_normal is not None:
            #     dm_logger.info(f"DEBUG: Falling back to getPoint (getRay fail). pt: {pt}")
            return pt
        except Exception as e:
            dm_logger.error(f"DEBUG: getPoint failed: {e}")
            return None

    def get_active_workplane_obj(self):
        """Returns the active DMWorkPlane object from the document."""
        doc = FreeCAD.ActiveDocument
        if not doc:
            return None
        for obj in doc.Objects:
            if hasattr(obj, "Proxy") and obj.Proxy.__class__.__name__ == "DMWorkPlane":
                return obj
        return None

    def get_base_plane(self):
        """Returns (normal, origin) for the current working plane."""
        # If we have a working_plane already established for this tool session, use it.
        if hasattr(self, "working_plane") and self.working_plane:
            n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
            o = self.working_plane.Base
            return n, o

        # Otherwise, check for a persistent WorkPlane object in the document.
        wp_obj = self.get_active_workplane_obj()
        if wp_obj and hasattr(wp_obj, "Placement"):
            wp_placement = wp_obj.Placement
            n = wp_placement.Rotation.multVec(FreeCAD.Vector(0,0,1))
            o = wp_placement.Base
            return n, o

        # Fallback to camera facing
        if not self.view:
            return FreeCAD.Vector(0,0,1), FreeCAD.Vector(0,0,0)
            
        cam_node = self.view.getCameraNode()
        cam_pos = FreeCAD.Vector(*cam_node.position.getValue().getValue()) if cam_node else FreeCAD.Vector(0,0,100)
        
        vd = self.view.getViewDirection()
        n = FreeCAD.Vector(-vd[0], -vd[1], -vd[2])
        n.normalize()
        
        # We can just pick origin as 0,0,0 and offset appropriately or just 0,0,0
        # If we have a focal point or target we could use that. Let's just use 0,0,0
        return n, FreeCAD.Vector(0,0,0)

    def get_mouse_plane_pt(self, event_dict):
        """Intersection of mouse ray with working plane."""
        n, o = self.get_base_plane()
        return self.get_mouse_world_pos(event_dict, n, o)
            

    # ------------------------------------------------------------------
    # Event loop
    # ------------------------------------------------------------------

    def event_cb(self, event_dict):
        try:
            event_type = event_dict.get("Type", "Unknown")

            if event_type == "SoMouseButtonEvent":
                btn = event_dict.get("Button", "None")
                state = event_dict.get("State", "None")
                if state == "DOWN":
                    if btn == "BUTTON3":
                        # Right click drops tool
                        QtCore.QTimer.singleShot(0, self.terminate)
                        return True # CONSUME PRESS
                    elif btn == "BUTTON1":
                        return self.handle_click(event_dict)
                    else:
                        # Allow all other buttons (BUTTON2, etc) to pass to FreeCAD for navigation
                        return False
                
                elif state == "UP":
                    if btn == "BUTTON3":
                        return True # CONSUME RELEASE to suppress context menu
                    return False
            elif event_type == "SoLocation2Event":
                self.handle_move(event_dict)
            elif event_type == "SoKeyboardEvent":
                if event_dict["State"] == "DOWN":
                    key = event_dict.get("Key", "None")
                    dm_logger.debug(f"DEBUG: SoKeyboardEvent DOWN: {key}")
                    return self.handle_keyboard(event_dict)
            else:
                # For tools that need to update on camera move (like WorkPlaneCreator preview)
                if self.state == 1:
                    # We only care about this if it's NOT a mouse click (handled above)
                    # and we want to refresh the orientation/position
                    try:
                        # Synthetic event dict for handle_move
                        mouse_pos = self.view.getCursorPos()
                        self.handle_move({"Position": mouse_pos})
                    except Exception:
                        pass
            return False
        except Exception:
            dm_logger.exception("event_cb error")
            return False

    def handle_keyboard(self, event_dict):
        key = str(event_dict.get("Key", "None")).upper()
        
        # ESC to cancel
        if key in ["ESCAPE", "ESC"]:
            QtCore.QTimer.singleShot(0, self.terminate)
            return True
            
        # Toggle Cutter Mode (C)
        if key == "C":
             self.toggle_cutter_mode()
             return True

        # Axis Toggles (X, Y, Z) -> Focus Panel
        target_axis = None
        if key == "X": target_axis = "x"
        elif key == "Y": target_axis = "y"
        elif key == "Z": target_axis = "z"
        
        if target_axis:
            self.toggle_axis(target_axis)
            return True
            
        # Reset Tool (R)
        if key == "R":
            self.reset_state()
            return True
            
        return False

    def reset_state(self):
        """Resets the tool to state 1."""
        self.state = 1
        self.start_point = None
        self.current_point = None
        self.height = 0.0
        self.on_state_change(self.state)
        self.view.redraw()

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
            self.panel.clear_focus()
            # Trigger update to snap back to mouse
            self.update_from_locks()
        else:
            # Focus Field
            self.active_axis = target_axis
            self.panel.focus_field(target_axis)

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
        pass

    def handle_click(self, event_dict):
        try:
            btn = event_dict.get("Button")
            dm_logger.debug(f"DEBUG: handle_click: State={self.state}, Button={btn}")
            
            # Right-click (BUTTON3) to finish or drop
            if btn == "BUTTON3":
                if self.state > 1:
                    self.finish()
                else:
                    self.terminate()
                return True

            if btn != "BUTTON1":
                return False

            n, o = self.get_base_plane()
            pt = self.get_mouse_world_pos(event_dict, n, o)
            if pt is None:
                dm_logger.warn("DEBUG: handle_click: pt is None!")
                return False
                
            dm_logger.debug(f"DEBUG: handle_click: Mouse World Pos: {pt.x:.2f}, {pt.y:.2f}, {pt.z:.2f}")

            if getattr(self, "state", 0) == 0:
                self.state = 1 # Force state 1 if inadvertently set to 0.
                
            if self.state == 1:
                # Pin 1: Fix the starting point and move to State 2
                self.start_point = pt
                self.current_point = pt
                
                # Setup working_plane based on the base plane for local transformations
                rot = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), n)
                self.working_plane = FreeCAD.Placement(pt, rot)
                
                self.state = 2
                self.drag_start_screen_y = event_dict["Position"][1]
                dm_logger.debug(f"DEBUG: handle_click: Moving to State 2. Start point: {self.start_point}")
                self.on_state_change(self.state)
                self.update_preview()
            elif self.state == 2:
                # Pin 3: Finish
                dm_logger.debug("DEBUG: handle_click: State 2 -> Finish")
                self.finish()
            
            if self.current_point:
                dm_logger.info(f"Pin location {self.state}: {self.current_point.x:.2f}, {self.current_point.y:.2f}, {self.current_point.z:.2f}")
            return True
        except Exception:
            dm_logger.exception("handle_click error")
            return False

    def handle_move(self, event_dict):
        if getattr(self, "state", 0) == 0:
            self.state = 1
            
        if self.state == 1:
            n, o = self.get_base_plane()
            pt = self.get_mouse_world_pos(event_dict, n, o)
            self.current_point = pt
            self.on_move_state_1(event_dict)
            self.update_preview(debug_pt=pt)
            self.update_ui()
            
        elif self.state == 2:
            # Standard height drag calculation
            current_screen_y = event_dict["Position"][1]
            if self.drag_start_screen_y is not None:
                delta = current_screen_y - self.drag_start_screen_y
                self.apply_height(delta / 4.0)
            
            self.on_move_state_2(event_dict)
            
            # Show crosshair at the top
            n, o = self.get_base_plane()
            if o and hasattr(self, "height"):
                o = o + n * self.height
            
            raw_pt = self.get_mouse_world_pos(event_dict, n, o)
            self.current_point = raw_pt
            self.update_preview(debug_pt=raw_pt)
            self.update_ui()

    def on_move_state_1(self, event_dict):
        """Hook for subclasses to update internal parameters in state 1."""
        pass

    def on_move_state_2(self, event_dict):
        """Hook for subclasses to update internal parameters in state 2."""
        pass

    def on_state_change(self, new_state):
        self.update_ui()

    def apply_height(self, height_delta):
        if hasattr(self, "height"):
            if self.locked_height is not None:
                self.height = self.locked_height
            else:
                # height_delta is the change from the drag start
                self.height = height_delta
            
            # Epsilon guard to avoid flat shapes
            if abs(self.height) < 0.001:
                self.height = 0.001 if self.height >= 0 else -0.001

    def to_local(self, p):
        if p is None:
            return None
        if not hasattr(self, "working_plane") or not self.working_plane:
            return p
        mat = self.working_plane.toMatrix()
        mat.invert()
        return mat.multVec(p)

    def to_global(self, p):
        if p is None:
            return None
        if not hasattr(self, "working_plane") or not self.working_plane:
            return p
        return self.working_plane.toMatrix().multVec(p)

    def get_face_under_mouse(self, event_dict):
        pos = event_dict["Position"]
        try:
            info = self.view.getObjectInfo((pos[0], pos[1]))
            if info and "Object" in info and "Component" in info:
                 return info["Object"], info["Component"]
        except Exception:
            pass
        return None, None

# ─────────────────────────────────────────────────────────────────────────────
# NURBSPrimitiveCreator
# ─────────────────────────────────────────────────────────────────────────────

class NURBSPrimitiveCreator(PrimitiveBase):
    """
    Base for creators that produce a DM object.
    Uses the actual DMObject for real-time feedback.
    """

    def __init__(self):
        super().__init__()
        self._active_obj = None    # The DMObject being created/edited
        self._finished = False     # Guard for finalization

        self._last_shape_type = None
        self._last_shape_params = None
        self._last_placement = None
        
        self._debug_pt = None      # Store current mouse 3D for debug dot
        self._preview_cursor = None # Crosshair object

    # ------------------------------------------------------------------
    # Active Object Updates
    # ------------------------------------------------------------------

    def update_active_object(self, shape_type, params, placement=None):
        """
        Updates the active DM object or creates it if it doesn't exist.
        """
        if self._terminated:
            return

        self._last_shape_type = shape_type
        self._last_shape_params = params
        
        # Track 3D cursor for debugging
        self._debug_pt = params.get("debug_pt")
        
        # Track placement
        active_placement = placement
        if not active_placement and hasattr(self, "working_plane"):
            active_placement = self.working_plane
        self._last_placement = active_placement
        
        # Map world coords to local space if using a placement
        local_params = dict(params)
        if active_placement:
            # Helper to map a point or list of points
            def map_p(obj):
                if obj is None:
                    return None
                if isinstance(obj, (list, tuple)):
                    return [self.to_local(p) for p in obj]
                if hasattr(obj, "x"): # It's a Vector
                    return self.to_local(obj)
                return obj

            for k in ["Position", "Points", "HandleIn", "HandleOut", "debug_pt"]:
                if k in local_params and local_params[k] is not None:
                    local_params[k] = map_p(local_params[k])

        # Create or update
        if self._active_obj is None:
            from core.dm_object import create_dm_object
            self._active_obj = create_dm_object("DMObject", shape_type, local_params, placement=active_placement)
            # Set initial label if possible
            if self._active_obj:
                self._active_obj.Label = shape_type.capitalize()
        else:
            # Update properties
            for k, v in local_params.items():
                target_k = "DebugPoint" if k == "debug_pt" else k
                if hasattr(self._active_obj, target_k):
                    try:
                        setattr(self._active_obj, target_k, v)
                    except Exception as e:
                        if 'dm_logger' in globals() or 'dm_logger' in locals():
                            dm_logger.debug(f"DEBUG: Failed to update property {target_k}: {e}")
                        else:
                            print(f"DEBUG: Failed to update property {target_k}: {e}")
                elif k == "Position" and placement is None:
                    self._active_obj.Placement.Base = v
            
            if placement:
                self._active_obj.Placement = placement
            
            self._active_obj.touch()
            if self.doc:
                self.doc.recompute()

        # Note: Legacy DM_Cursor (Part::Feature) has been removed in favor of 
        # more efficient Coin3D overlays or can be re-implemented as a pure 
        # view-side node if needed.
        pass

    def terminate(self):
        """Clean up: delete active object if not finished."""
        if self._terminated:
            return
        
        super().terminate()
        
        # Legacy cursor cleanup removed
        pass

        if not self._finished and self._active_obj:
            try:
                # Use FreeCAD.ActiveDocument if self.doc is stale or None
                doc = self.doc or FreeCAD.ActiveDocument
                if doc and self._active_obj.Name in doc.Objects:
                    doc.removeObject(self._active_obj.Name)
                    doc.recompute()
            except Exception:
                pass
        
        self._active_obj = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def finish(self):
        """Schedule the finalization to happen safely outside the event loop."""
        QtCore.QTimer.singleShot(0, self._do_finish)


    def _do_finish(self):
        """Standard finalization for all DM primitives."""
        if self._finished:
            return
            
        try:
            if self._active_obj:
                dm_logger.debug(f"_do_finish: Finalizing active object {self._active_obj.Name}")
                # Rename to its final type-based label if it still has the default
                if "DMObject" in self._active_obj.Label:
                    self._active_obj.Label = self._active_obj.ShapeType.capitalize()
            else:
                dm_logger.debug(f"_do_finish: No active object to finalize")
        except Exception as e:
            dm_logger.error(f"_do_finish error: {e}")

        self._finished = True
        self.terminate()

        # Close task panel if open
        try:
            import FreeCADGui
            FreeCADGui.Control.closeDialog()
        except Exception:
            pass




