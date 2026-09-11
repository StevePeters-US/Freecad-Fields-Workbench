# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import numpy as np
from freecad.fields.core.objects.fld_point import FldPoint
from freecad.fields.core.objects.fld_line import FldLineSet
from freecad.fields.core.sdf.sdf_constants import EXTRUDE_GIZMO_COLOR, EXTRUDE_CONNECTOR_COLOR
from freecad.fields.core.sdf.sdf_face_extrude import bezier_ring_samples


class ExtrudeGizmo:
    """
    Unified Extrude Gizmo for drawing base ring, top ring, connector lines,
    and FldPoint handle spheres across all tools (CurveExtrude, SurfaceExtrude, Cage face extrude).
    """

    def __init__(self, points_root):
        self.points_root = points_root
        self._base_fld_pts = []
        self._top_fld_pts = []
        self._base_wire = None
        self._top_wire = None
        self._connector_lines = None

    def update(self, base_ring, top_ring, base_handles=None, top_handles=None, handle_radius=0.5):
        """
        Updates the gizmo geometry given 3D base ring vertices and top ring vertices,
        optionally with cubic Bezier handles for curved edges.
        """
        if base_ring is None or top_ring is None or len(base_ring) == 0 or len(top_ring) == 0:
            self.clear()
            return

        P_base = np.asarray([[p.x, p.y, p.z] if isinstance(p, FreeCAD.Vector) else p for p in base_ring], dtype=np.float64)
        P_top = np.asarray([[p.x, p.y, p.z] if isinstance(p, FreeCAD.Vector) else p for p in top_ring], dtype=np.float64)

        H_base = (np.asarray([[p.x, p.y, p.z] if isinstance(p, FreeCAD.Vector) else p for p in base_handles], dtype=np.float64)
                  if base_handles is not None and len(base_handles) > 0 else None)
        H_top = (np.asarray([[p.x, p.y, p.z] if isinstance(p, FreeCAD.Vector) else p for p in top_handles], dtype=np.float64)
                 if top_handles is not None and len(top_handles) > 0 else None)

        base_ctrl = [FreeCAD.Vector(*p) for p in P_base]
        top_ctrl = [FreeCAD.Vector(*p) for p in P_top]

        # Handle spheres at vertices
        r = float(handle_radius)
        while len(self._base_fld_pts) < len(base_ctrl):
            ctrl_pt = FldPoint(base_ctrl[len(self._base_fld_pts)])
            ctrl_pt.draw_point(self.points_root, r, color=EXTRUDE_GIZMO_COLOR)
            self._base_fld_pts.append(ctrl_pt)
        while len(self._top_fld_pts) < len(top_ctrl):
            ctrl_pt = FldPoint(top_ctrl[len(self._top_fld_pts)])
            ctrl_pt.draw_point(self.points_root, r, color=EXTRUDE_GIZMO_COLOR)
            self._top_fld_pts.append(ctrl_pt)

        for i, pt in enumerate(base_ctrl):
            self._base_fld_pts[i].position = pt
            self._base_fld_pts[i].update_draw(radius=r)
        for i, pt in enumerate(top_ctrl):
            self._top_fld_pts[i].position = pt
            self._top_fld_pts[i].update_draw(radius=r)

        # Trim unused handle spheres if count decreased
        while len(self._base_fld_pts) > len(base_ctrl):
            ctrl_pt = self._base_fld_pts.pop()
            ctrl_pt.undraw()
        while len(self._top_fld_pts) > len(top_ctrl):
            ctrl_pt = self._top_fld_pts.pop()
            ctrl_pt.undraw()

        # Tessellate rings using bezier_ring_samples
        samples_base, _ = bezier_ring_samples(P_base, H_base, n_subdiv=8)
        samples_top, _ = bezier_ring_samples(P_top, H_top, n_subdiv=8)

        wire_base = [FreeCAD.Vector(*p) for p in samples_base]
        wire_base.append(wire_base[0])  # Close loop

        wire_top = [FreeCAD.Vector(*p) for p in samples_top]
        wire_top.append(wire_top[0])  # Close loop

        if self._base_wire is None:
            self._base_wire = FldLineSet(self.points_root, color=EXTRUDE_GIZMO_COLOR, width=2.0)
        self._base_wire.update_lines(wire_base)

        if self._top_wire is None:
            self._top_wire = FldLineSet(self.points_root, color=EXTRUDE_GIZMO_COLOR, width=2.0)
        self._top_wire.update_lines(wire_top)

        # Vertical connector lines between corresponding base and top vertices
        conn_pts = []
        conn_counts = []
        for b, t in zip(base_ctrl, top_ctrl):
            conn_pts.extend([b, t])
            conn_counts.append(2)

        if self._connector_lines is None:
            self._connector_lines = FldLineSet(self.points_root, color=EXTRUDE_CONNECTOR_COLOR, width=1.0)
        self._connector_lines.update_lines(conn_pts, conn_counts)

    def clear(self):
        """Erases all gizmo elements from the Coin3D scene graph."""
        for ctrl_pt in self._base_fld_pts:
            ctrl_pt.undraw()
        for ctrl_pt in self._top_fld_pts:
            ctrl_pt.undraw()
        self._base_fld_pts.clear()
        self._top_fld_pts.clear()

        if self._base_wire is not None:
            self._base_wire.undraw()
            self._base_wire = None
        if self._top_wire is not None:
            self._top_wire.undraw()
            self._top_wire = None
        if self._connector_lines is not None:
            self._connector_lines.undraw()
            self._connector_lines = None
