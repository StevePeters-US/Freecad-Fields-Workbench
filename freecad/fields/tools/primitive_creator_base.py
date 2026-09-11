# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/primitive_creator_base.py

Shared base class for interactive SDF primitive creator tools
(BoxCreator, SphereCreator, CylinderCreator, etc.) — handles the
creation-stage state machine, live mesh preview, edit-mode handle
dragging, and the transform gizmo.
"""
import FreeCAD
import FreeCADGui
from PySide import QtCore, QtWidgets
from pivy import coin
from freecad.fields.core import fld_logger
from freecad.fields.core.input.input_manager import FldInputManager
from freecad.fields.core.objects.fld_point import FldPoint
from freecad.fields.core.objects.fld_line import FldLineSet
from freecad.fields.core.objects.fld_object import (
    create_fld_object, apply_shaded_display_mode)
from freecad.fields.core.fld_mesher import mesh_timer
from freecad.fields.tools.fld_base import DragTimerMixin, ToolState
from freecad.fields.tools.fld_sdf_tool_base import FldSdfToolBase
from freecad.fields.tools.primitive_panel import PrimitiveTaskPanel
import math

# Cell size for interactive preview in mm (larger = faster updates)
_PREVIEW_CELL_SIZE = 20.0

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


class PrimitiveCreatorBase(FldSdfToolBase, DragTimerMixin):
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
        #: Control point the gizmo is parked on, or None for the primitive's
        #: origin. Transient viewport state only: it never touches OriginMode,
        #: never re-bases Placement, and is not what the task panel reads.
        #: Kept in sync with _selected_indices as "primary/most recently
        #: clicked" -- used where a single reference point is needed (Current
        #: Point capture, single-selection call sites).
        self._selected_point_idx = None
        #: Full multi-point selection (Blender-style click / shift-click /
        #: marquee). Never contains the anchor index -- see _anchor_idx().
        self._selected_indices = set()
        self._current_point_idx = None
        self._drag_plane_n = None
        self._drag_plane_o = None

        # Rubber-band (marquee) select: pressing on empty space defers rather
        # than clearing the selection immediately -- it only resolves into a
        # plain click-deselect (or a no-op, if Shift) or a marquee-select once
        # released. See _edit_on_mouse_press / _marquee_drag_update.
        self._marquee_press_pos = None   # Qt logical pixel pos at press, or None
        self._marquee_active = False     # True once movement crossed the threshold
        self._marquee_shift = False      # Shift held at press -> additive marquee
        self._marquee_band = None        # QRubberBand, created lazily

        self._is_editing = False
        self._came_from_creation = False  # True while in post-creation edit session
        self._drag_return_state = ToolState.IDLE
        self._edit_pivot = None
        self._edit_last_angle = 0.0
        self._center_handle = None
        self._rot_handle = None
        self._rot_line = None

        # Axis constraint state (edit mode)
        self._constraint_axis  = None   # 'x' | 'y' | 'z' | None
        self._constraint_plane = None   # 'yz' | 'xz' | 'xy' | None
        self._constraint_space = 'global'  # 'global' | 'local'
        self._constraint_last_key = None   # last key pressed - enables global→local cycle
        self._drag_constraint_base = None  # handle world pos at drag-start

        # Snap mode for edit drag (mirrors first-point snap pipeline when active)
        self._edit_snap_mode = 'off'    # 'off' | 'workplane_sdf' | 'all'
        self._gizmo_space    = 'local'   # 'world' | 'local'
        self._rot_ring_ref   = None      # FreeCAD.Vector, set at rotation drag start
        self._rot_ring_tang  = None      # FreeCAD.Vector, set at rotation drag start

        # Constraint axis visual
        self._constraint_line = None    # FldLineSet drawn along active axis

        # Transform gizmo (edit mode only)
        self._gizmo = None
        self._rot_ring_axis = None      # ring axis frozen at drag start (TW-001)
        self._plane_drag_norm = None    # plane normal frozen at drag start
        self._drag_start_points = None  # snapshot of points at gizmo drag start (TW-011, TW-012)
        self._drag_start_wp_base = None # snapshot of working_plane.Base at drag start
        self._drag_start_wp_rot = None  # snapshot of working_plane.Rotation at drag start (TW-012)
        self._rot_total = 0.0           # total unwrapped angle accumulated during drag (TW-012)
        self._gizmo_transaction = False # True if document transaction is open for gizmo drag (TW-013)
        self._snap_indicator = None     # FldSnapIndicator visual (TW-017, TW-018)
        self._snap_guide = None         # FldSnapGuide visual (TW-040)
        self._last_snap_grid_step = 1.0 # Last non-zero grid step for RMB toggle (TW-021)
        self._last_snap_angle_step = 15.0 # Last non-zero angle step for RMB toggle (TW-021)

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
            FreeCADGui.Control.showDialog(self.panel)
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
        self._selected_point_idx = None
        self._selected_indices = set()
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
            for i, pt in enumerate(self.points):
                if pt is not None and (FreeCAD.Vector(pt) - origin).Length <= tol:
                    self._current_point_idx = i
                    break

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

    def restore_original(self):
        obj = self._preview_obj
        if not obj or not getattr(self, "_is_editing", False) or not hasattr(self, "_original_props"):
            return
        props = self._original_props
        try:
            orig_field = props.get("SdfField")
            if orig_field is not None and hasattr(obj, "Proxy"):
                obj.Proxy.SdfField = orig_field
                from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                label = f"{obj.Document.Name}.{obj.Name}"
                FldSceneVoxelRenderer.get_instance().update_field(label, orig_field)
            if "Points" in props and hasattr(obj, "Points"):
                obj.Points = list(props["Points"])
            if "Placement" in props:
                obj.Placement = props["Placement"]
            if props.get("OriginMode") is not None and hasattr(obj, "OriginMode"):
                obj.OriginMode = props["OriginMode"]
            obj.touch()
        except Exception as e:
            fld_logger.debug(f"PrimitiveCreatorBase.restore_original: {e}")
        view = self.view
        def _deferred_recompute(o=obj, v=view):
            try:
                if o.Document:
                    o.Document.recompute()
            except Exception as e:
                fld_logger.debug(f"PrimitiveCreatorBase.restore_original: {e}")
            if v:
                v.redraw()
        QtCore.QTimer.singleShot(0, _deferred_recompute)

    def cancel(self):
        self._hide_marquee_rect()
        self._marquee_press_pos = None
        self._marquee_active = False
        if getattr(self, "_gizmo_transaction", False):
            doc = getattr(self._preview_obj, "Document", None) or FreeCAD.ActiveDocument
            if doc and hasattr(doc, "abortTransaction"):
                doc.abortTransaction()
            self._gizmo_transaction = False
        self._drag_start_points = None
        self._drag_start_wp_base = None
        self._drag_start_wp_rot = None
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

    def _init_gizmo(self):
        from freecad.fields.core.input.fld_gizmo import FldTransformGizmo
        if self._gizmo:
            self._gizmo.undraw()
            self._gizmo = None
        if not self.working_plane:
            return
        self._gizmo = FldTransformGizmo()
        length = self._gizmo_length()
        self._gizmo.draw(self.points_root, self._gizmo_center(),
                         axes=self._gizmo_axes(), length=length,
                         rot_center=self._origin_point())
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

    def _update_gizmo(self):
        """Update transform gizmo position, axes, length, and origin rotation center."""
        if self._gizmo:
            self._gizmo.update(self._gizmo_center(), axes=self._gizmo_axes(),
                               length=self._gizmo_length(),
                               rot_center=self._origin_point())

    def _gizmo_axes(self):
        """Return axes dict for the current transform space."""
        if self._gizmo_space == 'local' and self.working_plane:
            rot = self.working_plane.Rotation
            return {
                'x': rot.multVec(FreeCAD.Vector(1, 0, 0)),
                'y': rot.multVec(FreeCAD.Vector(0, 1, 0)),
                'z': rot.multVec(FreeCAD.Vector(0, 0, 1)),
            }
        return {
            'x': FreeCAD.Vector(1, 0, 0),
            'y': FreeCAD.Vector(0, 1, 0),
            'z': FreeCAD.Vector(0, 0, 1),
        }

    def _gizmo_center(self):
        """World-space gizmo origin — the centroid of the selection, else the origin.

        Selecting one or more points parks the widget on their centroid so it
        can be translated and rotated about; with nothing selected the widget
        goes back to the primitive's origin under the current Set Origin mode.
        A single-point selection reduces to exactly that point.

        `get_origin()` deliberately does NOT follow this. The panel's Transform
        fields report the primitive's origin, which is a persisted property of
        the object (OriginMode + Placement); the selection is a viewport pivot
        that lives and dies with the edit session.
        """
        pts = getattr(self, "points", None)
        indices = getattr(self, "_selected_indices", None)
        if indices and pts:
            valid = [FreeCAD.Vector(pts[i]) for i in indices
                     if 0 <= i < len(pts) and pts[i] is not None]
            if valid:
                total = sum(valid, FreeCAD.Vector())
                return total * (1.0 / len(valid))
        return self._origin_point()

    # World length that keeps the gizmo ~110 px tall regardless of zoom. The
    # handle radius helper is already a px→world conversion (~8 px), so scale it.
    GIZMO_SCREEN_HANDLES = 14.0

    #: Handle colours. Yellow marks the origin (the point the gizmo sits on when
    #: nothing is selected); white marks the selected point and outranks it.
    HANDLE_COLOR          = (1.0, 0.5, 0.0)
    HANDLE_COLOR_ORIGIN   = (1.0, 1.0, 0.0)
    HANDLE_COLOR_SELECTED = (1.0, 1.0, 1.0)

    #: The anchor handle drags the whole primitive, so it is drawn larger than the
    #: shape handles. It no longer has a colour of its own -- yellow means origin.
    ANCHOR_HANDLE_SCALE = 1.4

    def _gizmo_length(self):
        return self._compute_handle_radius(self._gizmo_center()) * self.GIZMO_SCREEN_HANDLES

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
        indices = getattr(self, "_selected_indices", None)
        if indices is not None:
            self._selected_indices = {i for i in indices if valid_range(i)}
        idx = getattr(self, "_selected_point_idx", None)
        if idx is not None and not valid_range(idx):
            self._selected_point_idx = None
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
        def setter(mode):
            def apply(checked=False, m=mode):
                if checked:                 # ignore the toggled(False) half
                    self.set_origin_mode(m)
            return apply
        return [(m, setter(m), m == self._origin_mode) for m in self.ORIGIN_MODES]

    def get_transform_space_menu(self):
        """Checkable "Transform Space" entries for the right-click menu.

        The same toggle as the `T` key (`handle_keyboard`) — this is just the
        discoverable, mouse-only path to it.
        """
        return [
            ("World", lambda *args: self._set_gizmo_space('world'), self._gizmo_space == 'world'),
            ("Local", lambda *args: self._set_gizmo_space('local'), self._gizmo_space == 'local'),
        ]

    def get_context_menu(self, event_dict=None):
        items = list(super().get_context_menu(event_dict) or [])
        if not getattr(self, "points", None):
            return items    # nothing placed yet, so there is no origin to move
        prefix = [("Set Origin", self.get_origin_menu())]
        if getattr(self, "_is_editing", False) and self._gizmo:
            prefix.append(("Transform Space", self.get_transform_space_menu()))
        return prefix + [None] + items

    def _anchor_idx(self):
        """Index of the anchor point shown by the gizmo (no separate sphere drawn).
        Return -1 to draw sphere handles for all points (e.g. box corners).
        """
        return 0

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

    # ------------------------------------------------------------------
    # Edit mode: hover, handle selection, drag
    # ------------------------------------------------------------------

    def handle_move(self, event_dict):
        """Route mouse-move to the correct creation stage or edit-mode hover."""
        if self._is_editing:
            if self.state != ToolState.DRAGGING:
                self._edit_hover(event_dict)
            return

        if self.state in _CREATION_PREVIEW_STAGES:
            pos = self._project_for_stage(self.state, event_dict)
            if pos:
                self._on_stage_preview(self.state, pos)
                self.update_preview()
            return

        # PLACE_ANCHOR or IDLE: normal workplane hover (snap grid shown by FldBase)
        super().handle_move(event_dict)

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

        r = self._compute_handle_radius(self._gizmo_center())
        near_tol = r * 2.5
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

    def _edit_hover(self, event_dict):
        """Update cursor when hovering over a handle in edit mode."""
        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return
        self._update_gizmo()
        hit_idx, _ = self._hit_test_control_points(ray_p, ray_d)
        if hit_idx is not None:
            if self._gizmo and self._gizmo.set_highlight(None) and self.view:
                self.view.redraw()
            from PySide.QtCore import Qt
            self._set_cursor(Qt.PointingHandCursor)
        else:
            # Also test gizmo handles (translation arrows + rotation rings)
            if self._gizmo and self.working_plane:
                tol  = self._compute_handle_radius(self._gizmo_center()) * 2.5
                axis = self._gizmo.hit_test(ray_p, ray_d, tol)
                if self._gizmo.set_highlight(axis) and self.view:
                    self.view.redraw()
                if axis:
                    from PySide.QtCore import Qt
                    self._set_cursor(Qt.PointingHandCursor)
                    return
            self._restore_cursor()

    def _edit_on_mouse_press(self, event_dict):
        """Hit-test handles in edit mode.

        A control-point hit only *selects* — Blender-style: click to select,
        G to grab. It never starts a drag timer or moves anything by itself.
        Gizmo arrows/rings are the one thing that still drags on a direct
        press, same gesture as before.
        """
        fld_logger.debug(f"{type(self).__name__}._edit_on_mouse_press: _is_editing={self._is_editing}, pts={len(self.points)}, fld_pts={len(self.fld_points)}")
        btn = event_dict.get("Button")
        if btn != QtCore.Qt.LeftButton:
            return False

        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return True

        self._drag_axis_override = None

        # 1. Control points take priority over gizmo handles (axes and rotation rings).
        # Test all control points first with Z-depth sorting.
        hit_idx, hit_kind = self._hit_test_control_points(ray_p, ray_d)
        if hit_idx is not None:
            mods = event_dict.get("Modifiers", QtCore.Qt.NoModifier)
            is_shift = bool(mods & QtCore.Qt.ShiftModifier)
            self._select_point(hit_idx, shift=is_shift)
            return True

        # 2. Test gizmo handles (translation arrows + rotation rings) only if no control point was hit
        if self._gizmo and self.working_plane:
            tol  = self._compute_handle_radius(self._gizmo_center()) * 2.5
            axis = self._gizmo.hit_test(ray_p, ray_d, tol)
            if axis:
                self._dragging_idx = f'gizmo_{axis}'
                self._drag_start_points = [FreeCAD.Vector(p) for p in self.points]
                self._drag_start_wp_base = (FreeCAD.Vector(self.working_plane.Base)
                                            if self.working_plane else None)
                self._drag_start_wp_rot = (FreeCAD.Rotation(self.working_plane.Rotation)
                                           if self.working_plane else None)
                self._rot_total = 0.0
                doc = getattr(self._preview_obj, "Document", None) or FreeCAD.ActiveDocument
                if doc and hasattr(doc, "openTransaction"):
                    doc.openTransaction("Transform")
                    self._gizmo_transaction = True

                if axis.startswith('rot_'):
                    # Rotation ring drag — always pivot at the primitive's origin
                    from freecad.fields.core.input.fld_gizmo import _perp_pair
                    ax_key  = axis[4:]   # 'x', 'y', or 'z'
                    ax_vec  = self._gizmo._axes[ax_key]
                    self._rot_ring_axis = FreeCAD.Vector(ax_vec)
                    pivot   = self._origin_point()
                    self._edit_pivot        = pivot
                    self._drag_constraint_base = pivot
                    ref, tang = _perp_pair(ax_vec)
                    self._rot_ring_ref  = ref
                    self._rot_ring_tang = tang
                    # Seed the initial angle from the click position
                    pt = self.projector.get_mouse_world_pos(
                        event_dict, ax_vec, pivot, place_on_geometry=False)
                    if pt:
                        v = pt - pivot
                        self._edit_last_angle = math.atan2(v.dot(ref), v.dot(tang))
                    else:
                        self._edit_last_angle = 0.0
                elif axis.startswith('plane_'):
                    # Planar translation drag
                    plane_key = axis[6:]  # 'xy', 'xz', or 'yz'
                    norm_axis = {'xy': 'z', 'xz': 'y', 'yz': 'x'}[plane_key]
                    plane_norm = self._gizmo._axes[norm_axis]
                    self._plane_drag_norm = FreeCAD.Vector(plane_norm)
                    gizmo_ctr = self._gizmo_center()
                    click_pt = self.projector.get_mouse_world_pos(
                        event_dict, plane_norm, gizmo_ctr, place_on_geometry=False)
                    self._drag_constraint_base = click_pt if click_pt else gizmo_ctr
                else:
                    # Translation axis drag — base at gizmo center
                    ax_vec = self._gizmo._axes[axis]
                    gizmo_ctr = self._gizmo_center()
                    click_pt = FldInputManager.get_instance().get_axis_point(
                        self.view, gizmo_ctr, ax_vec, event_dict)
                    self._drag_constraint_base = click_pt if click_pt else gizmo_ctr
                self._start_drag_timer()
                return True

        # 3. Nothing under the cursor: defer. This may resolve into a plain
        # click-deselect or a rubber-band (marquee) select on release -- see
        # _end_marquee. Consumed outright (unlike the old immediate-clear
        # behavior) so a click-drag over empty space is ours, not a pass
        # through to FreeCAD's own viewport rubber-band selection.
        mods = event_dict.get("Modifiers", QtCore.Qt.NoModifier)
        self._marquee_shift = bool(mods & QtCore.Qt.ShiftModifier)
        self._marquee_press_pos = FldInputManager.get_instance().get_mouse_pos(event_dict)
        self._marquee_active = False
        self._dragging_idx = 'marquee'
        self._start_drag_timer()
        return True

    def _select_point(self, idx, shift=False):
        """Update the multi-point selection (Blender-style click / shift-click).

        `idx=None` clears the selection. A plain click on a valid point
        replaces the selection with just that point. A shift-click toggles
        `idx` in/out of the existing selection. `_selected_point_idx` is kept
        as the "primary" (most recently clicked) member, for call sites that
        only need a single reference point.

        Redraws only on an actual change: `_edit_hover` runs on every mouse move
        and a redraw per move is what TW-033 took out.
        """
        if idx is not None and not (self.points and 0 <= idx < len(self.points)
                                    and self.points[idx] is not None):
            idx = None

        before = frozenset(self._selected_indices)
        if idx is None:
            self._selected_indices = set()
            self._selected_point_idx = None
        elif shift:
            if idx in self._selected_indices:
                self._selected_indices.discard(idx)
                self._selected_point_idx = (next(iter(self._selected_indices))
                                             if self._selected_indices else None)
            else:
                self._selected_indices.add(idx)
                self._selected_point_idx = idx
        else:
            self._selected_indices = {idx}
            self._selected_point_idx = idx

        if frozenset(self._selected_indices) == before:
            return False
        self._update_gizmo()
        self._update_handle_positions(self.points)
        if self.view:
            self.view.redraw()
        return True

    def _point_axis_override(self, idx):
        """World-space unit vector point `idx` is intrinsically restricted to
        move along under G-grab / a gizmo-arrow drag, or None if it is free.

        Override where a point has meaning tied to one axis regardless of the
        global X/Y/Z constraint toggle -- e.g. a cylinder's height handle can
        only ever move along the cylinder's own local Z.
        """
        return None

    def _apply_selection_delta(self, delta):
        """Translate every selected point by `delta`.

        `delta` is always the *total* offset from where the drag/grab began,
        recomputed fresh every tick (never a per-tick increment) -- so this
        reads from `_drag_start_points` (the snapshot taken when the gizmo
        drag or G-grab started, see `_edit_on_mouse_press` and
        `FldBase.snapshot_modal_state`'s primitive branch) rather than
        `self.points`, the same way `_gizmo_drag_update`'s whole-primitive
        translate already does. Applying `delta` on top of the live,
        already-moved points would double it up every frame.

        Points with an intrinsic axis (see `_point_axis_override`) only take
        the component of `delta` along that axis. This is the one mechanism
        both G-grab (fld_base's modal GRAB, wired to primitives through
        FldBase.update_selected_vertex_pos) and a gizmo translation-arrow drag
        with a selection route through -- override in subclasses whose points
        can't move independently without breaking a shape invariant (e.g.
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

        skip = [self._preview_obj] if self._preview_obj else None

        # For DRAG_Z, re-project via axis instead of workplane XY
        if self.state == ToolState.DRAG_Z:
            pos = self._project_for_stage(ToolState.DRAG_Z, event_dict)
        else:
            pos = self._resolve_wp_click(event_dict, skip_objects=skip)

        if pos is None:
            return True

        self._on_stage_accept(self.state, pos)
        self._advance_creation_step()
        return True

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

    def _on_stage_accept(self, state, pos):
        """Subclass: record pos for the given creation stage."""
        pass

    def _on_stage_preview(self, state, pos):
        """Subclass: update internal state from pos so _get_preview_field() returns the right shape."""
        pass

    def _gizmo_rot_drag_update(self, axis_key):
        """Rotate all control points and working_plane around a gizmo ring axis."""
        if not self._gizmo or axis_key not in self._gizmo._axes:
            return
        # Frozen at drag start. Re-reading _gizmo._axes here would pick up the
        # rotation this drag just applied to working_plane (local space only),
        # so the measuring plane would chase the object and the drag runs away.
        ax_vec = self._rot_ring_axis or self._gizmo._axes[axis_key]
        pivot  = self._edit_pivot
        ref    = self._rot_ring_ref
        tang   = self._rot_ring_tang
        if pivot is None or ref is None or tang is None:
            return
        if self._drag_start_points is None:
            return

        mouse_pos = FldInputManager.get_instance()._last_qt_pos
        # A ring seen near edge-on makes the ray almost parallel to its plane, where
        # one pixel of mouse travel moves the intersection metres. cos(80°) ~= 0.17:
        # below that the reading is noise, so hold the current angle instead.
        view = getattr(self, "view", None)
        if view is not None:
            ray_p, ray_d = FldInputManager.get_instance().get_ray(view, {"Position": mouse_pos})
            if ray_d is not None and abs(ray_d.dot(ax_vec)) < 0.17:
                return
        pt = self.projector.get_mouse_world_pos(
            {"Position": mouse_pos}, ax_vec, pivot, place_on_geometry=False)
        if not pt:
            return

        v     = pt - pivot
        angle = math.atan2(v.dot(ref), v.dot(tang))
        raw_total = angle - self._edit_last_angle  # _edit_last_angle stays the CLICK angle
        # Unwrap so crossing the +-pi branch cut accumulates smoothly
        prev_total = getattr(self, "_rot_total", 0.0)
        total = raw_total + round((prev_total - raw_total) / (2 * math.pi)) * (2 * math.pi)
        self._rot_total = total

        deg = math.degrees(total)
        from freecad.fields.core.input import fld_snap
        if fld_snap.snap_active():
            from freecad.fields.core.fld_settings import get_snap_angle_step
            deg = fld_snap.snap_angle_deg(deg, get_snap_angle_step())

        try:
            import FreeCADGui
            mw = FreeCADGui.getMainWindow()
            if mw:
                sb = mw.statusBar()
                if sb:
                    sb.showMessage(f"Rotate: {deg:.1f}°", 1000)
        except Exception:
            pass

        rot = FreeCAD.Rotation(ax_vec, deg)
        self.points = [pivot + rot.multVec(p - pivot) for p in self._drag_start_points]
        if self.working_plane:
            if self._drag_start_wp_base is not None:
                self.working_plane.Base = pivot + rot.multVec(self._drag_start_wp_base - pivot)
            if self._drag_start_wp_rot is not None:
                self.working_plane.Rotation = rot.multiply(self._drag_start_wp_rot)

        self._update_gizmo()
        self._update_handle_positions(self.points)
        self.update_ui()
        self.update_preview()
        if self.view:
            self.view.redraw()

    def _gizmo_plane_drag_update(self, plane_key):
        """Translate all points + working_plane along the clicked gizmo plane handle."""
        if not self._gizmo or plane_key not in ('xy', 'xz', 'yz'):
            return
        norm_axis = {'xy': 'z', 'xz': 'y', 'yz': 'x'}[plane_key]
        plane_norm = getattr(self, "_plane_drag_norm", None) or self._gizmo._axes[norm_axis]
        mouse_pos = FldInputManager.get_instance()._last_qt_pos
        view = getattr(self, "view", None)
        if view is not None:
            ray_p, ray_d = FldInputManager.get_instance().get_ray(view, {"Position": mouse_pos})
            if ray_d is not None and abs(ray_d.dot(plane_norm)) < 0.08:
                return
        new_pt = self.projector.get_mouse_world_pos(
            {"Position": mouse_pos}, plane_norm, self._drag_constraint_base, place_on_geometry=False)
        if new_pt:
            # Total delta from the click anchor, constrained to the plane
            delta = new_pt - self._drag_constraint_base
            delta = delta - plane_norm * delta.dot(plane_norm)

            ax1_key, ax2_key = {'xy': ('x', 'y'), 'xz': ('x', 'z'), 'yz': ('y', 'z')}[plane_key]
            u1 = self._gizmo._axes[ax1_key]
            u2 = self._gizmo._axes[ax2_key]

            from freecad.fields.core.input import fld_snap
            if fld_snap.snap_active():
                from freecad.fields.core.fld_settings import get_snap_grid_step
                sel = getattr(self, "_selected_indices", None)
                if sel:
                    snap_ref = sum((self._drag_start_points[i] for i in sel
                                    if i < len(self._drag_start_points)), FreeCAD.Vector())
                    snap_ref = snap_ref * (1.0 / len(sel))
                else:
                    snap_ref = self._drag_start_points[0]

                step = get_snap_grid_step()
                d1 = fld_snap.snap_length(delta.dot(u1), step)
                d2 = fld_snap.snap_length(delta.dot(u2), step)
                delta = u1 * d1 + u2 * d2

                res = fld_snap.snap_world_point(
                    self.view, snap_ref + delta,
                    extra_points=[], exclude=[self._preview_obj] if self._preview_obj else [])
                ind = getattr(self, "_snap_indicator", None)
                if res.kind:
                    raw_snap_delta = res.point - snap_ref
                    delta = raw_snap_delta - plane_norm * raw_snap_delta.dot(plane_norm)
                    if ind:
                        ind.show(res.point, res.kind, view=self.view)
                elif ind:
                    ind.show(snap_ref + delta, "grid", view=self.view)
                if getattr(self, "_snap_guide", None):
                    self._snap_guide.show_plane(snap_ref, plane_norm, self.view, u_hint=u1)
            else:
                ind = getattr(self, "_snap_indicator", None)
                if ind:
                    ind.hide()
                if getattr(self, "_snap_guide", None):
                    self._snap_guide.hide()

            if getattr(self, "_selected_indices", None):
                self._apply_selection_delta(delta)
                if self.view:
                    self.view.redraw()
                return
            if self._drag_start_points is not None:
                self.points = [p + delta for p in self._drag_start_points]
            if self.working_plane and self._drag_start_wp_base is not None:
                self.working_plane.Base = self._drag_start_wp_base + delta
            self._update_gizmo()
            self._update_handle_positions(self.points)
            self.update_ui()
            self.update_preview()
        if self.view:
            self.view.redraw()

    def _gizmo_drag_update(self):
        """Translate or rotate all points + working_plane along/around the clicked gizmo handle."""
        axis = self._dragging_idx[len('gizmo_'):]
        if axis.startswith('rot_'):
            self._gizmo_rot_drag_update(axis[4:])   # 'rot_z' → 'z'
            return
        if axis.startswith('plane_'):
            self._gizmo_plane_drag_update(axis[6:]) # 'plane_xy' → 'xy'
            return
        if not self._gizmo or axis not in self._gizmo._axes:
            return
        ax_vec = self._gizmo._axes[axis]
        mouse_pos = FldInputManager.get_instance()._last_qt_pos
        new_pt = FldInputManager.get_instance().get_axis_point(
            self.view, self._drag_constraint_base, ax_vec, {"Position": mouse_pos})
        if new_pt:
            # Total delta from the click anchor, never a per-tick increment: the
            # anchor stays put so the result is re-derivable and quantizable.
            delta = new_pt - self._drag_constraint_base
            from freecad.fields.core.input import fld_snap
            if fld_snap.snap_active():
                from freecad.fields.core.fld_settings import get_snap_grid_step
                # Snap-probing needs a single reference point. With a selection
                # active that is the centroid of the *selected* points at drag
                # start, not always points[0] -- otherwise a corner-selection
                # drag would snap-quantize around the anchor's position instead
                # of the thing actually being dragged.
                sel = getattr(self, "_selected_indices", None)
                if sel:
                    snap_ref = sum((self._drag_start_points[i] for i in sel
                                    if i < len(self._drag_start_points)), FreeCAD.Vector())
                    snap_ref = snap_ref * (1.0 / len(sel))
                else:
                    snap_ref = self._drag_start_points[0]
                # Quantize along the axis, not per world component: an arrow drag is
                # one degree of freedom and rounding x/y/z separately would push the
                # result off the axis the user grabbed.
                d = fld_snap.snap_length(delta.dot(ax_vec), get_snap_grid_step())
                delta = FreeCAD.Vector(ax_vec) * d
                res = fld_snap.snap_world_point(
                    self.view, snap_ref + delta,
                    extra_points=[], exclude=[self._preview_obj] if self._preview_obj else [])
                ind = getattr(self, "_snap_indicator", None)
                if res.kind:
                    # Only take the component along the axis - a vertex off-axis must
                    # not drag the object sideways out of its constraint.
                    delta = FreeCAD.Vector(ax_vec) * (res.point - snap_ref).dot(ax_vec)
                    if ind:
                        ind.show(res.point, res.kind, view=self.view)
                elif ind:
                    ind.show(snap_ref + delta, "grid", view=self.view)
                if getattr(self, "_snap_guide", None):
                    self._snap_guide.show_axis(snap_ref, ax_vec, self.view)
            else:
                ind = getattr(self, "_snap_indicator", None)
                if ind:
                    ind.hide()
                if getattr(self, "_snap_guide", None):
                    self._snap_guide.hide()

            if getattr(self, "_selected_indices", None):
                # A selection exists: the arrow moves just those points (see
                # _apply_selection_delta), not the whole primitive.
                self._apply_selection_delta(delta)
                if self.view:
                    self.view.redraw()
                return
            if self._drag_start_points is not None:
                self.points = [p + delta for p in self._drag_start_points]
            if self.working_plane and self._drag_start_wp_base is not None:
                self.working_plane.Base = self._drag_start_wp_base + delta
            self._update_gizmo()
            self._update_handle_positions(self.points)
            self.update_ui()
            self.update_preview()
        if self.view:
            self.view.redraw()

    def _drag_update(self):
        """QTimer callback: drive an active gizmo-handle drag from the mouse.

        Plain control points no longer drag on press (click only selects, see
        _edit_on_mouse_press/_select_point) so _dragging_idx is only ever None,
        'marquee', or a 'gizmo_*'-prefixed string by the time this runs.
        """
        was_marquee = self._dragging_idx == 'marquee'
        if self._drag_check_lmb_released():
            self._clear_constraint_visual()
            if getattr(self, "_gizmo_transaction", False):
                doc = getattr(self._preview_obj, "Document", None) or FreeCAD.ActiveDocument
                if doc and hasattr(doc, "commitTransaction"):
                    doc.commitTransaction()
                self._gizmo_transaction = False
            self._drag_start_points = None
            self._drag_start_wp_base = None
            self._drag_start_wp_rot = None
            self._plane_drag_norm = None
            if self._snap_indicator:
                self._snap_indicator.hide()
            if getattr(self, "_snap_guide", None):
                self._snap_guide.hide()
            if was_marquee:
                self._end_marquee()
            return
        if self._dragging_idx is None:
            return

        if isinstance(self._dragging_idx, str) and self._dragging_idx.startswith('gizmo_'):
            self._gizmo_drag_update()
        elif self._dragging_idx == 'marquee':
            self._marquee_drag_update()

    #: Screen-space movement (logical px) before a press-on-empty-space
    #: commits to a marquee instead of resolving as a plain click.
    _MARQUEE_THRESHOLD_PX = 4.0

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
        """Draw/update the QRubberBand overlay. Best-effort: the selection math
        in _resolve_marquee_selection does not depend on this succeeding, so
        any failure here (no QRubberBand available, no viewport widget) just
        drops the visual, not the gesture itself.
        """
        try:
            from PySide.QtCore import QRect, QPoint
            rect = QRect(QPoint(int(p0[0]), int(p0[1])), QPoint(int(p1[0]), int(p1[1]))).normalized()
            band = self._marquee_band
            if band is None:
                # activeView().getWidget() doesn't exist on every FreeCAD build
                # (confirmed live: AttributeError) -- FldInputManager's own
                # cached viewport (populated by adopting the GL widget events
                # actually arrive from) is the one detection method proven to
                # work here, so try it first and fall back to getWidget() only
                # where that method happens to exist.
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
        """Resolve the deferred press-on-empty-space gesture on release.

        A drag that crossed the threshold resolves as a marquee select; one
        that never moved resolves as a plain click (clears the selection,
        unless Shift was held -- matching a Shift-click on nothing being a
        no-op rather than a toggle of nothing).
        """
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
        """Select every non-anchor point whose screen projection lands in the
        rectangle spanned by `p0`/`p1`. Replaces the selection normally;
        unions into it when `additive` (Shift held) -- marquee never toggles
        a point back out, only click does that.
        """
        from freecad.fields.core.input.view_projector import ViewProjector
        projector = getattr(self, "projector", None) or (ViewProjector(self.view) if self.view else None)
        if not projector:
            return
        x0, x1 = sorted((p0[0], p1[0]))
        y0, y1 = sorted((p0[1], p1[1]))
        anchor = self._anchor_idx()
        inside = set()
        for i, pt in enumerate(self.points):
            if pt is None or i == anchor:
                continue
            sp = projector.project_to_screen(FreeCAD.Vector(pt))
            if sp is None:
                continue
            sx, sy = sp
            if x0 <= sx <= x1 and y0 <= sy <= y1:
                inside.add(i)
        self._selected_indices = (set(self._selected_indices) | inside) if additive else inside
        self._selected_point_idx = (next(iter(self._selected_indices))
                                     if self._selected_indices else None)
        self._update_gizmo()
        self._update_handle_positions(self.points)
        if self.view:
            self.view.redraw()


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
            if obj and obj.Document:
                try:
                    # The shape may have changed since the mode was chosen, so
                    # settle the origin before Points and Placement are frozen.
                    self._rebase_to_origin()
                    field = self._get_edit_preview_field()
                    points = self._get_final_points()
                    if field is not None:
                        obj.Proxy.SdfField = field
                    if points is not None:
                        if not hasattr(obj, "Points"):
                            obj.addProperty("App::PropertyVectorList", "Points", "Sdf", "Control Points")
                        obj.Points = points
                    self._write_origin_mode(obj)
                    if self.working_plane:
                        if hasattr(self.working_plane, "getGlobalPlacement"):
                            obj.Placement = self.working_plane.getGlobalPlacement()
                        elif hasattr(self.working_plane, "Placement"):
                            obj.Placement = self.working_plane.Placement
                        else:
                            obj.Placement = self.working_plane
                    QtCore.QTimer.singleShot(0, lambda: self._commit_edit_deferred(obj))
                except Exception as e:
                    fld_logger.error(f"finish(): failed to commit edit: {e}")

            self._is_editing = False
            if getattr(self, "_gizmo_transaction", False):
                doc = getattr(obj, "Document", None) or FreeCAD.ActiveDocument
                if doc and hasattr(doc, "commitTransaction"):
                    doc.commitTransaction()
                self._gizmo_transaction = False
            self._drag_start_points = None
            self._drag_start_wp_base = None
            self._drag_start_wp_rot = None
            self._edit_pivot = None
            self._edit_last_angle = 0.0
            self._center_handle = None
            self._rot_handle = None
            self._rot_line = None
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

    def _commit_edit_deferred(self, obj):
        """Deferred recompute after editing - required for Shape assignment safety."""
        try:
            obj.touch()
            obj.Document.recompute([obj])
            self._on_committed(obj)
        except Exception as e:
            fld_logger.error(f"_commit_edit_deferred: {e}")


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

    def _compute_default_size(self):
        """Return a world-space length (~15% of viewport height) used to size the initial spawned primitive."""
        try:
            from pivy import coin
            cam = self.view.getCameraNode()
            if isinstance(cam, coin.SoOrthographicCamera):
                h = cam.height.getValue()
            else:
                import math
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

    def _update_handle_positions(self, world_pts, color=(1.0, 0.5, 0.0)):
        """Update fld_points to match the given world-space positions.
        
        Creates new FldPoint objects as needed, updates existing ones.
        """
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
    # See .claude/skills/fld_primitive_tool/SKILL.md "Task Panel Parameter Contract".
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

    def update_ui(self):
        """Push the tool's current state into the task panel (no-op without one)."""
        panel = getattr(self, "panel", None)
        if panel is None:
            return
        try:
            panel.update_ui()
        except Exception as e:
            fld_logger.debug(f"{type(self).__name__}.update_ui: {e}")

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
        for handle_attr in ("_center_handle", "_rot_handle"):
            handle = getattr(self, handle_attr, None)
            if handle is not None:
                handle.position = handle.position + delta
        if self.points:
            self._update_handle_positions(self.points)
        self._refresh_origin()
        self.update_preview()
        self.update_ui()
        if self.view:
            self.view.redraw()
        return True

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

    def set_parameters(self, params, changed=None):
        """Task panel entry point for typed dimensions.

        `changed` is the key the user actually edited; during creation only that
        key is pinned, so the mouse keeps driving the other dimensions.
        """
        if not self._apply_parameters(params):
            return False
        if not getattr(self, "_is_editing", False):
            if changed is None:
                self._locked_params.update(params)
            elif changed in params:
                self._locked_params[changed] = params[changed]
        if self.points:
            self._update_handle_positions(self.points)
        self._refresh_origin()
        self.update_preview()
        self.update_ui()
        if self.view:
            self.view.redraw()
        return True

    def _apply_param_locks(self):
        """Re-impose dimensions typed during creation. True if geometry changed."""
        if not self._locked_params or getattr(self, "_is_editing", False):
            return False
        params = self.get_parameters()
        if not params:
            return False
        merged = dict(params)
        dirty = False
        for key, val in self._locked_params.items():
            if key in merged and abs(merged[key] - val) > 1e-9:
                merged[key] = val
                dirty = True
        if not dirty:
            return False
        return bool(self._apply_parameters(merged))

    def update_preview(self):
        """Called on every mouse move by FldBase.handle_move. Updates the live mesh."""
        if getattr(self, "_terminated", False):
            return
        # In edit mode, use the edit-mode field builder which reads from self.points
        if getattr(self, "_is_editing", False):
            field = self._get_edit_preview_field()
        else:
            # _get_preview_field() rebuilds geometry from the creation state (mouse),
            # so any dimension the user typed has to be re-imposed on top of it.
            field = self._get_preview_field()
            if self._apply_param_locks():
                field = self._get_preview_field() or field
        # Panel mirrors the live geometry (position + dimensions) on every frame.
        self.update_ui()
        if field is None:
            return

        if self._preview_obj is None:
            if getattr(self, "_creating_obj", False):
                return
            self._creating_obj = True
            try:
                # Create the preview object for the first time
                # Use last part of class name without 'Creator' suffix
                name = type(self).__name__.replace("Creator", "")
                self._preview_obj = create_fld_object(name=name, shape_type="sdf")
                sdf_type = self.get_sdf_type()
                if sdf_type:
                    if not hasattr(self._preview_obj, "SdfType"):
                        self._preview_obj.addProperty("App::PropertyString", "SdfType", "Sdf", "SDF primitive type")
                    self._preview_obj.SdfType = sdf_type
                if hasattr(self._preview_obj, "Group"):
                    self._preview_obj.Group = getattr(self, "_create_group", "Additive")
            finally:
                self._creating_obj = False

        self._schedule_update(lambda: self._do_full_preview_update(field))

    def _do_full_preview_update(self, field=None):
        """Throttled update of both the mesh and the ghost visuals."""
        if field is None:
            field = (self._get_edit_preview_field()
                     if getattr(self, "_is_editing", False)
                     else self._get_preview_field())
        from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
        renderer = FldSceneVoxelRenderer.get_instance()
        renderer.begin_update_batch()
        try:
            self._apply_preview_field(field)
            self._update_ghost_visuals()
            if getattr(self, "_is_editing", False) and self._preview_obj and self._preview_obj.Document:
                obj = self._preview_obj
                # Always push recomposed boolean fields to renderer so the result updates live.
                self._update_boolean_parents_in_renderer()
                # Full FreeCAD doc recompute only on drag release (too slow to do every frame).
                if not getattr(self, "_is_dragging", False):
                    QtCore.QTimer.singleShot(0, lambda: self._recompute_boolean_parents(obj))
        finally:
            renderer.end_update_batch()
        if self.view:
            self.view.redraw()

    def _update_boolean_parents_in_renderer(self):
        """Push recomposed boolean fields directly to the renderer for real-time updates.
        Called every preview frame including during drags — avoids FreeCAD doc recompute.
        """
        if self._preview_obj is None or not self._preview_obj.Document:
            return
        try:
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
            from freecad.fields.core.sdf.sdf_boolean_compose import _recompose_boolean
            doc = self._preview_obj.Document
            renderer = FldSceneVoxelRenderer.get_instance()
            renderer.begin_update_batch()
            try:
                for obj in doc.Objects:
                    if getattr(obj, "ShapeType", None) != "sdf":
                        continue
                    inputs = getattr(obj, "BooleanInputs", None) or []
                    if self._preview_obj not in inputs:
                        continue
                    new_field = _recompose_boolean(obj)
                    if new_field is not None:
                        if hasattr(obj, "Proxy"):
                            obj.Proxy.SdfField = new_field
                        label = f"{doc.Name}.{obj.Name}"
                        renderer.update_field(label, new_field)
            finally:
                renderer.end_update_batch()
        except Exception as e:
            fld_logger.debug(f"Boolean parent renderer update error: {e}")

    def _recompute_boolean_parents(self, obj):
        """Deferred: recompute the primitive and its boolean parent dependents."""
        if getattr(self, "_terminated", False):
            return
        try:
            if obj and obj.Document:
                obj.touch()
                obj.Document.recompute()
        except Exception as e:
            fld_logger.debug(f"Boolean parent recompute error: {e}")


    def _update_ghost_visuals(self):
        """Standard implementation for primitive tools to show points/edges."""
        pass

    def _apply_preview_field(self, field):
        if self._preview_obj is None or not self._preview_obj.Document:
            return
        try:
            proxy = self._preview_obj.Proxy
            if proxy is None:
                return
            proxy.SdfField = field

            # Direct GPU update - skip FreeCAD recompute cycle
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
            label = f"{self._preview_obj.Document.Name}.{self._preview_obj.Name}"
            FldSceneVoxelRenderer.get_instance().update_field(label, field)
        except Exception as e:
            fld_logger.debug(f"PrimitiveCreatorBase preview update error: {e}")
        finally:
            self._update_pending = False

    def _finalize_object(self, name, terminate=True):
        """Commit the preview object as the final result, upgrading its mesh resolution."""
        self._rebase_to_origin()   # obj.Placement lands on the chosen origin
        field = self._get_final_field()
        points = self._get_final_points()
        # Captured here, not read inside the deferred commit: with Auto Repeat on,
        # reset_state() runs synchronously right after this and hands the drawing
        # plane back, so a later read would store points and a placement from two
        # different frames and the solid would jump.
        placement = self._get_placement()
        if field is None:
            if terminate: self.terminate()
            return
        
        # We don't set self._finished = True here if we want to repeat, 
        # because _finished prevents _do_terminate from cleaning up.
        # But we DO want to sever the preview object.
        QtCore.QTimer.singleShot(0, lambda: self.__do_commit(name, field, points, placement))
        if terminate:
            self._finished = True
            self.terminate()

    def __do_commit(self, name, field, points, placement=None):
        obj = self._preview_obj
        if obj is None or not obj.Document:
            # fallback: create fresh
            obj = create_fld_object(name=name, shape_type="sdf")

        # Rename to final name
        try:
            # We must be careful not to trigger recursive recomputes here if we are
            # already in a recompute loop.
            fld_logger.debug(f"Committing {name}: {len(points) if points else 0} points, placement={self.working_plane}")
            obj.Label = name
        except Exception as e:
            fld_logger.debug(f"__do_commit: failed to set Label on object: {e}")

        # Add points for editing
        if points is not None:
            try:
                if not hasattr(obj, "Points"):
                    obj.addProperty("App::PropertyVectorList", "Points", "Sdf", "Control Points")
                obj.Points = points
            except Exception as e:
                fld_logger.error(f"__do_commit: failed to set Points on '{getattr(obj, 'Label', '?')}': {e}")

        # Store SdfType for load-time field reconstruction
        sdf_type = self.get_sdf_type()
        if sdf_type:
            try:
                if not hasattr(obj, "SdfType"):
                    obj.addProperty("App::PropertyString", "SdfType", "Sdf", "SDF primitive type")
                obj.SdfType = sdf_type
            except Exception as e:
                fld_logger.debug(f"__do_commit: failed to set SdfType on object: {e}")

        self._write_origin_mode(obj)

        # Store source curve link for curve-based types
        source_curve = self._get_source_curve_link()
        if source_curve is not None:
            try:
                if not hasattr(obj, "SourceCurveLink"):
                    obj.addProperty("App::PropertyLink", "SourceCurveLink", "Sdf", "Source curve")
                obj.SourceCurveLink = source_curve
            except Exception as e:
                fld_logger.error(f"__do_commit: failed to set SourceCurveLink: {e}")

        # Store source surface link for surface-based extrusions
        if hasattr(self, "_surface_obj") and self._surface_obj is not None:
            try:
                if not hasattr(obj, "SourceSurfaceLink"):
                    obj.addProperty("App::PropertyLink", "SourceSurfaceLink", "Sdf", "Source surface")
                obj.SourceSurfaceLink = self._surface_obj
            except Exception as e:
                fld_logger.error(f"__do_commit: failed to set SourceSurfaceLink: {e}")

        proxy = obj.Proxy
        proxy.SdfField = field
        
        # The frame the points above were expressed in — see _finalize_object.
        if placement is None:
            placement = self._get_placement()
        if placement is not None:
            obj.Placement = placement
        
        obj.touch()
        obj.Document.recompute([obj])
        # create_fld_object() no longer sets this -- it needs a computed Shape,
        # and commit is the first point on this path that has one (IF-016).
        apply_shaded_display_mode(obj)
        self._on_committed(obj)
        # Print accumulated timer summary now that the tool is accepted
        primitive_name = type(self).__name__.replace("Creator", "")
        mesh_timer.summary(f"{primitive_name} preview ({_PREVIEW_CELL_SIZE}mm)")
        self._preview_obj = None  # Severed; the object is now the user's

    def _commit_and_enter_edit(self, name):
        """Commit creation at full resolution, then immediately enter edit mode on the result."""
        self._rebase_to_origin()   # obj.Placement lands on the chosen origin
        field = self._get_final_field()
        points = self._get_final_points()
        placement = self._get_placement()   # captured with the points, not re-read later
        if field is None:
            self.terminate()
            return

        # Pre-enter edit state so handle_move doesn't clobber tool vars during async delay
        self._is_editing = True
        self.state = ToolState.IDLE
        self._locked_params.clear()   # typed dims are baked into the committed geometry

        QtCore.QTimer.singleShot(0, lambda: self._do_commit_and_edit(name, field, points, placement))

    def _do_commit_and_edit(self, name, field, points, placement=None):
        """Async: commit the object at full resolution then switch to edit mode on it."""
        if getattr(self, "_terminated", False):
            return

        obj = self._preview_obj
        if obj is None or not obj.Document:
            obj = create_fld_object(name=name, shape_type="sdf")

        try:
            obj.Label = name
        except Exception as e:
            fld_logger.debug(f"_do_commit_and_edit: failed to set Label on object: {e}")

        if points is not None:
            try:
                if not hasattr(obj, "Points"):
                    obj.addProperty("App::PropertyVectorList", "Points", "Sdf", "Control Points")
                obj.Points = points
            except Exception as e:
                fld_logger.error(f"_do_commit_and_edit: failed to set Points on '{getattr(obj, 'Label', '?')}': {e}")

        sdf_type = self.get_sdf_type()
        if sdf_type:
            try:
                if not hasattr(obj, "SdfType"):
                    obj.addProperty("App::PropertyString", "SdfType", "Sdf", "SDF primitive type")
                obj.SdfType = sdf_type
            except Exception as e:
                fld_logger.debug(f"_do_commit_and_edit: failed to set SdfType on object: {e}")

        self._write_origin_mode(obj)

        source_curve = self._get_source_curve_link()
        if source_curve is not None:
            try:
                if not hasattr(obj, "SourceCurveLink"):
                    obj.addProperty("App::PropertyLink", "SourceCurveLink", "Sdf", "Source curve")
                obj.SourceCurveLink = source_curve
            except Exception as e:
                fld_logger.error(f"_do_commit_and_edit: failed to set SourceCurveLink: {e}")

        # Store source surface link for surface-based extrusions
        if hasattr(self, "_surface_obj") and self._surface_obj is not None:
            try:
                if not hasattr(obj, "SourceSurfaceLink"):
                    obj.addProperty("App::PropertyLink", "SourceSurfaceLink", "Sdf", "Source surface")
                obj.SourceSurfaceLink = self._surface_obj
            except Exception as e:
                fld_logger.error(f"_do_commit_and_edit: failed to set SourceSurfaceLink: {e}")

        primitive_name = type(self).__name__.replace("Creator", "")
        obj.Proxy.SdfField = field

        # Set placement BEFORE calling edit_object so edit_object reads the correct
        # placement and reconstructs world-space handle positions from local obj.Points.
        # The frame the points were expressed in — see _commit_and_enter_edit.
        if placement is None:
            placement = self._get_placement()
        if placement is not None:
            obj.Placement = placement

        obj.touch()
        obj.Document.recompute([obj])
        apply_shaded_display_mode(obj)
        mesh_timer.summary(f"{primitive_name} → edit mode")

        # Clear creation visuals before entering edit mode
        for fld_pt in self.fld_points:
            fld_pt.undraw()
        if getattr(self, "_center_handle", None):
            self._center_handle.undraw()
            self._center_handle = None
        if getattr(self, "_rot_handle", None):
            self._rot_handle.undraw()
            self._rot_handle = None
        if getattr(self, "_rot_line", None):
            self._rot_line.undraw()
            self._rot_line = None
        self.fld_points.clear()
        if self.fld_line_set:
            self.fld_line_set.undraw()
            self.fld_line_set = None

        # Enter edit mode on the committed object.
        # Mark that this edit session began from creation so cancel() knows to delete it.
        self._came_from_creation = True
        self.edit_object(obj)
        FreeCADGui.updateGui()
        if self.view:
            self.view.redraw()

    def handle_keyboard(self, event_dict):
        key = event_dict.get("Key")
        key_text = str(event_dict.get("Text", "None")).upper()
        text = str(event_dict.get("Text", "")).lower()

        if self._is_editing and isinstance(self._dragging_idx, str) and self._dragging_idx.startswith('gizmo_'):
            axis = self._dragging_idx[len('gizmo_'):]
            is_rot = axis.startswith('rot_')
            ax_name = axis[4:].upper() if is_rot else axis.upper()

            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                val = self.numeric_value()
                if val is not None:
                    if is_rot:
                        ax_vec = self._rot_ring_axis or self._gizmo._axes[axis[4:]]
                        pivot = self._edit_pivot
                        rot = FreeCAD.Rotation(ax_vec, val)
                        self.points = [pivot + rot.multVec(p - pivot) for p in self._drag_start_points]
                        if self.working_plane:
                            if self._drag_start_wp_base is not None:
                                self.working_plane.Base = pivot + rot.multVec(self._drag_start_wp_base - pivot)
                            if self._drag_start_wp_rot is not None:
                                self.working_plane.Rotation = rot.multiply(self._drag_start_wp_rot)
                    else:
                        ax_vec = self._gizmo._axes[axis]
                        delta = FreeCAD.Vector(ax_vec) * val
                        self.points = [p + delta for p in self._drag_start_points]
                        if self.working_plane and self._drag_start_wp_base is not None:
                            self.working_plane.Base = self._drag_start_wp_base + delta

                    self._update_gizmo()
                    self._update_handle_positions(self.points)
                    self.update_ui()
                    self.update_preview()
                    if self.view:
                        self.view.redraw()

                self._stop_drag_timer()
                if getattr(self, "_gizmo_transaction", False):
                    doc = getattr(self._preview_obj, "Document", None) or FreeCAD.ActiveDocument
                    if doc and hasattr(doc, "commitTransaction"):
                        doc.commitTransaction()
                    self._gizmo_transaction = False
                self._dragging_idx = None
                self._drag_start_points = None
                self._drag_start_wp_base = None
                self._drag_start_wp_rot = None
                self._input_buffer = ""
                self._modal_transform = None
                self._axis_lock = None
                self._update_modal_hud()
                if self._snap_indicator:
                    self._snap_indicator.hide()
                return True

            if key == QtCore.Qt.Key_Backspace:
                self._input_buffer = self._input_buffer[:-1]
                self._modal_transform = "ROTATE" if is_rot else "GRAB"
                self._axis_lock = (ax_name,)
                self._update_modal_hud()
                return True
            if text == '-':
                if self._input_buffer.startswith('-'):
                    self._input_buffer = self._input_buffer[1:]
                else:
                    self._input_buffer = '-' + self._input_buffer
                self._modal_transform = "ROTATE" if is_rot else "GRAB"
                self._axis_lock = (ax_name,)
                self._update_modal_hud()
                return True
            if text in "0123456789" or (text == '.' and '.' not in self._input_buffer):
                self._input_buffer += text
                self._modal_transform = "ROTATE" if is_rot else "GRAB"
                self._axis_lock = (ax_name,)
                self._update_modal_hud()
                return True

        if self._is_editing and key == QtCore.Qt.Key_T:
            self._gizmo_space = 'local' if self._gizmo_space == 'world' else 'world'
            self._update_gizmo()
            if self.view:
                self.view.redraw()
            return True

        if (self._is_editing and self._dragging_idx is None
                and key == QtCore.Qt.Key_A
                and bool(event_dict.get("Modifiers", QtCore.Qt.NoModifier) & QtCore.Qt.ShiftModifier)):
            self._select_all()
            return True

        return super().handle_keyboard(event_dict)

    def _select_all(self):
        """Select every selectable point (all valid points but the anchor)."""
        anchor = self._anchor_idx()
        self._selected_indices = {i for i, pt in enumerate(self.points)
                                   if pt is not None and i != anchor}
        self._selected_point_idx = (next(iter(self._selected_indices))
                                     if self._selected_indices else None)
        self._update_gizmo()
        self._update_handle_positions(self.points)
        if self.view:
            self.view.redraw()

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
            ("Projection Surface: None",           lambda *args: self._set_edit_snap('off'),           self._edit_snap_mode == 'off'),
            ("Projection Surface: Workplane + SDF", lambda *args: self._set_edit_snap('workplane_sdf'), self._edit_snap_mode == 'workplane_sdf'),
            ("Projection Surface: All Geometry",    lambda *args: self._set_edit_snap('all'),           self._edit_snap_mode == 'all'),
            None,  # separator
        ] + self.get_snap_pref_menu()
        return items

    def _set_gizmo_space(self, space):
        self._gizmo_space = space
        self._update_gizmo()
        if self.view:
            self.view.redraw()

    def _set_edit_snap(self, mode):
        self._edit_snap_mode = mode

    def reset_state(self):
        """Override to clear internal primitive state (points, visuals)."""
        super().reset_state()
        # Return to first creation step (or IDLE if no steps defined)
        self.state = self.CREATION_STEPS[0] if self.CREATION_STEPS else ToolState.IDLE
        self._anchor_pt = None
        self._selected_point_idx = None
        self._selected_indices = set()
        self._current_point_idx = None
        self._hide_marquee_rect()
        self._marquee_press_pos = None
        self._marquee_active = False
        self._origin_mode = self.DEFAULT_ORIGIN_MODE
        # Auto Repeat keeps this instance alive across commits, so hand the
        # user's drawing plane back before the next primitive starts.
        if self._drawing_plane is not None:
            self.working_plane = self._drawing_plane
            self._drawing_plane = None
        self._create_group = "Additive"
        self._locked_params.clear()
        self.points = []
        for fld_pt in self.fld_points:
            fld_pt.undraw()
        if getattr(self, "_center_handle", None):
            self._center_handle.undraw()
            self._center_handle = None
        if getattr(self, "_rot_handle", None):
            self._rot_handle.undraw()
            self._rot_handle = None
        if getattr(self, "_rot_line", None):
            self._rot_line.undraw()
            self._rot_line = None
        self.fld_points.clear()
        if self.fld_line_set:
            self.fld_line_set.undraw()
            self.fld_line_set = None
        # Clear constraint state and visual
        self._constraint_axis = None
        self._constraint_plane = None
        self._constraint_space = 'global'
        self._drag_constraint_base = None
        self._edit_snap_mode = 'off'
        self._drag_return_state = ToolState.IDLE
        self._clear_constraint_visual()
        if self._gizmo:
            self._gizmo.undraw()
            self._gizmo = None
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
            self._hide_marquee_rect()
            for fld_pt in self.fld_points:
                try:
                    fld_pt.undraw()
                except Exception as e:
                    fld_logger.debug(f"PrimitiveCreatorBase._do_terminate: Failed to undraw point: {e}")
            if getattr(self, "_center_handle", None):
                try:
                    self._center_handle.undraw()
                except Exception as e:
                    fld_logger.debug(f"PrimitiveCreatorBase._do_terminate: Failed to undraw center handle: {e}")
                self._center_handle = None
            if getattr(self, "_rot_handle", None):
                try:
                    self._rot_handle.undraw()
                except Exception as e:
                    fld_logger.debug(f"PrimitiveCreatorBase._do_terminate: Failed to undraw rotation handle: {e}")
                self._rot_handle = None
            if getattr(self, "_rot_line", None):
                try:
                    self._rot_line.undraw()
                except Exception as e:
                    fld_logger.debug(f"PrimitiveCreatorBase._do_terminate: Failed to undraw rotation line: {e}")
                self._rot_line = None
            self.fld_points.clear()
            if self.fld_line_set:
                try:
                    self.fld_line_set.undraw()
                except Exception as e:
                    fld_logger.debug(f"PrimitiveCreatorBase._do_terminate: Failed to undraw line set: {e}")
                self.fld_line_set = None
            try:
                self._clear_constraint_visual()
            except Exception as e:
                fld_logger.debug(f"PrimitiveCreatorBase._do_terminate: Failed to clear constraint visual: {e}")
            if getattr(self, "_gizmo", None):
                try:
                    self._gizmo.undraw()
                except Exception as e:
                    fld_logger.debug(f"PrimitiveCreatorBase._do_terminate: Failed to undraw gizmo: {e}")
                self._gizmo = None
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


