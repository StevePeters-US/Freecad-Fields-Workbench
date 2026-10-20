# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/creators/box_creator.py

Interactive SDF box primitive creator tool.
"""
import FreeCAD
from PySide import QtCore, QtWidgets
from freecad.fields.core import fld_logger
from freecad.fields.core.input.input_manager import FldInputManager
from freecad.fields.core.objects.fld_line import FldLineSet
from freecad.fields.core.sdf.sdf.box import SdfBoxField
from freecad.fields.tools.fld_base import ToolState
from freecad.fields.tools.primitive_creator_base import (
    PrimitiveCreatorBase, StageEntry, MIN_PRIMITIVE_DIM_MM, MIN_PRIMITIVE_DIM_TYPED_MM
)

# Opposite corner index for box corners 1..8 (index 0 is the center handle)
_BOX_OPPOSITE = {1: 7, 2: 8, 3: 5, 4: 6, 5: 3, 6: 4, 7: 1, 8: 2}


class BoxCreator(PrimitiveCreatorBase):

    CREATION_STEPS = [ToolState.PLACE_ANCHOR, ToolState.DRAG_XY, ToolState.DRAG_Z]

    def get_command_id(self):
        return "Fields_CreateBox"

    def get_sdf_type(self):
        return "box"

    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None
        self._height_drag_base = None
        self._profile_end = None

    def _anchor_idx(self):
        return 0  # index 0 is the center/origin handle; corners are at 1-8

    def _first_point(self):
        """The corner the user clicked — points[0] here is a derived centroid.

        `_rebuild_box_points` normalises the drag into min/max corners and puts
        their average at points[0], so the base class's "points[0] is the first
        point" reading would report the middle of the box and make the RMB
        "Set Origin" modes indistinguishable.

        `_anchor_pt` is the actual click and survives the create-then-edit
        handoff. After a document reload it is gone, so fall back to the local
        (-,-,-) corner at index 1: stable, and the corner most boxes were drawn
        from anyway.
        """
        if self._anchor_pt is not None:
            return FreeCAD.Vector(self._anchor_pt)
        if len(self.points) >= 2 and self.points[1] is not None:
            return FreeCAD.Vector(self.points[1])
        return super()._first_point()

    def edit_object(self, obj):
        super().edit_object(obj)  # loads self.points from obj.Points (world-space corners)
        # A box committed in "Current Point" mode had its Placement re-based onto
        # the clicked corner, so recover the click the reload dropped. In
        # "Center" mode the Base is the centroid and tells us nothing, and
        # _first_point() falls back to the local min corner.
        if self._anchor_pt is None and getattr(obj, "OriginMode", None) in ("Current Point", "First Point"):
            self._anchor_pt = FreeCAD.Vector(obj.Placement.Base)
        corners = list(getattr(self, "points", []))
        if len(corners) != 8:
            fld_logger.warn(
                f"BoxCreator.edit_object: expected 8 corners from obj.Points, got {len(corners)}. "
                f"Points property present: {hasattr(obj, 'Points')}, "
                f"Points count: {len(getattr(obj, 'Points', []))}"
            )
            return
        center = FreeCAD.Vector(
            sum(p.x for p in corners) / 8,
            sum(p.y for p in corners) / 8,
            sum(p.z for p in corners) / 8,
        )
        self.points = [center] + corners  # index 0 = origin, 1-8 = corners
        self.current_point = None
        self.state = ToolState.IDLE
        if self._origin_mode in ("Current Point", "First Point") and self.points:
            origin = FreeCAD.Vector(obj.Placement.Base)
            tol = self._compute_handle_radius(origin)
            nearest = None   # distance of the best handle so far; ties keep the first
            for i in range(1, len(self.points)):
                pt = self.points[i]
                if pt is None:
                    continue
                dist = (FreeCAD.Vector(pt) - origin).Length
                if dist <= tol and (nearest is None or dist < nearest):
                    nearest = dist
                    self._current_point_idx = i
        # Move the gizmo (created by super()) to the correct center position.
        # Use update() rather than _init_gizmo() to avoid recreating Coin3D nodes.
        self._update_gizmo()

        self._draw_point_handles()

    # ------------------------------------------------------------------
    # Creation stage hooks
    # ------------------------------------------------------------------

    def _on_stage_accept(self, state, pos):
        if state == ToolState.PLACE_ANCHOR:
            self._anchor_pt = pos
            self._profile_end = pos
            self._field_placement = self._get_placement()
        elif state == ToolState.DRAG_XY:
            self._profile_end = pos
            a, b = self._anchor_pt, self._profile_end
            self._height_drag_base = (a + b) * 0.5
            self.current_point = self._height_drag_base
        elif state == ToolState.DRAG_Z:
            self.current_point = pos
            self._rebuild_box_points()

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_XY:
            self._profile_end = pos
            self._draw_base_rect()
        elif state == ToolState.DRAG_Z:
            self.current_point = pos
            self._rebuild_box_points()

    def _stage_entry(self, state):
        """Size (plane x, plane y) while dragging the footprint; Height after (INP-055)."""
        wp = self.working_plane or FreeCAD.Placement()
        if state == ToolState.DRAG_XY and self._anchor_pt is not None and self._profile_end is not None:
            d = wp.Rotation.inverted().multVec(self._profile_end - self._anchor_pt)
            return StageEntry("Size", "mm", [abs(d.x), abs(d.y)],
                              [-1 if d.x < 0 else 1, -1 if d.y < 0 else 1])
        if state == ToolState.DRAG_Z and self._height_drag_base is not None and self.current_point is not None:
            h = self._normal_offset(self.current_point)
            return StageEntry("Height", "mm", [abs(h)], [-1 if h < 0 else 1])
        return None

    def _stage_point_for(self, state, values):
        wp = self.working_plane or FreeCAD.Placement()
        if state == ToolState.DRAG_XY:
            u = wp.Rotation.multVec(FreeCAD.Vector(1, 0, 0))
            v = wp.Rotation.multVec(FreeCAD.Vector(0, 1, 0))
            return self._anchor_pt + u * values[0] + v * values[1]
        return self._normal_stage_point(values[0])

    def _rebuild_box_points(self):
        """Build self.points (8 world corners) from anchor, profile_end, and current_point."""
        if not self._anchor_pt or not self._profile_end:
            return
        wp = self.working_plane
        if wp:
            inv = wp.inverse()
            loc_a = inv.multVec(self._anchor_pt)
            loc_b = inv.multVec(self._profile_end)
            if self.current_point and self.current_point != self._height_drag_base:
                loc_h = inv.multVec(self.current_point)
                top_z = loc_h.z
            else:
                top_z = loc_a.z + 1.0  # minimal height before user sets it
            base_z = loc_a.z
        else:
            loc_a = self._anchor_pt
            loc_b = self._profile_end
            top_z = self.current_point.z if self.current_point else loc_a.z + 1.0
            base_z = loc_a.z

        cx = (loc_a.x + loc_b.x) / 2.0
        cy = (loc_a.y + loc_b.y) / 2.0
        cz = (base_z + top_z) / 2.0
        hx = max(abs(loc_a.x - loc_b.x) / 2.0, MIN_PRIMITIVE_DIM_MM)
        hy = max(abs(loc_a.y - loc_b.y) / 2.0, MIN_PRIMITIVE_DIM_MM)
        hz = max(abs(top_z - base_z) / 2.0, MIN_PRIMITIVE_DIM_MM)
        center = FreeCAD.Vector(cx, cy, cz)
        half = FreeCAD.Vector(hx, hy, hz)
        pts_local = self._box_corners_local(center, half)
        if wp:
            corners = [wp.multVec(lc) for lc in pts_local]
        else:
            corners = pts_local
        ctr = FreeCAD.Vector(
            sum(p.x for p in corners) / 8,
            sum(p.y for p in corners) / 8,
            sum(p.z for p in corners) / 8,
        )
        self.points = [ctr] + corners

    def _draw_base_rect(self):
        """Draw a 4-edge rectangle ghost on the workplane during DRAG_XY."""
        if not self._anchor_pt or not self._profile_end:
            return
        wp = self.working_plane
        if wp:
            inv = wp.inverse()
            loc_a = inv.multVec(self._anchor_pt)
            loc_b = inv.multVec(self._profile_end)
            z = loc_a.z
            corners_local = [
                FreeCAD.Vector(loc_a.x, loc_a.y, z),
                FreeCAD.Vector(loc_b.x, loc_a.y, z),
                FreeCAD.Vector(loc_b.x, loc_b.y, z),
                FreeCAD.Vector(loc_a.x, loc_b.y, z),
            ]
            corners = [wp.multVec(lc) for lc in corners_local]
        else:
            a, b = self._anchor_pt, self._profile_end
            corners = [
                a,
                FreeCAD.Vector(b.x, a.y, a.z),
                b,
                FreeCAD.Vector(a.x, b.y, a.z),
            ]
        line_pts = []
        for i in range(4):
            line_pts.extend([corners[i], corners[(i + 1) % 4]])
        if self.fld_line_set is None:
            self.fld_line_set = FldLineSet(self.points_root, color=(1.0, 0.6, 0.2), pattern=0x0F0F)
        self.fld_line_set.update_lines(line_pts, segments=[2] * 4)

    def _update_box_lines(self):
        if self.fld_line_set:
            # Corners are at indices 1-8; bottom face 1-4, top face 5-8
            edges = [(1,2),(2,3),(3,4),(4,1),(5,6),(6,7),(7,8),(8,5),(1,5),(2,6),(3,7),(4,8)]
            line_pts = []
            for i, j in edges:
                line_pts.extend([self.points[i], self.points[j]])
            self.fld_line_set.update_lines(line_pts, segments=[2] * 12)

    def _gizmo_points_changed(self):
        super()._gizmo_points_changed()
        self._update_box_lines()

    # Per-axis min/max corner-index sets, matching the ordering _apply_selection_delta
    # (and _rebuild_box_points/edit_object before it) builds corners 1-8 in.
    _AXIS_MIN_CORNERS = {'x': {1, 4, 5, 8}, 'y': {1, 2, 5, 6}, 'z': {1, 2, 3, 4}}
    _AXIS_MAX_CORNERS = {'x': {2, 3, 6, 7}, 'y': {3, 4, 7, 8}, 'z': {5, 6, 7, 8}}

    def _apply_selection_delta(self, delta):
        """Move the selected corners by `delta`, keeping the box a valid box.

        Generalizes single-corner dragging: for each axis, look at which
        side(s) (min/max) the *selected* corners touch in the drag-start
        snapshot, and shift that bound by delta's component on that axis. A
        selection spanning both sides of an axis (e.g. two diagonal corners,
        or all 8 -- "select all" then G) moves both bounds together, which
        degenerates into translating the whole box on that axis with its
        size unchanged. With exactly one corner selected this reproduces the
        original single-corner-drag behavior exactly. The anchor (index 0)
        is never selectable, so it never reaches here.
        """
        sel = self._selected_indices & set(range(1, 9))
        start = getattr(self, "_drag_start_points", None)
        if not sel or not start or len(start) < 9:
            return

        wp = self.working_plane
        inv = wp.inverse() if wp else None
        local_corners = [inv.multVec(p) if inv else p for p in start[1:9]]
        x_min = min(p.x for p in local_corners)
        x_max = max(p.x for p in local_corners)
        y_min = min(p.y for p in local_corners)
        y_max = max(p.y for p in local_corners)
        z_min = min(p.z for p in local_corners)
        z_max = max(p.z for p in local_corners)

        local_delta = inv.Rotation.multVec(delta) if inv else FreeCAD.Vector(delta)

        min_dim = MIN_PRIMITIVE_DIM_MM
        if sel & self._AXIS_MIN_CORNERS['x']:
            x_min = min(x_min + local_delta.x, x_max - min_dim)
        if sel & self._AXIS_MAX_CORNERS['x']:
            x_max = max(x_max + local_delta.x, x_min + min_dim)
        if sel & self._AXIS_MIN_CORNERS['y']:
            y_min = min(y_min + local_delta.y, y_max - min_dim)
        if sel & self._AXIS_MAX_CORNERS['y']:
            y_max = max(y_max + local_delta.y, y_min + min_dim)
        if sel & self._AXIS_MIN_CORNERS['z']:
            z_min = min(z_min + local_delta.z, z_max - min_dim)
        if sel & self._AXIS_MAX_CORNERS['z']:
            z_max = max(z_max + local_delta.z, z_min + min_dim)

        pts_local = [
            FreeCAD.Vector(x_min, y_min, z_min),  # 1
            FreeCAD.Vector(x_max, y_min, z_min),  # 2
            FreeCAD.Vector(x_max, y_max, z_min),  # 3
            FreeCAD.Vector(x_min, y_max, z_min),  # 4
            FreeCAD.Vector(x_min, y_min, z_max),  # 5
            FreeCAD.Vector(x_max, y_min, z_max),  # 6
            FreeCAD.Vector(x_max, y_max, z_max),  # 7
            FreeCAD.Vector(x_min, y_max, z_max),  # 8
        ]
        new_corners = [wp.multVec(p) if wp else p for p in pts_local]
        new_ctr = FreeCAD.Vector(
            sum(p.x for p in new_corners) / 8.0,
            sum(p.y for p in new_corners) / 8.0,
            sum(p.z for p in new_corners) / 8.0,
        )
        self.points = [new_ctr] + new_corners
        self._update_gizmo()
        self._update_handle_positions(self.points)
        self._update_box_lines()
        self.update_preview()
        if self.view:
            self.view.redraw()

    def _field_from_two_corners(self, corner_a_world, corner_b_world):
        """Rebuild SdfBoxField from two opposite world corners."""
        wp = self.working_plane
        if wp:
            inv = wp.inverse()
            lc_a = inv.multVec(corner_a_world)
            lc_b = inv.multVec(corner_b_world)
        else:
            lc_a, lc_b = corner_a_world, corner_b_world
        cx = (lc_a.x + lc_b.x) / 2.0
        cy = (lc_a.y + lc_b.y) / 2.0
        cz = (lc_a.z + lc_b.z) / 2.0
        sx = max(abs(lc_a.x - lc_b.x), 0.1)
        sy = max(abs(lc_a.y - lc_b.y), 0.1)
        sz = max(abs(lc_a.z - lc_b.z), 0.1)
        placement = self._get_placement()
                
        return SdfBoxField(FreeCAD.Vector(cx, cy, cz), FreeCAD.Vector(sx, sy, sz), placement=placement)

    def _get_preview_field(self):
        if not self._anchor_pt or not self._profile_end:
            return None
        self._rebuild_box_points()
        if len(self.points) < 9:
            return None
        return self._field_from_two_corners(self.points[1], self.points[7])

    def _get_final_field(self):
        if self._is_editing:
            if len(self.points) < 9:
                return None
            return self._field_from_two_corners(self.points[1], self.points[7])
        self._rebuild_box_points()
        if len(self.points) < 9:
            return None
        return self._field_from_two_corners(self.points[1], self.points[7])

    def _get_final_points(self):
        if self._is_editing:
            if len(self.points) < 9:
                return None
            corners = self.points[1:9]
            wp = self.working_plane
            if wp is not None:
                inv = wp.inverse()
                return [inv.multVec(p) for p in corners]
            return list(corners)
        self._rebuild_box_points()
        if len(self.points) < 9:
            return None
        corners = self.points[1:9]
        wp = self.working_plane
        if wp is not None:
            inv = wp.inverse()
            return [inv.multVec(p) for p in corners]
        return list(corners)

    def get_parameters(self):
        if len(self.points) < 9: return {}
        local_pts = [self.to_local(p) for p in self.points[1:9]]
        xs = [p.x for p in local_pts]
        ys = [p.y for p in local_pts]
        zs = [p.z for p in local_pts]
        l = max(xs) - min(xs)
        w = max(ys) - min(ys)
        h = max(zs) - min(zs)
        return {"Length": l, "Width": w, "Height": h}

    def _apply_parameters(self, params):
        if len(self.points) < 9:
            return False
        l = max(float(params.get("Length", 1.0)), MIN_PRIMITIVE_DIM_TYPED_MM)
        w = max(float(params.get("Width", 1.0)), MIN_PRIMITIVE_DIM_TYPED_MM)
        h = max(float(params.get("Height", 1.0)), MIN_PRIMITIVE_DIM_TYPED_MM)

        if not self._is_editing and self._anchor_pt is not None and self._profile_end is not None:
            # Creation: the clicked anchor corner stays put and the drag directions
            # are preserved. Written back into the creation state (not just the
            # corners) so _rebuild_box_points() reproduces the typed size.
            loc_a = self.to_local(self._anchor_pt)
            loc_b = self.to_local(self._profile_end)
            sx = 1.0 if loc_b.x >= loc_a.x else -1.0
            sy = 1.0 if loc_b.y >= loc_a.y else -1.0
            self._profile_end = self.to_global(
                FreeCAD.Vector(loc_a.x + sx * l, loc_a.y + sy * w, loc_a.z))
            sz = 1.0
            if self.current_point is not None and self.to_local(self.current_point).z < loc_a.z:
                sz = -1.0
            self.current_point = self.to_global(
                FreeCAD.Vector(loc_a.x, loc_a.y, loc_a.z + sz * h))
            self._rebuild_box_points()
            return True

        # Edit mode: resize about the box centre.
        local_pts = [self.to_local(p) for p in self.points[1:9]]
        xs = [p.x for p in local_pts]; cx = (max(xs) + min(xs)) / 2
        ys = [p.y for p in local_pts]; cy = (max(ys) + min(ys)) / 2
        zs = [p.z for p in local_pts]; cz = (max(zs) + min(zs)) / 2
        loc_center = FreeCAD.Vector(cx, cy, cz)
        half = FreeCAD.Vector(l / 2.0, w / 2.0, h / 2.0)
        new_corners = [self.to_global(p) for p in self._box_corners_local(loc_center, half)]
        new_ctr = FreeCAD.Vector(
            sum(p.x for p in new_corners) / 8,
            sum(p.y for p in new_corners) / 8,
            sum(p.z for p in new_corners) / 8,
        )
        self.points = [new_ctr] + new_corners
        self._update_box_lines()
        return True

    def _get_edit_preview_field(self):
        if len(self.points) < 9:
            return None
        return self._field_from_two_corners(self.points[1], self.points[7])


