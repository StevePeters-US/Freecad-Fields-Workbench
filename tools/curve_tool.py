import FreeCAD
import math
from PySide import QtCore, QtGui
from pivy import coin
from .dm_base import NURBSPrimitiveCreator
from core import dm_logger
from core.input_manager import DMInputManager

class CurveCreator(NURBSPrimitiveCreator):
    """Tool to create a DMCurve object from clicked points."""
    def __init__(self):
        super().__init__()
        self.points = []
        self.is_closed = False
        self.current_point = None

        # Point dragging state (REMOVED - EditTool handles this now)
        self.dragged_index = -1
        self._drag_start_pos = None

        # Sphere-based control point visualization
        self._hovered_idx = -1
        self._cursor_active = False
        self.sg = self.view.getSceneGraph() if self.view else None
        self.points_root = coin.SoSeparator()
        self.point_seps = []
        self.point_mats = []
        self.point_transforms = []
        self.point_spheres = []
        if self.sg:
            self.sg.addChild(self.points_root)

    def _do_terminate(self):
        if getattr(self, "_cursor_active", False):
            QtGui.QApplication.restoreOverrideCursor()
            self._cursor_active = False
        try:
            if self.sg and self.points_root:
                self.sg.removeChild(self.points_root)
        except Exception as e:
            dm_logger.debug(f"CurveCreator._do_terminate: Failed to remove point spheres: {e}")
        super()._do_terminate()

    def _compute_handle_radius(self):
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
                ref = self.points[0] if self.points else FreeCAD.Vector(0, 0, 0)
                depth = (ref - cam_pos_v).Length
                fov = cam.heightAngle.getValue()
                half_world_h = depth * math.tan(fov / 2.0)
            else:
                half_world_h = 100.0
            px_per_world = (vp_h / 2.0) / max(half_world_h, 1e-6)
            return max(2.0, 8.0 / px_per_world)
        except Exception:
            return 5.0

    def _sync_spheres(self, pts):
        """Ensure the Coin3D sphere list matches the given point list and update positions."""
        radius = self._compute_handle_radius()

        # Add missing spheres
        while len(self.point_seps) < len(pts):
            sep = coin.SoSeparator()
            mat = coin.SoMaterial()
            mat.diffuseColor.setValue(1, 0.5, 0)   # orange at rest
            mat.specularColor.setValue(0.8, 0.8, 0.8)
            mat.shininess.setValue(0.7)
            xf = coin.SoTransform()
            sphere = coin.SoSphere()
            sphere.radius = radius
            sep.addChild(mat)
            sep.addChild(xf)
            sep.addChild(sphere)
            self.points_root.addChild(sep)
            self.point_seps.append(sep)
            self.point_mats.append(mat)
            self.point_transforms.append(xf)
            self.point_spheres.append(sphere)

        # Remove extra spheres
        while len(self.point_seps) > len(pts):
            sep = self.point_seps.pop()
            self.point_mats.pop()
            self.point_transforms.pop()
            self.point_spheres.pop()
            self.points_root.removeChild(sep)

        # Update positions and radii
        for pt, xf, sphere in zip(pts, self.point_transforms, self.point_spheres):
            xf.translation.setValue(pt.x, pt.y, pt.z)
            sphere.radius = radius

    def _hit_test(self, ray_p, ray_d):
        """
        Perpendicular distance hit test against placed control point spheres.

        Uses perpendicular distance from the ray to each sphere centre (world
        space). This works for both perspective and orthographic cameras: for
        orthographic, get_ray() returns (scene_pt_on_focal_plane, view_dir),
        so the ray origin may be at a different depth than the sphere.
        Perpendicular distance is depth-independent and always correct.
        """
        if not self.points or ray_p is None or ray_d is None:
            return -1, float('inf')
        radius = self._compute_handle_radius()
        best_dist = float('inf')
        best_idx = -1
        for i, center in enumerate(self.points):
            v = center - ray_p
            proj = v.dot(ray_d)
            perp = (ray_p + ray_d * proj - center).Length
            if perp < radius and perp < best_dist:
                best_dist = perp
                best_idx = i
        return best_idx, best_dist

    def _set_hover(self, idx):
        """Recolour point spheres and update OS cursor for hovered index (-1 = none)."""
        if idx == self._hovered_idx:
            return
        # Restore previous handle to orange
        if self._hovered_idx != -1 and self._hovered_idx < len(self.point_mats):
            self.point_mats[self._hovered_idx].diffuseColor.setValue(1, 0.5, 0)
        self._hovered_idx = idx
        if idx == -1:
            if self._cursor_active:
                QtGui.QApplication.restoreOverrideCursor()
                self._cursor_active = False
        else:
            if idx < len(self.point_mats):
                self.point_mats[idx].diffuseColor.setValue(0.3, 1.0, 0.3)  # green on hover
            if not self._cursor_active:
                QtGui.QApplication.setOverrideCursor(QtCore.Qt.CrossCursor)
                self._cursor_active = True

    def on_button1_up(self, event_dict):
        # Reset hover state after any click
        for mat in self.point_mats:
            mat.diffuseColor.setValue(1, 0.5, 0)
        self._hovered_idx = -1
        return False

    def handle_click(self, event_dict):
        try:
            # Call projector directly to capture wp_hit (DMBase wrapper discards it).
            raw = self.projector.get_mouse_plane_pt(
                event_dict,
                place_on_geometry=self.place_on_geometry,
                working_plane=self.working_plane,
            )
            pt, wp_hit = raw if isinstance(raw, tuple) else (raw, None)

            if pt is None:
                return False

            if self.state == 0:
                if not self.working_plane:
                    if wp_hit is not None:
                        self.working_plane = wp_hit.Placement
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
                self.state = 1
                self._sync_spheres(self.points)
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
                self._sync_spheres(self.points)
                self.update_preview()
                self.update_ui()
                return True

            return False
        except Exception:
            dm_logger.exception("Curve handle_click error")
            return False

    def handle_move(self, event_dict):
        if self.state == 0:
            # Snap to faces etc.
            super().handle_move(event_dict)
        elif self.state == 1:
            # Hover detection over existing points
            ray_p, ray_d = DMInputManager.get_instance().get_ray(self.view, event_dict)
            hit_idx, _ = self._hit_test(ray_p, ray_d)
            self._set_hover(hit_idx)

            # Update temporary "current_point" for preview
            pt = self.get_mouse_plane_pt(event_dict)
            if pt is None:
                return

            # Snapping to start point
            if len(self.points) >= 2:
                from core.dm_object import get_picking_radius
                dist = (pt - self.points[0]).Length
                if dist < get_picking_radius():
                    pt = self.points[0]
            self.current_point = pt
            self.update_preview()
            self.update_ui()

    def handle_keyboard(self, event_dict):
        return super().handle_keyboard(event_dict)

    def _get_auto_handles(self, points):
        """Compute automatic smooth handles for each point in the list."""
        n = len(points)
        if n < 2:
            return [], []

        h_in = [p for p in points]
        h_out = [p for p in points]

        # Simple Catmull-Rom like tangent: T_i = (P_{i+1} - P_{i-1}) / 2
        # Handle distance = 1/3 of segment length
        for i in range(n):
            p = points[i]

            # If both are manual, skip expensive tangent math for this point
            # (Reserved for future manual handle control via context menu)
            # if is_in_manual and is_out_manual: continue

            if self.is_closed:
                # Wrap indices for periodic curve
                prev_p = points[(i - 1) % n]
                next_p = points[(i + 1) % n]
            else:
                prev_p = points[i-1] if i > 0 else (points[1] - (points[1]-points[0]) if n > 1 else p)
                next_p = points[i+1] if i < n-1 else (points[-1] + (points[-1]-points[-2]) if n > 1 else p)

            tangent = (next_p - prev_p) * 0.5
            dist = tangent.Length
            if dist > 0.0001:
                # Limit handles to roughly 1/3 of segment length
                h_out[i] = p + (tangent * 0.33)
                h_in[i] = p - (tangent * 0.33)

            # For open curves, suppress the "outbound" handle of the final point
            # and the "inbound" handle of the first point to avoid sticking out.
            if not self.is_closed:
                if i == 0:
                    h_in[i] = p
                if i == n - 1:
                    h_out[i] = p

        return h_in, h_out

    def update_preview(self, drag_pt=None):
        if not self.points:
            return
        pts = list(self.points)
        # Handle preview for unfinalized point
        if self.current_point and self.dragged_index == -1:
            pts.append(self.current_point)

        params = {
            "Points": pts,
            "Closed": self.is_closed
        }

        # Calculate auto-handles
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
        # Optional: update panel with point count or last segment length
        pass

    def _do_finish(self):
        """Finalize the curve."""
        if len(self.points) < 2:
            self.terminate()
            return

        # Explicitly drop the un-clicked trailing mouse point
        self.current_point = None

        # Ensure active object is fully updated one last time
        self.update_preview()

        self._finished = True
        # dm_logger.debug(f"Curve finalized: {self._active_obj.Name if self._active_obj else 'None'}")

        # Reset but keep object
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
