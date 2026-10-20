# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/primitive_creator_base.py

Shared base class for interactive SDF primitive creator tools
(BoxCreator, SphereCreator, CylinderCreator, etc.) — handles the
creation-stage state machine, live mesh preview, edit-mode handle
dragging, and the transform gizmo.
"""
from dataclasses import dataclass

import FreeCAD
import FreeCADGui
from PySide import QtCore
from pivy import coin
from freecad.fields.core import fld_logger
from freecad.fields.core.input.fld_menu_tree import MenuItem, SEPARATOR
from freecad.fields.core.input.input_manager import FldInputManager
from freecad.fields.core.input.gizmo import typed
from freecad.fields.core.mesh.fld_mesher import mesh_timer
from freecad.fields.tools.fld_base import DragTimerMixin, ToolState
from freecad.fields.tools.fld_sdf_tool_base import FldSdfToolBase
from freecad.fields.tools.primitive_commit import PrimitiveCommitMixin
from freecad.fields.tools.primitive_gizmo import PrimitiveGizmoMixin
from freecad.fields.tools.primitive_origin import PrimitiveOriginMixin
from freecad.fields.tools.primitive_preview import PrimitivePreviewMixin
from freecad.fields.tools.primitive_selection import (  # noqa: F401  re-imported: tools import it from here
    GIZMO_HIT_TOLERANCE_MULT, PrimitiveSelectionMixin)
from freecad.fields.tools.primitive_panel import PrimitiveTaskPanel
import math

MIN_PRIMITIVE_DIM_MM = 0.5         # smallest legal drag-created half-dimension
MIN_PRIMITIVE_DIM_TYPED_MM = 0.01  # smallest legal typed-in dimension

_STAGE_HINTS = {
    ToolState.PLACE_ANCHOR: "Click to place anchor",
    ToolState.DRAG_XY:      "Move mouse and click to set profile",
    ToolState.DRAG_Z:       "Move mouse and click to set height",
    ToolState.CUSTOM_1:     "Move mouse and click to set parameter 1",
    ToolState.CUSTOM_2:     "Move mouse and click to set parameter 2",
    ToolState.CUSTOM_3:     "Move mouse and click to set parameter 3",
}

_CREATION_PREVIEW_STAGES = frozenset({
    ToolState.DRAG_XY, ToolState.DRAG_Z,
    ToolState.CUSTOM_1, ToolState.CUSTOM_2, ToolState.CUSTOM_3,
})


@dataclass
class StageEntry:
    """A creation stage's readout and typed entry (INP-054)."""
    label: str      # "Size", "Height"
    unit: str       # "mm"
    values: list    # the live sizes, each >= 0
    signs: list     # +1 / -1 per size: the side of the anchor the drag is on


class PrimitiveCreatorBase(PrimitiveOriginMixin, PrimitiveSelectionMixin,
                           PrimitiveGizmoMixin, PrimitivePreviewMixin,
                           PrimitiveCommitMixin, FldSdfToolBase, DragTimerMixin):
    """Base class for SDF primitive creator tools with live mesh preview."""

    # Subclasses declare their step sequence, e.g.:
    #   CREATION_STEPS = [ToolState.PLACE_ANCHOR, ToolState.DRAG_XY, ToolState.DRAG_Z]
    CREATION_STEPS = []

    @staticmethod
    def preview_minor_radius(major_r: float) -> float:
        """Fallback minor/tube radius shown before the user's third click sets it explicitly."""
        return max(major_r * 0.15, 1.0)

    def _get_sdf_obj(self):
        return getattr(self, "_preview_obj", None)

    def _get_default_group(self):
        return getattr(self, "_create_group", "Additive")

    def _set_default_group(self, group):
        self._create_group = group

    def __init__(self):
        super().__init__()
        self._preview_obj = None     # Live FreeCAD object for preview
        self._update_pending = False  # Throttle rapid updates
        self._creating_obj = False    # Re-entrancy guard
        self._create_group = "Additive"  # Q hotkey toggle during creation
        # Reset the shared timer so preview calls for this tool session are isolated
        mesh_timer.reset()

        self.fld_points = []
        self.fld_line_set = None
        self.points_root = coin.SoAnnotation()
        if self.view and self.view.getSceneGraph():
            self.view.getSceneGraph().addChild(self.points_root)

        # Edit mode drag state
        self._dragging_idx = None
        self._current_point_idx = None
        self._drag_plane_n = None
        self._drag_plane_o = None

        self._stage_typed = None        # while sizes are typed: {"buffers", "index", "mouse", "signs", "held"}
        self._stage_readout = None      # FldReadout, made on first use
        self._is_editing = False
        self._came_from_creation = False  # True while in post-creation edit session
        self._drag_return_state = ToolState.IDLE
        self._edit_pivot = None
        self._edit_last_angle = 0.0

        # Snap mode for edit drag (mirrors first-point snap pipeline when active)
        self._edit_snap_mode = 'off'    # 'off' | 'workplane_sdf' | 'all'

        # Transform gizmo (edit mode only)
        self.gizmo_controller = None    # GizmoController in edit mode (INP-040); the input filter's hook
        self._drag_start_points = None  # snapshot of points
        self._snap_indicator = None     # FldSnapIndicator visual (TW-017, TW-018)
        self._snap_guide = None         # FldSnapGuide visual (TW-040)

        # Per-drag axis override (set by subclasses for constrained handles)
        self._drag_axis_override = None

        # Creation-phase anchor (set on PLACE_ANCHOR accept)
        self._anchor_pt = None

        # Which point of itself the primitive's origin sits on. See ORIGIN_MODES.
        self._origin_mode = self.DEFAULT_ORIGIN_MODE

        # The plane the user is drawing on, snapshotted before _rebase_to_origin
        # moves working_plane onto this primitive's origin. reset_state() puts it
        # back, so an Auto Repeat run draws the next primitive on the plane the
        # user picked and not on the last one's centre.
        self._drawing_plane = None

        # Dimensions typed into the task panel during creation. The stage preview
        # re-derives geometry from the mouse on every move, so typed values are
        # pinned here and re-imposed by _apply_param_locks().
        self._locked_params = {}

        self.points = []

        # Unified workplane pre-load logic
        self._init_working_plane()

        # Enter first creation step
        if self.CREATION_STEPS:
            self.state = self.CREATION_STEPS[0]
            fld_logger.info(f"{type(self).__name__}: {_STAGE_HINTS.get(self.state, 'Click to begin')}")

        # Panel is shown after _post_init runs (after edit_object detection)
        self.panel = None

    def get_sdf_type(self):
        return ""

    def _get_source_curve_link(self):
        return None

    def _post_init(self):
        """Override: detect selected object first, THEN show the task panel."""
        super()._post_init()  # runs _detect_selected_object() + updateGui()
        # Now show the panel (after _is_editing is correctly set)
        if not getattr(self, "_terminated", False):
            self.panel = PrimitiveTaskPanel(self)
            gui_doc = FreeCADGui.getDocument(self.doc.Name)
            FreeCADGui.Control.showDialog(self.panel, gui_doc)
            self._dialog_open = True

    def get_command_id(self):
        if self.state == ToolState.EDIT_MODE:
            return "Fields_EditObject"
        return super().get_command_id()

    def get_handled_types(self):
        return ["sdf"]

    def _is_compatible_object(self, obj):
        """All primitive creators share ShapeType == "sdf", so the generic
        handled-types match isn't specific enough — also require the
        object's stamped SdfType to match this creator's own type (e.g. a
        BoxCreator must not silently enter edit mode on a selected Sphere).
        """
        expected = self.get_sdf_type()
        if not expected:
            return True
        return getattr(obj, "SdfType", None) == expected

    def edit_object(self, obj):
        """Load an existing SDF object into the tool for editing.

        Base: sets preview obj, loads raw points, sets workplane.
        Subclasses call super() then reconstruct their specific state and draw handles.
        """
        super().edit_object(obj)
        fld_logger.debug(f"{type(self).__name__}: Editing existing object {obj.Label}")
        self._preview_obj = obj
        self.state = ToolState.EDIT_MODE
        self._selection = []
        self._drag_return_state = ToolState.EDIT_MODE

        self.working_plane = obj.Placement
        self._field_placement = None # Clear stale creation placement
        self._working_plane_is_fallback = False

        # obj.Placement already sits on the chosen origin (set_origin_mode
        # re-based it before the commit); this only restores the check mark.
        stored_mode = getattr(obj, "OriginMode", None)
        if stored_mode == "First Point":
            stored_mode = "Current Point"
        if stored_mode in self.ORIGIN_MODES:
            self._origin_mode = stored_mode

        if hasattr(obj, "Points"):
            self.points = [self.working_plane.multVec(pt) for pt in obj.Points]

        if self._origin_mode == "Current Point" and self.points:
            origin = FreeCAD.Vector(obj.Placement.Base)
            tol = self._compute_handle_radius(origin)
            nearest = None   # distance of the best handle so far; ties keep the first
            for i, pt in enumerate(self.points):
                if pt is None:
                    continue
                dist = (FreeCAD.Vector(pt) - origin).Length
                if dist <= tol and (nearest is None or dist < nearest):
                    nearest = dist
                    self._current_point_idx = i

        self._original_props = {
            "Points": list(getattr(obj, "Points", [])),
            "Placement": FreeCAD.Placement(obj.Placement.Base, obj.Placement.Rotation),
            "SdfField": getattr(getattr(obj, "Proxy", None), "SdfField", None),
            # The mode names which point that Placement sits on, so restoring one
            # without the other would leave the object describing itself wrongly.
            "OriginMode": getattr(obj, "OriginMode", None),
        }

        self._add_transform_handles()
        self._init_gizmo()

    def cancel(self):
        self._drop_stage_typing()
        self._hide_marquee_rect()
        self._marquee_press_pos = None
        self._marquee_active = False
        self._dispose_gizmo()
        self._clear_drag_snapshot()
        if getattr(self, "_snap_indicator", None):
            self._snap_indicator.undraw()
            self._snap_indicator = None
        if getattr(self, "_snap_guide", None):
            self._snap_guide.undraw()
            self._snap_guide = None
        # If the user cancels immediately after creation auto-commits and enters edit mode,
        # treat it as a creation cancel: flip _is_editing so _do_terminate deletes the object.
        if getattr(self, "_came_from_creation", False):
            self._is_editing = False
            self._came_from_creation = False
        super().cancel()

    #: Handle colours. Yellow marks the origin (the point the gizmo sits on when
    #: nothing is selected); white marks the selected point and outranks it.
    HANDLE_COLOR          = (1.0, 0.5, 0.0)
    HANDLE_COLOR_ORIGIN   = (1.0, 1.0, 0.0)
    HANDLE_COLOR_SELECTED = (1.0, 1.0, 1.0)

    #: The anchor handle drags the whole primitive, so it is drawn larger than the
    #: shape handles. It no longer has a colour of its own -- yellow means origin.
    ANCHOR_HANDLE_SCALE = 1.4

    def get_context_menu(self, event_dict=None):
        items = list(super().get_context_menu(event_dict) or [])
        if not getattr(self, "points", None):
            return items    # nothing placed yet, so there is no origin to move
        prefix = [MenuItem("Set Origin", children=self.get_origin_menu())]
        return prefix + [SEPARATOR] + items

    def _anchor_idx(self):
        """Index of the anchor point shown by the gizmo (no separate sphere drawn).
        Return -1 to draw sphere handles for all points (e.g. box corners).
        """
        return 0

    # ------------------------------------------------------------------
    # Edit mode: hover, handle selection, drag
    # ------------------------------------------------------------------

    def handle_move(self, event_dict):
        """Editing: the driver (the D-52 tick, control points through `_tool_hover`, the
        gizmo's hover). Creating: the stage preview, or FldBase's workplane hover."""
        if self._is_editing:
            super().handle_move(event_dict)
            return

        if self.state in _CREATION_PREVIEW_STAGES:
            pos = self._snap_stage_point(self.state, self._project_for_stage(self.state, event_dict),
                                         event_dict)
            if pos:
                self._on_stage_preview(self.state, pos)
                if self._stage_typed is not None:
                    self._refresh_typed_from_mouse()    # INP-057: typed sizes hold, the rest follow
                self.update_preview()
                self._show_stage_readout()
            return

        # PLACE_ANCHOR or IDLE: FldBase's workplane hover (snap grid shown by FldBase).
        # Not super(): with no controller while creating, the driver swallows the move.
        FldSdfToolBase.handle_move(self, event_dict)

    def _edit_on_mouse_press(self, event_dict):
        """LMB in edit mode: delegates to SelectionGizmoMixin / GizmoDriverMixin."""
        if event_dict.get("Button") != QtCore.Qt.LeftButton:
            return False
        return super().on_button1_down(event_dict)

    def _point_axis_override(self, idx):
        """World-space unit vector point `idx` is intrinsically restricted to
        move along under a gizmo-arrow drag, or None if it is free.

        Override where a point has meaning tied to one axis regardless of the
        global X/Y/Z constraint toggle -- e.g. a cylinder's height handle can
        only ever move along the cylinder's own local Z.
        """
        return None

    # ------------------------------------------------------------------
    # Creation state machine - driven by CREATION_STEPS
    # ------------------------------------------------------------------

    def _primitive_name(self):
        return type(self).__name__.replace("Creator", "")

    def on_button1_down(self, event_dict):
        """Unified creation click handler: accept current stage and advance."""
        if self._is_editing:
            return self._edit_on_mouse_press(event_dict)

        if not self.CREATION_STEPS or self.state not in self.CREATION_STEPS:
            return True

        if self._stage_typed is not None:
            return self._accept_typed_stage()    # a click accepts the typed sizes, not the mouse

        skip = [self._preview_obj] if self._preview_obj else None

        # For DRAG_Z, re-project via axis instead of workplane XY
        if self.state == ToolState.DRAG_Z:
            pos = self._project_for_stage(ToolState.DRAG_Z, event_dict)
        else:
            pos = self._resolve_wp_click(event_dict, skip_objects=skip)
        # The click lands where the Ctrl-snapped preview showed it.
        pos = self._snap_stage_point(self.state, pos, event_dict)

        if pos is None:
            return True

        self._on_stage_accept(self.state, pos)
        self._advance_creation_step()
        self._show_stage_readout()
        return True

    # ------------------------------------------------------------------
    # Typed sizes while dragging out a stage (INP-054)
    # ------------------------------------------------------------------

    def _stage_entry(self, state):
        """The readout and typed sizes for creation stage `state`, or None for neither."""
        return None

    def _stage_point_for(self, state, values):
        """The point `_on_stage_preview`/`_on_stage_accept` take for signed sizes `values`."""
        raise NotImplementedError

    def _plane_axes(self):
        """(u, v, n): the working plane's axes in world space."""
        rot = (self.working_plane or FreeCAD.Placement()).Rotation
        return (rot.multVec(FreeCAD.Vector(1, 0, 0)), rot.multVec(FreeCAD.Vector(0, 1, 0)),
                rot.multVec(FreeCAD.Vector(0, 0, 1)))

    def _normal_offset(self, pos):
        """Signed distance of `pos` from the DRAG_Z base along the plane normal."""
        base = getattr(self, "_height_drag_base", None) or self._anchor_pt
        return (pos - base).dot(self._plane_axes()[2])

    def _normal_stage_point(self, v):
        """The DRAG_Z point `v` along the plane normal from its base (`_project_for_stage`)."""
        base = getattr(self, "_height_drag_base", None) or self._anchor_pt
        return base + self._plane_axes()[2] * v

    def _in_plane(self, centre, p):
        """`p - centre` with its normal component removed."""
        d = p - centre
        n = self._plane_axes()[2]
        return d - n * d.dot(n)

    def _radial_stage_point(self, centre, toward, v):
        """The point `v` from `centre`, in the plane, in the direction of `toward`
        (the plane's u when `toward` is on the centre)."""
        d = self._in_plane(centre, toward)
        if d.Length < 1e-9:
            d = self._plane_axes()[0]
        return centre + d.normalize() * v

    def takes_typed_entry(self):
        """True while a creation stage with an entry is being dragged out: the input filter
        then claims digits from FreeCAD's view shortcuts (INP-053)."""
        return (not self._is_editing and self.state in _CREATION_PREVIEW_STAGES
                and self.state in self.CREATION_STEPS
                and self._stage_entry(self.state) is not None)

    def on_key_press(self, event_dict):
        """Typed sizes while dragging out a stage (INP-054) come first; else FldBase."""
        if self._stage_key(event_dict.get("TypedKey")):
            return True
        return super().on_key_press(event_dict)

    def _stage_key(self, name):
        """One key for the stage's typed sizes. True = consumed. With nothing typed, a
        digit, `.` or `-` starts typing, and so does Tab, which then moves to the next size
        (INP-062: Tab must never reach the tool's toggle-edit, which ends the tool). Enter,
        Esc and Backspace stay the tool's."""
        if name is None or not self.takes_typed_entry():
            return False
        if self._stage_typed is None:
            if name not in typed.TYPED_CHARS and name != "tab":
                return False
            e = self._stage_entry(self.state)
            self._stage_typed = {"buffers": [""] * len(e.values), "index": 0,
                                 "mouse": list(e.values), "signs": list(e.signs),
                                 "held": [None] * len(e.values)}
        t = self._stage_typed
        if name == "esc":
            return self._cancel_typed_size()
        if name == "enter":
            return self._accept_typed_stage()
        if name == "tab":
            t["index"] = (t["index"] + 1) % len(t["buffers"])
        else:
            i = t["index"]
            if not t["buffers"][i]:
                t["held"][i] = t["signs"][i]    # INP-059: a typed size keeps the side it started on
            t["buffers"][i] = typed.edit_buffer(t["buffers"][i], name)
        self._preview_typed_stage()
        return True

    def _typed_stage_values(self):
        """The signed sizes: a typed size on the side it was typed on (INP-059), an untyped
        one the mouse's size on the mouse's side."""
        t = self._stage_typed
        return [(h if b else s) * typed.buffer_value(b, f)
                for b, f, s, h in zip(t["buffers"], t["mouse"], t["signs"], t["held"])]

    def _refresh_typed_from_mouse(self):
        """While some sizes are typed: take the mouse's sizes and sides from the preview it
        just drove, then preview again with the typed sizes held (INP-057)."""
        e = self._stage_entry(self.state)
        if e is None:
            return
        t = self._stage_typed
        t["mouse"], t["signs"] = list(e.values), list(e.signs)
        self._on_stage_preview(self.state, self._stage_point_for(self.state, self._typed_stage_values()))

    def _cancel_typed_size(self):
        """Esc or RMB while sizes are typed (INP-057): the active size drops its typed value
        and follows the mouse; on a size with nothing typed, every size does. When none is
        typed any more, typing ends. True: the key or click is consumed."""
        t = self._stage_typed
        i = t["index"]
        if t["buffers"][i]:
            t["buffers"][i] = ""
        else:
            t["buffers"] = [""] * len(t["buffers"])
        pos = self._stage_point_for(self.state, self._typed_stage_values())
        if not any(t["buffers"]):
            self._stage_typed = None
        self._on_stage_preview(self.state, pos)
        self.update_preview()
        self._show_stage_readout()
        return True

    def on_context_menu(self, event_dict):
        """RMB while a size is typed in a creation stage cancels it, like Esc, and opens no
        menu (INP-057; the workbench's RMB = Esc while a value is pending, D-49). Otherwise
        as before: the gizmo's handling, then the tool's menu."""
        if self._stage_typed is not None and self.takes_typed_entry():
            return self._cancel_typed_size()
        return super().on_context_menu(event_dict)

    def _preview_typed_stage(self):
        pos = self._stage_point_for(self.state, self._typed_stage_values())
        self._on_stage_preview(self.state, pos)
        self.update_preview()
        self._show_stage_readout()

    def _accept_typed_stage(self):
        """Enter, or an LMB press, while sizes are typed: accept the stage with them."""
        pos = self._stage_point_for(self.state, self._typed_stage_values())
        self._stage_typed = None
        self._on_stage_accept(self.state, pos)
        self._advance_creation_step()
        self._show_stage_readout()
        return True

    def _show_stage_readout(self):
        """The box under the dragged point: the live sizes, or the typed ones with brackets
        on the active size. Hidden when the stage has no entry (or creation is over)."""
        e = self._stage_entry(self.state) if self.takes_typed_entry() else None
        if e is None:
            if self._stage_readout is not None:
                self._stage_readout.hide()
            return
        t = self._stage_typed
        if t is None:
            text = f"{e.label}  {', '.join(typed.fmt(v) for v in e.values)} {e.unit}"
        else:
            shown = [typed.TYPED_HTML.format(b) if b else typed.LIVE_HTML.format(typed.fmt(f))
                     for b, f in zip(t["buffers"], t["mouse"])]
            shown[t["index"]] = f"[{shown[t['index']]}]"
            text = typed.ROW_HTML.format(f"{e.label}  {', '.join(shown)} {e.unit}")
        if self._stage_readout is None:
            from freecad.fields.core.input.fld_readout import FldReadout
            self._stage_readout = FldReadout()
        self._stage_readout.show(self.view, text)

    def _drop_stage_typing(self):
        """Forget typed sizes and delete the box (cancel, reset, terminate)."""
        self._stage_typed = None
        if self._stage_readout is not None:
            self._stage_readout.dispose()

    def _advance_creation_step(self):
        """Move to the next creation stage, or commit when all stages are done."""
        steps = self.CREATION_STEPS
        if self.state not in steps:
            return
        idx = steps.index(self.state)
        if idx + 1 < len(steps):
            next_state = steps[idx + 1]
            self.state = next_state
            fld_logger.info(f"{self._primitive_name()}: {_STAGE_HINTS.get(next_state, '')}")
        else:
            self.state = ToolState.FINALIZED
            self._commit_and_enter_edit(self._primitive_name())

    def _project_for_stage(self, state, event_dict):
        """Return the world-space point appropriate for the given creation stage."""
        wp = self.working_plane
        if state == ToolState.DRAG_Z:
            base = getattr(self, "_height_drag_base", None) or self._anchor_pt
            if base is None:
                return None
            normal = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1)) if wp else FreeCAD.Vector(0, 0, 1)
            return FldInputManager.get_instance().get_axis_point(self.view, base, normal, event_dict)
        else:
            # DRAG_XY / CUSTOM_N: project onto workplane XY locked to anchor Z
            normal = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1)) if wp else FreeCAD.Vector(0, 0, 1)
            origin = self._anchor_pt or (wp.Base if wp else FreeCAD.Vector())
            return self.projector.get_mouse_world_pos(
                event_dict, normal, origin, place_on_geometry=False
            )

    def _snap_stage_point(self, state, pos, event_dict):
        """Ctrl-snap a creation-stage point without leaving that stage's constraint.

        PLACE_ANCHOR and DRAG_XY quantize the in-plane offset (from the plane origin,
        or from the anchor once there is one) in the working plane's own axes, then
        drop any off-plane part, so a vertex candidate is projected into the plane.
        DRAG_Z quantizes the height along the plane normal from the drag base. The
        CUSTOM stages turn the point into a parameter (a radius, a bevel), which a
        grid point would not quantize, so they pass through.
        """
        if pos is None or state not in (ToolState.PLACE_ANCHOR, ToolState.DRAG_XY, ToolState.DRAG_Z):
            return pos
        from freecad.fields.core.input import fld_snap
        if not fld_snap.snap_active():
            if getattr(self, "_snap_indicator", None):
                self._snap_indicator.hide()
            return pos
        wp = self.working_plane or FreeCAD.Placement()
        u = wp.Rotation.multVec(FreeCAD.Vector(1, 0, 0))
        v = wp.Rotation.multVec(FreeCAD.Vector(0, 1, 0))
        n = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        exclude = [self._preview_obj] if getattr(self, "_preview_obj", None) else []
        if state == ToolState.DRAG_Z:
            base = getattr(self, "_height_drag_base", None) or self._anchor_pt
            if base is None:
                return pos
            snapped = self._apply_snap(pos, event_dict, origin=base, axis_unit_vec=n, exclude=exclude)
            return base + n * (snapped - base).dot(n)
        origin = self._anchor_pt if (state == ToolState.DRAG_XY and self._anchor_pt) else wp.Base
        snapped = self._apply_snap(pos, event_dict, origin=origin, frame_axes=(u, v), exclude=exclude)
        return snapped - n * (snapped - origin).dot(n)

    def _on_stage_accept(self, state, pos):
        """Subclass: record pos for the given creation stage."""
        pass

    def _on_stage_preview(self, state, pos):
        """Subclass: update internal state from pos so _get_preview_field() returns the right shape."""
        pass


    def _sync_edit_points(self):
        """Sync fld_point positions back to tool-specific variables. Override in subclasses."""
        pass

    def _get_edit_preview_field(self):
        """Return the SDF field for the current edit state. Defaults to _get_preview_field."""
        return self._get_preview_field()

    # Axis constraint helpers inherited from FldBase

    def finish(self):
        """In edit mode, finish commits edits then terminates unless autorepeat. Otherwise, standard creation finish."""
        if getattr(self, "_terminated", False):
            return
        if getattr(self, "_is_editing", False):
            # Persist edits back to the FreeCAD object before leaving edit mode
            obj = self._preview_obj
            self._dispose_gizmo(commit=True)
            if obj and obj.Document:
                self._persist_edit(obj)

            self._is_editing = False
            self._clear_drag_snapshot()
            self._edit_pivot = None
            self._edit_last_angle = 0.0
            self._preview_obj = None
            if self.autorepeat:
                self.reset_state()
            else:
                self._finished = True
                self.terminate()
            return

        if self.is_in_progress():
            name = type(self).__name__.replace("Creator", "")
            if self.autorepeat:
                self._finalize_object(name, terminate=False)
                self.reset_state()
                fld_logger.info(f"{name} accepted. Tool remains active.")
            else:
                self._finalize_object(name, terminate=True)
        else:
            self.terminate()



    def _init_working_plane(self):
        """Pre-load a workplane if one isn't already detected from selection."""
        self._detect_selected_workplane()
        if not self.working_plane:
            visible_wps = self.get_visible_workplanes()
            if visible_wps:
                wp = visible_wps[0]
                if hasattr(wp, "getGlobalPlacement"):
                    self.working_plane = wp.getGlobalPlacement()
                elif hasattr(wp, "Placement"):
                    self.working_plane = wp.Placement
                else:
                    self.working_plane = wp
                self._working_plane_is_fallback = True

    def _compute_default_size(self):
        """Return a world-space length (~15% of viewport height) used to size the initial spawned primitive."""
        try:
            cam = self.view.getCameraNode()
            if isinstance(cam, coin.SoOrthographicCamera):
                h = cam.height.getValue()
            else:
                h = 2.0 * cam.focalDistance.getValue() * math.tan(cam.heightAngle.getValue() / 2.0)
            size = h * 0.15
        except Exception as e:
            fld_logger.debug(f"PrimitiveCreatorBase._compute_default_size: {e}")
            size = 200.0
        return max(5.0, min(size, 500.0))

    @staticmethod
    def _box_corners_local(center, half_size):
        """Return list of 8 FreeCAD.Vector corners in local space.
        
        Order: (-,-,-) (+,-,-) (+,+,-) (-,+,-) (-,-,+) (+,-,+) (+,+,+) (-,+,+)
        """
        c, h = center, half_size
        return [
            FreeCAD.Vector(c.x - h.x, c.y - h.y, c.z - h.z),
            FreeCAD.Vector(c.x + h.x, c.y - h.y, c.z - h.z),
            FreeCAD.Vector(c.x + h.x, c.y + h.y, c.z - h.z),
            FreeCAD.Vector(c.x - h.x, c.y + h.y, c.z - h.z),
            FreeCAD.Vector(c.x - h.x, c.y - h.y, c.z + h.z),
            FreeCAD.Vector(c.x + h.x, c.y - h.y, c.z + h.z),
            FreeCAD.Vector(c.x + h.x, c.y + h.y, c.z + h.z),
            FreeCAD.Vector(c.x - h.x, c.y + h.y, c.z + h.z),
        ]

    def _add_transform_handles(self):
        pass  # Gizmo rings/arrows are the transform handles; legacy center/rot dots removed

    def _get_preview_field(self):
        """Subclasses return the current field based on click state + current_point."""
        return None

    def _get_final_field(self):
        """Subclasses return the final field when placement is committed."""
        return None

    def _get_final_points(self):
        """Default implementation: returns self.points mapped to local space.
        
        This ensures that 'obj.Points' always stores coordinates in the field's
        local coordinate system (relative to obj.Placement / self.working_plane).
        """
        if not self.points:
            return None
            
        # Re-map stored world points to local space using the locked working plane
        return [self.to_local(p) for p in self.points]

    # ------------------------------------------------------------------
    # Task panel contract — origin (Transform) + named dimensions (Parameters)
    # ------------------------------------------------------------------

    #: World-space creation-state vectors moved by translate(); subclasses extend.
    TRANSLATABLE_ATTRS = ("_anchor_pt", "_profile_end", "current_point",
                          "_height_drag_base")

    #: False for creators whose geometry is anchored to a linked object (a curve or
    #: surface): moving them from the panel would detach the result from its source.
    SUPPORTS_ORIGIN_EDIT = True

    # ------------------------------------------------------------------
    # The origin contract: Set Origin and the working plane
    # ------------------------------------------------------------------

    #: Which modes this primitive offers in the right-click "Set Origin" menu.
    #:
    #: Subclasses never override this: every primitive supports "Center" and
    #: "Current Point". What differs across primitives is what the modes mean:
    #: "Center" is always the solid's geometric centre; "Current Point" is the
    #: currently selected/active control point (or reference point fallback).
    ORIGIN_MODES = ("Center", "Current Point")
    DEFAULT_ORIGIN_MODE = "Center"

    def get_parameters(self):
        """Named dimensions in mm for the panel's Parameters group.

        Empty dict = nothing to show yet (e.g. before the first creation click).
        """
        return {}

    def _apply_parameters(self, params):
        """Subclass hook: write params back into the geometry AND, during creation,
        into the state the stage preview rebuilds from. Return True when applied.

        Never call update_preview/update_ui from here — set_parameters does that.
        """
        return False


    def _update_ghost_visuals(self):
        """Standard implementation for primitive tools to show points/edges."""
        pass



    def get_snapping_menu(self):
        # The three entries above the separator are a mutually-exclusive pick of
        # *what surface a free (non-axis-locked) point drag projects onto* --
        # nothing, the workplane/SDF, or any geometry. They do NOT disable
        # snapping; grid/vertex/angle snap (below the separator, the shared
        # block from FldBase used by every tool) still applies regardless of
        # this pick, and grid snap defaults ON. Labelled "Projection Surface"
        # rather than "Snap ..." specifically so it can't read as a master
        # snap-off switch -- that confusion is exactly what shipped before.
        items = [
            MenuItem("Projection Surface: None", action=lambda: self._set_edit_snap('off'),
                     checked=self._edit_snap_mode == 'off'),
            MenuItem("Projection Surface: Workplane + SDF", action=lambda: self._set_edit_snap('workplane_sdf'),
                     checked=self._edit_snap_mode == 'workplane_sdf'),
            MenuItem("Projection Surface: All Geometry", action=lambda: self._set_edit_snap('all'),
                     checked=self._edit_snap_mode == 'all'),
            SEPARATOR,
        ] + self.get_snap_pref_menu()
        return items

    def _set_edit_snap(self, mode):
        self._edit_snap_mode = mode

    def _undraw_creation_handles(self):
        """Undraw the creation-phase point handles and line set.

        Unguarded on purpose: an `undraw()` that raises propagates. `_do_terminate`
        keeps its own copy that swallows each failure.
        """
        for fld_pt in self.fld_points:
            fld_pt.undraw()
        self.fld_points.clear()
        if self.fld_line_set:
            self.fld_line_set.undraw()
            self.fld_line_set = None

    def reset_state(self):
        """Override to clear internal primitive state (points, visuals)."""
        super().reset_state()
        self._drop_stage_typing()
        # Return to first creation step (or IDLE if no steps defined)
        self.state = self.CREATION_STEPS[0] if self.CREATION_STEPS else ToolState.IDLE
        self._anchor_pt = None
        self._current_point_idx = None
        self._origin_mode = self.DEFAULT_ORIGIN_MODE
        # Auto Repeat keeps this instance alive across commits, so hand the
        # user's drawing plane back before the next primitive starts.
        if self._drawing_plane is not None:
            self.working_plane = self._drawing_plane
            self._drawing_plane = None
        self._create_group = "Additive"
        self._locked_params.clear()
        self.points = []
        self._undraw_creation_handles()
        self._edit_snap_mode = 'off'
        self._drag_return_state = ToolState.IDLE
        self._dispose_gizmo()
        if self._snap_indicator:
            self._snap_indicator.undraw()
            self._snap_indicator = None
        if getattr(self, "_snap_guide", None):
            self._snap_guide.undraw()
            self._snap_guide = None
        self.update_ui()
        self.view.redraw()


    def _do_terminate(self):
        try:
            self._drop_stage_typing()
            self._hide_marquee_rect()
            for fld_pt in self.fld_points:
                try:
                    fld_pt.undraw()
                except Exception as e:
                    fld_logger.debug(f"PrimitiveCreatorBase._do_terminate: Failed to undraw point: {e}")
            self.fld_points.clear()
            if self.fld_line_set:
                try:
                    self.fld_line_set.undraw()
                except Exception as e:
                    fld_logger.debug(f"PrimitiveCreatorBase._do_terminate: Failed to undraw line set: {e}")
                self.fld_line_set = None
            try:
                self._dispose_gizmo()
            except Exception as e:
                fld_logger.debug(f"PrimitiveCreatorBase._do_terminate: Failed to dispose gizmo: {e}")
            if getattr(self, "_snap_indicator", None):
                try:
                    self._snap_indicator.undraw()
                except Exception as e:
                    fld_logger.debug(f"PrimitiveCreatorBase._do_terminate: Failed to undraw snap indicator: {e}")
                self._snap_indicator = None
            if getattr(self, "_snap_guide", None):
                try:
                    self._snap_guide.undraw()
                except Exception as e:
                    fld_logger.debug(f"PrimitiveCreatorBase._do_terminate: Failed to undraw snap guide: {e}")
                self._snap_guide = None
            if self.view and self.view.getSceneGraph() and self.points_root:
                self.view.getSceneGraph().removeChild(self.points_root)
        except Exception as e:
            fld_logger.debug(f"PrimitiveCreatorBase._do_terminate: {e}")
        super()._do_terminate()


