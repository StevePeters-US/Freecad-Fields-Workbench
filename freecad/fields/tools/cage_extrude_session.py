# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""CageExtrudeSession: one modal face extrusion on a deform cage -- begin, drag, commit or cancel."""
import FreeCAD
import numpy as np

from freecad.fields.tools.extrude_gizmo import ExtrudeGizmo
from freecad.fields.core.input.input_manager import FldInputManager
from freecad.fields.core import fld_logger
from freecad.fields.core import fld_perf
from freecad.fields.core.sdf.sdf_face_extrude import newell_normal
from freecad.fields.core.objects.fld_face_extrude_objects import ring_handles, ring_rest_handles, carry_ring_handles_to_cap
from freecad.fields.core.objects.fld_deform_objects import push_extrusion, set_last_extrusion_top, pop_extrusion
from freecad.fields.core.sdf.sdf.cage import CageTopology


class CageExtrudeSession:
    """State and lifecycle of a modal extrusion: begin(), update_drag(), commit() / cancel()."""

    def __init__(self, tool):
        self.tool = tool
        self.active = False
        self.face = None
        self.normal = None
        self.base_ring = None
        self.base_handles = None
        self.plane_normal = None
        self.plane_origin = None
        self.world_start = None
        self.gizmo = None

    def dispose(self):
        """Clear and drop the extrude gizmo."""
        if self.gizmo:
            self.gizmo.clear()
            self.gizmo = None

    def begin(self, field, face_index):
        """Start extruding `face_index` of the deform cage `field`."""
        tool = self.tool
        fi = face_index
        fld_logger.info(f"CageEditTool: extruding face {fi} (analytic face extrude)")
        self.face = fi

        # Rest-frame ring geometry. The ring vertices ARE cage vertices, so their
        # rest positions are known exactly -- no inverse-MVC needed. The drag is
        # applied in the rest frame, and the cage's own deformation then carries
        # the prism along with the surface.
        ring = list(field._face_verts[fi])
        base_ring = field.rest_vertices[ring]
        base_handles = ring_rest_handles(field, ring)

        # Outward rest-frame normal: face winding carries no outward guarantee,
        # and an inward sweep would extrude into the solid.
        normal = newell_normal(base_ring)
        if np.dot(normal, base_ring.mean(axis=0) - field.rest_vertices.mean(axis=0)) < 0.0:
            normal = -normal
        self.normal = FreeCAD.Vector(*normal)
        self.base_ring = base_ring
        self.base_handles = base_handles

        centroid = field.vertices[ring].mean(axis=0)
        view_dir = tool.view.getViewDirection()
        self.plane_normal = FreeCAD.Vector(
            -view_dir.x, -view_dir.y, -view_dir.z).normalize()
        self.plane_origin = FreeCAD.Vector(*centroid)
        self.world_start = tool.projector.get_mouse_world_pos(
            {"Position": FldInputManager.get_instance()._last_qt_pos},
            self.plane_normal,
            self.plane_origin,
            place_on_geometry=False,
        ) or self.plane_origin

        with fld_perf.phase(f"push extrusion record (face {fi})", category="cage"):
            push_extrusion(tool._target_obj, base_ring, base_ring, base_handles)
        self.active = True

    def update_drag(self):
        tool = self.tool
        # An extrude drag has its own timing: without it every row of the drag
        # summary reads 0.0 for the one interaction we most need to account for.
        import time
        metrics = getattr(tool, "_drag_session_metrics", None)
        if metrics is not None:
            metrics["ticks"] = metrics.get("ticks", 0) + 1

        t0 = time.perf_counter()
        pt = tool.projector.get_mouse_world_pos(
            {"Position": FldInputManager.get_instance()._last_qt_pos},
            self.plane_normal,
            self.plane_origin,
            place_on_geometry=False,
        )
        if metrics is not None:
            metrics["get_mouse_pos"] = metrics.get("get_mouse_pos", 0.0) + (time.perf_counter() - t0)
        if pt is None:
            return
        dist = float((pt - self.world_start).dot(self.normal))
        sweep = np.array([self.normal.x,
                          self.normal.y,
                          self.normal.z]) * dist
        # Only the analytic E is rebuilt per tick. The topology grows once, at
        # commit -- rebuilding the cage every tick is the 17 ms/tick regression.
        t0 = time.perf_counter()
        new_field = set_last_extrusion_top(
            tool._target_obj, self.base_ring + sweep)
        if metrics is not None:
            metrics["mutate_cage"] = metrics.get("mutate_cage", 0.0) + (time.perf_counter() - t0)
        if new_field is not None:
            t0 = time.perf_counter()
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
            label = f"{tool._target_obj.Document.Name}.{tool._target_obj.Name}"
            FldSceneVoxelRenderer.get_instance().update_field(label, new_field)
            if metrics is not None:
                metrics["commit_field"] = metrics.get("commit_field", 0.0) + (time.perf_counter() - t0)

        if tool.points_root:
            if self.gizmo is None:
                self.gizmo = ExtrudeGizmo(tool.points_root)
            field = getattr(getattr(tool._target_obj, "Proxy", None), "SdfField", None)
            if field is not None and self.face is not None:
                ring = list(field._face_verts[self.face])
                # The gizmo is drawn, so every point of it must be in the CURRENT
                # frame. The ring's vertices and handles are cage control points, so
                # their deformed positions are read directly -- no MVC needed. Taking
                # the handles from the rest frame instead is what made the preview
                # curves swoop off the surface.
                base_ring = [FreeCAD.Vector(*p) for p in field.vertices[ring]]
                base_h = ring_handles(field, ring, frame="current")
                # Forward-MVC image of (rest ring + sweep) for exact top ring on deformed cages
                if hasattr(field, "mvc_forward_map") and not getattr(field, "_is_identity", True):
                    rest_verts = field.rest_vertices[ring]
                    rest_h = ring_handles(field, ring, frame="rest")
                    top_pts = field.mvc_forward_map(rest_verts + sweep)
                    top_h_pts = field.mvc_forward_map(rest_h + sweep)
                    top_ring = [FreeCAD.Vector(*p) for p in top_pts]
                    top_h = top_h_pts
                else:
                    top_ring = [FreeCAD.Vector(*(p + sweep)) for p in field.vertices[ring]]
                    top_h = base_h + sweep
                r = tool._compute_handle_radius()
                self.gizmo.update(base_ring, top_ring, base_h, top_h, handle_radius=r)

        if tool.view:
            t0 = time.perf_counter()
            tool.view.redraw()
            if metrics is not None:
                metrics["redraw_view"] = metrics.get("redraw_view", 0.0) + (time.perf_counter() - t0)
        return

    def commit(self):
        """Grow the active deform cage over the extrusion committed to its properties."""
        # Committing is the one place the cage's topology grows, and it is the
        # start of the quiet stretch between one extrude and the next.
        with fld_perf.phase(
                f"commit extrusion (face {self.face})",
                category="cage"):
            self._commit_inner()

    def _commit_inner(self):
        tool = self.tool
        if self.gizmo:
            self.gizmo.clear()
        from freecad.fields.core.sdf.sdf.cage_deform import SdfCageDeformField
        obj = tool._target_obj
        fi = self.face
        field = getattr(getattr(obj, "Proxy", None), "SdfField", None)
        if fi is None or not isinstance(field, SdfCageDeformField):
            return

        base_ring = self.base_ring
        base_handles = self.base_handles
        from freecad.fields.core.objects.fld_deform_objects import FldDeformCageProxy
        records = FldDeformCageProxy._extrusion_records(obj)
        if not records:
            return
        top_ring = records[-1][1]
        sweep = top_ring - base_ring
        k = len(base_ring)
        ring = list(field._face_verts[fi])

        # Grow a COPY: CageTopology.extrude_face re-inits the object it is called on
        # (cage.py:208), and field.topology is shared with the field that is about to
        # become the union's source.
        topo = CageTopology(*field.topology.net_arrays())
        old_n = len(topo.vertices)
        topo.extrude_face(fi, 1.0)
        n0 = len(topo.vertices) - k
        for i in range(k):
            topo.vertices[n0 + i].pos = top_ring[i].copy()

        # New cap vertex i came from ring[i]: it inherits that vertex's displacement,
        # so curr = rest + disp = (rest_base + sweep) + (curr_base - rest_base).
        v_map_dict = {n0 + i: ring[i] for i in range(k)}
        v_map = lambda v: v_map_dict.get(v, v)
        cap_curr = {n0 + i: field.vertices[ring[i]] + sweep[i] for i in range(k)}

        new_field = tool._rebuild_cage_from_topology(
            topo, vertex_map=v_map, vertex_overrides=cap_curr)

        # Cap curvature: the cap ring inherits the source face's arc handles, carried
        # along the sweep. Same transform build_extrusion_cage uses -- one helper.
        if new_field is not None and base_handles is not None:
            for handles in (new_field.rest_handles, new_field.handles):
                carry_ring_handles_to_cap(
                    handles, topo.edges, base_handles, base_ring, top_ring,
                    top_offset=n0, edge_straight=new_field._edge_straight)
            new_field.displacements[len(new_field.vertices):] = (
                new_field.handles - new_field.rest_handles)
            new_field.recompute_face_aabbs()

        self.face = None
        self.base_ring = None
        self.base_handles = None

    def cancel(self):
        """Discard the pending extrusion record and restore the pre-extrude field."""
        tool = self.tool
        obj = tool._target_obj
        if obj is not None and self.face is not None:
            new_field = pop_extrusion(obj)
            self.face = None
            if new_field is not None:
                from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                label = f"{obj.Document.Name}.{obj.Name}"
                FldSceneVoxelRenderer.get_instance().update_field(label, new_field)
        if self.gizmo:
            self.gizmo.clear()
        if tool.view:
            tool.view.redraw()
