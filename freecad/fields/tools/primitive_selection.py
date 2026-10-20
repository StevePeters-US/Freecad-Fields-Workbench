# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/primitive_selection.py

Picking, selecting and drawing a primitive creator's control-point handles:
hit test, click / shift-click / marquee / select-all, the selection delta a
gizmo drag applies, and the handle colours and radii.

Stateless mixin: all selection state (`_selected_indices`, `_marquee_*`, ...) is
set in `PrimitiveCreatorBase.__init__`. It must precede `FldSdfToolBase` in the
bases tuple, and subclass overrides (`BoxCreator._apply_selection_delta`) still
win over it. `GIZMO_HIT_TOLERANCE_MULT` lives here, and
`primitive_creator_base` imports it back so its four other importers keep working.
"""

import FreeCAD
import FreeCADGui
from PySide.QtCore import Qt
from PySide import QtWidgets
from freecad.fields.core import fld_logger
from freecad.fields.core.input.input_manager import FldInputManager
from freecad.fields.core.input.gizmo.models import AXES, HandleId, Kind, world_axes
from freecad.fields.core.objects.fld_point import FldPoint

GIZMO_HIT_TOLERANCE_MULT = 2.5     # tolerance multiplier for gizmo hit testing


class PrimitiveSelectionMixin:
    """The selection and handle half of PrimitiveCreatorBase."""

    @property
    def _selected_indices(self):
        return set(self._selection)

    @_selected_indices.setter
    def _selected_indices(self, value):
        self._selection = sorted(value) if value else []

    @property
    def _selected_point_idx(self):
        return self._selection[-1] if self._selection else None

    @_selected_point_idx.setter
    def _selected_point_idx(self, value):
        if value is None:
            self._selection = []
        else:
            cur = [k for k in self._selection if k != value]
            cur.append(value)
            self._selection = cur

    def _pick_point(self, ray_p, ray_d):
        self._drag_axis_override = None
        hit_idx, _ = self._hit_test_control_points(ray_p, ray_d)
        return hit_idx

    def _point_world(self, i):
        return self.points[i] if 0 <= i < len(self.points) else None

    def _is_selectable(self, i):
        return bool(self.points and 0 <= i < len(self.points) and self.points[i] is not None and i != self._anchor_idx())

    def _selectable_keys(self):
        anchor = self._anchor_idx()
        return [i for i, pt in enumerate(self.points) if pt is not None and i != anchor]

    def _point_pressed(self, key):
        self._drag_axis_override = None

    def _point_drag_handle(self, i):
        axis = self._point_axis_override(i)
        wp = self.working_plane
        if axis is not None and wp and len(self._selected_indices) == 1:
            for k, v in world_axes().items():
                if abs(wp.Rotation.multVec(v).dot(axis)) > 0.999:
                    return HandleId(Kind.TRANSLATE, (k,)), "local"
        return HandleId(Kind.TRANSLATE, AXES), None

    def _selection_changed(self):
        self._update_handle_positions(self.points)

    def _tool_drag_update(self):
        """The drag timer outside a gizmo drag: the parent's marquee. On the release the
        snap visuals go first."""
        if not FldInputManager.get_instance().is_left_mouse_down():
            self._hide_snap_visuals()
        super()._tool_drag_update()

    def _handle_color(self, i, base=None):
        """Colour for control-point handle `i`.

        Selected outranks origin outranks plain. Note what is *not* here: the
        anchor index. Yellow used to mean "the handle that drags the whole
        shape", which on a box is a derived centroid and not the origin at all,
        so the marker and the gizmo sat on different points. The anchor is now
        told apart by size (ANCHOR_HANDLE_SCALE), not by colour.
        """
        if i in getattr(self, "_selected_indices", ()):
            return self.HANDLE_COLOR_SELECTED
        if i == self._origin_idx():
            return self.HANDLE_COLOR_ORIGIN
        return base or self.HANDLE_COLOR

    def _handle_radius_for(self, i, r):
        return r * self.ANCHOR_HANDLE_SCALE if i == self._anchor_idx() else r

    def _draw_point_handles(self, points=None):
        """Draw a sphere handle per point and refresh the preview and panel.

        The tail every subclass `edit_object()` shares. `points` defaults to
        `self.points`; pass a slice when only some points get a handle (prism
        draws three). `None` entries are skipped -- a partially reconstructed
        point list is normal in edit mode.
        """
        r = self._compute_handle_radius()
        for i, pt in enumerate(self.points if points is None else points):
            if pt is None:
                continue
            fld_pt = FldPoint(pt)
            fld_pt.draw_point(self.points_root, self._handle_radius_for(i, r),
                              color=self._handle_color(i))
            self.fld_points.append(fld_pt)
        self.update_preview()
        self.update_ui()

    def _hit_test_control_points(self, ray_p, ray_d):
        """Hit test all *selectable* control points with Z-depth sorting.

        Control points take absolute priority over gizmo handles (axes and
        rotation rings) — a precise click always wins. But the *tolerance* is
        not uniform: selected points keep a generous pick radius (`near_tol`,
        easy to grab without pixel-perfect aim), while every other point is
        held to its plain visual radius (`r`).

        That distinction matters because `near_tol` is a world-space disk
        with no falloff along the ray (see `_hit_test_perp`): once it is
        bigger than "roughly on this handle", an *unrelated* point's halo can
        reach clear across the gizmo parked on the selection — worst right
        after dragging out a small primitive, where the view is zoomed out
        relative to it. A precise click still resolves correctly either way;
        only the generous margin is selection-gated.

        The anchor (`_anchor_idx()`) is never a candidate: it is a guide for
        rotation/placement only, not a selectable/draggable point.

        Returns (best_idx, 'point'), or (None, None).
        """
        if not ray_p or not ray_d or not getattr(self, "points", None):
            return None, None

        r = self._compute_handle_radius(self._gizmo_centre())
        near_tol = r * GIZMO_HIT_TOLERANCE_MULT
        privileged = set(getattr(self, "_selected_indices", ()))
        anchor = self._anchor_idx()

        best_idx, best_perp, best_depth = None, float('inf'), float('inf')
        for i, pt in enumerate(self.points):
            if pt is None or i == anchor:
                continue
            tol = near_tol if (not privileged or i in privileged) else r
            idx, perp = self._hit_test_perp(ray_p, ray_d, [pt], tolerance=tol)
            if idx is None:
                continue
            proj = (FreeCAD.Vector(pt) - ray_p).dot(ray_d)
            if proj < best_depth - 1e-4 or (abs(proj - best_depth) <= 1e-4 and perp < best_perp):
                best_depth, best_perp, best_idx = proj, perp, i

        if best_idx is not None:
            return best_idx, 'point'

        return None, None

    def _tool_refresh(self):
        if self.points:
            self._update_handle_positions(self.points)

    def _apply_selection_delta(self, delta):
        """Translate every selected point by `delta`.

        `delta` is always the *total* offset from where the drag began,
        recomputed fresh every tick (never a per-tick increment) -- so this
        reads from `_drag_start_points` (the snapshot taken when the gizmo
        drag started, see `_edit_on_mouse_press`) rather than
        `self.points`, the same way `_gizmo_drag_update`'s whole-primitive
        translate already does. Applying `delta` on top of the live,
        already-moved points would double it up every frame.

        Points with an intrinsic axis (see `_point_axis_override`) only take
        the component of `delta` along that axis. This is routed through
        a gizmo translation-arrow drag with a selection -- override in subclasses
        whose points can't move independently without breaking a shape invariant (e.g.
        BoxCreator, where all 8 corners together describe one rectangular box).
        """
        start = getattr(self, "_drag_start_points", None) or self.points
        changed = False
        for i in sorted(self._selected_indices):
            if i >= len(start) or start[i] is None or i >= len(self.points):
                continue
            axis = self._point_axis_override(i)
            step = FreeCAD.Vector(axis) * delta.dot(axis) if axis else delta
            self.points[i] = start[i] + step
            changed = True
        if not changed:
            return
        self._update_handle_positions(self.points)
        self._refresh_origin()
        self.update_ui()
        self.update_preview()
        if self.view:
            self.view.redraw()

    def _update_handle_positions(self, world_pts, color=None):
        """Update fld_points to match the given world-space positions.
        
        Creates new FldPoint objects as needed, updates existing ones.
        """
        if color is None:
            color = self.HANDLE_COLOR
        while len(self.fld_points) < len(world_pts):
            self.fld_points.append(FldPoint(world_pts[len(self.fld_points)]))
        
        r = self._compute_handle_radius(ref_pt=world_pts[0] if world_pts else None)
        for i, pt in enumerate(world_pts):
            if i >= len(self.fld_points):
                break
            self.fld_points[i].position = pt
            pt_color = self._handle_color(i, base=color)
            r_i = self._handle_radius_for(i, r)
            if self.fld_points[i]._point_sep is None:
                self.fld_points[i].draw_point(self.points_root, radius=r_i, color=pt_color)
            else:
                self.fld_points[i].update_draw(radius=r_i)
                self.fld_points[i].set_color(pt_color)
        self._add_transform_handles()
