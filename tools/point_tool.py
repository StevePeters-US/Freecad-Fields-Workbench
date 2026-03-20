import FreeCAD
from PySide import QtCore
from pivy import coin
from .dm_base import NURBSPrimitiveCreator
from core import dm_logger
from core.dm_point import DMPoint

class PointCreator(NURBSPrimitiveCreator):
    """Tool to create a DMPoint object at a clicked location."""

    def get_command_id(self):
        return "DM_CreatePoint"

    def __init__(self):
        super().__init__()
        self.created_points = []
        self.state = 0
        self.working_plane = None
        self._working_plane_is_fallback = True
        self._cursor_dm_pt = None
        self.dm_points = []
        self.sg = self.view.getSceneGraph() if self.view else None
        self.points_root = coin.SoSeparator()
        if self.sg:
            self.sg.addChild(self.points_root)
        dm_logger.debug("PointCreator initialized")

    def _do_terminate(self):
        if self._cursor_dm_pt:
            self._cursor_dm_pt.undraw()
            self._cursor_dm_pt = None
        for dm_pt in self.dm_points:
            dm_pt.undraw()
        self.dm_points.clear()
        try:
            if self.sg and self.points_root:
                self.sg.removeChild(self.points_root)
        except Exception as e:
            dm_logger.debug(f"PointCreator._do_terminate: {e}")
        super()._do_terminate()

    def is_in_progress(self):
        """Returns True if points have been created in this session."""
        return len(self.created_points) > 0

    def handle_click(self, event_dict):
        try:
            if event_dict.get("Button") != QtCore.Qt.LeftButton:
                return False

            pt = self._resolve_wp_click(event_dict, debug=False)
            if pt is None:
                return False

            hit_desc = getattr(self, "_last_hit_desc", "Unknown")
            # dm_logger.info(f"Placing point at: ({pt.x:.2f}, {pt.y:.2f}, {pt.z:.2f}) on {hit_desc}")

            self.update_active_object("point", {"Position": pt, "debug_pt": pt})

            if self._active_obj:
                self._active_obj.Label = "Point"
                self.created_points.append(self._active_obj.Name)
                self._active_obj = None

            # Permanent sphere at committed position
            dm_pt = DMPoint(pt)
            dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pt))
            self.dm_points.append(dm_pt)

            return True
        except Exception:
            dm_logger.exception("PointCreator.handle_click error")
            return False

    def handle_move(self, event_dict):
        pt = self._resolve_wp_click(event_dict)
        if pt:
            self.update_active_object("point", {"Position": pt, "debug_pt": pt})
            if self._cursor_dm_pt is None:
                self._cursor_dm_pt = DMPoint(pt)
                self._cursor_dm_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pt))
            else:
                self._cursor_dm_pt.position = pt
                self._cursor_dm_pt.update_draw(radius=self._compute_handle_radius(ref_pt=pt))

    # Use base class finish() / _do_finish() which calls terminate()

    def handle_keyboard(self, event_dict):
        key_code = event_dict.get("Key")
        # ESC to cancel and remove all points created in this session
        if key_code == QtCore.Qt.Key_Escape:
            self.cancel_points()
            return True
        return super().handle_keyboard(event_dict)

    def cancel_points(self):
        doc = self.doc or FreeCAD.ActiveDocument
        if doc:
            for pt_name in self.created_points:
                try:
                    doc.removeObject(pt_name)
                except Exception as e:
                    dm_logger.error(f"Failed to remove point {pt_name}: {e}")
            
            if self._active_obj:
                try:
                    doc.removeObject(self._active_obj.Name)
                except Exception as e:
                    dm_logger.debug(f"PointCreator.cancel_points: Failed to remove active object: {e}")
            self._active_obj = None
            
            doc.recompute()
            
        self.created_points.clear()
        self._finished = True # prevent terminate from trying to remove again
        self.terminate()

    def on_tool_option_0(self):
        dm_logger.info("snapping point tool")
        
    def on_tool_option_1(self):
        dm_logger.info("snapping point tool")

    def get_context_menu(self, event_dict=None):
        base_menu = super().get_context_menu(event_dict)
        return base_menu + [
            "-",
            ("Point Option 1", lambda: dm_logger.info("Selected Point Option 1")),
            ("Point Option 2", lambda: dm_logger.info("Selected Point Option 2"))
        ]
