# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""The cage editor's universal gizmo: `CageGizmoTarget` and `CageGizmoMixin` (Round 14).

`CageGizmoMixin` also owns the cage's bulk geometry writer, `_set_all_control_points`.
"""
import FreeCAD
import numpy as np

from freecad.fields.core.input.gizmo.models import (
    ALL_HANDLES,
    GizmoFrame,
    world_axes,
)
from freecad.fields.core.input.gizmo.transform import array_map
from freecad.fields.core.sdf.sdf.cage import HandleType
from freecad.fields.core.sdf.sdf.cage_deform import SdfCageDeformField
from freecad.fields.tools.cage_picking import pick_handle
from freecad.fields.tools.selection_gizmo import SelectionGizmoMixin, SelectionGizmoTarget


class CageGizmoTarget(SelectionGizmoTarget):
    """The universal gizmo's `GizmoTarget` for the cage editor (Round 14). Every handle acts on
    the selection about its centroid, or on the whole cage about its vertex centroid when
    nothing is selected. No frame and no handles while an extrusion is live. Every apply is a
    total from the snapshot."""

    def live(self):
        return not self.tool._extrude.active and self.tool._cage_field() is not None

    def geometry(self):
        t = self.tool
        f = t._cage_field()
        straight = getattr(f, "_edge_straight", None)
        return {
            "vertices": f.vertices.copy(),
            "handles": f.handles.copy(),
            "types": list(f._handle_types),
            "straight": None if straight is None else list(straight),
        }

    def gizmo_apply(self, snap, delta):
        self.tool._map_control_points(snap, array_map(delta))

    def gizmo_restore(self, snap):
        t = self.tool
        f = t._cage_field()
        if f is None:
            return
        t._restore_types(f, snap)
        t._set_all_control_points(snap["vertices"].copy(), snap["handles"].copy())
        t._commit_changes(final=True)

    def gizmo_commit(self):
        self.tool._commit_changes(final=True)


class CageGizmoMixin(SelectionGizmoMixin):
    """The gizmo half of `CageEditTool`. Comes before `FldSdfToolBase` in the MRO."""

    GIZMO_TARGET = CageGizmoTarget
    MARQUEE = True

    def _gizmo_editable(self):
        return self._cage_field() is not None

    def _pick_point(self, ray_p, ray_d):
        return pick_handle(self, ray_p, ray_d, include_vertices=True)

    def _point_world(self, key):
        return self.points[key] if 0 <= key < len(self.points) else None

    def _selectable_keys(self):
        """The marquee's keys: the cage vertices. Edge handles are click-only."""
        return list(range(self._n_verts))

    def _point_pressed(self, key):
        if key < self._n_verts:
            self._last_clicked_vertex = key
        else:
            self._last_clicked_edge = (key - self._n_verts) // 2

    def _selection_changed(self):
        self._sync_control_point_visuals()

    def _cage_field(self):
        """The target's cage field, or None when it is not one we may mutate."""
        proxy = getattr(self._target_obj, "Proxy", None)
        field = getattr(proxy, "SdfField", None) if proxy else None
        if field is None or not self._target_is_cage_field():
            return None
        return field

    def _set_all_control_points(self, verts, handles, align=()):
        """Bulk-write cage geometry, refresh derived state and resync the view. `align`:
        vertices whose ALIGNED handles re-project after the write."""
        field = self._cage_field()
        if field is None:
            return
        field.vertices[:] = verts
        field.handles[:] = handles
        for vi in align:
            field.project_aligned_handles(vi)
        for hi in range(len(field.handles)):
            field._handle_offsets[hi] = field.handles[hi] - field.vertices[field.handle_owner(hi)]
        # Mirror what each field's own move_vertex does: SdfCageField rebuilds
        # its topology from the moved vertices, while the deform field's
        # topology lives in the rest frame and must not be rebuilt from the
        # deformed ones.
        if not isinstance(field, SdfCageDeformField):
            topo = getattr(field, "topology", None)
            if topo is not None and hasattr(topo, "rebuild_from_geometry"):
                topo.rebuild_from_geometry(field.vertices)
        field.recompute_face_aabbs()
        field.geometry_version = getattr(field, "geometry_version", 0) + 1
        self.points = ([FreeCAD.Vector(p[0], p[1], p[2]) for p in field.vertices]
                       + [FreeCAD.Vector(p[0], p[1], p[2]) for p in field.handles])
        self._sync_control_point_visuals()
        self._commit_changes()

    def _gizmo_exclude(self):
        """The cage's own control points never snap."""
        return [self._target_obj] if self._target_obj else []

    def _gizmo_unselected_centre(self):
        """The centroid of every vertex."""
        pts = self.points[:self._n_verts]
        if not pts:
            return None
        return sum(pts, FreeCAD.Vector()) * (1.0 / len(pts))

    @staticmethod
    def _restore_types(field, snap):
        """Handle types and straight edges back to the snapshot's."""
        field._handle_types[:] = snap["types"]
        straight = getattr(field, "_edge_straight", None)
        if straight is not None and snap["straight"] is not None:
            straight[:] = snap["straight"]

    def _map_control_points(self, snap, f):
        """Write `f` of the snapshot for the moved set (Round 14 design).

        The moved set is the selection (every control point when none is selected) plus each
        selected vertex's non-FREE handles, which ride with it as `move_vertex` carries them.
        A handle moved without its vertex turns LINKED into FREE and un-straightens its edge,
        and an ALIGNED one re-projects its vertex's handles. A
        straight edge's handles stay at its thirds.
        """
        field = self._cage_field()
        if field is None:
            return
        n = self._n_verts
        self._restore_types(field, snap)
        sel = snap["selection"] or tuple(range(n + len(snap["handles"])))
        sel_v = sorted(i for i in sel if i < n)
        own = {i - n for i in sel if i >= n}
        attached = {hi for vi in sel_v for hi in field._vert_handles.get(vi, ())}
        riding = {hi for hi in attached if field._handle_types[hi] != HandleType.FREE}
        loose = sorted(own - attached)
        moved_h = sorted(own | riding)

        verts = snap["vertices"].copy()
        handles = snap["handles"].copy()
        if sel_v:
            verts[sel_v] = f(verts[sel_v])
        if moved_h:
            handles[moved_h] = f(handles[moved_h])
        straight = getattr(field, "_edge_straight", None)
        for hi in loose:
            if field._handle_types[hi] == HandleType.LINKED:
                field._handle_types[hi] = HandleType.FREE
            if straight and straight[hi // 2]:
                straight[hi // 2] = False
        if straight:
            for ei, is_straight in enumerate(straight):
                if is_straight:
                    va, vb = field._edges[ei]
                    handles[2 * ei] = verts[va] + (verts[vb] - verts[va]) / 3.0
                    handles[2 * ei + 1] = verts[va] + 2.0 * (verts[vb] - verts[va]) / 3.0
        align = sorted({field.handle_owner(hi) for hi in loose
                        if field._handle_types[hi] == HandleType.ALIGNED})
        self._set_all_control_points(verts, handles, align=align)
