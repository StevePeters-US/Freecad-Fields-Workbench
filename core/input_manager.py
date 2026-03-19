import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui
import math
from core import dm_logger

class DMInputManager(QtCore.QObject):
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = DMInputManager()
        return cls._instance


    def __init__(self):
        super().__init__()
        self._left_mouse_down = False
        self._middle_mouse_down = False
        self._right_mouse_down = False
        self._shift_down = False
        self._control_down = False
        self._last_qt_pos = (0, 0)
        self._is_initialized = False
        self._sel_observer = None

    def _is_menu_active(self):
        from core.dm_menu import DMMenuManager
        return DMMenuManager.get_instance().is_menu_active()

    def eventFilter(self, obj, event):
        try:
            # Drop auto-repeat events to prevent multiple menus
            if getattr(event, "isAutoRepeat", lambda: False)():
                if event.type() in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease, QtCore.QEvent.ShortcutOverride):
                    return True

            # [Event Owner: Qt Event Filter] Coordinate tracking
            if event.type() in [QtCore.QEvent.MouseMove, QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease]:
                try:
                    # Qt events use Logical Pixels. Coin3D / FreeCAD getRay expects Physical Pixels.
                    # Multiply by devicePixelRatio to fix 'point not under mouse' on High DPI screens.
                    ratio = 1.0
                    if hasattr(obj, "devicePixelRatioF"):
                        ratio = float(obj.devicePixelRatioF())
                    elif hasattr(obj, "devicePixelRatio"):
                        ratio = float(obj.devicePixelRatio())
                        
                    self._last_qt_pos = (int(event.pos().x() * ratio), int(event.pos().y() * ratio))
                except Exception as e:
                    dm_logger.debug(f"Coordinate mapping error: {e}")
                    self._last_qt_pos = (int(event.pos().x()), int(event.pos().y()))
            
            # [Event Owner: Qt Event Filter] Modifier state
            if event.type() in [QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease]:
                if event.key() == QtCore.Qt.Key_Shift:
                    self._shift_down = (event.type() == QtCore.QEvent.KeyPress)
                elif event.key() == QtCore.Qt.Key_Control:
                    self._control_down = (event.type() == QtCore.QEvent.KeyPress)

            # [Event Owner: Qt Event Filter] Button state tracking
            if event.type() in [QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease]:
                is_press = (event.type() == QtCore.QEvent.MouseButtonPress)
                if event.button() == QtCore.Qt.LeftButton:
                    self._left_mouse_down = is_press
                elif event.button() == QtCore.Qt.MiddleButton:
                    self._middle_mouse_down = is_press
                elif event.button() == QtCore.Qt.RightButton:
                    self._right_mouse_down = is_press

            from core.dm_tool_manager import DMToolManager
            tool = DMToolManager.get_instance().get_active_tool()

            # --- Dispatch to Active Tool ---
            if tool:
                # Construct clean Qt-native event dict
                event_dict = {
                    "Button": event.button() if hasattr(event, "button") else QtCore.Qt.NoButton,
                    "Modifiers": event.modifiers() if hasattr(event, "modifiers") else QtCore.Qt.NoModifier,
                    "Position": self._last_qt_pos,
                }
                
                if event.type() == QtCore.QEvent.MouseMove:
                    consumed = tool.on_mouse_move(event_dict)
                    if self._middle_mouse_down:
                        return False # Pass through for FreeCAD navigation
                    return bool(consumed) if consumed is not None else True
                
                elif event.type() == QtCore.QEvent.MouseButtonPress:
                    consumed = tool.on_mouse_press(event_dict)
                    return bool(consumed)
                
                elif event.type() == QtCore.QEvent.MouseButtonRelease:
                    consumed = tool.on_mouse_release(event_dict)
                    return bool(consumed)
                
                elif event.type() == QtCore.QEvent.MouseButtonDblClick:
                    consumed = tool.on_mouse_press(event_dict)
                    return bool(consumed)
                    
                elif event.type() == QtCore.QEvent.KeyPress:
                    event_dict["Key"] = event.key()
                    event_dict["Text"] = event.text()
                    if tool.on_key_press(event_dict):
                        return True
                    # Let through if tool didn't consume it
                    
                elif event.type() == QtCore.QEvent.KeyRelease:
                    event_dict["Key"] = event.key()
                    if tool.on_key_release(event_dict):
                        return True
                        
                elif event.type() == QtCore.QEvent.ContextMenu:
                    tool.on_context_menu(event_dict)
                    return True # Swallowed

                elif event.type() == QtCore.QEvent.ShortcutOverride:
                    # Claim 'S', 'D', 'E' for tool
                    text = event.text().lower() if hasattr(event, "text") else ""
                    if text in ['s', 'd', 'e']:
                        event.accept()
                        return True

            # --- Global Handling (No tool active) ---
            else:
                # SDF object selection on LMB press
                if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                    from core.dm_selection_manager import DMSelectionManager
                    DMSelectionManager.get_instance().try_sdf_selection(self._last_qt_pos)
                    # Let through to FreeCAD

                # Double-click to edit SDF
                elif event.type() == QtCore.QEvent.MouseButtonDblClick and event.button() == QtCore.Qt.LeftButton:
                    from core.dm_selection_manager import DMSelectionManager
                    sel_mgr = DMSelectionManager.get_instance()
                    sel_mgr.try_sdf_selection(self._last_qt_pos)
                    sel = FreeCADGui.Selection.getSelection()
                    if sel:
                        obj = sel[0]
                        proxy_name = getattr(getattr(obj, "Proxy", None), "__class__", type(None)).__name__
                        if proxy_name == "DMObjectProxy" and getattr(obj, "ShapeType", "") == "frep":
                            from tools.edit_tool import FRepEditTool
                            FRepEditTool().activate()
                            return True

                # Global hotkeys
                elif event.type() == QtCore.QEvent.KeyPress:
                    key = event.key()
                    text = event.text().lower() if hasattr(event, "text") else ""
                    
                    if (key == QtCore.Qt.Key_D or text == 'd') and not self._is_menu_active():
                        from core.dm_menu import DMMenuManager
                        if not DMMenuManager.get_instance()._ignore_hotkeys:
                            DMMenuManager.get_instance().show_context_menu()
                            return True

                    if (key == QtCore.Qt.Key_E or text == 'e') and not self._is_menu_active():
                        sel = FreeCADGui.Selection.getSelection()
                        if sel:
                            obj = sel[0]
                            proxy_name = getattr(getattr(obj, "Proxy", None), "__class__", type(None)).__name__
                            if proxy_name == "DMWorkPlane":
                                from tools.work_plane_tool import WorkPlaneCreator
                                WorkPlaneCreator(); return True
                            elif proxy_name == "DMObjectProxy":
                                st = getattr(obj, "ShapeType", "")
                                if st == "curve":
                                    from tools.edit_tool import EditTool
                                    EditTool().activate(); return True
                                elif st == "frep":
                                    from tools.edit_tool import FRepEditTool
                                    FRepEditTool().activate(); return True

                # ShortcutOverride for 'E' when no tool
                elif event.type() == QtCore.QEvent.ShortcutOverride:
                    text = event.text().lower() if hasattr(event, "text") else ""
                    if text == 'e':
                        sel = FreeCADGui.Selection.getSelection()
                        if any(hasattr(o, "Proxy") and getattr(o.Proxy, "__class__", None).__name__ in ("DMWorkPlane", "DMObjectProxy") for o in sel):
                            event.accept(); return True

                # Suppress FreeCAD context menu if DM menu is open
                elif event.type() == QtCore.QEvent.ContextMenu:
                    if self._is_menu_active():
                        return True

        except Exception as e:
            dm_logger.error(f"DMInputManager eventFilter error: {e}")

        return False

    def get_mouse_pos(self, event_dict=None):
        """Standardized Top-Left coordinate retrieval for all tools.
        After refactor, we standardize on Qt-native coordinates.
        """
        if event_dict and "Position" in event_dict:
            self._last_qt_pos = event_dict["Position"]
        return self._last_qt_pos

    def get_drag_delta(self, start_pos, current_event_dict):
        """Returns (dx, dy) where dy is screen-space (unflipped)."""
        curr_pos = self.get_mouse_pos(current_event_dict)
        return (curr_pos[0] - start_pos[0], curr_pos[1] - start_pos[1])

    def get_qt_cursor_pos(self, view=None):
        """Returns the current mouse position in Top-Left coordinates."""
        return self._last_qt_pos

    def is_shift_down(self):
        """Single source of truth for Shift key state."""
        return self._shift_down

    def is_ctrl_down(self):
        """Single source of truth for Control key state."""
        return self._control_down

    def is_left_mouse_down(self):
        """Single source of truth for left mouse button state."""
        return self._left_mouse_down

    def is_middle_mouse_down(self):
        """Single source of truth for middle mouse button state."""
        return self._middle_mouse_down

    def get_ray(self, view, event_dict=None):
        """Centralized ray generation from screen coordinates."""
        if not view: return None, None
        pos = self.get_mouse_pos(event_dict)
        x, y = int(pos[0]), int(pos[1])

        try:
            # 1. Try FreeCAD's native getRay (0.20+)
            if hasattr(view, "getRay"):
                ray = view.getRay(x, y)
                if ray:
                    r_base, r_dir = None, None
                    if isinstance(ray, dict):
                        r_base, r_dir = FreeCAD.Vector(ray["base"]), FreeCAD.Vector(ray["dir"])
                    elif isinstance(ray, tuple):
                        r_base, r_dir = FreeCAD.Vector(ray[0]), FreeCAD.Vector(ray[1])
                    
                    if isinstance(ray, dict):
                        return (FreeCAD.Vector(ray["base"]), FreeCAD.Vector(ray["dir"]))
                    elif isinstance(ray, tuple):
                        return (FreeCAD.Vector(ray[0]), FreeCAD.Vector(ray[1]))

            # 2. Support view.getPoint fallback (used by WorkPlaneManager)
            # We synthesize a ray direction from camera to focus point if getPoint is used
            scene_pt = None
            try: scene_pt = view.getPoint(x, y)
            except: pass

            cam = view.getCameraNode()
            if not cam: return None, None
            
            p = cam.position.getValue()
            ray_p = FreeCAD.Vector(p[0], p[1], p[2])
            
            if scene_pt:
                if hasattr(cam, "height"):
                    # Orthographic: all rays are parallel to the view direction.
                    # scene_pt is already the correct lateral position; use it as
                    # the ray origin so the plane intersection is exact.
                    vd = view.getViewDirection()
                    ray_d = FreeCAD.Vector(vd[0], vd[1], vd[2])
                    ray_d.normalize()
                    return scene_pt, ray_d
                else:
                    # Perspective: ray goes from camera through scene_pt.
                    ray_d = scene_pt - ray_p
                    ray_d.normalize()
                    return ray_p, ray_d

            # 3. Pure Math Fallback (Directly from Camera)
            rot = cam.orientation.getValue()
            viewer = view.getViewer()
            w, h = 1000.0, 1000.0
            if hasattr(viewer, "getGlxSize"):
                sz = viewer.getGlxSize(); w, h = float(sz[0]), float(sz[1])
            elif hasattr(viewer, "getSize"):
                sz = viewer.getSize(); w = float(sz.width() if hasattr(sz, "width") else sz[0]); h = float(sz.height() if hasattr(sz, "height") else sz[1])
            
            aspect = w / h
            quat = rot.getValue()
            qx, qy, qz, qw = quat[0], quat[1], quat[2], quat[3]
            
            # Forward vector
            fx, fy, fz = 2.0*(qx*qz + qw*qy), 2.0*(qy*qz - qw*qx), 1.0 - 2.0*(qx*qx + qy*qy)
            forward = FreeCAD.Vector(-fx, -fy, -fz)
            # Up vector
            ux, uy, uz = 2.0*(qx*qy - qw*qz), 1.0 - 2.0*(qx*qx + qz*qz), 2.0*(qy*qz + qw*qx)
            up = FreeCAD.Vector(ux, uy, uz)
            # Right vector
            rx, ry, rz = 1.0 - 2.0*(qy*qy + qz*qz), 2.0*(qx*qy + qw*qz), 2.0*(qx*qz - qw*qy)
            right = FreeCAD.Vector(rx, ry, rz)

            if hasattr(cam, "heightAngle"): # Perspective
                ha = cam.heightAngle.getValue()
                ndc_x, ndc_y = (x/w)*2.0 - 1.0, 1.0 - (y/h)*2.0
                plane_h = math.tan(ha/2.0); plane_w = plane_h * aspect
                ray_d = forward + right*(ndc_x*plane_w) + up*(ndc_y*plane_h)
                ray_d.normalize()
                return ray_p, ray_d
            elif hasattr(cam, "height"): # Ortho
                height = cam.height.getValue(); width = height * aspect
                ndc_x, ndc_y = (x/w)*2.0 - 1.0, 1.0 - (y/h)*2.0
                ray_p_ortho = ray_p + right*(ndc_x*width/2.0) + up*(ndc_y*height/2.0)
                forward.normalize()  # modifies in-place; returns None — do not use return value
                return ray_p_ortho, forward

        except Exception as e:
            dm_logger.debug(f"DMInputManager.get_ray failed: {e}")
        return None, None

    def get_projected_point(self, view, base_point_3d, normal_3d, event_dict):
        """
        Calculates the 3D point along (base_point_3d + t*normal_3d) that corresponds
        to the current mouse position.  Uses a screen-space projection so it is 1:1
        with mouse movement in both perspective and orthographic views.

        Algorithm:
          1. Derive camera right/up vectors from the camera's orientation quaternion.
          2. Project the 3D normal into screen-space (right, up) components.
          3. Compute pixels-per-world-unit scale from camera FOV/height.
          4. Measure mouse delta since drag start along the projected normal direction.
          5. Convert pixel delta → world-space offset along normal → return new point.
        """
        if not view: return base_point_3d

        try:
            normal_3d_copy = FreeCAD.Vector(normal_3d)
            n_len = normal_3d_copy.Length
            if n_len < 1e-10:
                return base_point_3d
            normal_3d_copy.normalize()

            # --- Step 1: Camera basis vectors from orientation quaternion ---
            cam = view.getCameraNode()
            if not cam:
                return base_point_3d

            rot = cam.orientation.getValue()
            qx, qy, qz, qw = rot.getValue()
            # Right (X screen axis) and Up (Y screen axis) in world space
            ux = 2*(qx*qy - qw*qz); uy = 1 - 2*(qx*qx + qz*qz); uz = 2*(qy*qz + qw*qx)
            rx = 1 - 2*(qy*qy + qz*qz); ry = 2*(qx*qy + qw*qz); rz = 2*(qx*qz - qw*qy)
            cam_right = FreeCAD.Vector(rx, ry, rz)
            cam_up    = FreeCAD.Vector(ux, uy, uz)

            # --- Step 2: Project the world normal into screen-space ---
            # scr_nx = how much the normal points in the screen-right direction
            # scr_ny = how much it points in the screen-up direction (positive = up)
            scr_nx = normal_3d_copy.dot(cam_right)
            scr_ny = normal_3d_copy.dot(cam_up)
            # Qt Y is positive-DOWN, so negate the up component for pixel space
            scr_ny_px = -scr_ny   # screen-up world → screen-top Qt pixel direction

            scr_len_ndc = math.sqrt(scr_nx*scr_nx + scr_ny*scr_ny)
            if scr_len_ndc < 1e-6:
                return base_point_3d  # Normal points straight at camera – degenerate

            # --- Step 3: Pixels-per-world-unit scale ---
            # Get viewport size
            viewer = view.getViewer()
            vp_w, vp_h = 1000.0, 1000.0
            try:
                if hasattr(viewer, "getGlxSize"):
                    sz = viewer.getGlxSize(); vp_w, vp_h = float(sz[0]), float(sz[1])
                elif hasattr(viewer, "getSize"):
                    sz = viewer.getSize()
                    vp_w = float(sz[0] if isinstance(sz, (list,tuple)) else sz.width())
                    vp_h = float(sz[1] if isinstance(sz, (list,tuple)) else sz.height())
            except Exception:
                pass

            # World units that span half the viewport height
            if hasattr(cam, "height"):           # Orthographic
                half_world_h = cam.height.getValue() / 2.0
            elif hasattr(cam, "heightAngle"):     # Perspective
                # Distance from camera to base point
                cam_p_vals = cam.position.getValue()
                cam_pos = FreeCAD.Vector(cam_p_vals[0], cam_p_vals[1], cam_p_vals[2])
                depth = (base_point_3d - cam_pos).Length
                fov = cam.heightAngle.getValue()
                half_world_h = depth * math.tan(fov / 2.0)
            else:
                half_world_h = 100.0

            # pixels per world unit: viewport_half_height_px / half_world_height_world
            px_per_world = (vp_h / 2.0) / max(half_world_h, 1e-6)

            # scr_len in pixels for 1 world unit along normal
            scr_len_px = scr_len_ndc * px_per_world

            # --- Step 4: Mouse pixel delta since drag start ---
            curr_pos = self.get_mouse_pos(event_dict)
            drag_start = event_dict.get("DragStart2D") if event_dict else None
            if drag_start is None:
                drag_start = curr_pos   # No anchor → zero delta (safe fallback)

            dx = curr_pos[0] - drag_start[0]          # positive = mouse moved right
            dy = curr_pos[1] - drag_start[1]          # positive = mouse moved down (Qt)

            # Project pixel delta onto screen-space normal direction
            # (scr_nx in right direction, scr_ny_px in down direction)
            scr_len_unit = math.sqrt(scr_nx*scr_nx + scr_ny_px*scr_ny_px)
            if scr_len_unit < 1e-9:
                return base_point_3d
            pixel_delta = (dx * scr_nx + dy * scr_ny_px) / scr_len_unit

            # --- Step 5: Convert to world offset ---
            world_delta = pixel_delta / max(scr_len_px, 1e-6)

            return base_point_3d + normal_3d_copy * world_delta

        except Exception as e:
            dm_logger.debug(f"get_projected_point failed: {e}")
        return base_point_3d

    def get_axis_point(self, view, base, normal, event_dict):
        """
        Returns the point on the axis (base + t * normal) that is closest to
        the mouse ray.  This is the geometrically correct way to drag out a
        height: the result literally sits where the cursor points in 3D space,
        independent of camera angle or zoom level.

        Uses the standard closest-point-of-two-skew-lines formula:
            w = ray_origin - base
            b = ray_dir · normal
            s = (w·normal - b*(w·ray_dir)) / (1 - b²)
            result = base + s * normal
        """
        ray_p, ray_d = self.get_ray(view, event_dict)
        if ray_p is None or ray_d is None:
            return base
        try:
            n = FreeCAD.Vector(normal)
            n.normalize()

            w = ray_p - base          # vector from axis origin to ray origin
            b = ray_d.dot(n)          # cos(angle) between ray dir and axis
            denom = 1.0 - b * b       # sin²(angle); zero when parallel

            if abs(denom) < 1e-8:
                # Axis is pointing straight at the camera — no depth info.
                return base

            e = w.dot(n)
            d = w.dot(ray_d)
            s = (e - b * d) / denom   # signed distance along axis

            return base + n * s

        except Exception as ex:
            dm_logger.debug(f"get_axis_point failed: {ex}")
            return base

    def get_view_transform(self, view, target_pt):
        """
        Returns a Placement parallel to the screen at target_pt.
        Consolidates matrix math for camera-facing visuals.
        """
        if not view: return FreeCAD.Placement()
        
        try:
            vd = view.getViewDirection()
            ud = view.getUpDirection()
            
            # Basis vectors
            z_axis = FreeCAD.Vector(-vd[0], -vd[1], -vd[2]); z_axis.normalize()
            up_axis = FreeCAD.Vector(ud[0], ud[1], ud[2]); up_axis.normalize()
            x_axis = up_axis.cross(z_axis); x_axis.normalize()
            y_axis = z_axis.cross(x_axis); y_axis.normalize()
            
            m = FreeCAD.Matrix(
                x_axis.x, y_axis.x, z_axis.x, target_pt.x,
                x_axis.y, y_axis.y, z_axis.y, target_pt.y,
                x_axis.z, y_axis.z, z_axis.z, target_pt.z,
                0.0,      0.0,      0.0,      1.0
            )
            return FreeCAD.Placement(m)
        except Exception as e:
            dm_logger.debug(f"get_view_transform failed: {e}")
        return FreeCAD.Placement(target_pt, FreeCAD.Rotation())

    def scale_drag_delta(self, delta_px):
        """Scales a pixel delta to model units (rough approximation)."""
        # In this project, tools often use a scale factor like 4.0 for sensitivity
        return delta_px / 4.0


    def initialize(self):
        try:
            if not getattr(self, "_is_initialized", False):
                QtGui.QApplication.instance().installEventFilter(self)
                from core.dm_tool_manager import DMSelectionObserver
                self._sel_observer = DMSelectionObserver()
                FreeCADGui.Selection.addObserver(self._sel_observer)
                self._is_initialized = True
        except Exception as e:
            dm_logger.error(f"DMInputManager initialization error: {e}")

    def restore(self):
        try:
            if getattr(self, "_is_initialized", False):
                QtGui.QApplication.instance().removeEventFilter(self)
                if self._sel_observer is not None:
                    FreeCADGui.Selection.removeObserver(self._sel_observer)
                    self._sel_observer = None
                self._is_initialized = False
        except Exception as e:
            dm_logger.error(f"DMInputManager restore error: {e}")
