# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""The universal gizmo controller (`universal_gizmo` skill, reference/interaction.md).

Owns one drawable, hover, press, the drag, snapping, undo and the HUD. What it moves
is a `GizmoTarget`; the services it needs come from a `GizmoHost`. Qt-free and
Coin-free itself: it calls the drawable and the host.
"""
import math

import FreeCAD

from freecad.fields.core import fld_logger
from freecad.fields.core.input import fld_snap
from freecad.fields.core.input.fld_gizmo import FldTransformGizmo
from freecad.fields.core.input.gizmo import measure, typed
from freecad.fields.core.input.gizmo.models import (
    AXES, DragState, GizmoDelta, GizmoFrame, HandleId, Kind, Phase, TypedEntry, world_axes)

_TRANSACTION = "Transform"


class GizmoController:
    """State machine behind the universal gizmo. See the module docstring."""

    GIZMO_PX = 112.0   # L = host.handle_scale(origin) * GIZMO_PX   (geometry.md, Size)
    PICK_PX = 20.0     # tol = host.handle_scale(origin) * PICK_PX  (geometry.md, Hit-testing)

    def __init__(self, host, drawable=None):
        self.host = host
        self.drawable = drawable if drawable is not None else FldTransformGizmo()
        self.space = "world"
        self.last_handle = None
        self.state = DragState()
        self._target = None
        self._drawn_names = None
        self._press_live = False
        self._prompting = False     # the HUD shows the empty entry for last_handle (D-51)

    # --- setup --------------------------------------------------------------------

    def set_space(self, space):
        """Draw and drag along global axes ("world") or the target's ("local")."""
        if space not in ("world", "local"):
            raise ValueError(f"space must be 'world' or 'local', got {space!r}")
        self.space = space
        self.refresh()

    def attach(self, target):
        """Make `target` the thing the gizmo moves. Any previous target is detached first."""
        self.detach()
        self._target = target
        self.refresh()

    def detach(self):
        """Drop the target. A drag in progress is cancelled (restored, aborted) first."""
        if self.state.phase is not Phase.IDLE:
            self.cancel()
        self.clear_prompt()
        self._press_live = False
        self.last_handle = None
        self.drawable.undraw()
        self._drawn_names = None
        self._target = None

    def refresh(self):
        """Redraw from `target.gizmo_frame()` and `target.gizmo_handles()`."""
        frame = self._target.gizmo_frame() if self._target is not None else None
        if frame is None:
            self.drawable.undraw()
            self._drawn_names = None
            return
        axes = frame.local_axes if self.space == "local" else world_axes()
        length = self.host.handle_scale(frame.origin) * self.GIZMO_PX
        names = {h.name for h in self._target.gizmo_handles()}
        if self._drawn_names != names:
            self.drawable.draw(self.host.scene_root(), frame.origin, axes=axes, length=length,
                               rot_center=frame.pivot, handles=names)
            self._drawn_names = names
            if self.state.handle is not None:
                self.drawable.set_highlight(self.state.handle.name)
        else:
            self.drawable.update(frame.origin, axes=axes, length=length, rot_center=frame.pivot)
        if self._prompting and self.state.phase is Phase.IDLE:
            self._show_prompt()

    # --- hover and press ----------------------------------------------------------

    def _hit(self, pos, frame):
        """The handle name under screen `pos`, or None."""
        ray = self.host.ray(pos)
        if ray is None:
            return None, None
        tol = self.host.handle_scale(frame.origin) * self.PICK_PX
        return self.drawable.hit_test(ray[0], ray[1], tol), ray

    def hover(self, pos):
        """Highlight the handle under `pos`. IDLE only. True if the highlight changed."""
        if self.state.phase is not Phase.IDLE or self._target is None:
            return False
        frame = self._target.gizmo_frame()
        if frame is None:
            return False
        name, _ray = self._hit(pos, frame)
        if name is not None and self.last_handle is not None and name != self.last_handle.name:
            self.clear_prompt()
        return self.drawable.set_highlight(name)

    def press(self, pos):
        """Start a drag if `pos` is on a handle the target offers. True = consumed.
        In TYPED, any press commits the typed value (D-49). Any press clears the prompt."""
        self.clear_prompt()
        if self.state.phase is Phase.TYPED:
            # D-49: a click while a typed value is pending keeps it, like Enter. The
            # press and its release are the gizmo's, so no tool action lands in the
            # transaction.
            self._press_live = True
            return self._commit()
        if self.state.phase is not Phase.IDLE or self._target is None:
            return False
        frame = self._target.gizmo_frame()
        if frame is None:
            return False
        name, ray = self._hit(pos, frame)
        if name is None:
            return False
        h = HandleId.from_name(name)
        if h.axes == AXES:
            offered = self._target.gizmo_handles()
            move, uniform = HandleId(Kind.TRANSLATE, AXES), HandleId(Kind.SCALE, AXES)
            if self.host.shift_held() and uniform in offered:
                h = uniform
            elif move in offered:
                h = move
            else:
                h = uniform
        if h not in self._target.gizmo_handles():
            return False
        self._start_drag(h, pos, ray, frame, self.space)
        return True

    def _start_drag(self, h, pos, ray, frame, space):
        """DRAG on `h` from a press at `pos`: freeze the frame in `space`, set the anchor,
        snapshot, open the transaction. `last_handle` only for an offered handle (D-57)."""
        axes = ({k: FreeCAD.Vector(v) for k, v in frame.local_axes.items()}
                if space == "local" else world_axes())
        origin, pivot = FreeCAD.Vector(frame.origin), FreeCAD.Vector(frame.pivot)
        st = DragState(phase=Phase.DRAG, handle=h, press_pos=pos,
                       frozen=GizmoFrame(origin, axes, pivot),
                       length=self.host.handle_scale(origin) * self.GIZMO_PX)
        self._set_anchor(st, ray, pos)

        st.snapshot = self._target.gizmo_snapshot()
        self.host.open_transaction(_TRANSACTION)
        self.state = st
        self._press_live = True
        if h in self._target.gizmo_handles():
            self.last_handle = h
        self.drawable.set_highlight(h.name)

    def press_handle(self, handle, pos, space=None):
        """Start a DRAG on `handle` as a press at `pos` would, without the hit test: a point
        drag (D-57). `handle` need not be offered; `space` replaces the controller's for this
        drag only. True if it started (IDLE, a target, a frame and a ray)."""
        if self.state.phase is not Phase.IDLE or self._target is None:
            return False
        frame = self._target.gizmo_frame()
        ray = self.host.ray(pos) if pos is not None else None
        if frame is None or ray is None:
            return False
        self.clear_prompt()
        self._start_drag(handle, pos, ray, frame, space or self.space)
        return True

    def _set_anchor(self, st, ray, pos):
        """Where the mouse measures from, for `st.handle` in `st.frozen`: the point under
        `ray` on the handle's constraint, the ring angle, or the screen distance, as a
        press at `pos` takes it. Used by `press` and by typing from the prompt (D-52)."""
        h, fr = st.handle, st.frozen
        axes, origin, pivot = fr.local_axes, fr.origin, fr.pivot
        rp, rd = ray
        if h.kind is Kind.TRANSLATE and len(h.axes) == 3:      # the free move (D-57)
            st.view_dir = FreeCAD.Vector(rd).normalize()
            p = measure.ray_plane(rp, rd, origin, st.view_dir)
            st.anchor = p if p is not None else origin
        elif h.kind is Kind.TRANSLATE or (h.kind is Kind.SCALE and not h.uniform):
            a = axes[h.axes[0]]
            t0 = measure.line_param(rp, rd, origin, a)
            st.anchor = origin + a * (t0 if t0 is not None else 0.0)
        elif h.kind is Kind.PLANE:
            p = measure.ray_plane(rp, rd, origin, axes[self._normal_axis(h)])
            st.anchor = p if p is not None else origin
        elif h.kind is Kind.ROTATE:
            a = axes[h.axes[0]]
            p = measure.ray_plane(rp, rd, pivot, a)
            st.press_angle = measure.ring_angle(p, pivot, a) if p is not None else 0.0
            st.last_angle = 0.0
            st.anchor = pivot
        else:  # uniform SCALE: measured from `press_pos` (D-60)
            st.anchor = pivot

    @staticmethod
    def _normal_axis(h):
        """The frame axis a PLANE handle does not act on."""
        return next(a for a in AXES if a not in h.axes)

    # --- the drag -----------------------------------------------------------------

    def _measure(self, ray):
        """The total delta from the press for `ray`, or None to hold the last value."""
        st = self.state
        h, fr = st.handle, st.frozen
        rp, rd = ray
        delta = GizmoDelta(h, FreeCAD.Vector(fr.pivot), fr.local_axes)
        if h.kind is Kind.TRANSLATE and len(h.axes) == 3:      # the free move (D-57)
            proj = getattr(self._target, "gizmo_project", None)
            p = proj(self.host.mouse_pos()) if proj is not None else None
            if p is not None:
                delta.translation = FreeCAD.Vector(p) - fr.origin   # lands exactly on p
            else:
                p = measure.ray_plane(rp, rd, st.anchor, st.view_dir)
                if p is None:
                    return None
                delta.translation = p - st.anchor
        elif h.kind is Kind.TRANSLATE:
            a = fr.local_axes[h.axes[0]]
            t = measure.line_param(rp, rd, st.anchor, a)
            if t is None:
                return None
            delta.translation = a * t
        elif h.kind is Kind.PLANE:
            n = fr.local_axes[self._normal_axis(h)]
            if measure.edge_on(rd, n, measure.PLANE_EDGE_ON):
                return None
            p = measure.ray_plane(rp, rd, st.anchor, n)
            if p is None:
                return None
            tr = p - st.anchor
            delta.translation = tr - n * tr.dot(n)
        elif h.kind is Kind.ROTATE:
            a = fr.local_axes[h.axes[0]]
            if measure.edge_on(rd, a, measure.ROTATE_EDGE_ON):
                return None
            p = measure.ray_plane(rp, rd, fr.pivot, a)
            if p is None:
                return None
            ang = measure.ring_angle(p, fr.pivot, a)
            st.last_angle = measure.unwrap(ang - st.press_angle, st.last_angle)
            delta.rotation_deg = math.degrees(st.last_angle)
            delta.rotation_axis = FreeCAD.Vector(a)
        elif not h.uniform:
            a = fr.local_axes[h.axes[0]]
            t = measure.line_param(rp, rd, st.anchor, a)
            if t is None:
                return None
            delta.scale[h.axes[0]] = 1.0 + t / st.length
        else:
            # D-60: horizontal travel from the press, one gizmo length per doubling; right
            # grows, left shrinks, never through zero. The box sits on the pivot (D-55), so a
            # distance from the pivot could only grow.
            pos = self.host.mouse_pos()
            if pos is None or st.press_pos is None:
                return None
            factor = 2.0 ** ((pos[0] - st.press_pos[0]) / self.GIZMO_PX)
            delta.scale = {k: factor for k in AXES}
        return delta

    def _vertex_for(self, delta):
        """The vertex candidate to snap a translation to, or None."""
        fr = self.state.frozen
        settings = self.host.snap_settings()
        if not settings.vertex:
            return None
        here = fr.origin + delta.translation
        here_px = self.host.to_screen(here)
        if not here_px:
            return None
        best, best_d = None, settings.pixel_radius
        for cand in list(self.host.vertex_candidates(here)) + list(self._target.gizmo_snap_points()):
            c_px = self.host.to_screen(cand)
            if not c_px:
                continue
            d = math.hypot(c_px[0] - here_px[0], c_px[1] - here_px[1])
            if d <= best_d:
                best, best_d = cand, d
        return best

    def tick(self):
        """One step of the mouse: measure the total from the press and snap. In DRAG, apply
        it; in TYPED (D-52), it drives the components with nothing typed."""
        st = self.state
        if not self.mouse_drives:
            return
        ray = self.host.ray(self.host.mouse_pos())
        if ray is None:
            return
        delta = self._measure(ray)
        if delta is None:
            delta = st.mouse_delta
            if delta is None:
                return
        elif self.host.ctrl_held():
            vertex = None
            if delta.handle.kind in (Kind.TRANSLATE, Kind.PLANE):
                vertex = self._vertex_for(delta)
            delta = measure.snap_delta(delta, self.host.snap_settings(), st.frozen.origin, vertex)
        st.mouse_delta = delta
        if st.phase is Phase.TYPED:
            self._apply_typed()
            return
        try:
            self._target.gizmo_apply(st.snapshot, delta)
        except Exception:
            fld_logger.exception("GizmoController.tick: gizmo_apply failed")
            self._abort()
            return
        self._show_hud(self._hud(delta))
        self.refresh()
        self.host.redraw()

    @staticmethod
    def _hud(delta):
        h = delta.handle
        label = "".join(a.upper() for a in h.axes)
        if h.kind in (Kind.TRANSLATE, Kind.PLANE):
            text = f"Move {label}  {', '.join(typed.fmt(delta.translation.dot(delta.axes[a])) for a in h.axes)} mm"
        elif h.kind is Kind.ROTATE:
            text = f"Rotate {label}  {typed.fmt(delta.rotation_deg)}°"
        elif h.uniform:
            text = f"Scale  ×{typed.fmt(delta.scale['x'])}"
        else:
            text = f"Scale {label}  ×{typed.fmt(delta.scale[h.axes[0]])}"
        return text + ("  snap" if delta.snapped else "")

    # --- ending a drag --------------------------------------------------------------

    def _end(self):
        """Back to IDLE, with the gizmo redrawn where the target now is (after a
        cancel that is not where the last tick left it)."""
        self._prompting = False
        self._show_hud(None)
        self.drawable.set_highlight(None)
        self.state = DragState()
        self.refresh()

    def _abort(self):
        """Put the target back, abort the transaction, return to IDLE."""
        snapshot = self.state.snapshot
        try:
            self._target.gizmo_restore(snapshot)
        except Exception:
            fld_logger.exception("GizmoController: gizmo_restore failed")
        self.host.abort_transaction()
        self._end()

    def _commit(self):
        """Keep the drag or typed transform: gizmo_commit, commit the transaction, IDLE."""
        try:
            self._target.gizmo_commit()
        except Exception:
            fld_logger.exception("GizmoController: gizmo_commit failed")
            self._abort()
            return True
        self.host.commit_transaction()
        self._end()
        self._show_prompt()
        return True

    def release(self):
        """LMB release. Commits a DRAG. True when the release belongs to the gizmo: it
        ends a drag, or it pairs with a press the gizmo consumed whose drag has since
        moved to TYPED or been committed by Enter (the tool never sees that release)."""
        pressed, self._press_live = self._press_live, False
        if self.state.phase is Phase.DRAG:
            return self._commit()
        return pressed

    def cancel(self):
        """Restore the snapshot and abort, at once: a drag's Esc/RMB, the last step of
        `step_back`, detach, the tool's cancel. True if a DRAG or TYPED ended."""
        if self.state.phase is Phase.IDLE:
            return False
        self._abort()
        return True

    def step_back(self):
        """Esc or RMB while a transform is open (D-54). In TYPED: the active component drops
        its typed value and follows the mouse again; on an empty one, every component does;
        with nothing typed, the transform is cancelled. In DRAG: cancel. True if consumed
        (False in IDLE)."""
        st = self.state
        if st.phase is Phase.TYPED and any(st.typed.buffers):
            t = st.typed
            if t.buffers[t.index]:
                t.buffers[t.index] = ""
            else:
                t.buffers = [""] * len(t.buffers)
            return self._apply_typed()
        return self.cancel()

    def commit(self):
        """Keep a drag or typed transform in progress, e.g. when the tool finishes.
        True if one was open."""
        if self.state.phase is Phase.IDLE:
            return False
        return self._commit()

    @property
    def highlighted(self):
        """The name of the lit handle (hovered, or being dragged), or None."""
        return self.drawable._highlight

    @property
    def mouse_drives(self):
        """True while the mouse moves the target: DRAG, and TYPED until it ends (D-52). A
        tool calls `tick` on mouse moves while this holds."""
        return self.state.phase in (Phase.DRAG, Phase.TYPED)

    def clear_hover(self):
        """Drop the hover highlight, in IDLE only. True if it changed."""
        if self.state.phase is not Phase.IDLE:
            return False
        return self.drawable.set_highlight(None)

    def _show_prompt(self):
        """After a commit: the HUD waits as an empty entry for `last_handle` (D-51), so the
        user sees that typing moves it again. Nothing when that handle isn't offered."""
        h = self.last_handle
        if h is None or self._target is None or h not in self._target.gizmo_handles():
            self.clear_prompt()
            return
        self._prompting = True
        self._show_hud(self._hud_prompt(h))

    def clear_prompt(self):
        """Hide the prompt if it shows (D-51). True if it did."""
        if not self._prompting:
            return False
        self._prompting = False
        self._show_hud(None)
        return True

    # --- typed values (TYPED) ---------------------------------------------------------

    _LIVE_KEYS = typed.TYPED_CHARS | {"backspace", "tab", "enter", "esc"}

    def _typed_handle(self):
        """The handle typed entry may start on from IDLE: last_handle, if still offered."""
        h = self.last_handle
        if h is None or self._target is None or h not in self._target.gizmo_handles():
            return None
        return h

    def claims_key(self, name):
        """Whether the host claims key `name` from its own shortcuts (interaction.md,
        "Key claims"). In DRAG or TYPED every live key; in IDLE only a character that
        can start typed entry, and only when there is a handle to start it on."""
        if name is None or self._target is None:
            return False
        if self.state.phase is not Phase.IDLE:
            return name in self._LIVE_KEYS
        if name == "esc":
            return self._prompting
        return name in typed.TYPED_CHARS and self._typed_handle() is not None

    def key(self, name):
        """One key press, by key name. True = consumed (the tool doesn't get it)."""
        if not self.claims_key(name):
            return False
        phase = self.state.phase
        if phase is Phase.IDLE:
            if name == "esc":
                return self.clear_prompt()
            if not self._begin_typed(self._typed_handle()):
                return False
            return self._type(name)
        if name == "esc":
            return self.step_back()
        if name == "enter":
            return self._commit()
        if phase is Phase.DRAG:
            if name in typed.TYPED_CHARS:
                st = self.state
                st.phase = Phase.TYPED
                st.typed = TypedEntry(st.handle, [""] * typed.components(st.handle))
                return self._type(name)
            return True                      # Backspace, Tab: claimed, no effect in DRAG
        if name == "tab":
            t = self.state.typed
            t.index = (t.index + 1) % len(t.buffers)
            return self._apply_typed()
        return self._type(name)              # a typed character or Backspace

    def _begin_typed(self, h):
        """IDLE → TYPED on `h`: snapshot, open the transaction, freeze the frame, and
        anchor the mouse where it is now, as a press on `h` would (D-52). False (nothing
        opened) without a frame or a mouse ray."""
        frame = self._target.gizmo_frame()
        if frame is None:
            return False
        pos = self.host.mouse_pos()
        ray = self.host.ray(pos) if pos is not None else None
        if ray is None:
            fld_logger.debug("GizmoController: no mouse ray; typing not started")
            return False
        axes = ({k: FreeCAD.Vector(v) for k, v in frame.local_axes.items()}
                if self.space == "local" else world_axes())
        origin = FreeCAD.Vector(frame.origin)
        st = DragState(phase=Phase.TYPED, handle=h, press_pos=pos,
                       frozen=GizmoFrame(origin, axes, FreeCAD.Vector(frame.pivot)),
                       length=self.host.handle_scale(origin) * self.GIZMO_PX)
        self._set_anchor(st, ray, pos)
        st.snapshot = self._target.gizmo_snapshot()
        st.typed = TypedEntry(h, [""] * typed.components(h))
        self.host.open_transaction(_TRANSACTION)
        self._prompting = False
        self.state = st
        self.drawable.set_highlight(h.name)
        return True

    def _type(self, name):
        t = self.state.typed
        t.buffers[t.index] = typed.edit_buffer(t.buffers[t.index], name)
        return self._apply_typed()

    def _apply_typed(self):
        """Apply what the buffers mean. Typed values are exact: never snapped. Untyped
        components are the mouse's (D-52); the identity until the mouse has moved."""
        st = self.state
        mouse = st.mouse_delta
        delta = typed.typed_delta(st.handle, st.frozen, st.typed.buffers, mouse)
        try:
            self._target.gizmo_apply(st.snapshot, delta)
        except Exception:
            fld_logger.exception("GizmoController: gizmo_apply failed (typed)")
            self._abort()
            return True
        shown = (typed.values_of(mouse) if mouse is not None
                 else [typed.identity(st.handle)] * len(st.typed.buffers))
        self._show_hud(*self._hud_typed(st.handle, st.typed, shown))
        self.refresh()
        self.host.redraw()
        return True

    def _show_hud(self, text, rich=None):
        """The HUD line in both places (universal_gizmo interaction.md HUD): the status bar,
        plain, and the readout strip (D-53), which gets `rich` when given (D-52). None
        clears both."""
        self.host.status(text)
        self.host.readout(rich if rich is not None else text)

    @staticmethod
    def _hud_typed(h, entry, shown):
        """The typed line, plain and rich (D-52): each component's buffer when typed, else
        `shown` (the mouse's value, or the identity), with brackets on the component Tab is
        on. Rich: typed bold, the rest grey, as the stage box (INP-060)."""
        plain = [b if b else typed.fmt(v) for b, v in zip(entry.buffers, shown)]
        rich = [typed.TYPED_HTML.format(b) if b else typed.LIVE_HTML.format(typed.fmt(v))
                for b, v in zip(entry.buffers, shown)]
        i = entry.index
        plain[i], rich[i] = f"[{plain[i]}]", f"[{rich[i]}]"
        return (GizmoController._hud_entry(h, plain),
                typed.ROW_HTML.format(GizmoController._hud_entry(h, rich)))

    @staticmethod
    def _hud_prompt(h):
        """`Move X  [ ] mm`: the empty entry typing would fill (D-51); the other components
        show their identity."""
        shown = [typed.fmt(typed.identity(h))] * typed.components(h)
        shown[0] = "[ ]"
        return GizmoController._hud_entry(h, shown)

    @staticmethod
    def _hud_entry(h, shown):
        label = "".join(a.upper() for a in h.axes)
        if h.kind in (Kind.TRANSLATE, Kind.PLANE):
            return f"Move {label}  {', '.join(shown)} mm"
        if h.kind is Kind.ROTATE:
            return f"Rotate {label}  {shown[0]}°"
        if h.uniform:
            return f"Scale  ×{shown[0]}"
        return f"Scale {label}  ×{shown[0]}"

    # --- Ctrl + wheel ---------------------------------------------------------------

    def wheel(self, notches):
        """Ctrl + wheel: step the active handle's snap increment. True = consumed."""
        if self._target is None:
            return False
        h = self.state.handle or self.last_handle
        kind = h.step_kind if h else "grid"
        new = self.host.step_snap(kind, notches)
        self.host.status(fld_snap.snap_step_label(kind, new))
        if self.mouse_drives:
            self.tick()
        return True
