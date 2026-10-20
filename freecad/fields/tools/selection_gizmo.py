# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""`SelectionGizmoMixin` and `SelectionGizmoTarget`: the universal gizmo for every editor whose
gizmo acts on a selection of points (the cage, the primitives, the work plane, the curve editor).

The mixin owns the selection (one ordered list of opaque keys; the last is the anchor), its one
setter, select / toggle / select-all, the driver's tool-handle hooks (hover, press, D-57's point
drag), press-on-nothing and an optional marquee. `SelectionGizmoTarget` owns the frame (the
selection's centroid), the handle gate and the snapshot's selection. A tool sets `GIZMO_TARGET`
to its subclass of the target, sets the class flags, and overrides the hooks marked "Hook"."""
import FreeCAD
import FreeCADGui
from PySide import QtCore, QtWidgets

from freecad.fields.core import fld_logger
from freecad.fields.core.input.fld_menu_tree import MenuItem
from freecad.fields.core.input.gizmo.models import ALL_HANDLES, AXES, GizmoFrame, HandleId, Kind, world_axes
from freecad.fields.core.input.input_manager import FldInputManager
from freecad.fields.tools.gizmo_driver import GizmoDriverMixin


class SelectionGizmoTarget:
    """The base `GizmoTarget` of a `SelectionGizmoMixin` tool. The frame sits on
    `tool._gizmo_centre()`, turns with `rotation()` and pivots on `pivot(centre)`; no frame and no
    handles while `live()` is False. A subclass supplies `geometry()` and the three writes. Every
    apply is a total from the snapshot."""

    def __init__(self, tool):
        self.tool = tool

    # --- hooks ----------------------------------------------------------------------

    def live(self):
        """False hides the gizmo for now. Hook; default True."""
        return True

    def rotation(self):
        """The local axes' rotation. Hook; default the edited object's Placement."""
        pl = getattr(getattr(self.tool, "_target_obj", None), "Placement", None)
        return FreeCAD.Rotation(pl.Rotation) if pl is not None else FreeCAD.Rotation()

    def pivot(self, centre):
        """The rotate/scale centre for a frame on `centre`. Hook; default `centre`."""
        return centre

    def offered(self):
        """The handles offered while live. Hook; default all 13."""
        return ALL_HANDLES

    def geometry(self):
        """A dict of everything the writes need; the snapshot adds "selection". Hook."""
        raise NotImplementedError

    def gizmo_apply(self, snap, delta):
        raise NotImplementedError

    def gizmo_restore(self, snap):
        raise NotImplementedError

    def gizmo_commit(self):
        raise NotImplementedError

    # --- shared -----------------------------------------------------------------------

    def gizmo_frame(self):
        if not self.live():
            return None
        c = self.tool._gizmo_centre()
        if c is None:
            return None
        rot = self.rotation()
        axes = {k: rot.multVec(v) for k, v in world_axes().items()}
        return GizmoFrame(FreeCAD.Vector(c), axes, FreeCAD.Vector(self.pivot(c)))

    def gizmo_handles(self):
        return self.offered() if self.live() else frozenset()

    def gizmo_snapshot(self):
        return {**self.geometry(), "selection": tuple(self.tool._selection)}

    def gizmo_snap_points(self):
        return []


class SelectionGizmoMixin(GizmoDriverMixin):
    """The gizmo half for any tool that manipulates a selection of points."""

    # Class flags
    GIZMO_TARGET = None
    MULTI_SELECT = True
    MARQUEE = False
    _MARQUEE_THRESHOLD_PX = 4.0

    # State defaults
    _selection = ()
    _pressed_point = None
    _marquee_press_pos = None
    _marquee_active = False
    _marquee_shift = False
    _marquee_band = None

    # --- Hooks -----------------------------------------------------------------------

    def _gizmo_editable(self):
        """Hook: True when the tool's target is in an editable state for the gizmo."""
        return False

    def _pick_point(self, ray_p, ray_d):
        """Hook: hit-test a point under the ray. Returns an opaque key or None."""
        return None

    def _point_world(self, key):
        """Hook: world position of the point identified by `key` (FreeCAD.Vector)."""
        raise NotImplementedError

    def _is_selectable(self, key):
        """Hook: True if `key` is allowed to be selected."""
        return True

    def _selectable_keys(self):
        """Hook: list of all selectable keys (used by select-all and marquee)."""
        return []

    def _selection_changed(self):
        """Hook: called when `_selection` changes."""

    def _point_pressed(self, key):
        """Hook: called when point `key` was clicked/pressed."""

    def _point_drag_handle(self, key):
        """Hook: (HandleId, space) to start when dragging point `key`."""
        return (HandleId(Kind.TRANSLATE, AXES), None)

    def _show_hover(self, key):
        """Hook: visual feedback when hovering point `key` (or None on leaving)."""
        try:
            if key is not None:
                self._set_cursor(QtCore.Qt.PointingHandCursor)
        except Exception as e:
            fld_logger.debug(f"{type(self).__name__}._show_hover: {e}")

    def _gizmo_unselected_centre(self):
        """Hook: fallback frame centre when `_selection` is empty (or None for no gizmo)."""
        return None

    # --- Shared methods --------------------------------------------------------------

    def _make_gizmo_target(self):
        return self.GIZMO_TARGET(self) if (self.GIZMO_TARGET is not None and self._gizmo_editable()) else None

    def _set_selection(self, keys):
        """Set the selection list. Returns True if selection changed, False otherwise."""
        keys_list = list(keys)
        if keys_list == list(self._selection):
            return False
        self._selection = keys_list
        self._selection_changed()
        self._update_gizmo()
        if self.view:
            self.view.redraw()
        return True

    def _key_exists(self, key):
        """Hook: True if `key` still exists in the underlying object."""
        return self._point_world(key) is not None

    def _reload_after_undo(self):
        valid = [k for k in self._selection if self._key_exists(k)]
        self._set_selection(valid)
        self._selection_changed()

    def _select_point(self, key, shift=False):
        """Update selection on clicking `key` with optional `shift` toggle."""
        if key is not None and not self._is_selectable(key):
            key = None

        if key is None:
            new_sel = []
        elif shift and self.MULTI_SELECT:
            cur = list(self._selection)
            if key in cur:
                cur.remove(key)
                new_sel = cur
            else:
                cur.append(key)
                new_sel = cur
        else:
            new_sel = [key]

        return self._set_selection(new_sel)

    def _select_all(self):
        """Select all selectable keys."""
        return self._set_selection(self._selectable_keys())

    def get_context_menu(self, event_dict=None):
        items = list(super().get_context_menu(event_dict)) if hasattr(super(), "get_context_menu") else []
        enabled = bool(self._selectable_keys()) and getattr(self, "_dragging_idx", None) is None and getattr(self, "_is_editing", False)
        items.append(MenuItem("Select All", action=self._select_all, enabled=enabled, shortcut="Shift+A"))
        return items

    def _gizmo_centre(self):
        """The centroid of selected points, else `_gizmo_unselected_centre()`."""
        pts = [self._point_world(k) for k in self._selection if k is not None]
        pts = [p for p in pts if p is not None]
        if pts:
            return sum(pts, FreeCAD.Vector()) * (1.0 / len(pts))
        return self._gizmo_unselected_centre()

    def _get_event_ray(self, event_dict):
        if hasattr(self, "_get_ray"):
            return self._get_ray(event_dict)
        im = FldInputManager.get_instance()
        if im is not None:
            return im.get_ray(self.view, event_dict)
        return None, None

    def _tool_hover(self, event_dict):
        ray_p, ray_d = self._get_event_ray(event_dict)
        if ray_p is None or ray_d is None:
            self._show_hover(None)
            return True
        key = self._pick_point(ray_p, ray_d)
        self._show_hover(key)
        return key is not None

    def _tool_press(self, event_dict):
        self._pressed_point = None
        ray_p, ray_d = self._get_event_ray(event_dict)
        if ray_p is None or ray_d is None:
            return True
        key = self._pick_point(ray_p, ray_d)
        if key is None:
            # On miss, find nearest selectable point, perp distance, and tolerance
            near_info = None
            try:
                keys = self._selectable_keys()
                pts = [self._point_world(k) for k in keys]
                tol = self._compute_handle_radius() if hasattr(self, "_compute_handle_radius") else None
                best_idx, best_dist = (None, float("inf"))
                if pts and hasattr(self, "_hit_test_perp"):
                    # Use a very large tolerance to find the nearest point regardless of cutoff
                    best_idx, best_dist = self._hit_test_perp(ray_p, ray_d, pts, tolerance=float("inf"))
                elif pts:
                    for i, pt in enumerate(pts):
                        if pt is None:
                            continue
                        v = pt - ray_p
                        proj = v.dot(ray_d)
                        perp = (ray_p + ray_d * proj - pt).Length
                        if perp < best_dist:
                            best_dist = perp
                            best_idx = i
                if best_idx is not None and best_idx < len(keys):
                    near_info = (keys[best_idx], best_dist, tol)
            except Exception as e:
                fld_logger.debug(f"{type(self).__name__}._tool_press: nearest point lookup failed: {e}", category="input")
            fld_logger.debug(
                f"{type(self).__name__}._tool_press: miss; nearest={near_info}",
                category="input",
            )
            return False
        fld_logger.debug(
            f"{type(self).__name__}._tool_press: picked key={key}",
            category="input",
        )
        mods = event_dict.get("Modifiers", QtCore.Qt.NoModifier)
        shift = bool(self.MULTI_SELECT and (mods & QtCore.Qt.ShiftModifier))
        self._select_point(key, shift=shift)
        self._pressed_point = key
        self._point_pressed(key)
        return True

    def _tool_point_drag(self, event_dict):
        if self._pressed_point is not None and self._pressed_point in self._selection:
            return self._point_drag_handle(self._pressed_point)
        return None

    def on_button1_down(self, event_dict):
        if super().on_button1_down(event_dict):
            return True
        return self._press_missed(event_dict)

    def reset_state(self):
        super().reset_state()
        self._selection = []
        self._pressed_point = None
        self._marquee_press_pos = None
        self._marquee_active = False
        self._marquee_shift = False
        self._hide_marquee_rect()

    def _press_missed(self, event_dict):
        if self.MARQUEE:
            mods = event_dict.get("Modifiers", QtCore.Qt.NoModifier)
            self._marquee_shift = bool(mods & QtCore.Qt.ShiftModifier)
            self._marquee_press_pos = FldInputManager.get_instance().get_mouse_pos(event_dict)
            self._marquee_active = False
            self._dragging_idx = 'marquee'
            self._start_drag_timer()
            return True
        shift = bool(event_dict.get("Modifiers", QtCore.Qt.NoModifier) & QtCore.Qt.ShiftModifier)
        if not shift:
            self._set_selection([])
        return True

    def _tool_drag_update(self):
        if getattr(self, "_dragging_idx", None) == 'marquee':
            if not FldInputManager.get_instance().is_left_mouse_down():
                self._stop_drag_timer()
                self._dragging_idx = None
                self._end_marquee()
                return
            self._marquee_drag_update()

    # --- Marquee support --------------------------------------------------------------

    def _marquee_drag_update(self):
        """Grow/update the rubber-band rectangle from press-pos to the mouse."""
        cur = FldInputManager.get_instance()._last_qt_pos
        if cur is None or self._marquee_press_pos is None:
            return
        dx = cur[0] - self._marquee_press_pos[0]
        dy = cur[1] - self._marquee_press_pos[1]
        if not self._marquee_active:
            if (dx * dx + dy * dy) < self._MARQUEE_THRESHOLD_PX ** 2:
                return
            self._marquee_active = True
        self._show_marquee_rect(self._marquee_press_pos, cur)

    def _show_marquee_rect(self, p0, p1):
        """Draw/update the QRubberBand overlay."""
        try:
            from PySide.QtCore import QRect, QPoint
            rect = QRect(QPoint(int(p0[0]), int(p0[1])), QPoint(int(p1[0]), int(p1[1]))).normalized()
            band = self._marquee_band
            if band is None:
                widget = FldInputManager.get_instance()._cached_viewport
                if widget is None:
                    av = FreeCADGui.activeView()
                    if av and hasattr(av, "getWidget"):
                        widget = av.getWidget()
                if widget is None:
                    return
                band = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Rectangle, widget)
                self._marquee_band = band
            band.setGeometry(rect)
            band.show()
        except Exception as e:
            fld_logger.debug(f"{type(self).__name__}._show_marquee_rect: {e}")

    def _hide_marquee_rect(self):
        band = getattr(self, "_marquee_band", None)
        if band is not None:
            try:
                band.hide()
                band.deleteLater()
            except Exception as e:
                fld_logger.debug(f"{type(self).__name__}._hide_marquee_rect: {e}")
            self._marquee_band = None

    def _end_marquee(self):
        """Resolve the deferred press-on-empty-space gesture on release."""
        was_active = self._marquee_active
        press = self._marquee_press_pos
        shift = self._marquee_shift
        release = FldInputManager.get_instance()._last_qt_pos
        self._hide_marquee_rect()
        self._marquee_press_pos = None
        self._marquee_active = False
        if was_active and press is not None and release is not None:
            self._resolve_marquee_selection(press, release, additive=shift)
        elif not shift:
            self._select_point(None)

    def _resolve_marquee_selection(self, p0, p1, additive):
        """Select every key whose world position's screen projection lands in p0..p1."""
        from freecad.fields.core.input.view_projector import ViewProjector
        projector = getattr(self, "projector", None) or (ViewProjector(self.view) if self.view else None)
        if not projector:
            return
        x0, x1 = sorted((p0[0], p1[0]))
        y0, y1 = sorted((p0[1], p1[1]))
        inside = []
        for k in self._selectable_keys():
            if not self._is_selectable(k):
                continue
            pt = self._point_world(k)
            if pt is None:
                continue
            sp = projector.project_to_screen(FreeCAD.Vector(pt))
            if sp is None:
                continue
            sx, sy = sp
            if x0 <= sx <= x1 and y0 <= sy <= y1:
                inside.append(k)
        if additive:
            new_sel = list(self._selection) + [k for k in inside if k not in self._selection]
        else:
            new_sel = inside
        self._set_selection(new_sel)
