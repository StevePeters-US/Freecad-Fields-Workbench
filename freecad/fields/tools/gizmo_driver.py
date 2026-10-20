# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""`GizmoDriverMixin`: a tool's mouse to its universal gizmo controller.

Hover, press, the drag timer's ticks, the release and RMB go to `gizmo_controller`
(`universal_gizmo` reference/interaction.md); keys reach it through the input filter,
which looks for that attribute (INP-035). The tool supplies `_make_gizmo_target()` and
`points_root`, and lists this mixin before its tool base so these methods win.
The primitive creators drive it too (INP-104..107): their
control points win through `_tool_press`, as the work plane's spheres do.
"""
import math

from PySide import QtCore

from freecad.fields.core import fld_logger
from freecad.fields.core.input.fld_menu_tree import MenuItem, SEPARATOR
from freecad.fields.core.input.gizmo.models import Phase
from freecad.fields.core.input.input_manager import FldInputManager


class GizmoSpaceMixin:
    """World/Local for a tool's gizmo, the same in every tool: `T` and the "Transform
    Space" menu. Starts in Local."""

    _gizmo_space = "local"      # 'world' | 'local'

    def _set_gizmo_space(self, space):
        self._gizmo_space = space
        ctl = getattr(self, "gizmo_controller", None)
        if ctl is not None:
            ctl.set_space(space)
        if self.view:
            self.view.redraw()

    def _toggle_gizmo_space(self):
        self._set_gizmo_space("local" if self._gizmo_space == "world" else "world")

    def get_transform_space_menu(self):
        """Checkable World/Local entries for the right-click menu; the same toggle as `T`."""
        return [
            MenuItem("World", action=lambda: self._set_gizmo_space("world"),
                     checked=self._gizmo_space == "world"),
            MenuItem("Local", action=lambda: self._set_gizmo_space("local"),
                     checked=self._gizmo_space == "local"),
        ]


class _GizmoUndoObserver:
    def __init__(self, tool):
        self._tool = tool

    def _on_step(self, doc):
        if doc is getattr(self._tool, "doc", None):
            QtCore.QTimer.singleShot(0, self._tool._after_undo)

    def slotUndoDocument(self, doc):
        self._on_step(doc)

    def slotRedoDocument(self, doc):
        self._on_step(doc)


class GizmoDriverMixin(GizmoSpaceMixin):
    """See the module docstring."""

    gizmo_controller = None
    _POINT_DRAG_PX = 4.0
    _point_press = None   # (press pos, handle, space) while a point press may become a drag (D-57)

    # --- lifecycle ------------------------------------------------------------------

    def _make_gizmo_target(self):
        """The tool's GizmoTarget, or None for no gizmo. Override."""
        return None

    def _gizmo_root(self):
        """The Coin node the gizmo draws under. Default `points_root`."""
        return self.points_root

    def _gizmo_exclude(self):
        """Objects whose vertices never snap (the host's `exclude`). Default none."""
        return ()

    def _init_gizmo(self):
        """A controller on `_make_gizmo_target()`, in the tool's current space. Any
        previous one is disposed first."""
        from freecad.fields.core.input.gizmo.controller import GizmoController
        from freecad.fields.core.input.gizmo.fld_host import FldGizmoHost
        self._dispose_gizmo()
        target = self._make_gizmo_target()
        if target is None:
            return
        ctl = GizmoController(FldGizmoHost(self.view, self._gizmo_root(),
                                           exclude=self._gizmo_exclude()))
        ctl.space = self._gizmo_space
        ctl.attach(target)
        self.gizmo_controller = ctl
        import FreeCAD
        if hasattr(FreeCAD, "addDocumentObserver"):
            obs = _GizmoUndoObserver(self)
            self._undo_observer = obs
            FreeCAD.addDocumentObserver(obs)

    def _dispose_gizmo(self, commit=False):
        """Drop the controller. A transform in progress is committed (`commit`) or
        restored; the annotation layer and the readout go."""
        if hasattr(self, "_undo_observer") and self._undo_observer is not None:
            try:
                import FreeCAD
                if hasattr(FreeCAD, "removeDocumentObserver"):
                    FreeCAD.removeDocumentObserver(self._undo_observer)
            except Exception as e:
                from freecad.fields.core import fld_logger
                fld_logger.debug(f"GizmoDriverMixin._dispose_gizmo: {e}")
            self._undo_observer = None
        ctl = self.gizmo_controller
        if ctl is None:
            return
        if commit:
            ctl.commit()
        ctl.detach()
        ctl.host.dispose()
        self.gizmo_controller = None

    def _reload_after_undo(self):
        """Hook called by `_after_undo` to sync tool selection/nodes with document."""
        pass

    def _after_undo(self):
        if getattr(self, "_terminated", False):
            return
        ctl = self.gizmo_controller
        if ctl is None or ctl.state.phase != Phase.IDLE:
            return
        self._reload_after_undo()
        self._update_gizmo()
        if self.view and hasattr(self.view, "redraw"):
            self.view.redraw()

    def _update_gizmo(self):
        """Redraw the gizmo where the target now is."""
        if self.gizmo_controller is not None:
            self.gizmo_controller.refresh()

    def _with_transform_space(self, items):
        if self.gizmo_controller is None:
            return list(items or [])
        return [MenuItem("Transform Space", children=self.get_transform_space_menu()),
                SEPARATOR] + list(items or [])

    def get_context_menu(self, event_dict=None):
        """The tool's menu with "Transform Space" on top while the gizmo is up, as the
        primitives have it (`primitive_creator_base.py:301-308`)."""
        items = list(super().get_context_menu(event_dict) or [])
        return self._with_transform_space(items)


    def cancel(self):
        """Restore a transform in progress before the tool reverts its own edits: the
        other order would write the drag's snapshot over `restore_original()`."""
        self._dispose_gizmo()
        super().cancel()

    def finish(self):
        """Keep a transform in progress, then finish."""
        self._dispose_gizmo(commit=True)
        super().finish()

    # --- the tool's own handles (heightmap's amplitude sphere) -----------------------

    def _tool_hover(self, event_dict):
        """True if a tool handle is under the mouse (the tool set the cursor)."""
        return False

    def _tool_press(self, event_dict):
        """True if an LMB press started a drag of a tool handle."""
        return False

    def _tool_point_drag(self, event_dict):
        """After `_tool_press` took a press on a point: `(handle, space)` to drag that point
        through the gizmo once the mouse moves `_POINT_DRAG_PX` (D-57), or None. Override."""
        return None

    def _tool_drag_update(self):
        """The drag timer during a tool-handle drag."""

    def _tool_refresh(self):
        """Re-size the tool's own handles for the current zoom, on the same IDLE moves that
        refresh the gizmo. Override."""

    def _gizmo_drag_ended(self, committed):
        """After the release that ended a gizmo drag; the timer is already stopped.
        `committed` is False when the drag was no longer open at the release: Esc/RMB
        restored it, or digits moved it to TYPED. Override."""

    # --- the mouse ------------------------------------------------------------------

    def handle_move(self, event_dict):
        """While the mouse drives the controller (D-52) a move ticks it, unless the drag
        timer does (a gizmo drag). Otherwise refresh the gizmo, then the tool's handles, then the
        gizmo's hover."""
        ctl = self.gizmo_controller
        if ctl is None:
            return
        if ctl.mouse_drives:
            if getattr(self, "_dragging_idx", None) != "gizmo":
                ctl.tick()
            return
        if getattr(self, "_is_dragging", False):
            return
        if self._point_press is not None and self._point_drag_tick(event_dict.get("Position")):
            return
        ctl.refresh()       # re-measures the gizmo's size, so it stays steady while zooming
        self._tool_refresh()
        if self._tool_hover(event_dict):
            if ctl.clear_hover() and self.view:
                self.view.redraw()
            return
        if ctl.hover(event_dict.get("Position")) and self.view:
            self.view.redraw()
        if ctl.highlighted:
            self._set_cursor(QtCore.Qt.PointingHandCursor)
        else:
            self._restore_cursor()

    def _point_drag_tick(self, pos):
        """A pending point press (D-57): with LMB released it lapses (False). Under 4 px it
        waits, and the move is consumed (True). At 4 px it becomes a gizmo drag from the
        press (True)."""
        press, handle, space = self._point_press
        if not FldInputManager.get_instance().is_left_mouse_down():
            self._point_press = None
            return False
        if pos is None or math.hypot(pos[0] - press[0], pos[1] - press[1]) < self._POINT_DRAG_PX:
            return True
        self._point_press = None
        if not self.gizmo_controller.press_handle(handle, press, space):
            return False
        self._dragging_idx = "gizmo"
        self._start_drag_timer()
        return True

    def on_button1_down(self, event_dict):
        """LMB. In TYPED it commits the typed value (D-49). Otherwise a tool handle first,
        then a gizmo handle, which starts a drag. True = consumed."""
        ctl = self.gizmo_controller
        if ctl is None:
            return False
        pos = event_dict.get("Position")
        if ctl.state.phase is Phase.TYPED:
            fld_logger.debug(
                f"{type(self).__name__}.on_button1_down: pos={pos} phase={ctl.state.phase} branch=typed",
                category="input",
            )
            ctl.press(pos)
            return True
        ctl.clear_prompt()          # D-51: any LMB press
        if self._tool_press(event_dict):
            fld_logger.debug(
                f"{type(self).__name__}.on_button1_down: pos={pos} phase={ctl.state.phase} branch=tool",
                category="input",
            )
            spec = self._tool_point_drag(event_dict)
            self._point_press = (pos,) + tuple(spec) if spec else None
            return True
        if ctl.press(pos):
            fld_logger.debug(
                f"{type(self).__name__}.on_button1_down: pos={pos} phase={ctl.state.phase} branch=gizmo",
                category="input",
            )
            self._dragging_idx = "gizmo"
            self._start_drag_timer()
            return True
        fld_logger.debug(
            f"{type(self).__name__}.on_button1_down: pos={pos} phase={ctl.state.phase} branch=missed",
            category="input",
        )
        return False

    def on_button1_up(self, event_dict):
        """The release of a press the gizmo took is the gizmo's (interaction.md)."""
        return self._gizmo_release()

    def _drag_update(self):
        """Drag timer: the gizmo's drag, else the tool's."""
        if getattr(self, "_dragging_idx", None) == "gizmo":
            self._gizmo_timer_tick()
        else:
            self._tool_drag_update()

    def _gizmo_timer_tick(self):
        """Tick while LMB is down. A release the filter never delivered (let go outside the
        view) ends the drag here."""
        ctl = self.gizmo_controller
        if ctl is not None and FldInputManager.get_instance().is_left_mouse_down():
            ctl.tick()
            return
        self._gizmo_release()

    def _gizmo_release(self):
        """End a gizmo drag (stop the timer), pass the release to the controller, then
        `_gizmo_drag_ended`. True when the release is the gizmo's."""
        self._point_press = None
        ctl = self.gizmo_controller
        ended = getattr(self, "_dragging_idx", None) == "gizmo"
        if ended:
            self._stop_drag_timer()
            self._dragging_idx = None
        if ctl is None:
            return False
        committed = ctl.state.phase is Phase.DRAG
        took = ctl.release()
        if ended:
            self._gizmo_drag_ended(committed)
        return took

    def _gizmo_step_back(self):
        ctl = self.gizmo_controller
        if ctl is not None and ctl.step_back():
            return True
        return False

    def on_context_menu(self, event_dict):
        """RMB while a transform is open is Esc (D-49, D-54) and opens no menu."""
        if self._gizmo_step_back():
            return True
        return super().on_context_menu(event_dict)

    def on_ctrl_wheel(self, notches):
        """Ctrl + wheel steps the active handle's snap increment (universal_gizmo). With no
        controller, or one with no target, it falls through to FldBase's grid step."""
        ctl = self.gizmo_controller
        if ctl is not None and ctl.wheel(notches):
            return
        super().on_ctrl_wheel(notches)


