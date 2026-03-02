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
    # Class-level reference to the currently active tool to allow 
    # global event filters (like right-click suppression) to reach it.
    active_tool = None

    def __init__(self):
        if PrimitiveBase.active_tool and hasattr(PrimitiveBase.active_tool, 'terminate'):
            try:
                PrimitiveBase.active_tool.terminate()
            except Exception:
                pass
                
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
        PrimitiveBase.active_tool = self
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
        if PrimitiveBase.active_tool is self:
            PrimitiveBase.active_tool = None
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
                # Direct getRay (often added in FC 0.20+)
                if hasattr(self.view, "getRay"):
                    ray = self.view.getRay(x, y)
                
                # If that fails or isn't available, rely on Coin3D viewer
                if not ray:
                    viewer = self.view.getViewer()
                    # Viewport size for Qt vs Coin Y-flip 
                    # Coin uses bottom-left origin, Qt uses top-left
                    if hasattr(viewer, "getGlxSize"):
                        sz = viewer.getGlxSize() # Returns SbVec2s
                        h = sz[1]
                    elif hasattr(viewer, "getSize"):
                        sz = viewer.getSize() # often returns QSize
                        h = sz.height() if hasattr(sz, "height") else sz[1]
                    else:
                        h = 1000 # Blind fallback

                    def try_viewer_ray(cur_y):
                        if hasattr(viewer, "getRay"):
                            return viewer.getRay(int(x), int(cur_y))
                        return None
                    
                    ray = try_viewer_ray(y)
                    if not ray:
                        ray = try_viewer_ray(h - y)
                        
            except Exception as e:
                dm_logger.debug(f"DEBUG: Ray acquisition failed: {e}")

            if ray:
                dm_logger.debug(f"DEBUG: ray acquired. Type: {type(ray)}, Value: {ray}")
                try:
                    # FreeCAD getRay sometimes returns a dict {'base': Vector, 'dir': Vector}
                    if isinstance(ray, dict):
                        ray_p = ray.get('base', ray.get('Base'))
                        ray_d = ray.get('dir', ray.get('Direction'))
                    else:
                        ray_p = ray[0]
                        ray_d = ray[1]
                        
                    dm_logger.debug(f"DEBUG: ray_p={ray_p}, ray_d={ray_d}")
                    if ray_p and ray_d:
                        denom = ray_d.dot(plane_normal)
                        dm_logger.debug(f"DEBUG: denom={denom}")
                        if abs(denom) > 1e-6:
                            t = (plane_point - ray_p).dot(plane_normal) / denom
                            pt = ray_p + ray_d * t
                            dm_logger.debug(f"DEBUG: Intersection at t={t}, pt={pt}")
                            return pt
                        else:
                            dm_logger.debug("DEBUG: denom too small (ray parallel to plane)")
                except Exception as e:
                    dm_logger.debug(f"DEBUG: Error parsing ray data: {e}")
            else:
                 pass
        
        # Fallback to depth-buffered point on surface or synthesize ray
        try:
            pt = self.view.getPoint(x, y)
            
            if plane_normal is not None and plane_point is not None:
                # Try to synthesize a ray using the camera position and the getPoint result
                # This works because getPoint(x, y) guaranteed lies on the view ray for pixel (x,y)
                try:
                    cam = self.view.getCameraNode()
                    if cam and hasattr(cam, "position"):
                        cam_vec = cam.position.getValue()
                        # SbVec3f gives tuple via getValue() or direct index
                        if hasattr(cam_vec, "getValue"):
                            cam_pos_tuple = cam_vec.getValue()
                        else:
                            cam_pos_tuple = (cam_vec[0], cam_vec[1], cam_vec[2])
                        
                        ray_p = FreeCAD.Vector(*cam_pos_tuple)
                        ray_d = pt - ray_p
                        ray_d.normalize()
                        
                        denom = ray_d.dot(plane_normal)
                        if abs(denom) > 1e-6:
                            t = (plane_point - ray_p).dot(plane_normal) / denom
                            pt_on_plane = ray_p + ray_d * t
                            return pt_on_plane
                except Exception as e:
                    pass

            return pt
        except Exception as e:
            dm_logger.error(f"DEBUG: getPoint failed: {e}")
            return None

    def get_visible_workplanes(self):
        """Returns a list of all visible DMWorkPlane objects in the document."""
        doc = FreeCAD.ActiveDocument
        if not doc:
            return []
            
        planes = []
        for obj in doc.Objects:
            if hasattr(obj, "Proxy") and obj.Proxy.__class__.__name__ == "DMWorkPlane":
                if hasattr(obj, "Visibility") and obj.Visibility:
                    planes.append(obj)
        return planes

    def get_base_plane(self, wp_obj=None):
        """Returns (normal, origin) for a given workplane, or the default viewport plane."""
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
        
        if hasattr(self.view, "getFocus"):
            return n, self.view.getFocus()
        return n, FreeCAD.Vector(0,0,0)

    def get_mouse_plane_pt(self, event_dict):
        """Intersection of mouse ray with the closest visible working plane."""
        # 1. If we have a working_plane already established for this tool session, stick to it.
        # This prevents the plane from jumping mid-operation (like drawing a box)
        if hasattr(self, "working_plane") and self.working_plane:
            n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
            o = self.working_plane.Base
            return self.get_mouse_world_pos(event_dict, n, o)

        # 2. Get the actual 3D point the mouse is hovering over in the scene
        pos = event_dict.get("Position", (0, 0))
        x, y = pos[0], pos[1]
        
        if not self.view:
            return FreeCAD.Vector(0,0,0)
            
        scene_pt = None
        try:
            scene_pt = self.view.getPoint(x, y)
        except Exception:
            pass

        # 3. If we don't have a valid scene point, we can't reliably synthesize a ray. 
        # Fall back to default plane.
        if scene_pt is None:
            n, o = self.get_base_plane(None)
            return self.get_mouse_world_pos(event_dict, n, o)

        # 4. Synthesize the ray from camera through scene_pt
        try:
            cam = self.view.getCameraNode()
            if not cam or not hasattr(cam, "position"):
                raise ValueError("No camera")
            cam_vec = cam.position.getValue()
            if hasattr(cam_vec, "getValue"):
                cam_pos_tuple = cam_vec.getValue()
            else:
                cam_pos_tuple = (cam_vec[0], cam_vec[1], cam_vec[2])
            
            ray_p = FreeCAD.Vector(*cam_pos_tuple)
            ray_d = scene_pt - ray_p
            ray_d.normalize()
            
            # 5. Intersect ray with ALL visible workplanes, pick the closest one
            visible_wps = self.get_visible_workplanes()
            closest_t = float('inf')
            closest_pt = None
            
            for wp in visible_wps:
                n, o = self.get_base_plane(wp)
                denom = ray_d.dot(n)
                if abs(denom) > 1e-6:
                    t = (o - ray_p).dot(n) / denom
                    if t > 0 and t < closest_t:
                        # Check if intersection point is within the bounds of the workplane visual
                        pt_candidate = ray_p + ray_d * t
                        
                        # Calculate bounds check (basic rectangle check)
                        # We bring the point into the workplane's local coordinate system
                        if hasattr(wp, "Placement"):
                            wp_inv_plac = wp.Placement.inverse()
                            local_pt = wp_inv_plac.multVec(pt_candidate)
                            
                            w = wp.Width if hasattr(wp, "Width") else 100.0
                            l = wp.Length if hasattr(wp, "Length") else 100.0
                            
                            if abs(local_pt.x) <= l/2.0 and abs(local_pt.y) <= w/2.0:
                                closest_t = t
                                closest_pt = pt_candidate
                                closest_wp = wp
            
            if closest_pt is not None:
                # If we hit a workplane and we don't already have one locked, set it
                if not getattr(self, "working_plane", None):
                   FreeCADGui.Selection.clearSelection()
                   FreeCADGui.Selection.addSelection(closest_wp)
                return closest_pt
                
        except Exception as e:
            dm_logger.debug(f"DEBUG: Auto-workplane raycast failed: {e}")
            pass

        # 6. Fallback: just return the getPoint directly, or intersect default plane
        n, o = self.get_base_plane(None)
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
                        # Right click finishes tool
                        QtCore.QTimer.singleShot(0, self.finish)
                        return True # CONSUME PRESS
                    elif btn == "BUTTON1":
                        return self.handle_click(event_dict)
                    else:
                        # Allow all other buttons (BUTTON2, etc) to pass to FreeCAD for navigation
                        return False
                
                elif state == "UP":
                    if btn == "BUTTON3":
                        return True # CONSUME RELEASE to suppress FreeCAD context menu
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
            self.update_preview()
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
            
            self.current_point = raw_pt
            self.update_preview()
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
        self._last_shape_type = shape_type
        self._last_shape_params = params
        
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

            for k in ["Position", "Points", "HandleIn", "HandleOut"]:
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
                if hasattr(self._active_obj, k):
                    try:
                        setattr(self._active_obj, k, v)
                    except Exception as e:
                        if 'dm_logger' in globals() or 'dm_logger' in locals():
                            dm_logger.debug(f"DEBUG: Failed to update property {k}: {e}")
                        else:
                            print(f"DEBUG: Failed to update property {k}: {e}")
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
                dm_logger.debug(f"DEBUG terminate: Attempting to remove {self._active_obj.Name}")
                if doc and doc.getObject(self._active_obj.Name):
                    doc.removeObject(self._active_obj.Name)
                    doc.recompute()
                    dm_logger.debug(f"DEBUG terminate: Removed successfully.")
                else:
                    dm_logger.debug(f"DEBUG terminate: Object {self._active_obj.Name} not found in doc.")
            except Exception as e:
                dm_logger.error(f"DEBUG terminate Error: {e}")
                import traceback
                traceback.print_exc()
        
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




