# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/primitive_gizmo.py

The edit-mode universal gizmo integration of the primitive creators:
`PrimitiveGizmoTarget` (the GizmoTarget protocol implementation), `PrimitiveGizmoMixin`
(supplies the driver's target and hooks: `_make_gizmo_target`, `_gizmo_exclude`,
`_gizmo_drag_ended`, snap indicator and guide).
"""

import FreeCAD
from freecad.fields.core.input.gizmo.models import ALL_HANDLES, AXES, Kind
from freecad.fields.core.input.gizmo.transform import delta_rotation, point_map
from freecad.fields.tools.fld_base import ToolState
from freecad.fields.tools.selection_gizmo import SelectionGizmoMixin, SelectionGizmoTarget


class PrimitiveGizmoTarget(SelectionGizmoTarget):
    """The universal gizmo's `GizmoTarget` for a primitive creator in edit mode
    (`universal_gizmo` reference/models.md). An arrow or plane square moves the selected
    points when there is a selection, else every point and the working plane; a ring or
    a scale cube acts on every point and the working plane about the origin point.
    Every apply is a total from the snapshot."""

    _AXIS_SCALE = frozenset(h for h in ALL_HANDLES if h.kind is Kind.SCALE and not h.uniform)

    def live(self):
        return bool(self.tool.working_plane)

    def rotation(self):
        wp = self.tool.working_plane
        return FreeCAD.Rotation(wp.Rotation) if wp else FreeCAD.Rotation()

    def pivot(self, centre):
        return self.tool._origin_point()

    def offered(self):
        """All 13, except the axis scale cubes in world space: scaling a primitive's
        points along axes that aren't its own would shear it."""
        if self.tool._gizmo_space == "world":
            return ALL_HANDLES - self._AXIS_SCALE
        return ALL_HANDLES

    def geometry(self):
        t = self.tool
        wp = t.working_plane
        return {
            "points": [None if p is None else FreeCAD.Vector(p) for p in t.points],
            "wp": FreeCAD.Placement(wp) if wp else None,
            "centre": FreeCAD.Vector(t._gizmo_centre()),
        }

    def gizmo_apply(self, snap, delta):
        t = self.tool
        kind = delta.handle.kind
        if kind in (Kind.TRANSLATE, Kind.PLANE) and snap["selection"]:
            t._drag_start_points = list(snap["points"])
            t._apply_selection_delta(delta.translation)
        else:
            f = point_map(delta)
            t.points = [None if p is None else f(p) for p in snap["points"]]
            wp0 = snap["wp"]
            if t.working_plane and wp0 is not None:
                t.working_plane.Base = f(wp0.Base)
                if kind is Kind.ROTATE:
                    t.working_plane.Rotation = delta_rotation(delta).multiply(wp0.Rotation)
            t._gizmo_points_changed()
        self._snap_feedback(snap, delta)

    def gizmo_restore(self, snap):
        t = self.tool
        t.points = [None if p is None else FreeCAD.Vector(p) for p in snap["points"]]
        wp0 = snap["wp"]
        if t.working_plane and wp0 is not None:
            t.working_plane.Base = FreeCAD.Vector(wp0.Base)
            t.working_plane.Rotation = FreeCAD.Rotation(wp0.Rotation)
        t._drag_start_points = None
        t._hide_snap_visuals()
        t._gizmo_points_changed()

    def gizmo_commit(self):
        t = self.tool
        t._drag_start_points = None
        t._hide_snap_visuals()
        full = getattr(t, "_do_full_preview_update", None)
        if full:
            full()

    def gizmo_project(self, pos):
        """D-57: where a free point drag lands, by the Projection Surface setting. 'off'
        (the default): None, the camera-facing plane. Otherwise the working plane / SDF, or any
        geometry, under `pos`, never onto the edited object itself."""
        t = self.tool
        mode = getattr(t, "_edit_snap_mode", "off")
        if mode == "off" or pos is None:
            return None
        skip = [t._preview_obj] if getattr(t, "_preview_obj", None) else None
        res = t.projector.get_mouse_plane_pt({"Position": pos}, place_on_geometry=(mode == "all"),
                                             working_plane=t.working_plane, skip_objects=skip)
        return FreeCAD.Vector(res[0]) if res and res[0] is not None else None

    def _snap_feedback(self, snap, delta):
        """The snap indicator and guide the old drag showed: at the snapped point while
        Ctrl changed a translation, hidden otherwise. The delta doesn't say whether a
        vertex or the grid won, so the indicator shows "grid"."""
        t = self.tool
        h = delta.handle
        if not delta.snapped or h.kind not in (Kind.TRANSLATE, Kind.PLANE):
            t._hide_snap_visuals()
            return
        at = snap["centre"] + delta.translation
        ind = getattr(t, "_snap_indicator", None)
        if ind:
            ind.show(at, "grid", view=t.view)
        guide = getattr(t, "_snap_guide", None)
        if guide:
            if h.kind is Kind.TRANSLATE:
                if len(h.axes) == 1:
                    guide.show_axis(snap["centre"], delta.axes[h.axes[0]], t.view)
            else:
                normal = delta.axes[next(a for a in AXES if a not in h.axes)]
                guide.show_plane(snap["centre"], normal, t.view, u_hint=delta.axes[h.axes[0]])


class PrimitiveGizmoMixin(SelectionGizmoMixin):
    """The transform-gizmo half of PrimitiveCreatorBase: the driver's controller on a
    `PrimitiveGizmoTarget`, and the snap indicator and guide that target shows."""

    GIZMO_TARGET = PrimitiveGizmoTarget
    MARQUEE = True

    def _gizmo_editable(self):
        """No working plane: no frame, so no gizmo (Round 4 design)."""
        return bool(self.working_plane)

    def _gizmo_unselected_centre(self):
        return self._origin_point()

    def _gizmo_exclude(self):
        """The edited object's own vertices never snap (Round 4 inventory 6)."""
        return [self._preview_obj] if self._preview_obj else []

    def _init_gizmo(self):
        """Edit mode: the driver's controller, then a fresh snap indicator and guide."""
        super()._init_gizmo()
        if self.gizmo_controller is None:
            return
        if self._snap_indicator:
            self._snap_indicator.undraw()
        from freecad.fields.core.input.fld_snap import FldSnapIndicator
        self._snap_indicator = FldSnapIndicator()
        self._snap_indicator.draw(self.points_root)
        if getattr(self, "_snap_guide", None):
            self._snap_guide.undraw()
        from freecad.fields.core.input.fld_snap import FldSnapGuide
        self._snap_guide = FldSnapGuide()
        self._snap_guide.draw(self.points_root)

    def _gizmo_drag_ended(self, committed):
        """Back to the state the press left. A release that didn't commit ran no full
        update (Esc/RMB restored, or typing took over), so the preview is still the
        interactive one."""
        self.state = getattr(self, "_drag_return_state", ToolState.IDLE)
        if not committed:
            self._do_full_preview_update()

    def _gizmo_points_changed(self):
        """After the gizmo moved or restored the points: handles, panel, preview.
        (The controller redraws the gizmo itself.)"""
        self._update_handle_positions(self.points)
        self.update_ui()
        self.update_preview()

    def _hide_snap_visuals(self):
        """Hide the snap indicator and the snap guide, whichever exist."""
        ind = getattr(self, "_snap_indicator", None)
        if ind:
            ind.hide()
        if getattr(self, "_snap_guide", None):
            self._snap_guide.hide()

    def _clear_drag_snapshot(self):
        """Forget the point/plane snapshot a gizmo drag measured from."""
        self._drag_start_points = None
