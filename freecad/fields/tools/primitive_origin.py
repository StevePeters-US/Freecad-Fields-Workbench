# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/primitive_origin.py

The Set Origin contract for the primitive creators: `points[0]` (or the solid's
centre) is the primitive's origin, `working_plane` is re-based onto it at commit
(`_rebase_to_origin`) and becomes `obj.Placement`, and moving the origin never
moves the solid.

Stateless mixin: every field it reads is set in `PrimitiveCreatorBase.__init__`,
and `ORIGIN_MODES`, `DEFAULT_ORIGIN_MODE`, `SUPPORTS_ORIGIN_EDIT` and
`TRANSLATABLE_ATTRS` stay on that class, reached through `self.`. It must precede
`FldSdfToolBase` in the bases tuple (see `test_primitive_creator_mro.py`), and
subclass overrides (`_first_point` on Sphere/Cylinder/Box) still win over it.
"""

import FreeCAD
from freecad.fields.core import fld_logger
from freecad.fields.core.input.fld_menu_tree import MenuItem, SEPARATOR


class PrimitiveOriginMixin:
    """The origin half of PrimitiveCreatorBase: where the origin is, how it is switched, how it is stored."""

    def _center_point(self):
        """World centre of the solid, or None when there is no geometry yet.

        Read off the field's AABB. Every primitive here is symmetric about its
        own centre, so the axis-aligned box of the solid is symmetric about it
        too and the reading is exact even on a rotated working plane, where the
        AABB itself is not tight.
        """
        # In edit mode the geometry is re-derived from self.points; during
        # creation it comes from the stage state, and the two builders read
        # different sources. Asking the wrong one gives the wrong centre.
        if getattr(self, "_is_editing", False):
            builders = (self._get_edit_preview_field, self._get_final_field)
        else:
            builders = (self._get_preview_field, self._get_final_field,
                        self._get_edit_preview_field)
        field = None
        for build in builders:
            try:
                field = build()
            except Exception as e:
                fld_logger.debug(f"{type(self).__name__}._center_point: {build.__name__}: {e}")
                field = None
            if field is not None:
                break
        if field is None:
            return None
        try:
            lo, hi = field.bounding_box()
        except Exception as e:
            fld_logger.debug(f"{type(self).__name__}._center_point: bounding_box: {e}")
            return None
        return (FreeCAD.Vector(lo) + FreeCAD.Vector(hi)) * 0.5

    def _first_point(self):
        """World position of the primitive's own reference point, or None.

        `points[0]` by default, where the first click *is* stored -- a torus's
        tube bottom, a prism's centre. Override in two cases:

        * points[0] is something the tool derived rather than something the user
          placed. `BoxCreator` builds a centroid there, so reading points[0]
          would report the box's middle as its first point.
        * points[0] is the centre, which would make this mode a duplicate of
          "Center". `SphereCreator` and `CylinderCreator` return points[1]
          instead -- the point on the bounding circle the radius was dragged to.
        """
        pts = getattr(self, "points", None)
        if pts:
            idx = self._anchor_idx()
            if 0 <= idx < len(pts) and pts[idx] is not None:
                return FreeCAD.Vector(pts[idx])
            if pts[0] is not None:
                return FreeCAD.Vector(pts[0])
        anchor = getattr(self, "_anchor_pt", None)
        if anchor is not None:
            return FreeCAD.Vector(anchor)
        return None

    def _first_point_idx(self):
        """Index of the default reference point (e.g. corner 1 for box)."""
        pts = getattr(self, "points", None)
        if not pts:
            return None
        first = self._first_point()
        if first is not None:
            tol = self._compute_handle_radius(first)
            for i, pt in enumerate(pts):
                if pt is not None and (FreeCAD.Vector(pt) - first).Length <= tol:
                    return i
        return 1 if len(pts) > 1 else 0

    def _current_point(self):
        """World position of the 'Current Point' origin mode."""
        pts = getattr(self, "points", None)
        idx = getattr(self, "_current_point_idx", None)
        if idx is not None and pts and 0 <= idx < len(pts) and pts[idx] is not None:
            return FreeCAD.Vector(pts[idx])
        first = self._first_point()
        if first is not None:
            return first
        if pts and len(pts) > 1 and pts[1] is not None:
            return FreeCAD.Vector(pts[1])
        return None

    def _origin_point(self):
        """World position of the primitive's origin under the current mode.

        Never None: falls back through the current point, the creation anchor and
        the working plane, so the gizmo and the panel always have somewhere to
        sit.
        """
        mode = getattr(self, "_origin_mode", None)
        if mode == "Center":
            centre = self._center_point()
            if centre is not None:
                return centre
        elif mode in ("Current Point", "First Point"):
            cur = self._current_point()
            if cur is not None:
                return cur
        first = self._first_point()
        if first is not None:
            return first
        wp = getattr(self, "working_plane", None)
        if wp is not None:
            return FreeCAD.Vector(wp.Base)
        return FreeCAD.Vector()

    def set_origin_mode(self, mode):
        """Move the primitive's origin to `mode` without moving the solid.

        `working_plane` *is* the field placement, and becomes `obj.Placement` on
        commit. Re-basing it on the new origin re-expresses every stored point in
        that frame -- `_get_final_points()` maps through `to_local` -- while the
        world points, and therefore the geometry, stay exactly where they are.
        Rotation is untouched.
        """
        if mode == "First Point":
            mode = "Current Point"
        if mode not in self.ORIGIN_MODES:
            fld_logger.debug(f"{type(self).__name__}.set_origin_mode: unknown mode {mode!r}")
            return False
        if mode == self._origin_mode:
            return False

        previous = self._origin_mode
        self._origin_mode = mode
        if mode == "Current Point":
            sel = getattr(self, "_selected_point_idx", None)
            if sel is not None:
                self._current_point_idx = sel
            elif getattr(self, "_current_point_idx", None) is None:
                self._current_point_idx = self._first_point_idx()
        # During creation the frame is still the plane the user is drawing on and
        # every later click projects onto it, so moving its Base now would tilt
        # the rest of the primitive off the plane. _finalize_object() re-bases
        # once, at commit, when no more projection happens.
        if getattr(self, "_is_editing", False):
            self._rebase_to_origin()
            self._write_live_origin_mode()
        fld_logger.info(f"Origin: {previous} -> {mode}")

        self._update_gizmo()
        if getattr(self, "points", None) and getattr(self, "fld_points", None):
            self._update_handle_positions(self.points)
        self.update_ui()
        if self.view:
            self.view.redraw()
        return True

    def _rebase_to_origin(self):
        """Move the frame's Base onto the primitive's origin, geometry unmoved.

        Called on every mode change and once more at commit, so `obj.Placement`
        always sits on the chosen origin -- including in the default "First
        Point" mode, where the frame would otherwise still be the raw working
        plane. Only Base moves; rotation and the world points are untouched, so
        `_get_final_points()` simply re-expresses the same solid in the new
        frame.
        """
        if not getattr(self, "points", None):
            return False
        wp = self._get_placement()
        origin = self._origin_point()
        if wp is None or origin is None:
            fld_logger.debug(f"{type(self).__name__}._rebase_to_origin: no working plane to re-base")
            return False
        if (FreeCAD.Vector(wp.Base) - origin).Length < 1e-9:
            return False
        if self._drawing_plane is None and not getattr(self, "_is_editing", False):
            self._drawing_plane = FreeCAD.Placement(FreeCAD.Vector(wp.Base), wp.Rotation)
        self.working_plane = FreeCAD.Placement(FreeCAD.Vector(origin), wp.Rotation)
        return True

    def _refresh_origin(self):
        """Re-seat the origin after the shape changed under it.

        In "Center" mode the origin is a property of the solid, not of a point:
        dragging a cylinder's radius handle slides the mid-height point, and
        stretching a box moves its centroid. Both halves have to follow -- the
        frame, which is what becomes obj.Placement, and the gizmo the user grabs.
        Only re-bases while editing; during creation the frame is still the
        drawing plane (see set_origin_mode).
        """
        pts = getattr(self, "points", None)
        valid_range = lambda i: pts and 0 <= i < len(pts) and pts[i] is not None
        if hasattr(self, "_selection") and self._selection:
            valid = [i for i in self._selection if valid_range(i)]
            if valid != list(self._selection):
                self._set_selection(valid)
        if getattr(self, "_is_editing", False):
            self._rebase_to_origin()
        self._update_gizmo()

    def _write_origin_mode(self, obj):
        """Store the origin mode on the object so a later re-edit resumes in it.

        Without this the RMB submenu would check the right entry only for as
        long as the tool instance lives: obj.Placement would already sit on the
        chosen origin while the object claimed the default mode, and the first
        shape change of the next edit would drag the origin back.
        """
        if obj is None:
            return False
        try:
            if not hasattr(obj, "OriginMode"):
                obj.addProperty("App::PropertyEnumeration", "OriginMode", "Sdf",
                                "Which point of the primitive its Placement sits on")
                obj.OriginMode = list(self.ORIGIN_MODES)
            obj.OriginMode = self._origin_mode
            return True
        except Exception as e:
            fld_logger.debug(f"{type(self).__name__}._write_origin_mode: {e}")
            return False

    def _write_live_origin_mode(self):
        """Persist the mode on the object being edited, if there is one."""
        if getattr(self, "_is_editing", False):
            return self._write_origin_mode(getattr(self, "_preview_obj", None))
        return False

    def get_origin_menu(self):
        """Checkable "Set Origin" entries for the right-click menu."""
        return [MenuItem(m, action=lambda m=m: self.set_origin_mode(m),
                         checked=(m == self._origin_mode))
                for m in self.ORIGIN_MODES]

    def _origin_idx(self):
        """Index of the control point that coincides with the origin, or None.

        The origin is not always a control point: in "Center" mode a cylinder's
        or a torus's middle is a property of the solid with no handle on it, and
        then nothing gets the origin colour -- the gizmo alone marks it.

        Tolerance is the handle radius, so "the marker and the gizmo are on the
        same dot" is decided in the same units the user sees.
        """
        pts = getattr(self, "points", None)
        if not pts:
            return None
        mode = getattr(self, "_origin_mode", None)
        if mode in ("Current Point", "First Point"):
            idx = getattr(self, "_current_point_idx", None)
            if idx is not None and 0 <= idx < len(pts) and pts[idx] is not None:
                return idx
        origin = self._origin_point()
        tol = self._compute_handle_radius(origin)
        best, best_d = None, tol
        for i, pt in enumerate(pts):
            if pt is None:
                continue
            d = (FreeCAD.Vector(pt) - origin).Length
            if d <= best_d:
                best, best_d = i, d
        return best

    def _get_placement(self):
        """Return a FreeCAD.Placement from self.working_plane, or None."""
        wp = getattr(self, "working_plane", None)
        if wp is None:
            return None
        if hasattr(wp, "getGlobalPlacement"):
            return wp.getGlobalPlacement()
        elif hasattr(wp, "Placement"):
            return wp.Placement
        return wp

    def _origin_placement(self):
        """Placement whose Base is points[0] — the primitive's own origin.

        The workbench convention is that points[0] *is* the primitive's origin:
        the gizmo pivots there, the anchor handle drags the whole shape from it,
        and the task panel's Transform group reads it. Fields that carry a centre
        of their own (sphere, cylinder, torus, box) honour that by offsetting in
        local space. Fields built around the local origin -- SdfRevolutionField
        revolves about local Z at (0,0), SdfExtrusionField spans +/-h/2 about
        local z=0 -- have no such parameter, so the frame has to move instead.

        Rotation is the working plane's; only the origin shifts. Returns
        _get_placement() unchanged when there is no first point yet.
        """
        wp = self._get_placement()
        pts = getattr(self, "points", None)
        if not pts or pts[0] is None:
            return wp
        rot = wp.Rotation if wp is not None else FreeCAD.Rotation()
        return FreeCAD.Placement(FreeCAD.Vector(pts[0]), rot)

    def get_origin(self):
        """World-space position shown in the panel's Transform X/Y/Z fields.

        The primitive's origin under the current mode — see `_origin_point()`,
        which the gizmo reads too. Never empty, so the fields always read
        something.
        """
        return self._origin_point()

    def set_origin(self, world_pt):
        """Move the whole primitive so get_origin() lands on world_pt."""
        if not self.SUPPORTS_ORIGIN_EDIT:
            return False
        delta = FreeCAD.Vector(world_pt) - self.get_origin()
        if delta.Length < 1e-9:
            return False
        self.translate(delta)
        return True

    def translate(self, delta):
        """Translate points, creation state, handles and working plane by delta.

        Mirrors _gizmo_drag_update: the working plane moves with the primitive so
        local coordinates (and therefore the field) are unchanged by a move.
        """
        if getattr(self, "points", None):
            self.points = [(p + delta) if p is not None else None for p in self.points]
        for attr in self.TRANSLATABLE_ATTRS:
            val = getattr(self, attr, None)
            if val is not None:
                setattr(self, attr, val + delta)
        if getattr(self, "working_plane", None) is not None:
            self.working_plane.Base = self.working_plane.Base + delta
        if self.points:
            self._update_handle_positions(self.points)
        self._refresh_origin()
        self.update_preview()
        self.update_ui()
        if self.view:
            self.view.redraw()
        return True
