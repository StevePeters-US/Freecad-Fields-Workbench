# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""CageNetOverlay: the cage net as Coin3D nodes -- control points, bezier curves, handle arms, x-ray."""
import FreeCAD
from pivy import coin
import numpy as np

from freecad.fields.tools.tool_mixins import HANDLE_TYPE_COLORS
from freecad.fields.core.objects.fld_point import FldPoint
from freecad.fields.core.objects.fld_line import FldLineSet
from freecad.fields.core import fld_logger


class CageNetOverlay:
    """The cage net as Coin3D nodes: control points, bezier curves, handle arms, x-ray."""

    def __init__(self, view, point_color):
        """`point_color(i)` returns the unselected display color of control point i."""
        self.view = view
        self._point_color = point_color
        self.fld_points = []
        # Two parents, one net. points_root is an SoAnnotation: Coin renders it in the
        # delayed pass with GL_DEPTH_TEST off, which is exactly the x-ray look. depth_root
        # is an ordinary separator, so its children depth-test against the SDF -- the voxel
        # renderer writes real gl_FragDepth (post_process_shaders.py:192) with depth
        # writes on (fld_scene_voxel_renderer.py:825), so this occludes correctly whatever the child order.
        # net_sep carries the whole cage net and moves between the two; the extrude gizmo
        # stays on points_root always.
        self.points_root = coin.SoAnnotation() if (coin and hasattr(coin, "SoAnnotation")) else None
        self.depth_root = coin.SoSeparator() if coin else None
        self.net_sep    = coin.SoSeparator() if coin else None
        from freecad.fields.core.fld_settings import get_cage_xray
        self.xray = get_cage_xray()
        sg = self.view.getSceneGraph() if self.view else None
        if sg:
            if self.points_root:
                sg.addChild(self.points_root)
            if self.depth_root:
                sg.addChild(self.depth_root)
        if self.net_sep:
            net_parent = self.points_root if self.xray else self.depth_root
            if net_parent:
                net_parent.addChild(self.net_sep)
        self.edge_curves = None
        self.handle_arms = None

    def build(self, points, n_verts, radius):
        """Draw one sphere per control point and create the curve and arm line sets."""
        self.fld_points = []
        for i, pt in enumerate(points):
            is_vert = i < n_verts
            fld_pt = FldPoint(pt)
            pt_color = self._point_color(i)
            fld_pt.draw_point(self.net_sep, radius * (1.0 if is_vert else 0.6), color=pt_color)
            self.fld_points.append(fld_pt)

        # Initialize line sets for curves and arms
        self.edge_curves = FldLineSet(self.net_sep, color=(0.0, 0.8, 1.0), width=2.0)
        self.handle_arms = FldLineSet(self.net_sep, color=HANDLE_TYPE_COLORS["LINKED"], width=1.2, pattern=0x0F0F)

    def set_xray(self, on):
        """Move the cage net between the always-on-top parent and the depth-tested one.

        Returns True when the state changed, False for every no-op and failure path.
        """
        on = bool(on)
        if not self.net_sep or on == self.xray:
            return False
        old_parent = self.points_root if self.xray else self.depth_root
        new_parent = self.points_root if on else self.depth_root
        if old_parent is None or new_parent is None:
            fld_logger.warn("CageEditTool.set_xray: overlay roots missing; toggle ignored")
            return False
        try:
            if old_parent.findChild(self.net_sep) >= 0:
                old_parent.removeChild(self.net_sep)
            new_parent.addChild(self.net_sep)
        except Exception as e:
            fld_logger.warn(f"CageEditTool.set_xray: reparent failed: {e}")
            return False
        self.xray = on
        from freecad.fields.core.fld_settings import set_cage_xray
        set_cage_xray(on)
        fld_logger.info(f"CageEditTool: x-ray {'on' if on else 'off'}")
        return True

    def has_point(self, i):
        """True when control point i has a drawn sphere."""
        return i < len(self.fld_points)

    def update_point(self, i, position):
        """Move the drawn sphere of control point i."""
        self.fld_points[i].position = position
        self.fld_points[i].update_draw()

    def update_point_colors(self, points, n_verts, handle_types):
        """Update point and handle colors based on their current types."""
        if not self.fld_points:
            return
        for i in range(len(points)):
            is_vert = i < n_verts
            if is_vert:
                pt_color = HANDLE_TYPE_COLORS["vertex"]
            else:
                hi = i - n_verts
                ht = handle_types[hi] if (handle_types and hi < len(handle_types)) else 1
                pt_color = HANDLE_TYPE_COLORS.get(ht, HANDLE_TYPE_COLORS["LINKED"])
            if i < len(self.fld_points):
                self.fld_points[i].set_color(pt_color)

    def sync_visuals(self, points, selection):
        """Reposition every sphere and colour selected points green, the rest by type."""
        selection_set = set(selection)
        sel_color = (0.1, 1.0, 0.1)
        for i, pt in enumerate(points):
            if i < len(self.fld_points):
                fld_pt = self.fld_points[i]
                fld_pt.position = pt
                c = sel_color if i in selection_set else self._point_color(i)
                fld_pt.set_color(c)
                fld_pt.update_draw()

    @property
    def has_lines(self):
        """True once build() has created both line sets."""
        return bool(self.edge_curves and self.handle_arms)

    def update_lines(self, points, n_verts, edges):
        """Resample every edge's bezier curve and handle arms from the control points."""
        curve_pts = []
        curve_segments = []
        arm_pts = []
        arm_segments = []

        n_samples = 16
        t_vals = np.linspace(0.0, 1.0, n_samples)

        for i, (vi, vj) in enumerate(edges):
            if vi >= len(points) or vj >= len(points) or (n_verts + 2*i + 1) >= len(points):
                continue
            p0 = points[vi]
            p1 = points[n_verts + 2*i]
            p2 = points[n_verts + 2*i + 1]
            p3 = points[vj]

            # Convert to numpy arrays for calculation
            v0 = np.array([p0.x, p0.y, p0.z])
            v1 = np.array([p1.x, p1.y, p1.z])
            v2 = np.array([p2.x, p2.y, p2.z])
            v3 = np.array([p3.x, p3.y, p3.z])

            # Sample cubic Bezier
            for t in t_vals:
                u = 1.0 - t
                pt = u*u*u*v0 + 3.0*u*u*t*v1 + 3.0*u*t*t*v2 + t*t*t*v3
                curve_pts.append(FreeCAD.Vector(float(pt[0]), float(pt[1]), float(pt[2])))
            curve_segments.append(n_samples)

            # Handle arms
            arm_pts.extend([p0, p1, p3, p2])
            arm_segments.extend([2, 2])

        self.edge_curves.update_lines(curve_pts, curve_segments)
        self.handle_arms.update_lines(arm_pts, arm_segments)

    def teardown(self):
        """Undraw every node and detach both roots from the scene graph."""
        try:
            # Clean up Coin3D handles
            for fld_pt in self.fld_points:
                try:
                    fld_pt.undraw()
                except Exception as e:
                    fld_logger.warn(f"CageEditTool._do_terminate: failed to undraw point: {e}")
            self.fld_points = []

            # Clean up lines
            if self.edge_curves:
                self.edge_curves.undraw()
                self.edge_curves = None
            if self.handle_arms:
                self.handle_arms.undraw()
                self.handle_arms = None

            sg = self.view.getSceneGraph() if self.view else None
            if sg:
                for root in (self.points_root, self.depth_root):
                    if root is None:
                        continue
                    try:
                        sg.removeChild(root)
                    except Exception as e:
                        fld_logger.debug(f"CageEditTool._do_terminate: removeChild failed: {e}")
            self.points_root = None
            self.depth_root = None
            self.net_sep = None
        except Exception as e:
            fld_logger.warn(f"CageEditTool._do_terminate exception: {e}")
