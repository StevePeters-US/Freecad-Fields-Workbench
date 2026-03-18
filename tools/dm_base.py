"""Base classes for DM primitive creators.

Event Ownership Contract
========================
- Qt filter (DMInputManager): state tracking, FreeCAD suppression, global hotkeys.
  Never calls tool methods except via synthetic event_dict to on_button3_down.
- Coin3D callback (event_cb): ALL tool logic — clicks, moves, keyboard, finish.
- Modifiers: always read from DMInputManager (is_shift_down, is_ctrl_down, etc.).
- Drag: QTimer polls DMInputManager._last_qt_pos. Coin3D location events are
  suppressed during LMB hold, so always use the timer pattern.
"""

import FreeCAD
import FreeCADGui
from PySide import QtCore
import traceback
import math

from core import dm_logger
from core.work_plane import WorkPlaneManager
from core.view_projector import ViewProjector
from core.input_manager import DMInputManager

# ─────────────────────────────────────────────────────────────────────────────
# DragTimerMixin
# ─────────────────────────────────────────────────────────────────────────────

class DragTimerMixin:
    """Consolidated QTimer-based polling for tool dragging."""
    def _start_drag_timer(self, interval_ms=16):
        self._stop_drag_timer()
        self._drag_timer = QtCore.QTimer()
        self._drag_timer.timeout.connect(self._drag_update)
        self._drag_timer.start(interval_ms)

    def _stop_drag_timer(self):
        if hasattr(self, "_drag_timer") and self._drag_timer:
            self._drag_timer.stop()
            self._drag_timer = None

    def _drag_update(self):
        """Override in subclasses."""
        pass

    def _drag_check_lmb_released(self):
        """Returns True if LMB was released, stopping the timer and resetting state."""
        if not DMInputManager.get_instance().is_left_mouse_down():
            self._stop_drag_timer()
            self.state = 0
            if hasattr(self, "_selected_element"): self._selected_element = None
            if hasattr(self, "_dragging_idx"): self._dragging_idx = None
            return True
        return False

# ─────────────────────────────────────────────────────────────────────────────
# PrimitiveCreatorBase
# ─────────────────────────────────────────────────────────────────────────────

# Tool States
STATE_IDLE = 0
STATE_ACTIVE = 1
STATE_DRAGGING = 2
STATE_FINALIZED = 3

class DMBase:
    # Class-level reference to the currently active tool to allow 
    place_on_geometry = True
    _last_btn3_time = 0.0 # Instance variable per tool

    def __init__(self):
        from core.dm_tool_manager import DMToolManager
        tool_mgr = DMToolManager.get_instance()
        active_tool = tool_mgr.get_active_tool()
        if active_tool and hasattr(active_tool, 'terminate'):
            try:
                active_tool.terminate()
            except Exception as e:
                dm_logger.debug(f"DMBase.__init__: Failed to terminate previous tool: {e}")
                
        self._terminated = False
        self.view = FreeCADGui.activeView()
        self.doc = FreeCAD.ActiveDocument
        
        if not self.view and FreeCADGui.ActiveDocument:
            try:
                self.view = FreeCADGui.ActiveDocument.ActiveView
            except Exception as e:
                dm_logger.debug(f"DMBase.__init__: Failed to get view from document: {e}")
        
        if not self.view:
            dm_logger.error("DMBase: Could not find active view!")
            return


        dm_logger.debug(f"{self.__class__.__name__} initialized")
        tool_mgr.set_active_tool(self)
        self.projector = ViewProjector(self.view)
        self.callback = self.view.addEventCallback("SoEvent", self.event_cb)

        self.start_point   = None
        self.current_point = None
        self.center        = None # Legacy, use start_point
        self.state         = 0
        
        self.working_plane = None
        self._working_plane_is_fallback = True  # Default to True until a real WP is hit
        self.snap_face = None
        self.snap_enabled = False
        self.snap_type = "Workplane Grid"

        # Shared UX state
        self.height = 0.0
        self.is_cutter = False
        self.panel = None
        self._dialog_open = False
        self._finish_scheduled = False

        # Shared constraint state
        self.active_axis = None
        self.locked_length = None
        self.locked_width = None
        self.locked_height = None

        self._cursor_active = False
        self._last_btn3_time = 0.0
        self._update_pending = False

        # Check for selected WorkPlane
        self._detect_selected_workplane()

    def _set_cursor(self, cursor):
        """Set override cursor, tracking state."""
        if not getattr(self, "_cursor_active", False):
            from PySide import QtGui
            QtGui.QApplication.setOverrideCursor(cursor)
            self._cursor_active = True

    def _restore_cursor(self):
        """Restore cursor if we set it."""
        if getattr(self, "_cursor_active", False):
            from PySide import QtGui
            QtGui.QApplication.restoreOverrideCursor()
            self._cursor_active = False

    def _hit_test_perp(self, ray_p, ray_d, points, tolerance=None):
        """
        Returns (best_idx, best_perp_dist) for list of FreeCAD.Vector points.
        Uses perpendicular distance (depth-independent, correct for ortho cameras).
        tolerance defaults to _compute_handle_radius() if None.
        """
        if not ray_p or not ray_d or not points:
            return None, float('inf')
        
        if tolerance is None:
            tolerance = self._compute_handle_radius()
            
        best_idx = None
        best_perp = float('inf')
        
        for i, pt in enumerate(points):
            if pt is None: continue
            v = pt - ray_p
            proj = v.dot(ray_d)
            if proj < 0: continue
            
            # Perpendicular distance to ray
            perp = (ray_p + ray_d * proj - pt).Length
            if perp < tolerance and perp < best_perp:
                best_perp = perp
                best_idx = i
                
        return best_idx, best_perp

    def _resolve_wp_click(self, event_dict, skip_objects=None):
        """
        Call get_mouse_plane_pt, update self.working_plane from wp_hit
        if in state 0 (Idle) or if not already set.
        """
        # Merge caller's skip list with own preview/active objects
        all_skip = list(skip_objects) if skip_objects else []
        for attr in ("_preview_obj", "_active_obj"):
            obj = getattr(self, attr, None)
            if obj is not None and obj not in all_skip:
                all_skip.append(obj)

        # Call projector directly to preserve the (pt, wp_hit) tuple.
        # DMBase.get_mouse_plane_pt strips wp_hit before returning, so wp_hit
        # would always be None and working_plane would never update on click.
        result = self.projector.get_mouse_plane_pt(
            event_dict,
            place_on_geometry=getattr(self, "place_on_geometry", False),
            working_plane=getattr(self, "working_plane", None),
            skip_objects=all_skip or None
        )
        if isinstance(result, tuple):
            pos, wp_hit = result
        else:
            pos, wp_hit = result, None
            
        if wp_hit is not None:
            # Update plane if one isn't set, or if we are in the initial 'Idle' state
            # where we want to snap to whatever surface is under the first click.
            if self.working_plane is None or getattr(self, "state", 1) == 0:
                dm_logger.debug(f"DEBUG _resolve_wp_click: HIT on {type(wp_hit)}. state={getattr(self, 'state', None)}")
                
                # Determine if this is a "real" persistent WorkPlane object
                is_real_wp = False
                if hasattr(wp_hit, "Proxy") and wp_hit.Proxy.__class__.__name__ == "DMWorkPlane":
                    is_real_wp = True
                
                if hasattr(wp_hit, "getGlobalPlacement"):
                    self.working_plane = wp_hit.getGlobalPlacement()
                elif hasattr(wp_hit, "Placement"):
                    self.working_plane = wp_hit.Placement
                else:
                    # Assume it's already a FreeCAD.Placement or None
                    self.working_plane = wp_hit
                
                self._working_plane_is_fallback = not is_real_wp
        elif self.working_plane is None and getattr(self, "state", 1) == 0:
            # If no hit, and no current plane, use the class-level fallback if available.
            # We look for _last_working_plane on the subclass.
            last_wp = getattr(type(self), "_last_working_plane", None)
            if last_wp:
                self.working_plane = last_wp
            
        return pos

    def _schedule_update(self, callback, interval_ms=None):
        """Throttled single-shot update. Drops duplicate calls within the interval."""
        if getattr(self, "_update_pending", False):
            return
        if interval_ms is None:
            from core.dm_object import get_interactive_throttle_interval
            interval_ms = int(get_interactive_throttle_interval() * 1000)
        self._update_pending = True
        QtCore.QTimer.singleShot(interval_ms, callback)

    def _on_committed(self, obj):
        """Called after a tool successfully commits its object. Override to customize."""
        if obj:
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(obj.Document.Name, obj.Name)

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
        from PySide import QtCore
        QtCore.QTimer.singleShot(0, self._do_terminate)

    def _do_terminate(self):
        """
        Standard cleanup for all DM tools.
        Call chain: Subclass cleanup -> super()._do_terminate()
        """
        from core.dm_tool_manager import DMToolManager
        tool_mgr = DMToolManager.get_instance()
        if tool_mgr.get_active_tool() is self:
            tool_mgr.set_active_tool(None)
        
        if hasattr(self, "_stop_drag_timer"):
            self._stop_drag_timer()
            
        self._restore_cursor()
        
        self._finish_scheduled = False
        self._terminated = True
        try:
            if self.callback:
                self.view.removeEventCallback("SoEvent", self.callback)
                self.callback = None
            
            # Close task panel if open
            if getattr(self, "_dialog_open", False):
                import FreeCADGui
                FreeCADGui.Control.closeDialog()
                self._dialog_open = False
            
            # Clean up active/preview objects if not finished
            if not getattr(self, "_finished", False):
                obj_to_remove = getattr(self, "_active_obj", None) or getattr(self, "_preview_obj", None)
                if obj_to_remove:
                    doc = obj_to_remove.Document or self.doc or FreeCAD.ActiveDocument
                    if doc and doc.getObject(obj_to_remove.Name):
                        dm_logger.debug(f"DMBase._do_terminate: Removing unfinished object {obj_to_remove.Name}")
                        import FreeCADGui
                        FreeCADGui.updateGui()
                    if hasattr(self, "_preview_obj"): self._preview_obj = None
                    if hasattr(self, "_active_obj"): self._active_obj = None

            if self.view:
                self.view.redraw()
            import FreeCADGui
            FreeCADGui.updateGui()

        except Exception as e:
            dm_logger.debug(f"DMBase._do_terminate: Cleanup failed: {e}")


    def finish(self):
        """Standard 'Accept' behavior. Override in subclasses to commit and reset/terminate."""
        self.terminate()

    def is_in_progress(self):
        """Returns True if the tool has active state/points that can be 'Accepted'."""
        return False

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
    # Geometry helpers — Delegated to ViewProjector
    # ------------------------------------------------------------------

    def get_mouse_world_pos(self, event_dict, plane_normal=None, plane_point=None):
        """Delegated to ViewProjector (which uses DMInputManager for rays)."""
        return self.projector.get_mouse_world_pos(
            event_dict, plane_normal, plane_point, 
            place_on_geometry=getattr(self, "place_on_geometry", False)
        )

    def get_visible_workplanes(self):
        return self.projector.get_visible_workplanes()

    def get_base_plane(self, wp_obj=None):
        return self.projector.get_base_plane(wp_obj)

    def get_mouse_plane_pt(self, event_dict):
        """Delegated to ViewProjector (which uses DMInputManager for rays).

        Automatically excludes any preview/active object from SDF hit testing
        so that creation tools don't hit-test against themselves.
        """
        # Build skip list from preview/active objects to avoid self-intersection
        skip = []
        for attr in ("_preview_obj", "_active_obj"):
            obj = getattr(self, attr, None)
            if obj is not None:
                skip.append(obj)

        # If we are in state 0 (hovering) and the current plane is just a fallback 
        # (like a previous face snap or viewport alignment), we ignore it so 
        # that get_mouse_plane_pt can recalculate the best transient snap/alignment.
        active_plane = getattr(self, "working_plane", None)
        if getattr(self, "state", 0) == 0 and getattr(self, "_working_plane_is_fallback", True):
            active_plane = None

        result = self.projector.get_mouse_plane_pt(
            event_dict,
            place_on_geometry=getattr(self, "place_on_geometry", False),
            working_plane=active_plane,
            skip_objects=skip or None
        )
        if isinstance(result, tuple):
            pt, wp = result
            return pt
        return result



    # ------------------------------------------------------------------
    # Event loop & Overridable Input Hooks
    # ------------------------------------------------------------------

    def on_button1_down(self, event_dict):
        return self.handle_click(event_dict)

    def on_button2_down(self, event_dict):
        return False

    def on_button3_down(self, event_dict):
        # Double-fire guard: Right-click arrives via both Qt and Coin3D.
        import time
        now = time.monotonic()
        if now - getattr(self, "_last_btn3_time", 0.0) < 0.05:
            return True # Duplicate fire, consume silently
        self._last_btn3_time = now

        # If Middle Mouse or Shift is held, it's likely a view rotation chord. Do not finish!
        if DMInputManager.get_instance()._middle_mouse_down or DMInputManager.get_instance().is_shift_down():
            return False
            
        if hasattr(self, 'on_tool_menu') and self.on_tool_menu():
            return True
            
        if not getattr(self, '_finish_scheduled', False):
            self._finish_scheduled = True
            if self.is_in_progress():
                dm_logger.debug(f"{self.__class__.__name__}: RMB Accept (in-progress)")
                QtCore.QTimer.singleShot(0, self.finish)
            else:
                dm_logger.debug(f"{self.__class__.__name__}: RMB Exit (idle)")
                QtCore.QTimer.singleShot(0, self.terminate)
        return True # Consume Press

    def on_button1_up(self, event_dict):
        return False

    def on_button2_up(self, event_dict):
        return False

    def on_button3_up(self, event_dict):
        return True # Consume release to suppress FreeCAD context menu

    def event_cb(self, event_dict):
        try:
            event_type = event_dict.get("Type", "Unknown")

            if event_type == "SoMouseButtonEvent":
                btn = event_dict.get("Button", "None")
                state = event_dict.get("State", "None")
                
                if state == "DOWN":
                    if btn == "BUTTON1": return self.on_button1_down(event_dict)
                    elif btn == "BUTTON2": return self.on_button2_down(event_dict)
                    elif btn == "BUTTON3": return self.on_button3_down(event_dict)
                    return False
                
                elif state == "UP":
                    if btn == "BUTTON1": return self.on_button1_up(event_dict)
                    elif btn == "BUTTON2": return self.on_button2_up(event_dict)
                    elif btn == "BUTTON3": return self.on_button3_up(event_dict)
                    return False
            elif event_type == "SoLocation2Event":
                self.handle_move(event_dict)
            elif event_type == "SoKeyboardEvent":
                    return self.handle_keyboard(event_dict)
            else:
                # For tools that need to update on camera move (like WorkPlaneCreator preview)
                if self.state == 1:
                    # We only care about this if it's NOT a mouse click (handled above)
                    # and we want to refresh the orientation/position
                    try:
                        # Synthetic event dict for handle_move
                        mouse_pos = DMInputManager.get_instance().get_mouse_pos(None)
                        self.handle_move({"QtPosition": mouse_pos})
                    except Exception as e:
                        dm_logger.debug(f"event_cb: Synthetic handle_move failed: {e}")
            return False
        except Exception:
            dm_logger.exception("event_cb error")
            return False

    def handle_keyboard(self, event_dict):
        key = str(event_dict.get("Key", "None")).upper()
        
        # ESC to cancel
        if key in ["ESCAPE", "ESC"]:
            self.terminate()
            return True
            
        # ENTER/RETURN to finish
        if key in ["ENTER", "RETURN", "PAD_ENTER"]:
            self.finish()
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
            
        # Tool Option 0 (Shift)
        if "SHIFT" in key:
            self.on_tool_option_0()
            return False
            
        # Tool Option 1 (Ctrl)
        if "CONTROL" in key or "CTRL" in key:
            self.on_tool_option_1()
            return False
            
        # Snapping Menu (S)
        if key == "S":
            if hasattr(self, 'get_snapping_menu'):
                from core.dm_menu import DMMenuManager
                DMMenuManager.get_instance().trigger_dynamic_menu(self.get_snapping_menu())
                return True

        # Tool Menu (D)
        if key == "D":
            items = None
            if hasattr(self, 'get_context_menu'):
                items = self.get_context_menu()
            if items:
                from core.dm_menu import DMMenuManager
                DMMenuManager.get_instance().trigger_dynamic_menu(items)
                return True
            self.on_tool_menu()
            return True
            
        return False

    def on_tool_option_0(self):
        pass
        
    def on_tool_option_1(self):
        pass
        
    def on_tool_menu(self):
        pass

    def toggle_place_on_geometry(self, checked=None):
        if checked is not None:
            DMBase.place_on_geometry = checked
        else:
            DMBase.place_on_geometry = not DMBase.place_on_geometry
        if hasattr(self, "update_ui"):
            self.update_ui()

    def get_context_menu(self, event_dict=None):
        return [
            ("Place on Geometry", self.toggle_place_on_geometry, DMBase.place_on_geometry)
        ]

    def reset_state(self):
        """Resets the tool to its initial idle state (state 0)."""
        self.state = 0
        self.start_point = None
        self.current_point = None
        self.height = 0.0
        self._finish_scheduled = False
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
            
            if btn != "BUTTON1":
                return False

            # Use get_mouse_plane_pt to respect workplanes and snapping
            result = self.get_mouse_plane_pt(event_dict)
            if isinstance(result, tuple):
                pt, wp_hit = result
            else:
                pt, wp_hit = result, None

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
                
                # Setup working_plane if we hit something or use fallback
                if wp_hit:
                    if hasattr(wp_hit, "getGlobalPlacement"):
                        self.working_plane = wp_hit.getGlobalPlacement()
                    elif hasattr(wp_hit, "Placement"):
                        self.working_plane = wp_hit.Placement
                    else:
                        self.working_plane = wp_hit
                elif not self.working_plane:
                    # Fallback to camera facing if nothing hit and no plane set
                    n, o = self.get_base_plane()
                    rot = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), n)
                    self.working_plane = FreeCAD.Placement(pt, rot)
                
                self.state = 2
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
        except Exception as e:
            dm_logger.exception(f"handle_click error: {e}")
            return False

    def handle_move(self, event_dict):
        if getattr(self, "state", 0) == 0:
            # Idle hover: dynamic snapping to surfaces/planes
            self.current_point = self._resolve_wp_click(event_dict)
            self.on_move_state_0(event_dict)
            self.update_preview()
            self.update_ui()
            
        elif self.state == 1:
            self.current_point = self.get_mouse_plane_pt(event_dict)
            self.on_move_state_1(event_dict)
            self.update_preview()
            self.update_ui()
            
        elif self.state == 2:
            self.on_move_state_2(event_dict)
            self.update_preview()
            self.update_ui()

    def on_move_state_0(self, event_dict):
        """Hook for subclasses to update internal parameters in state 0."""
        pass

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
        """Delegated to ViewProjector."""
        geo = self.projector.get_geometry_info(event_dict)
        if geo:
            world_hit, world_n, obj, subname = geo
            return obj.Name, subname
        return None, None

    def _compute_handle_radius(self, ref_pt=None):
        """Sphere radius in world units — sized to look ~8 px on screen."""
        try:
            cam = self.view.getCameraNode()
            viewer = self.view.getViewer()
            vp_h = 800.0
            try:
                if hasattr(viewer, "getGlxSize"):
                    sz = viewer.getGlxSize(); vp_h = float(sz[1])
                elif hasattr(viewer, "getSize"):
                    sz = viewer.getSize()
                    vp_h = float(sz[1] if isinstance(sz, (list, tuple)) else sz.height())
            except Exception:
                pass
            if hasattr(cam, "height"):
                half_world_h = cam.height.getValue() / 2.0
            elif hasattr(cam, "heightAngle"):
                cam_vals = cam.position.getValue()
                cam_pos_v = FreeCAD.Vector(cam_vals[0], cam_vals[1], cam_vals[2])
                ref = ref_pt if ref_pt is not None else FreeCAD.Vector(0, 0, 0)
                depth = (ref - cam_pos_v).Length
                half_world_h = depth * math.tan(cam.heightAngle.getValue() / 2.0)
            else:
                half_world_h = 100.0
            px_per_world = (vp_h / 2.0) / max(half_world_h, 1e-6)
            return max(2.0, 8.0 / px_per_world)
        except Exception:
            return 5.0

# ─────────────────────────────────────────────────────────────────────────────
# NURBSPrimitiveCreator
# ─────────────────────────────────────────────────────────────────────────────

class NURBSPrimitiveCreator(DMBase):
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
            # Guard against re-entrant calls (can happen if FreeCADGui.updateGui()
            # inside create_dm_object processes Qt events that fire our timer again).
            if getattr(self, "_creating_obj", False):
                return
            self._creating_obj = True
            try:
                from core.dm_object import create_dm_object
                self._active_obj = create_dm_object("DMObject", shape_type, local_params, placement=active_placement)
                if self._active_obj:
                    self._active_obj.Label = shape_type.capitalize()
            finally:
                self._creating_obj = False
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
        """Clean up: defer to DMBase._do_terminate."""
        if self._terminated:
            return
        super().terminate()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def finish(self):
        """Schedule the finalization to happen safely outside the event loop."""
        if self._finished:
            return
        # If we have an active object, finalize it but DON'T terminate yet if we want to repeat.
        # Subclasses (like PointCreator) might override this to keep the tool active.
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



    # ------------------------------------------------------------------
    # Snapping
    # ------------------------------------------------------------------

    def on_tool_option_1(self):
        """Ctrl key → cycle snapping type."""
        self.toggle_snapping()

    def set_snap_type(self, type_name):
        self.snap_type = type_name
        dm_logger.info(f"Snapping type set to {self.snap_type}")

    def toggle_snapping(self):
        self.snap_enabled = not self.snap_enabled
        state = "enabled" if self.snap_enabled else "disabled"
        dm_logger.info(f"Snapping is now {state} (Type: {self.snap_type})")

    def get_snapping_menu(self):
        return [
            ("Workplane Grid", lambda: self.set_snap_type("Workplane Grid")),
            ("Workplane Radius", lambda: self.set_snap_type("Workplane Radius")),
            ("Vertex Snapping", lambda: self.set_snap_type("Vertex Snapping"))
        ]




