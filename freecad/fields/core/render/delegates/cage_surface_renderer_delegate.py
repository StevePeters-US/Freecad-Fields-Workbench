# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/delegates/cage_surface_renderer_delegate.py

Rendering delegate for SDF cage-surface boundary curves: draws the
smooth Bezier boundary curves of a box/octahedron control cage from its
control points and corner handles.
"""
try:
    from pivy import coin
except ImportError:
    coin = None

from freecad.fields.core.objects.fld_object import get_line_width, get_show_cage_curves
from freecad.fields.core.render.delegates import add_to_scene


class CageSurfaceRendererDelegate:
    def __init__(self):
        self._cage_curves_sep = None
        self._cage_curves_style = None
        self._cage_curves_mat = None
        self._cage_curves_coords = None
        self._cage_curves_lines = None

    def on_prefs_changed(self, renderer, lw):
        """Apply line-width prefs. No `show_wire` param (CR-054): unlike
        `SdfRendererDelegate`, this delegate has no debug wireframe overlay to
        gate -- see that delegate's `on_prefs_changed` docstring for why the
        arity differs deliberately across delegates.
        """
        if self._cage_curves_style:
            self._cage_curves_style.lineWidth = lw

    def setup_cage_curves(self, renderer):
        if not coin: return
        if self._cage_curves_sep:
            return

        self._cage_curves_sep = coin.SoSeparator()
        add_to_scene(renderer, self._cage_curves_sep)

        # Smooth solid lines
        self._cage_curves_style = coin.SoDrawStyle()
        self._cage_curves_style.lineWidth = get_line_width()
        self._cage_curves_sep.addChild(self._cage_curves_style)

        # Premium light blue/cyan color for patch edges
        self._cage_curves_mat = coin.SoMaterial()
        self._cage_curves_mat.diffuseColor.setValue(0.0, 0.6, 0.9)
        self._cage_curves_sep.addChild(self._cage_curves_mat)

        self._cage_curves_coords = coin.SoCoordinate3()
        self._cage_curves_sep.addChild(self._cage_curves_coords)

        self._cage_curves_lines = coin.SoLineSet()
        self._cage_curves_sep.addChild(self._cage_curves_lines)

    def rebuild_cage_curves(self, renderer, fp):
        if not coin: return
        if not self._cage_curves_coords:
            self.setup_cage_curves(renderer)
        if not self._cage_curves_coords:
            return

        if not get_show_cage_curves():
            self._cage_curves_coords.point.setNum(0)
            self._cage_curves_lines.numVertices.setNum(0)
            return

        # Get vertices and handles from fp.Points
        pts = list(fp.Points) if hasattr(fp, "Points") else []
        if not pts:
            self._cage_curves_coords.point.setNum(0)
            self._cage_curves_lines.numVertices.setNum(0)
            return

        from freecad.fields.core.objects.fld_face_extrude_objects import cage_net_of
        net = cage_net_of(fp)
        if net is None or not hasattr(net, "vertices") or not hasattr(net, "_edges"):
            self._cage_curves_coords.point.setNum(0)
            self._cage_curves_lines.numVertices.setNum(0)
            return
        n_verts = len(net.vertices)
        edges = net._edges

        import numpy as np
        curve_pts = []
        num_vertices = []
        n_samples = 16
        t_vals = np.linspace(0.0, 1.0, n_samples)

        inv_pl = fp.Placement.inverse() if fp.Placement else None

        for i, (vi, vj) in enumerate(edges):
            if vi >= len(pts) or vj >= len(pts) or (n_verts + 2*i + 1) >= len(pts):
                continue
            p0 = pts[vi]
            p1 = pts[n_verts + 2*i]
            p2 = pts[n_verts + 2*i + 1]
            p3 = pts[vj]

            if inv_pl:
                p0 = inv_pl.multVec(p0)
                p1 = inv_pl.multVec(p1)
                p2 = inv_pl.multVec(p2)
                p3 = inv_pl.multVec(p3)

            # Convert FreeCAD.Vector to numpy array
            v0 = np.array([p0.x, p0.y, p0.z])
            v1 = np.array([p1.x, p1.y, p1.z])
            v2 = np.array([p2.x, p2.y, p2.z])
            v3 = np.array([p3.x, p3.y, p3.z])

            for t in t_vals:
                u = 1.0 - t
                pt = u*u*u*v0 + 3.0*u*u*t*v1 + 3.0*u*t*t*v2 + t*t*t*v3
                curve_pts.append(coin.SbVec3f(float(pt[0]), float(pt[1]), float(pt[2])))
            num_vertices.append(n_samples)

        self._cage_curves_coords.point.setNum(len(curve_pts))
        if curve_pts:
            self._cage_curves_coords.point.setValues(0, curve_pts)
        self._cage_curves_lines.numVertices.setNum(len(num_vertices))
        if num_vertices:
            self._cage_curves_lines.numVertices.setValues(0, num_vertices)
