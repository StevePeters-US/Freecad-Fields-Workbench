"""Base classes for DM primitive creators."""

import FreeCAD
import FreeCADGui
from PySide import QtCore
import traceback

from FCDirectModeling import dm_logger
from FCDirectModeling.work_plane import WorkPlaneManager



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

        dm_logger.debug(f"DEBUG: PrimitiveBase active view: {self.view.ObjectName if hasattr(self.view, 'ObjectName') else 'Unknown'} ({type(self.view)})")

        self.callback = self.view.addEventCallback("SoEvent", self.event_cb)

        self.start_point   = None
        self.current_point = None
        self.center        = None # Legacy, use start_point
        self.state         = 0
        
        self.working_plane = None
        self.wp_manager = WorkPlaneManager(self.view)
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

    def terminate(self):
        self._terminated = True
        try:
            if self.callback:
                self.view.removeEventCallback("SoEvent", self.callback)
                self.callback = None
            
            if self.wp_manager:
                self.wp_manager.hide()
                self.wp_manager = None
            
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
                # Diagnostics
                # dm_logger.debug(f"DEBUG: view type: {type(self.view)}")
                
                # Try direct getRay (Standard in most FreeCAD versions)
                if hasattr(self.view, "getRay"):
                    ray = self.view.getRay(x, y)
                else:
                    # Try via viewer
                    viewer = self.view.getViewer()
                    if hasattr(viewer, "getRay"):
                        ray = viewer.getRay(x, y)
            except Exception as e:
                dm_logger.debug(f"DEBUG: Ray acquisition failed: {e}")
                pass

            if ray:
                ray_p = ray[0]
                ray_d = ray[1]
                
                denom = ray_d.dot(plane_normal)
                if abs(denom) > 1e-6:
                    t = (plane_point - ray_p).dot(plane_normal) / denom
                    return ray_p + ray_d * t
            else:
                # If we have no ray but we NEED to be on a plane, 
                # we are in trouble if we just use getPoint (which is depth-buffered).
                # Fallback to getPoint if raycasting somehow fails.
                pass
        
        try:
            return self.view.getPoint(x, y)
        except Exception as e:
            dm_logger.error(f"DEBUG: getPoint failed: {e}")
            return None

    def get_base_plane(self):
        """Returns (normal, origin) for the current working plane."""
        if not self.working_plane:
            return None, None
        n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
        o = self.working_plane.Base
        return n, o

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
                if event_dict["State"] == "DOWN":
                    btn = event_dict.get("Button", "None")
                    dm_logger.debug(f"DEBUG: SoMouseButtonEvent DOWN: {btn}")
                    if btn == "BUTTON2":
                        # Right click drops tool
                        QtCore.QTimer.singleShot(0, self.terminate)
                        return True
                    return self.handle_click(event_dict)
            elif event_type == "SoLocation2Event":
                self.handle_move(event_dict)
            elif event_type == "SoKeyboardEvent":
                if event_dict["State"] == "DOWN":
                    key = event_dict.get("Key", "None")
                    dm_logger.debug(f"DEBUG: SoKeyboardEvent DOWN: {key}")
                    return self.handle_keyboard(event_dict)
            return False
        except Exception:
            dm_logger.exception("event_cb error")
            return False

    def handle_keyboard(self, event_dict):
        key = str(event_dict.get("Key", "None")).upper()
        
        # ESC to cancel
        if key == "ESCAPE":
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
        """Resets the tool to state 0, allowing work plane re-detection."""
        self.state = 0
        self.start_point = None
        self.current_point = None
        self.height = 0.0
        if self.wp_manager:
            self.wp_manager.show()
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
                if self.state > 0:
                    self.finish()
                else:
                    self.terminate()
                return True

            if btn != "BUTTON1":
                return False

            pt = self.get_mouse_world_pos(event_dict)
            if pt is None:
                dm_logger.warn("DEBUG: handle_click: pt is None!")
                return False
                
            dm_logger.debug(f"DEBUG: handle_click: Mouse World Pos: {pt.x:.2f}, {pt.y:.2f}, {pt.z:.2f}")

            if self.state == 0:
                # Pin 1: Fix the working plane and move to State 1
                if self.wp_manager:
                    self.working_plane = self.wp_manager.get_placement()
                    dm_logger.debug(f"DEBUG: handle_click: Locked Working Plane: {self.working_plane}")
                    # Ensure start_point is EXACTLY on this plane
                    n, o = self.get_base_plane()
                    pt = self.get_mouse_world_pos(event_dict, n, o)
                else:
                    rot = self.working_plane.Rotation if self.working_plane else FreeCAD.Rotation()
                    self.working_plane = FreeCAD.Placement(pt, rot)
                
                self.start_point = pt
                self.current_point = pt
                self.state = 1
                dm_logger.debug(f"DEBUG: handle_click: Moving to State 1. Start point: {self.start_point}")
                self.on_state_change(self.state)
                self.update_preview()
            elif self.state == 1:
                # Pin 2: Move to State 2
                self.state = 2
                self.drag_start_screen_y = event_dict["Position"][1]
                dm_logger.debug(f"DEBUG: handle_click: Moving to State 2. State 1 end pt: {pt}")
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
        if self.state == 0:
            # Update work plane manager
            if self.wp_manager:
                self.wp_manager.update(event_dict)
            
            raw_pt = self.get_mouse_world_pos(event_dict)
            self.update_preview(debug_pt=raw_pt)
            
        elif self.state == 1:
            pt = self.get_mouse_world_pos(event_dict)
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
        if not hasattr(self, "working_plane") or not self.working_plane:
            return p
        mat = self.working_plane.toMatrix()
        mat.invert()
        return mat.multVec(p)

    def to_global(self, p):
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
        if "debug_pt" in params:
            self._debug_pt = params["debug_pt"]
        
        # Track placement
        if placement:
            self._last_placement = placement
        elif hasattr(self, "working_plane"):
            self._last_placement = self.working_plane
        
        # Create or update
        if self._active_obj is None:
            from FCDirectModeling.dm_object import create_dm_object
            self._active_obj = create_dm_object("DMObject", shape_type, params, placement=self._last_placement)
            # Set initial label if possible
            if self._active_obj:
                self._active_obj.Label = shape_type.capitalize()
        else:
            # Update properties
            for k, v in params.items():
                if hasattr(self._active_obj, k):
                    setattr(self._active_obj, k, v)
                elif k == "Position" and placement is None: # Special case for position update if not using placement
                    self._active_obj.Placement.Base = v
            
            if placement:
                self._active_obj.Placement = placement
            
            self._active_obj.touch()
            if self.doc:
                self.doc.recompute()

        # Update Crosshair Cursor
        debug_pt = params.get("debug_pt")
        if debug_pt and self.doc:
            try:
                if self._preview_cursor is None or self._preview_cursor.Name not in self.doc.Objects:
                    from FCDirectModeling.dm_object import get_point_size
                    self._preview_cursor = self.doc.addObject("Part::Feature", "DM_Cursor")
                    if hasattr(self._preview_cursor, "ViewObject") and self._preview_cursor.ViewObject:
                        self._preview_cursor.ViewObject.ShapeColor = (0.0, 0.4, 1.0)
                        self._preview_cursor.ViewObject.PointSize = get_point_size() * 1.5
                        self._preview_cursor.ViewObject.LineWidth = 2.0
                        self._preview_cursor.ViewObject.Selectable = False
                        if hasattr(self._preview_cursor.ViewObject, "LightModel"):
                            self._preview_cursor.ViewObject.LightModel = "NoLight"
                        if hasattr(self._preview_cursor, "ShowInTree"):
                             self._preview_cursor.ShowInTree = False

                # Create a simple cross shape
                import Part
                cross = Part.Compound([
                    Part.makeLine((debug_pt.x-2,debug_pt.y,debug_pt.z),(debug_pt.x+2,debug_pt.y,debug_pt.z)),
                    Part.makeLine((debug_pt.x,debug_pt.y-2,debug_pt.z),(debug_pt.x,debug_pt.y+2,debug_pt.z)),
                    Part.makeLine((debug_pt.x,debug_pt.y,debug_pt.z-2),(debug_pt.x,debug_pt.y,debug_pt.z+2))
                ])
                self._preview_cursor.Shape = cross
            except Exception:
                pass

    def terminate(self):
        """Clean up: delete active object if not finished."""
        if self._terminated:
            return
        
        super().terminate()
        
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
            if self._last_shape_type and self._last_shape_params:
                from FCDirectModeling.dm_object import create_dm_object
                dm_logger.debug(f"_do_finish: Finalizing. type={self._last_shape_type}, params={self._last_shape_params}, placement={self._last_placement}")
                # Create the final high-res DMObject
                create_dm_object(
                    self._last_shape_type.capitalize(), 
                    self._last_shape_type, 
                    self._last_shape_params,
                    placement=self._last_placement
                )
            else:
                dm_logger.debug(f"_do_finish: No data to finalize (type={self._last_shape_type})")
        except Exception as e:
            dm_logger.error(f"_do_finish FAILED: {e}")
            import traceback
            dm_logger.error(traceback.format_exc())

        self._finished = True
        self.terminate()

        # Close task panel if open
        try:
            import FreeCADGui
            FreeCADGui.Control.closeDialog()
        except Exception:
            pass




