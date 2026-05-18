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
        self._device_pixel_ratio = 1.0
        self._is_initialized = False
        self._sel_observer = None
        self._right_click_in_tool = False # State to suppress post-tool menus

    def _is_menu_active(self):
        from core.dm_menu import DMMenuManager
        return DMMenuManager.get_instance().is_menu_active()

    def eventFilter(self, obj, event):
        from core.dm_tool_manager import DMToolManager
        try:
            # Drop auto-repeat events to prevent multiple menus
            if getattr(event, "isAutoRepeat", lambda: False)():
                if event.type() in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease, QtCore.QEvent.ShortcutOverride):
                    return True

            # [Event Owner: Qt Event Filter] Coordinate tracking & Viewport detection
            # We must be extremely careful here. eventFilter is called on EVERY event.
            # Avoid expensive FreeCADGui calls on mouse move.
            
            # Key events are always processed for state tracking
            is_key_event = event.type() in [QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease]
            is_mouse_event = event.type() in [QtCore.QEvent.MouseMove, QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease, QtCore.QEvent.MouseButtonDblClick]
            
            if not is_key_event and not is_mouse_event:
                return False

            # [Event Owner: Qt Event Filter] Modifier & Button state tracking (GLOBAL)
            # We track these BEFORE any 'return False' to ensure drag/modifier state is always correct,
            # even if the mouse leaves the viewport or the press started on a decoration.
            if is_key_event:
                if event.key() == QtCore.Qt.Key_Shift:
                    self._shift_down = (event.type() == QtCore.QEvent.KeyPress)
                elif event.key() == QtCore.Qt.Key_Control:
                    self._control_down = (event.type() == QtCore.QEvent.KeyPress)
                # Ensure we refresh the viewport cache if context might have changed
                self._cached_viewport = None

            if event.type() in [QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease]:
                is_press = (event.type() == QtCore.QEvent.MouseButtonPress)
                if event.button() == QtCore.Qt.LeftButton:
                    self._left_mouse_down = is_press
                elif event.button() == QtCore.Qt.MiddleButton:
                    self._middle_mouse_down = is_press
                elif event.button() == QtCore.Qt.RightButton:
                    if is_press:
                        self._right_click_in_tool = (DMToolManager.get_instance().get_active_tool() is not None)
                    self._right_mouse_down = is_press
                    if not is_press: # Reset on release
                        if getattr(self, "_right_click_in_tool", False):
                            # We always swallow the release if the press started in a tool.
                            # We do NOT clear _right_click_in_tool yet, as we need it
                            # to suppress the upcoming ContextMenu event.
                            return True
                
            # --- Viewport Detection ---
            # We cache the viewport widget to avoid expensive FreeCADGui calls.
            if not hasattr(self, "_cached_viewport") or self._cached_viewport is None:
                self._cached_viewport = None
                try:
                    # Method 1: Active View
                    av = FreeCADGui.activeView()
                    if av and hasattr(av, "getWidget"):
                        # Get the main view widget
                        self._cached_viewport = av.getWidget()
                    
                    # Method 2: Fallback or refinement. We PREFER the specific GL widget 
                    # for size and coordinate accuracy if the obj looks like one.
                    # This ensures we use the exact 3D area, excluding title bars/padding.
                    obj_cls = obj.metaObject().className() if hasattr(obj, "metaObject") else ""
                    if "OpenGL" in obj_cls or "Quarter" in obj_cls:
                        # If the current object IS a GL widget, or we don't have a viewport yet, use it.
                        if not self._cached_viewport or "View3D" in (self._cached_viewport.metaObject().className() if hasattr(self._cached_viewport, "metaObject") else ""):
                            self._cached_viewport = obj
                except Exception:
                    pass

            # Is this event for the viewport or one of its child GL widgets?
            is_viewport_event = False
            if self._cached_viewport:
                if obj == self._cached_viewport:
                    is_viewport_event = True
                elif hasattr(self._cached_viewport, "isAncestorOf"):
                    # QWidget.isAncestorOf only accepts other QWidgets.
                    # QWindow events must be handled differently or ignored.
                    try:
                        is_viewport_event = self._cached_viewport.isAncestorOf(obj)
                    except (TypeError, Exception):
                        pass

            # Standardize coordinates relative to viewport & Track DPI
            if is_mouse_event:
                try:
                    # Store DPI ratio from viewport specifically
                    ratio = 1.0
                    target = self._cached_viewport if self._cached_viewport else obj
                    if hasattr(target, "devicePixelRatioF"):
                        ratio = float(target.devicePixelRatioF())
                    elif hasattr(target, "devicePixelRatio"):
                        ratio = float(target.devicePixelRatio())
                    self._device_pixel_ratio = ratio

                    # Correct way to store coordinates: always relative to the cached viewport.
                    global_pos = obj.mapToGlobal(event.pos()) if hasattr(obj, "mapToGlobal") else event.pos()
                    if self._cached_viewport and hasattr(self._cached_viewport, "mapFromGlobal"):
                        local_pos = self._cached_viewport.mapFromGlobal(global_pos)
                    else:
                        local_pos = event.pos()
                    self._last_qt_pos = (int(local_pos.x()), int(local_pos.y()))
                except Exception:
                    pass

            # --- Viewport Toggle (Ctrl+Space) ---
            if event.type() == QtCore.QEvent.KeyPress and is_viewport_event:
                if event.key() == QtCore.Qt.Key_Space and (event.modifiers() & QtCore.Qt.ControlModifier):
                    try:
                        mw = FreeCADGui.getMainWindow()
                        mdi = mw.findChild(QtGui.QMdiArea)
                        if mdi:
                            sub = mdi.activeSubWindow()
                            if sub:
                                if sub.isMaximized():
                                    sub.showNormal()
                                else:
                                    sub.showMaximized()
                                return True
                    except Exception as e:
                        dm_logger.debug(f"Viewport toggle failed: {e}")

            # Filter mouse events: only allow those in the viewport to reach the tools
            if is_mouse_event and not is_viewport_event:
                return False

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

                # Double-click to edit objects
                elif event.type() == QtCore.QEvent.MouseButtonDblClick and event.button() == QtCore.Qt.LeftButton:
                    from core.dm_selection_manager import DMSelectionManager
                    sel_mgr = DMSelectionManager.get_instance()
                    sel_mgr.try_sdf_selection(self._last_qt_pos)
                    sel = FreeCADGui.Selection.getSelection()
                    if sel:
                        obj = sel[0]
                        proxy_name = getattr(getattr(obj, "Proxy", None), "__class__", type(None)).__name__
                        is_wp = getattr(obj.Proxy, "is_dm_workplane", False)
                        if is_wp:
                            from tools.work_plane_tool import WorkPlaneCreator
                            WorkPlaneCreator(); return True
                        elif proxy_name == "DMObjectProxy":
                            st = getattr(obj, "ShapeType", "")
                            if st == "curve":
                                from tools.edit_tool import EditTool
                                EditTool().activate(); return True
                            elif st == "sdf":
                                from tools.edit_tool import SdfEditTool
                                SdfEditTool().activate(); return True
                        elif proxy_name == "DMNoiseProxy":
                            from tools.edit_tool import SdfEditTool
                            SdfEditTool().activate(); return True

                # Global hotkeys
                elif event.type() == QtCore.QEvent.KeyPress:
                    key = event.key()
                    text = event.text().lower() if hasattr(event, "text") else ""
                    
                    if (key == QtCore.Qt.Key_D or text == 'd') and not self._is_menu_active():
                        from core.dm_menu import DMMenuManager
                        if not DMMenuManager.get_instance()._ignore_hotkeys:
                            DMMenuManager.get_instance().show_context_menu()
                            return True

                    if (key == QtCore.Qt.Key_Q or text == 'q') and not self._is_menu_active():
                        sel = FreeCADGui.Selection.getSelection()
                        toggled_any = False
                        for obj in sel:
                            if hasattr(obj, "Group"):
                                cur = getattr(obj, "Group", "Group 1")
                                obj.Group = "Group 2" if cur == "Group 1" else "Group 1"
                                if obj.Document:
                                    obj.Document.recompute([obj])
                                    if hasattr(obj, "Proxy") and hasattr(obj.Proxy, "SdfField"):
                                        from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
                                        label = f"{obj.Document.Name}.{obj.Name}"
                                        DMSceneRayMarchRenderer.get_instance().update_field(label, obj.Proxy.SdfField)
                                toggled_any = True
                        if toggled_any:
                            return True

                    if (key == QtCore.Qt.Key_E or text == 'e') and not self._is_menu_active():
                        sel = FreeCADGui.Selection.getSelection()
                        if sel:
                            obj = sel[0]
                            proxy_name = getattr(getattr(obj, "Proxy", None), "__class__", type(None)).__name__
                            is_wp = getattr(obj.Proxy, "is_dm_workplane", False)
                            if is_wp:
                                from tools.work_plane_tool import WorkPlaneCreator
                                WorkPlaneCreator(); return True
                            elif proxy_name == "DMObjectProxy":
                                st = getattr(obj, "ShapeType", "")
                                if st == "curve":
                                    from tools.edit_tool import EditTool
                                    EditTool().activate(); return True
                                elif st == "sdf":
                                    from tools.edit_tool import SdfEditTool
                                    SdfEditTool().activate(); return True
                            elif proxy_name == "DMNoiseProxy":
                                from tools.edit_tool import SdfEditTool
                                SdfEditTool().activate(); return True

                # ShortcutOverride for 'E' when no tool
                elif event.type() == QtCore.QEvent.ShortcutOverride:
                    text = event.text().lower() if hasattr(event, "text") else ""
                    if text == 'e':
                        sel = FreeCADGui.Selection.getSelection()
                        if any(hasattr(o, "Proxy") and getattr(o.Proxy, "is_dm_workplane", False) for o in sel):
                            event.accept(); return True
                    elif text == 'q':
                        sel = FreeCADGui.Selection.getSelection()
                        if any(hasattr(o, "IsSubtractive") for o in sel):
                            event.accept(); return True

                # Suppress FreeCAD context menu if DM menu or tool (or just-closed tool) is active
                elif event.type() == QtCore.QEvent.ContextMenu:
                    if self._is_menu_active() or DMToolManager.get_instance().get_active_tool() or getattr(self, "_right_click_in_tool", False):
                        self._right_click_in_tool = False # Consume for this click
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

    def get_qt_cursor_pos(self, _view=None):
        """Returns the current mouse position in Top-Left logical coordinates."""
        return self._last_qt_pos

    def get_mouse_pos_phys(self, view, event_dict=None):
        """Returns (x_phys, y_phys) in Top-Left (Qt) physical pixels.
        Used for FreeCAD view.getObjectInfo() which expects widget-relative top-left.
        """
        pos = self.get_mouse_pos(event_dict)
        ratio = max(1.0, self._device_pixel_ratio)
        return int(round(pos[0] * ratio)), int(round(pos[1] * ratio))

    def get_gl_pos_phys(self, view, event_dict=None):
        """Returns (x_phys, y_phys) in Bottom-Left (OpenGL) physical pixels.
        Used for FreeCAD view.getPoint() and getRay() which expect flipped Y.
        """
        pos = self.get_mouse_pos(event_dict)
        vp_sz = self._get_vp_size(view)
        if not vp_sz:
            return None, None
        
        # Calculate flipped Y in logical space first to avoid rounding drift
        y_log_flipped = (vp_sz[1] - 1.0 - pos[1])
        ratio = max(1.0, self._device_pixel_ratio)
        return int(round(pos[0] * ratio)), int(round(y_log_flipped * ratio))

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

    def _get_vp_size(self, view):
        """Get viewport (width, height) in logical pixels matching _last_qt_pos space."""
        # Prioritize Method 0: Use the actual GL viewport size from the render manager (PHYSICAL)
        # And convert to LOGICAL using our tracked ratio.
        ratio = max(1.0, self._device_pixel_ratio)
        try:
            # Gui.View3D -> Gui.View3DInventorViewer -> SoRenderManager
            rm = view.getViewer().getSoRenderManager()
            sz_pixels = rm.getViewportRegion().getViewportSizePixels()
            if sz_pixels[0] > 0 and sz_pixels[1] > 0:
                return float(sz_pixels[0]) / ratio, float(sz_pixels[1]) / ratio
        except Exception:
            pass

        # Fallback Method 1: Use the cached viewport widget ourselves (LOGICAL)
        if hasattr(self, "_cached_viewport") and self._cached_viewport:
            try:
                return float(self._cached_viewport.width()), float(self._cached_viewport.height())
            except Exception:
                pass

        # Fallback Method 2: Qt widget size via FreeCAD view

        ratio = max(1.0, self._device_pixel_ratio)

        # Method 2: viewer size methods
        try:
            viewer = view.getViewer()
            for method_name in ("getSize", "getGlxSize"):
                if hasattr(viewer, method_name):
                    try:
                        sz = getattr(viewer, method_name)()
                        # This might return logical or physical depending on platform.
                        # We assume physical if it's much larger than view.width()
                        w = float(sz[0] if isinstance(sz, (list, tuple)) else sz.width())
                        h = float(sz[1] if isinstance(sz, (list, tuple)) else sz.height())
                        
                        # Heuristic: if size is roughly physical, scale it down
                        # (This is safer than blind division if ratio is already applied)
                        # However, for now we follow the instruction to ensure logical.
                        if h > 0:
                            return w / ratio, h / ratio
                    except Exception:
                        continue
        except Exception:
            pass

        # Method 3: Coin3D SoRenderManager - authoritative GL framebuffer size (physical pixels)
        try:
            viewer = view.getViewer()
            if hasattr(viewer, "getSoRenderManager"):
                rm = viewer.getSoRenderManager()
                if rm:
                    sz = rm.getViewportRegion().getViewportSizePixels()
                    return float(sz[0]) / ratio, float(sz[1]) / ratio
        except Exception:
            pass

        return None

    def _get_vp_height(self, view):
        """Returns logical viewport height; wrapper around _get_vp_size."""
        sz = self._get_vp_size(view)
        return sz[1] if sz else None
    def get_scene_point(self, view, event_dict=None):
        """Returns the 3D scene point under the cursor via view.getPoint().
        Standardizes on physical pixel mapping for the FreeCAD API.
        """
        if not view: return None
        x_phys, y_phys = self.get_gl_pos_phys(view, event_dict)
        if x_phys is None: return None

        try:
            return view.getPoint(x_phys, y_phys)
        except Exception as e:
            dm_logger.debug(f"get_scene_point failed: x={x_phys} y_phys={y_phys} err={e}")
            return None

    def get_ray(self, view, event_dict=None):
        """Centralized ray generation from screen coordinates."""
        if not view: return None, None
        pos = self.get_mouse_pos(event_dict)
        x, y_qt = pos[0], pos[1]
        
        x_phys, y_phys = self.get_gl_pos_phys(view, event_dict)
        if x_phys is None: return None, None

        try:
            # 1. Try FreeCAD's native getRay (0.20+)
            if hasattr(view, "getRay"):
                ray = view.getRay(x_phys, y_phys)
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
            try: scene_pt = view.getPoint(x_phys, y_phys)
            except Exception as e:
                dm_logger.debug(f"view.getPoint fallback failed: {e}")

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
            vp_sz = self._get_vp_size(view)
            w, h = vp_sz if vp_sz else (1000.0, 1000.0)
            
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
                # Path 3 NDC formula expects y-from-top (Qt convention), use y_qt not flipped y
                ndc_x, ndc_y = (x/w)*2.0 - 1.0, 1.0 - (y_qt/h)*2.0
                plane_h = math.tan(ha/2.0); plane_w = plane_h * aspect
                ray_d = forward + right*(ndc_x*plane_w) + up*(ndc_y*plane_h)
                ray_d.normalize()
                return ray_p, ray_d
            elif hasattr(cam, "height"): # Ortho
                height = cam.height.getValue(); width = height * aspect
                # Path 3 NDC formula expects y-from-top (Qt convention), use y_qt not flipped y
                ndc_x, ndc_y = (x/w)*2.0 - 1.0, 1.0 - (y_qt/h)*2.0
                ray_p_ortho = ray_p + right*(ndc_x*width/2.0) + up*(ndc_y*height/2.0)
                forward.normalize()  # modifies in-place; returns None - do not use return value
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
            vp_sz = self._get_vp_size(view)
            vp_h = vp_sz[1] if vp_sz else 1000.0

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
                # Axis is pointing straight at the camera - no depth info.
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
