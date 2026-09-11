# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/delegates/curve_renderer_delegate.py

Rendering delegate for NURBS curve control cages: handle-arm dashed
lines and control-point/handle spheres (drawn only while in edit mode).
"""
try:
    from pivy import coin
except ImportError:
    coin = None

from freecad.fields.core.render.field_appearance import DEFAULT_ADDITIVE_COLOR
from freecad.fields.core.render.delegates import add_to_scene


class CurveRendererDelegate:
    def __init__(self):
        self._ctrl_cage_sep = None
        self._style = None
        self._ctrl_coords = None
        self._ctrl_lines = None
        self._spheres_sep = None
        self._dm_point_spheres = []  # FldPoint instances for control points and handles

    def on_prefs_changed(self, renderer, lw):
        """Apply line-width prefs. No `show_wire` param (CR-054): unlike
        `SdfRendererDelegate`, this delegate has no debug wireframe overlay to
        gate -- see that delegate's `on_prefs_changed` docstring for why the
        arity differs deliberately across delegates.
        """
        if self._style:
            self._style.lineWidth = lw

    def setup_coin_overlay(self, renderer):
        if not coin: return

        self._ctrl_cage_sep = coin.SoSeparator()
        # For curves, this is the first (and only) child of vis_switch (when present),
        # so whichChild=0 keeps it visible.
        add_to_scene(renderer, self._ctrl_cage_sep)

        # Dashed lines for handle arms
        self._style = coin.SoDrawStyle()
        self._style.linePattern = 0x0F0F
        self._style.lineWidth = 1
        self._ctrl_cage_sep.addChild(self._style)

        self._ctrl_coords = coin.SoCoordinate3()
        self._ctrl_cage_sep.addChild(self._ctrl_coords)

        self._ctrl_lines = coin.SoLineSet()
        self._ctrl_cage_sep.addChild(self._ctrl_lines)

        # SoSphere nodes for control points and handles (via FldPoint)
        self._spheres_sep = coin.SoSeparator()
        self._ctrl_cage_sep.addChild(self._spheres_sep)

        # Note: do NOT call addDisplayMode here — it would add _ctrl_cage_sep
        # to FreeCAD's internal display-mode switch, which may hide it unless
        # the user explicitly selects "ControlCage" as the display mode.
        # The control cage is always managed by vis_switch + edit_mode gating.

    def rebuild_control_cage(self, renderer, fp):
        if not coin: return

        if not self._ctrl_coords:
            if hasattr(fp, "ShapeType") and fp.ShapeType == "curve":
                self.setup_coin_overlay(renderer)
        if not self._ctrl_coords:
            return

        # Clear previous FldPoint spheres
        from freecad.fields.core.objects.fld_point import FldPoint
        for fld_pt in self._dm_point_spheres:
            fld_pt.undraw()
        self._dm_point_spheres.clear()

        has_nodes = hasattr(fp, "ControlNodes") and fp.ControlNodes
        if has_nodes:
            inv_pl = fp.Placement.inverse()
            pts = [inv_pl.multVec(node.Coordinates) for node in fp.ControlNodes if node is not None]
        else:
            pts = list(fp.Points) if hasattr(fp, "Points") else []

        if not pts:
            self._ctrl_coords.point.setNum(0)
            self._ctrl_lines.numVertices.setNum(0)
            return
        h_in = list(fp.HandleIn) if hasattr(fp, "HandleIn") else []
        h_out = list(fp.HandleOut) if hasattr(fp, "HandleOut") else []
        h_types = list(fp.HandleTypes) if hasattr(fp, "HandleTypes") else []
        edit_mode = getattr(fp, "EditMode", False)

        # Colors for handle types:
        # 0 = Auto (light blue), 1 = Vector (green), 2 = Aligned (purple/magenta), 3 = Free (red)
        HANDLE_COLORS = {
            0: (0.2, 0.7, 1.0),
            1: (0.2, 0.8, 0.2),
            2: (0.8, 0.2, 0.8),
            3: (1.0, 0.2, 0.2),
        }

        # Sphere radius: use screen-space size so spheres look consistent at any zoom/scale
        r_knot = renderer._corner_sphere_radius()
        r_handle = r_knot * 0.75

        line_coords = []
        num_vertices = []

        for i, p in enumerate(pts):
            if not edit_mode:
                continue

            # Control point sphere
            fld_pt = FldPoint(p)
            fld_pt.draw_point(self._spheres_sep, radius=r_knot, color=DEFAULT_ADDITIVE_COLOR)
            fld_pt.is_handle = False
            self._dm_point_spheres.append(fld_pt)

            # Handle arm lines + handle spheres
            if i < len(h_in) and h_in[i] is not None and (h_in[i] - p).Length > 1e-4:
                line_coords.append(coin.SbVec3f(p.x, p.y, p.z))
                line_coords.append(coin.SbVec3f(h_in[i].x, h_in[i].y, h_in[i].z))
                num_vertices.append(2)

                type_in = h_types[2*i] if 2*i < len(h_types) else 0
                color_in = HANDLE_COLORS.get(type_in, (0.2, 0.7, 1.0))

                fld_h = FldPoint(h_in[i])
                fld_h.draw_point(self._spheres_sep, radius=r_handle, color=color_in)
                fld_h.is_handle = True
                self._dm_point_spheres.append(fld_h)

            if i < len(h_out) and h_out[i] is not None and (h_out[i] - p).Length > 1e-4:
                line_coords.append(coin.SbVec3f(p.x, p.y, p.z))
                line_coords.append(coin.SbVec3f(h_out[i].x, h_out[i].y, h_out[i].z))
                num_vertices.append(2)

                type_out = h_types[2*i + 1] if 2*i + 1 < len(h_types) else 0
                color_out = HANDLE_COLORS.get(type_out, (0.2, 0.7, 1.0))

                fld_h = FldPoint(h_out[i])
                fld_h.draw_point(self._spheres_sep, radius=r_handle, color=color_out)
                fld_h.is_handle = True
                self._dm_point_spheres.append(fld_h)

        self._ctrl_coords.point.setNum(len(line_coords))
        if line_coords:
            self._ctrl_coords.point.setValues(0, line_coords)
        self._ctrl_lines.numVertices.setNum(len(num_vertices))
        if num_vertices:
            self._ctrl_lines.numVertices.setValues(0, num_vertices)
