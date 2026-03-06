import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui
import math
from core import dm_logger

class DMInputEvent:
    """Wrapper for input events to allow broadcasting and consumption."""
    def __init__(self, event_type, key=None, mouse_pos=None, modifiers=None, qt_event=None):
        self.type = event_type
        self.key = key
        self.pos = mouse_pos # (x, y) Top-Left
        self.modifiers = modifiers or []
        self.qt_event = qt_event
        self.handled = False

class DMInputManager(QtCore.QObject):
    _instance = None
    input_event = QtCore.Signal(object)

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = DMInputManager()
        return cls._instance

    def __init__(self):
        super().__init__()
        self._middle_mouse_down = False
        self._shift_down = False
        self._control_down = False
        self._last_qt_pos = (0, 0)
        self._is_initialized = False

    def _is_menu_active(self):
        from core.dm_menu import DMMenuManager
        return DMMenuManager.get_instance().is_menu_active()

    def eventFilter(self, obj, event):
        try:
            # Drop auto-repeat events to prevent multiple menus
            if getattr(event, "isAutoRepeat", lambda: False)():
                if event.type() in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease, QtCore.QEvent.ShortcutOverride):
                    return True

            # 1. Coordinate tracking & Modifier state
            if event.type() in [QtCore.QEvent.MouseMove, QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease]:
                self._last_qt_pos = (event.pos().x(), event.pos().y())
            
            if event.type() in [QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease]:
                if event.key() == QtCore.Qt.Key_Shift:
                    self._shift_down = (event.type() == QtCore.QEvent.KeyPress)
                elif event.key() == QtCore.Qt.Key_Control:
                    self._control_down = (event.type() == QtCore.QEvent.KeyPress)

            if event.type() in [QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease]:
                if event.button() == QtCore.Qt.MiddleButton:
                    self._middle_mouse_down = (event.type() == QtCore.QEvent.MouseButtonPress)

            # 2. Broadcast the event
            wrapper = self._wrap_event(event)
            if wrapper:
                self.input_event.emit(wrapper)
                if wrapper.handled:
                    return True

            # 3. Handle default context menu ('D' key) if not handled by a tool
            if event.type() == QtCore.QEvent.KeyPress:
                key = event.key()
                text = event.text().lower() if hasattr(event, "text") else ""
                if (key == QtCore.Qt.Key_D or text == 'd') and not self._is_menu_active():
                    from core.dm_menu import DMMenuManager
                    if not DMMenuManager.get_instance()._ignore_hotkeys:
                        DMMenuManager.get_instance().show_context_menu()
                        return True

            # Suppress FreeCAD context menu if a menu is already open or specifically requested
            if event.type() == QtCore.QEvent.ContextMenu:
                if self._is_menu_active():
                    return True

        except Exception as e:
            dm_logger.error(f"DMInputManager eventFilter error: {e}")

        return False

    def _wrap_event(self, event):
        """Wraps a Qt event into a DMInputEvent."""
        etype = event.type()
        if etype == QtCore.QEvent.KeyPress:
            modifiers = []
            if self._shift_down: modifiers.append("Shift")
            if self._control_down: modifiers.append("Control")
            return DMInputEvent("KeyPress", key=event.key(), mouse_pos=self._last_qt_pos, modifiers=modifiers, qt_event=event)
        
        elif etype in [QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease]:
            btn = "Left" if event.button() == QtCore.Qt.LeftButton else ("Right" if event.button() == QtCore.Qt.RightButton else "Middle")
            state = "Press" if etype == QtCore.QEvent.MouseButtonPress else "Release"
            return DMInputEvent(f"Mouse{state}", key=btn, mouse_pos=(event.pos().x(), event.pos().y()), qt_event=event)

        elif etype == QtCore.QEvent.ShortcutOverride:
            # We always broadcast ShortcutOverride for 'S' and 'D' so tools can claim them
            key = event.key()
            text = event.text().lower() if hasattr(event, "text") else ""
            if key in (QtCore.Qt.Key_S, QtCore.Qt.Key_D) or text in ('s', 'd'):
                return DMInputEvent("ShortcutOverride", key=key, mouse_pos=self._last_qt_pos, qt_event=event)
                
        return None

    def get_mouse_pos(self, event_dict=None):
        """Standardized Top-Left coordinate retrieval for all tools."""
        if not event_dict:
            return self._last_qt_pos

        if "QtPosition" in event_dict:
            self._last_qt_pos = event_dict["QtPosition"]
            return self._last_qt_pos
            
        pos = event_dict.get("Position")
        if not pos:
            return self._last_qt_pos
            
        # Use the position as-is
        self._last_qt_pos = pos
        return self._last_qt_pos

    def get_drag_delta(self, start_pos, current_event_dict):
        """Returns (dx, dy) where dy is screen-space (unflipped)."""
        curr_pos = self.get_mouse_pos(current_event_dict)
        return (curr_pos[0] - start_pos[0], curr_pos[1] - start_pos[1])

    def get_qt_cursor_pos(self, view=None):
        """Returns the current mouse position in Top-Left coordinates."""
        return self._last_qt_pos

    def get_ray(self, view, event_dict=None):
        """Centralized ray generation from screen coordinates."""
        if not view: return None, None
        pos = self.get_mouse_pos(event_dict)
        x, y = pos[0], pos[1]

        try:
            # 1. Try FreeCAD's native getRay (0.20+)
            if hasattr(view, "getRay"):
                ray = view.getRay(x, y)
                if ray:
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
                return ray_p_ortho, forward.normalize()

        except Exception as e:
            dm_logger.debug(f"DMInputManager.get_ray failed: {e}")
        return None, None

    def get_projected_point(self, view, base_point_3d, normal_3d, event_dict):
        """
        Calculates the 3D point on a line (Base + Normal) closest to the current mouse ray.
        Used for height dragging (e.g., Box tool).
        """
        if not view: return base_point_3d
        
        ray_p, ray_d = self.get_ray(view, event_dict)
        if ray_p is None or ray_d is None: return base_point_3d

        try:
            # 1. Standard Line-Line Closest Point Math (Ray & Normal Line)
            # Ray: L1 = P1 + s*D1 
            # Normal Line: L2 = P2 + t*D2
            p1, d1 = ray_p, ray_d
            p2, d2 = base_point_3d, normal_3d.normalize()
            
            v = p1 - p2
            # Dot products
            b = d1.dot(d2)
            d = d1.dot(v)
            e = d2.dot(v)
            
            # The parameter t along the Normal Line (L2) for the closest point to Ray (L1)
            denom = b*b - 1.0 # (d1.dot(d1) * d2.dot(d2) - b*b) since d1, d2 are unit vectors
            if abs(denom) > 1e-6:
                t = (d*b - e) / denom
                return p2 + d2 * t
                
        except Exception as e:
            dm_logger.debug(f"get_projected_point ray-line math failed: {e}")
            
        return base_point_3d

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
                self._is_initialized = True
        except Exception as e:
            dm_logger.error(f"DMInputManager initialization error: {e}")

    def restore(self):
        try:
            if getattr(self, "_is_initialized", False):
                QtGui.QApplication.instance().removeEventFilter(self)
                self._is_initialized = False
        except Exception as e:
            dm_logger.error(f"DMInputManager restore error: {e}")
