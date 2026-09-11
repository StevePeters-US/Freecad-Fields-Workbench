# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from PySide import QtCore
from pivy import coin
from .fld_base import NURBSPrimitiveCreator
from freecad.fields.core import fld_logger
from freecad.fields.core.objects.fld_point import FldPoint

class PointCreator(NURBSPrimitiveCreator):
    """Tool to create a FldPoint object at a clicked location."""

    def get_command_id(self):
        return "Fields_CreatePoint"

    def __init__(self):
        super().__init__()
        self.created_points = []
        self.state = 0
        self.working_plane = None
        self._working_plane_is_fallback = True
        self._cursor_fld_pt = None
        self.fld_points = []
        self.sg = self.view.getSceneGraph() if self.view else None
        self.points_root = coin.SoSeparator()
        if self.sg:
            self.sg.addChild(self.points_root)
        fld_logger.debug("PointCreator initialized")

    def _do_terminate(self):
        try:
            if self._cursor_fld_pt:
                try:
                    self._cursor_fld_pt.undraw()
                except Exception as e:
                    fld_logger.debug(f"PointCreator._do_terminate: Failed to undraw cursor point: {e}")
                self._cursor_fld_pt = None
            for fld_pt in self.fld_points:
                try:
                    fld_pt.undraw()
                except Exception as e:
                    fld_logger.debug(f"PointCreator._do_terminate: Failed to undraw point: {e}")
            self.fld_points.clear()
            if self.sg and self.points_root:
                self.sg.removeChild(self.points_root)
        except Exception as e:
            fld_logger.debug(f"PointCreator._do_terminate: {e}")
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
            pt = self.resolve_world_point(event_dict, base_pt=pt)
            if pt is None:
                return False

            hit_desc = getattr(self, "_last_hit_desc", "Unknown")
            # fld_logger.info(f"Placing point at: ({pt.x:.2f}, {pt.y:.2f}, {pt.z:.2f}) on {hit_desc}")

            self.update_active_object("point", {"Position": pt, "debug_pt": pt})

            if self._active_obj:
                self._active_obj.Label = "Point"
                self.created_points.append(self._active_obj.Name)
                self._active_obj = None

            # Permanent sphere at committed position
            fld_pt = FldPoint(pt)
            fld_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pt))
            self.fld_points.append(fld_pt)

            return True
        except Exception:
            fld_logger.exception("PointCreator.handle_click error")
            return False

    def handle_move(self, event_dict):
        pt = self._resolve_wp_click(event_dict)
        if pt:
            pt = self.resolve_world_point(event_dict, base_pt=pt)
        if pt:
            self.update_active_object("point", {"Position": pt, "debug_pt": pt})
            if self._cursor_fld_pt is None:
                self._cursor_fld_pt = FldPoint(pt)
                self._cursor_fld_pt.draw_point(self.points_root, self._compute_handle_radius(ref_pt=pt))
            else:
                self._cursor_fld_pt.position = pt
                self._cursor_fld_pt.update_draw(radius=self._compute_handle_radius(ref_pt=pt))

    def _do_finish(self):
        """Clean up the active preview point so it is not committed in the document."""
        if self._active_obj:
            doc = self.doc or FreeCAD.ActiveDocument
            obj_name = self._active_obj.Name
            if doc:
                def _deferred_remove(d=doc, n=obj_name):
                    try:
                        d.removeObject(n)
                        d.recompute()
                    except Exception as e:
                        fld_logger.debug(f"PointCreator._do_finish: Failed to remove active preview object: {e}")
                QtCore.QTimer.singleShot(0, _deferred_remove)
            self._active_obj = None

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
            pt_names = list(self.created_points)
            active_name = self._active_obj.Name if self._active_obj else None
            def _deferred_remove(d=doc, names=pt_names, active=active_name):
                for pt_name in names:
                    try:
                        d.removeObject(pt_name)
                    except Exception as e:
                        fld_logger.error(f"Failed to remove point {pt_name}: {e}")
                if active:
                    try:
                        d.removeObject(active)
                    except Exception as e:
                        fld_logger.debug(f"PointCreator.cancel_points: Failed to remove active object: {e}")
                d.recompute()
            QtCore.QTimer.singleShot(0, _deferred_remove)
            self._active_obj = None

        self.created_points.clear()
        self._finished = True # prevent terminate from trying to remove again
        self.terminate()

    def on_tool_option_0(self):
        fld_logger.info("snapping point tool")
        
    def on_tool_option_1(self):
        fld_logger.info("snapping point tool")

    def get_context_menu(self, event_dict=None):
        return super().get_context_menu(event_dict)
