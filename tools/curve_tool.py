import FreeCAD
from PySide import QtCore, QtGui
from pivy import coin
from .dm_base import NURBSPrimitiveCreator
from core import dm_logger
from core.dm_point import DMPoint
from core.input_manager import DMInputManager

class CurveCreator(NURBSPrimitiveCreator):
    """Tool to create a DMCurve object from clicked points."""
    def __init__(self):
        super().__init__()
        self.points = []      # FreeCAD.Vector — committed curve point data
        self.dm_points = []   # DMPoint — visual spheres, parallel to self.points
        self.is_closed = False
        self.current_point = None
        self.dragged_index = -1
        self._drag_start_pos = None

        self._hovered_idx = -1
        self._cached_radius = None

        self.sg = self.view.getSceneGraph() if self.view else None
        self.points_root = coin.SoSeparator()
        if self.sg:
            self.sg.addChild(self.points_root)

    def _do_terminate(self):
        for dm_pt in self.dm_points:
            dm_pt.undraw()
        self.dm_points.clear()
        try:
            if self.sg and self.points_root:
                self.sg.removeChild(self.points_root)
        except Exception as e:
            dm_logger.debug(f"CurveCreator._do_terminate: {e}")
        super()._do_terminate()

    # ------------------------------------------------------------------
    # Sphere helpers
    # ------------------------------------------------------------------

    def _add_point_sphere(self, pt):
        """Create a DMPoint sphere for a newly committed point."""
        # Lock the radius on the first point so all spheres stay the same size.
        if self._cached_radius is None:
            self._cached_radius = self._compute_handle_radius(ref_pt=pt)

        dm_pt = DMPoint(pt)
        dm_pt.draw_point(self.points_root, self._cached_radius)
        self.dm_points.append(dm_pt)

    def _hit_test(self, ray_p, ray_d):
        """Perpendicular distance hit test against placed control point spheres."""
        return self._hit_test_perp(ray_p, ray_d, self.points)

    def _set_hover(self, idx):
        """Recolour point spheres and update OS cursor for hovered index (-1 = none)."""
        if idx == self._hovered_idx:
            return
        if self._hovered_idx != -1 and self._hovered_idx < len(self.dm_points):
            self.dm_points[self._hovered_idx].set_color((1, 0.5, 0))
        self._hovered_idx = idx
        if idx == -1 or idx is None:
            self._restore_cursor()
        else:
            if idx < len(self.dm_points):
                self.dm_points[idx].set_color((0.3, 1.0, 0.3))
            self._set_cursor(QtCore.Qt.CrossCursor)

    def on_button1_up(self, event_dict):
        for dm_pt in self.dm_points:
            dm_pt.set_color((1, 0.5, 0))
        self._hovered_idx = -1
        return False

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def handle_click(self, event_dict):
        try:
            pt = self._resolve_wp_click(event_dict)

            if pt is None:
                return False

            if self.state == 0:
                if not self.working_plane:
                    # _resolve_wp_click already set it
                    pass
                else:
                    n, _ = self.get_base_plane()
                    rot = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), n)
                    self.working_plane = FreeCAD.Placement(pt, rot)
                    # Re-project onto the locked plane for exact alignment.
                    pt = self.get_mouse_plane_pt(event_dict)
                    if pt is None:
                        self.working_plane = None
                        return False

                self.start_point = pt
                self.points.append(pt)
                self._add_point_sphere(pt)
                self.state = 1
                self.update_preview()
                self.update_ui()
                return True

            elif self.state == 1:
                if len(self.points) >= 2:
                    from core.dm_object import get_picking_radius
                    if (pt - self.points[0]).Length < get_picking_radius():
                        self.is_closed = True
                        self.current_point = None
                        self.finish()
                        return True

                self.points.append(pt)
                self._add_point_sphere(pt)
                self.update_preview()
                self.update_ui()
                return True

            return False
        except Exception:
            dm_logger.exception("Curve handle_click error")
            return False

    def handle_move(self, event_dict):
        if self.state == 0:
            super().handle_move(event_dict)
        elif self.state == 1:
            ray_p, ray_d = DMInputManager.get_instance().get_ray(self.view, event_dict)
            hit_idx, _ = self._hit_test(ray_p, ray_d)
            self._set_hover(hit_idx)

            pt = self.get_mouse_plane_pt(event_dict)
            if pt is None:
                return

            if len(self.points) >= 2:
                from core.dm_object import get_picking_radius
                if (pt - self.points[0]).Length < get_picking_radius():
                    pt = self.points[0]
            self.current_point = pt
            self.update_preview()
            self.update_ui()

    def handle_keyboard(self, event_dict):
        return super().handle_keyboard(event_dict)

    # ------------------------------------------------------------------
    # Curve geometry
    # ------------------------------------------------------------------

    def _get_auto_handles(self, points):
        """Compute automatic smooth handles for each point in the list."""
        n = len(points)
        if n < 2:
            return [], []

        h_in = [p for p in points]
        h_out = [p for p in points]

        for i in range(n):
            p = points[i]
            if self.is_closed:
                prev_p = points[(i - 1) % n]
                next_p = points[(i + 1) % n]
            else:
                prev_p = points[i-1] if i > 0 else (points[1] - (points[1]-points[0]) if n > 1 else p)
                next_p = points[i+1] if i < n-1 else (points[-1] + (points[-1]-points[-2]) if n > 1 else p)

            tangent = (next_p - prev_p) * 0.5
            if tangent.Length > 0.0001:
                h_out[i] = p + (tangent * 0.33)
                h_in[i] = p - (tangent * 0.33)

            if not self.is_closed:
                if i == 0:
                    h_in[i] = p
                if i == n - 1:
                    h_out[i] = p

        return h_in, h_out

    def update_preview(self, drag_pt=None):
        self._schedule_update(self._do_update_preview)

    def _do_update_preview(self):
        self._update_pending = False
        if self._terminated:
            return
        if not self.points:
            return
        pts = list(self.points)
        if self.current_point and self.dragged_index == -1:
            pts.append(self.current_point)

        params = {"Points": pts, "Closed": self.is_closed}
        if len(pts) >= 2:
            hi, ho = self._get_auto_handles(pts)
            params["HandleIn"] = hi
            params["HandleOut"] = ho
            params["PointTypes"] = [0] * len(pts)
            params["HandleTypes"] = [0] * (2 * len(pts))
        else:
            params["HandleIn"] = pts
            params["HandleOut"] = pts
            params["PointTypes"] = [0] * len(pts)
            params["HandleTypes"] = [0] * (2 * len(pts))

        self.update_active_object("curve", params)

    def update_ui(self):
        pass

    def _do_finish(self):
        """Finalize the curve."""
        if len(self.points) < 2:
            self.terminate()
            return
        # Drop the trailing mouse-follow point and do a synchronous final
        # update (bypass the throttle) so the object is correct before we
        # release ownership.
        self.current_point = None
        self._update_pending = False
        self._do_update_preview()
        self._on_committed(self._active_obj)
        self._finished = True
        self._active_obj = None
        self.terminate()

    def on_tool_option_0(self):
        dm_logger.info("snapping curve tool")

    def on_tool_option_1(self):
        dm_logger.info("snapping curve tool")

    def get_context_menu(self, event_dict=None):
        base_menu = super().get_context_menu(event_dict)
        return base_menu + [
            "-",
            ("Curve Option 1", lambda: dm_logger.info("Selected Curve Option 1")),
            ("Curve Option 2", lambda: dm_logger.info("Selected Curve Option 2"))
        ]
