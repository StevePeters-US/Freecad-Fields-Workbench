# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Base classes for Fields interactive tools.

Event Ownership Contract (Single-Owner Qt-Native)
================================================
- Qt filter (FldInputManager): owns all events — state tracking, global hotkeys,
  viewport detection, and dispatch to the active tool via on_mouse_press/move/etc.
- Tool methods (on_button1_down, handle_move, handle_keyboard, …): ALL tool logic.
- Modifiers: always read from FldInputManager (is_shift_down, is_ctrl_down, etc.).
- Drag: QTimer polls FldInputManager._last_qt_pos.
"""

import FreeCAD
import FreeCADGui
from PySide import QtCore
import math

from freecad.fields.core import fld_logger
from freecad.fields.core.input.work_plane import WorkPlaneManager
from freecad.fields.core.input.view_projector import ViewProjector
from freecad.fields.core.input.input_manager import FldInputManager

# ─────────────────────────────────────────────────────────────────────────────
# DragTimerMixin (re-exported from freecad.fields.tools.tool_mixins)
# ─────────────────────────────────────────────────────────────────────────────

from freecad.fields.tools.tool_mixins import (  # noqa: F401
    DragTimerMixin, AxisConstraintMixin, ControlCageDrawMixin, SnapPreferencesMixin,
)


# ─────────────────────────────────────────────────────────────────────────────
# PrimitiveCreatorBase
# ─────────────────────────────────────────────────────────────────────────────

from enum import IntEnum

# Shown in the status bar for the whole of edit mode. These keys exist nowhere in
# the UI -- there is no menu item or toolbar button for a modal transform -- so
# this line is their only discoverable surface. Keep it in step with
# `.claude/skills/fld_input_map/SKILL.md`.
EDIT_MODE_HINT = ("Edit mode  |  G move \u00b7 R rotate \u00b7 S scale  \u00b7  "
                  "X/Y/Z axis (Shift = plane)  \u00b7  type a number  \u00b7  "
                  "Enter/LMB apply \u00b7 Esc/RMB cancel \u00b7 Tab exit")

PRIMITIVE_SCALE_PROPS = ("Radius", "Radius1", "Radius2", "Length", "Width", "Height", "Thickness")


class ToolState(IntEnum):
    IDLE = 0
    ACTIVE = 1
    DRAGGING = 2
    FINALIZED = 3
    EDIT_MODE = 4
    PICK_RADIUS = 5  # Cylinder/Sphere specific (legacy)
    PICK_HEIGHT = 6  # Cylinder specific (legacy)
    PLACE_ANCHOR = 7   # Hover-snap to place first point, click to accept
    DRAG_XY      = 8   # Mouse moves on workplane XY; click to accept 2D profile
    DRAG_Z       = 9   # Mouse moves along WP normal; click to accept height
    CUSTOM_1     = 10  # Primitive-specific param 1 (e.g. bevel radius)
    CUSTOM_2     = 11
    CUSTOM_3     = 12


class FldBase(DragTimerMixin, AxisConstraintMixin, ControlCageDrawMixin, SnapPreferencesMixin):
    # Whether G/R/S do anything in this tool. A tool that sets this False must say
    # so in its status message -- a modal that silently does nothing is worse than
    # no modal at all.
    SUPPORTS_MODAL = True
    place_on_geometry = True
    _last_btn3_time = 0.0 # Instance variable per tool
    autorepeat = False

    def __init__(self):
        from freecad.fields.core.input.fld_tool_manager import FldToolManager
        tool_mgr = FldToolManager.get_instance()
        active_tool = tool_mgr.get_active_tool()
        if active_tool and hasattr(active_tool, 'terminate'):
            try:
                active_tool.terminate()
            except Exception as e:
                fld_logger.debug(f"FldBase.__init__: Failed to terminate previous tool: {e}")
                
        self._terminated = False
        self._is_dragging = False
        self.view = getattr(FreeCADGui, "activeView", lambda: None)()
        self.doc = getattr(FreeCAD, "ActiveDocument", None)
        
        if not self.view and getattr(FreeCADGui, "ActiveDocument", None):
            try:
                self.view = getattr(FreeCADGui.ActiveDocument, "ActiveView", None)
            except Exception as e:
                fld_logger.debug(f"FldBase.__init__: Failed to get view from document: {e}")
        
        if not self.view:
            fld_logger.debug("FldBase: Could not find active view (headless test environment)")
            self.projector = None
        else:
            self.projector = ViewProjector(self.view)

        fld_logger.debug(f"{self.__class__.__name__} initialized")
        tool_mgr.set_active_tool(self)

        self.start_point   = None
        self.current_point = None
        self.center        = None # Legacy, use start_point
        self.state         = ToolState.IDLE
        
        self.working_plane = None
        self._working_plane_is_fallback = True  # Default to True until a real WP is hit
        self.snap_face = None
        self.snap_enabled = False
        self.snap_type = "Workplane Grid"

        # Shared UX state
        self.height = 0.0
        self.is_cutter = False
        self.panel = None
        self._dialog_open = False
        self._finish_scheduled = False

        # Shared constraint state
        self.active_axis = None
        self.locked_length = None
        self.locked_width = None
        self.locked_height = None

        self._cursor_active = False
        self._last_btn3_time = 0.0
        self._update_pending = False
        self._pending_callback = None

        self._is_editing = False

        self._modal_transform = None  # None, "GRAB", "ROTATE", "SCALE"
        self._axis_lock = None         # None, ("X",), ("Y",), ("Z",), or plane e.g. ("X","Y")
        #: Space the *current* single-axis lock resolves in -- 'global' | 'local' | None.
        #: Blender-style 3-press cycle on X/Y/Z (see on_key_press): 1st press locks at
        #: _default_axis_lock_space, 2nd press flips this to the other space without
        #: touching the default, 3rd press clears both back to no lock.
        self._axis_lock_space = None
        self._default_axis_lock_space = 'global'
        self._input_buffer = ""        # Character buffer for numeric entry
        self._transform_origin = None  # Vector starting coordinate
        self._modal_snapshot = None    # Snapshot of object state for modal cancel
        self._modal_start_mouse = None # Mouse position when modal started
        self._snap_guide = None        # FldSnapGuide visual for modal transforms
        self._snap_guide_root = None   # Coin3D root node if not using points_root

        # Edit mode axis/plane constraints
        self._constraint_axis = None
        self._constraint_plane = None
        self._constraint_space = 'global'
        self._constraint_last_key = None
        self._constraint_line = None
        self._drag_constraint_base = None

        # Unified object selection detection for edit mode
        # Defer calling to ensure subclass __init__ is finished
        from PySide import QtCore
        QtCore.QTimer.singleShot(0, self._post_init)
        
        # Explicit highlight
        self.set_icon_active(True)

    def get_command_id(self):
        """Returns the FreeCAD command ID associated with this tool (e.g. 'Fields_CreateBox').
        Subclasses should override this to enable icon highlighting and other UI features.
        """
        return None

    def set_icon_active(self, is_active):
        cmd = self.get_command_id()
        if not cmd: return
        import FreeCADGui
        from PySide import QtGui, QtWidgets, QtCore
        mw_func = getattr(FreeCADGui, "getMainWindow", None)
        if not mw_func: return
        mw = mw_func()
        if not mw: return
        # QAction stays on QtGui: it lives in QtWidgets under Qt5 but moved TO
        # QtGui in Qt6 — the opposite direction from the widget classes. The
        # PySide shim resolves QtGui.QAction on both, QtWidgets.QAction only Qt5.
        action = mw.findChild(QtGui.QAction, cmd)
        if not action: return

        if not hasattr(FldBase, "_original_icons"):
            FldBase._original_icons = {}

        if is_active:
            if cmd not in FldBase._original_icons:
                FldBase._original_icons[cmd] = action.icon()
            orig = FldBase._original_icons[cmd]
            sizes = orig.availableSizes()
            size = sizes[0] if sizes else QtCore.QSize(32, 32)
            pix = orig.pixmap(size)
            
            painter = QtGui.QPainter(pix)
            painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceAtop)
            painter.fillRect(pix.rect(), QtGui.QColor(255, 170, 0, 100)) # Orange tint
            painter.end()
            action.setIcon(QtGui.QIcon(pix))
        else:
            if cmd in FldBase._original_icons:
                action.setIcon(FldBase._original_icons[cmd])

    def _post_init(self):
        """Final initialization after tool is fully constructed."""
        if getattr(self, "_terminated", False):
            return
        self._detect_selected_object()
        FreeCADGui.updateGui()

    def _set_cursor(self, cursor):
        """Set override cursor, tracking state."""
        if not getattr(self, "_cursor_active", False):
            from PySide import QtGui, QtWidgets
            QtWidgets.QApplication.setOverrideCursor(cursor)
            self._cursor_active = True

    def _restore_cursor(self):
        """Restore cursor if we set it."""
        if getattr(self, "_cursor_active", False):
            from PySide import QtGui, QtWidgets
            QtWidgets.QApplication.restoreOverrideCursor()
            self._cursor_active = False

    def _hit_test_perp(self, ray_p, ray_d, points, tolerance=None):
        """
        Returns (best_idx, best_perp_dist) for list of FreeCAD.Vector points.
        Uses perpendicular distance (depth-independent, correct for ortho cameras).
        tolerance defaults to _compute_handle_radius() if None.

        NOTE: Do NOT filter by proj < 0. In orthographic mode, get_ray() returns
        the focal-plane origin, not the camera position, so scene points can have
        negative or near-zero projection and must still be hit-tested.
        """
        if not ray_p or not ray_d or not points:
            return None, float('inf')
        
        if tolerance is None:
            tolerance = self._compute_handle_radius()
            
        best_idx = None
        best_perp = float('inf')
        best_depth = float('inf')
        
        for i, pt in enumerate(points):
            if pt is None: continue
            v = pt - ray_p
            proj = v.dot(ray_d)
            
            # Perpendicular distance to ray — no proj < 0 guard (ortho camera)
            perp = (ray_p + ray_d * proj - pt).Length
            if perp < tolerance:
                # Z-test: candidate nearest to camera along ray_d wins.
                # Tie-break by perpendicular distance to ray centerline.
                if proj < best_depth - 1e-4 or (abs(proj - best_depth) <= 1e-4 and perp < best_perp):
                    best_depth = proj
                    best_perp = perp
                    best_idx = i
                
        return best_idx, best_perp


    def _active_working_plane(self):
        """The workplane to project empty-space clicks onto, or None.

        Enforces the Workplane Fallback Rule (AGENTS.md): a plane the tool merely
        pre-loaded because it was visible (`_working_plane_is_fallback`) is not a
        plane the user chose, so while we are still deciding where the object
        goes -- IDLE or PLACE_ANCHOR -- empty space must fall through to the
        camera-facing plane instead. That is what lets get_mouse_plane_pt
        recalculate the best transient snap/alignment rather than reusing a
        previous face snap. Once the shape is being drawn the plane is locked
        deliberately and this returns it regardless.

        ViewProjector.get_mouse_plane_pt does NOT do this itself; it projects
        onto whatever `working_plane` it is handed. Passing it a plane this
        method rejected is the bug.
        """
        plane = getattr(self, "working_plane", None)
        if plane is None:
            return None
        state = getattr(self, "state", ToolState.IDLE)
        if state in (ToolState.IDLE, ToolState.PLACE_ANCHOR) and \
           getattr(self, "_working_plane_is_fallback", True):
            return None
        return plane

    def _resolve_wp_click(self, event_dict, skip_objects=None, debug=False):
        """
        Call get_mouse_plane_pt, update self.working_plane from wp_hit
        if in state 0 (Idle) or if not already set.
        """
        # Merge caller's skip list with own preview/active objects
        all_skip = list(skip_objects) if skip_objects else []
        for attr in ("_preview_obj", "_active_obj"):
            obj = getattr(self, attr, None)
            if obj is not None and obj not in all_skip:
                all_skip.append(obj)

        active_plane = self._active_working_plane()

        # Call projector directly to preserve the (pt, wp_hit) tuple.
        # FldBase.get_mouse_plane_pt strips wp_hit before returning, so wp_hit
        # would always be None and working_plane would never update on click.
        result = self.projector.get_mouse_plane_pt(
            event_dict,
            place_on_geometry=getattr(self, "place_on_geometry", False),
            working_plane=active_plane,
            skip_objects=all_skip or None,
            debug=debug
        )
        if isinstance(result, tuple) and len(result) == 3:
            pos, wp_hit, desc = result
            self._last_hit_desc = desc
        elif isinstance(result, tuple) and len(result) == 2:
            pos, wp_hit = result
            self._last_hit_desc = "Unknown"
        else:
            pos, wp_hit = result, None
            self._last_hit_desc = "None"
            
        if wp_hit is not None:
            # Update plane if we are in the initial state (IDLE or PLACE_ANCHOR)
            # where we want to snap to whatever surface is under the first click.
            # Once drawing has started (later stages), we lock the plane.
            cur_state = getattr(self, "state", ToolState.IDLE)
            if cur_state not in (ToolState.IDLE, ToolState.PLACE_ANCHOR):
                return pos

            is_real_wp = False
            if hasattr(wp_hit, "Proxy") and getattr(wp_hit.Proxy, "is_fld_workplane", False):
                is_real_wp = True
            
            if hasattr(wp_hit, "getGlobalPlacement"):
                self.working_plane = wp_hit.getGlobalPlacement()
            elif hasattr(wp_hit, "Placement"):
                self.working_plane = wp_hit.Placement
            else:
                # Assume it's already a FreeCAD.Placement or None
                self.working_plane = wp_hit
            
            self._working_plane_is_fallback = not is_real_wp
            
        return pos

    def _schedule_update(self, callback, interval_ms=None):
        """Throttled single-shot update. Always uses the LATEST callback."""
        self._pending_callback = callback  # Always overwrite with latest
        if getattr(self, "_update_pending", False):
            return  # Timer already running, it will pick up _pending_callback
        if interval_ms is None:
            from freecad.fields.core.objects.fld_object import get_interactive_throttle_interval
            interval_ms = int(get_interactive_throttle_interval() * 1000)
        self._update_pending = True
        QtCore.QTimer.singleShot(interval_ms, self._fire_pending_update)

    def _fire_pending_update(self):
        """Fire the most recently scheduled callback."""
        self._update_pending = False
        cb = getattr(self, "_pending_callback", None)
        if cb:
            cb()

    def _on_committed(self, obj):
        """Called after a tool successfully commits its object. Override to customize."""
        if obj:
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(obj.Document.Name, obj.Name)

    def _detect_selected_object(self):
        """Checks if a compatible object is selected and enters edit mode."""
        if getattr(self, "_is_editing", False):
            return
        handled_types = self.get_handled_types()
        # fld_logger.debug(f"{self.__class__.__name__} handled_types: {handled_types}")
        
        # Always allow WorkPlane selection even if not in handled_types 
        # (for tools that need a base plane but don't edit it).
        self._detect_selected_workplane()
        
        if not handled_types:
            FreeCADGui.updateGui()
            return
            
        try:
            selection = FreeCADGui.Selection.getSelection()
            fld_logger.debug(f"{self.__class__.__name__} selection: {[o.Label for o in selection]}")
            if not selection:
                FreeCADGui.updateGui()
                return
                
            for obj in selection:
                # 1. Check ShapeType property (robustly)
                st = None
                if hasattr(obj, "ShapeType"):
                    st = str(obj.ShapeType)
                elif hasattr(obj, "Proxy") and hasattr(obj.Proxy, "ShapeType"):
                    st = str(obj.Proxy.ShapeType)
                fld_logger.debug(f"Checking {obj.Label}: ShapeType='{st}' (handled: {handled_types})")
                
                if st and st in handled_types:
                    if not self._is_compatible_object(obj):
                        continue
                    fld_logger.debug(f"{self.__class__.__name__}: Detected compatible ShapeType '{st}'. Entering edit mode.")
                    self._is_editing = True
                    self.edit_object(obj)
                    self._update_modal_hud()
                    FreeCADGui.updateGui()
                    return

                # 2. Check Proxy class name
                proxy = getattr(obj, "Proxy", None)
                proxy_name = proxy.__class__.__name__ if proxy else None
                if proxy_name in handled_types:
                    if not self._is_compatible_object(obj):
                        continue
                    self._is_editing = True
                    self.edit_object(obj)
                    self._update_modal_hud()
                    FreeCADGui.updateGui()
                    return
            
            FreeCADGui.updateGui()
                    
        except Exception as e:
            fld_logger.debug(f"Error detecting selected object: {e}")
            FreeCADGui.updateGui()

    def get_handled_types(self):
        """Returns a list of ShapeType or Proxy class names handled by this tool."""
        return []

    def _is_compatible_object(self, obj):
        """Extra compatibility check run after a ShapeType/Proxy-class match in
        _detect_selected_object, before committing to edit mode. Override when a
        tool shares a generic ShapeType/Proxy with sibling tools that handle a
        different sub-kind (e.g. SDF primitives all use ShapeType == "sdf" but
        have distinct SdfField subclasses) to avoid editing a mismatched object.
        """
        return True

    def edit_object(self, obj):
        """Load an existing object into the tool for editing. Override in subclasses."""
        self._is_editing = True

    def _detect_selected_workplane(self):
        """Checks if a Fields_WorkPlane is selected and sets it as the active working plane."""
        try:
            selection = FreeCADGui.Selection.getSelection()
            if not selection:
                return
                
            for obj in selection:
                is_wp = False
                proxy_name = "None"
                if hasattr(obj, "Proxy") and obj.Proxy:
                    is_wp = getattr(obj.Proxy, "is_fld_workplane", False)
                    if is_wp:
                        is_wp = True
                
                # 2. Check for specific properties if proxy check is brittle
                if not is_wp and hasattr(obj, "Proxy") and hasattr(obj.Proxy, "execute") and hasattr(obj, "Length") and hasattr(obj, "Width"):
                    # This looks like one of our workplanes
                    is_wp = True
                
                if is_wp:
                    # Use getGlobalPlacement to handle nested objects
                    if hasattr(obj, "getGlobalPlacement"):
                        self.working_plane = obj.getGlobalPlacement()
                    else:
                        self.working_plane = obj.Placement
                    self._working_plane_is_fallback = False
                    fld_logger.info(f"Using selected workplane: {obj.Label}")
                    break
        except Exception as e:
            fld_logger.debug(f"Error detecting selected workplane: {e}")

    def terminate(self):
        """Standard entry point for termination. Sets guard and schedules async cleanup."""
        if getattr(self, "_terminated", False):
            return
        self._terminated = True

        # Clear the active tool synchronously to prevent incoming events from reaching
        # the terminating tool in the current event loop cycle.
        from freecad.fields.core.input.fld_tool_manager import FldToolManager
        tool_mgr = FldToolManager.get_instance()
        if tool_mgr.get_active_tool() is self:
            tool_mgr.set_active_tool(None)

        from PySide import QtCore
        QtCore.QTimer.singleShot(0, self._do_terminate)

    def _do_terminate(self):
        """
        Standard cleanup for all Fields tools.
        Call chain: Subclass cleanup -> super()._do_terminate()
        """
        from freecad.fields.core.input.fld_tool_manager import FldToolManager
        tool_mgr = FldToolManager.get_instance()
        if tool_mgr.get_active_tool() is self:
            tool_mgr.set_active_tool(None)
            
        self.set_icon_active(False)
        
        if hasattr(self, "_stop_drag_timer"):
            self._stop_drag_timer()
            
        self._restore_cursor()
        
        self._finish_scheduled = False
        self._terminated = True
        try:
            # Close task panel if open
            if getattr(self, "_dialog_open", False):
                FreeCADGui.Control.closeDialog()
                self._dialog_open = False

            try:
                self._clear_constraint_visual()
            except Exception as e:
                fld_logger.debug(f"FldBase._do_terminate: Failed to clear constraint visual: {e}")
            
            # Clean up active/preview objects if not finished
            if not getattr(self, "_finished", False):
                # Robustly collect objects to remove.
                # Use a set to avoid clearing the same object twice.
                objs_to_clear = []
                
                # Check known attributes
                for attr_name in ["_active_obj", "_preview_obj", "_preview_cursor"]:
                    obj = getattr(self, attr_name, None)
                    if obj is not None and obj not in [o for name, o in objs_to_clear]:
                        objs_to_clear.append((attr_name, obj))
                
                # Check generic temp object list if tool uses it
                if hasattr(self, "_temp_objs") and self._temp_objs:
                    for obj in self._temp_objs:
                        if obj is not None and obj not in [o for name, o in objs_to_clear]:
                            objs_to_clear.append((None, obj))

                for attr_name, obj in objs_to_clear:
                    try:
                        # Skip deleting the object if we are editing it!
                        is_active = (obj == getattr(self, "_active_obj", None))
                        is_preview = (obj == getattr(self, "_preview_obj", None))
                        if self._is_editing and (is_active or is_preview):
                            fld_logger.debug(f"FldBase._do_terminate: Skipping deletion of edited object {obj.Label}")
                            continue

                        # Use the object's own document if available, fallback to tool's doc
                        obj_doc = getattr(obj, "Document", None) or self.doc or FreeCAD.ActiveDocument
                        if obj_doc and hasattr(obj, "Name") and obj_doc.getObject(obj.Name):
                            obj_name = obj.Name
                            doc_name = obj_doc.Name
                            
                            fld_logger.debug(f"FldBase._do_terminate: Removing unfinished object {obj_name} from doc {doc_name}")
                            
                            # Explicitly unregister from scene renderer to prevent ghosts
                            try:
                                from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                                sr = FldSceneVoxelRenderer.get_instance()
                                sr.unregister_field(f"{doc_name}.{obj_name}")
                            except Exception as e:
                                fld_logger.debug(f"FldBase._do_terminate: Failed to unregister field: {e}")

                            obj_doc.removeObject(obj_name)
                            obj_doc.recompute()
                    except Exception as e:
                        fld_logger.debug(f"FldBase._do_terminate: Failed to remove object {getattr(obj, 'Name', 'unknown')}: {e}")
                    
                    # Clear the reference on the tool
                    if attr_name:
                        setattr(self, attr_name, None)

            if self.view:
                # Redraw active view to ensure renderer is updated
                try:
                    active_view = FreeCADGui.ActiveDocument.ActiveView
                    if active_view:
                        active_view.redraw()
                except Exception as e:
                    fld_logger.debug(f"FldBase._do_terminate: Failed to redraw view: {e}")
                        
            # Final prune of orphaned fields
            try:
                from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                FldSceneVoxelRenderer.get_instance().gc_fields()
            except Exception as e:
                fld_logger.debug(f"FldBase._do_terminate: Failed to gc fields: {e}")

            if getattr(self, "_snap_guide", None):
                try:
                    self._snap_guide.undraw()
                except Exception as e:
                    fld_logger.debug(f"FldBase._do_terminate: Failed to undraw snap guide: {e}")
                self._snap_guide = None
            if getattr(self, "_snap_guide_root", None):
                try:
                    if self.view and hasattr(self.view, "getSceneGraph") and self.view.getSceneGraph():
                        self.view.getSceneGraph().removeChild(self._snap_guide_root)
                except Exception as e:
                    fld_logger.debug(f"FldBase._do_terminate: Failed to remove snap guide root: {e}")
                self._snap_guide_root = None

            FreeCADGui.updateGui()

        except Exception as e:
            fld_logger.debug(f"FldBase._do_terminate: Cleanup failed: {e}")

    def get_constrained_drag_point(self, event_dict):
        base_pt = getattr(self, "_drag_constraint_base", None)
        axis_vec, plane_normal = self._get_constraint_vectors()
        if base_pt is not None and axis_vec:
            return FldInputManager.get_instance().get_axis_point(
                self.view, base_pt, axis_vec, event_dict)
        elif base_pt is not None and plane_normal:
            pt = self.projector.get_mouse_world_pos(
                event_dict, plane_normal, base_pt, place_on_geometry=False)
            if pt is not None:
                return pt
            
            # Fallback for edge-on views: project camera-plane intersection onto constraint plane
            if self.view:
                vd = self.view.getViewDirection()
                cam_normal = FreeCAD.Vector(-vd[0], -vd[1], -vd[2]).normalize()
                pt_cam = self.projector.get_mouse_world_pos(
                    event_dict, cam_normal, base_pt, place_on_geometry=False)
                if pt_cam is not None:
                    diff = pt_cam - base_pt
                    proj = plane_normal * diff.dot(plane_normal)
                    return pt_cam - proj
        return None

    def _apply_snap(self, pt, event_dict, *, origin=None, axis_unit_vec=None,
                    extra_points=(), exclude=(), is_modal=False):
        """Quantize `pt`. Vertex/origin/surface candidates win over the grid.

        `origin` + `axis_unit_vec` make this snap the DELTA along a locked axis
        rather than the absolute point -- which is what a lock means: the result
        must stay on the axis, and quantizing x/y/z independently would leave it.
        """
        from freecad.fields.core.input import fld_snap
        from freecad.fields.core.fld_settings import (
            get_snap_grid_step, get_snap_during_direct_drag)

        if not is_modal and not get_snap_during_direct_drag():
            if getattr(self, "_snap_indicator", None):
                self._snap_indicator.hide()
            return pt
        if not fld_snap.snap_active():
            if getattr(self, "_snap_indicator", None):
                self._snap_indicator.hide()
            return pt

        res = fld_snap.snap_world_point(
            self.view, pt, extra_points=extra_points, exclude=exclude,
            working_plane=getattr(self, "working_plane", None), event_dict=event_dict)
        if res.kind:
            if getattr(self, "_snap_indicator", None) and self.view:
                self._snap_indicator.show(res.point, res.kind, view=self.view)
            return res.point

        step = get_snap_grid_step()
        if origin is None:
            snapped = fld_snap.snap_vector_to_grid(pt, step)
        else:
            delta = pt - origin
            if axis_unit_vec is not None:
                snapped = origin + axis_unit_vec * fld_snap.snap_length(delta.dot(axis_unit_vec), step)
            else:
                snapped = origin + fld_snap.snap_vector_to_grid(delta, step)

        if getattr(self, "_snap_indicator", None) and self.view:
            if step > 0:
                self._snap_indicator.show(snapped, "grid", view=self.view)
            else:
                self._snap_indicator.hide()
        return snapped

    def resolve_world_point(self, event_dict, *, base_pt=None, plane_normal=None,
                            plane_origin=None, allow_snap=True, extra_points=None,
                            exclude=None, show_guide=True, place_on_geometry=False):
        """Turn a mouse position into a world point -- the ONE place that does this.

        Order: constraint override -> axis/plane lock -> projection (explicit plane,
        else workplane, else camera plane) -> snap -> guide. Every tool's drag path
        goes through here; nothing else in the workbench may call fld_snap directly.

        base_pt defaults to self._drag_constraint_base. allow_snap=False is for paths
        that are already quantised by something else, not for opting a tool out --
        the SnapDuringDirectDrag preference does that, in _apply_snap().
        """
        from freecad.fields.core.input.input_manager import FldInputManager
        new_pt = None
        if base_pt is None:
            base_pt = getattr(self, '_drag_constraint_base', None)

        axis_override = getattr(self, '_drag_axis_override', None)
        if axis_override is not None:
            if base_pt is not None:
                new_pt = FldInputManager.get_instance().get_axis_point(
                    self.view, base_pt, axis_override, event_dict)
                if show_guide and getattr(self, "_snap_guide", None) and self.view:
                    self._snap_guide.show_axis(base_pt, axis_override, self.view)
        else:
            new_pt = self.get_constrained_drag_point(event_dict)
            if new_pt is not None and show_guide and getattr(self, "_snap_guide", None) and self.view:
                axis_vec, p_norm = self._get_constraint_vectors()
                if base_pt is not None:
                    if axis_vec:
                        self._snap_guide.show_axis(base_pt, axis_vec, self.view)
                    elif p_norm:
                        u_hint = None
                        cp = getattr(self, "_constraint_plane", None)
                        if cp:
                            u_hint = {'xy': FreeCAD.Vector(1, 0, 0),
                                      'yz': FreeCAD.Vector(0, 1, 0),
                                      'xz': FreeCAD.Vector(1, 0, 0)}.get(cp)
                        self._snap_guide.show_plane(base_pt, p_norm, self.view, u_hint=u_hint)

        if new_pt is None:
            if plane_normal is not None and plane_origin is not None:
                new_pt = self.projector.get_mouse_world_pos(
                    event_dict, plane_normal, plane_origin,
                    place_on_geometry=place_on_geometry)
            elif base_pt is not None and getattr(self, '_edit_snap_mode', 'off') != 'off':
                skip = exclude if exclude is not None else ([self._preview_obj] if getattr(self, "_preview_obj", None) else None)
                place_on_geo = (getattr(self, '_edit_snap_mode', 'off') == 'all')
                snap_result = self.projector.get_mouse_plane_pt(
                    event_dict, place_on_geometry=place_on_geo,
                    working_plane=getattr(self, 'working_plane', None), skip_objects=skip)
                new_pt = snap_result[0] if snap_result else None
            else:
                plane_n = getattr(self, '_drag_plane_n', None)
                plane_o = getattr(self, '_drag_plane_o', None)
                if plane_n is None or plane_o is None:
                    if getattr(self, 'working_plane', None):
                        plane_n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
                        plane_o = self.working_plane.Base
                    elif self.view:
                        plane_n = FreeCAD.Vector(-self.view.getViewDirection())
                        plane_o = base_pt or FreeCAD.Vector(0, 0, 0)
                    else:
                        plane_n = FreeCAD.Vector(0, 0, 1)
                        plane_o = FreeCAD.Vector(0, 0, 0)
                new_pt = self.projector.get_mouse_world_pos(
                    event_dict, plane_n, plane_o,
                    place_on_geometry=place_on_geometry or getattr(self, 'place_on_geometry', False))

        if new_pt is not None and allow_snap:
            axis_vec, p_norm = self._get_constraint_vectors()
            snap_origin = base_pt if (axis_vec or p_norm or axis_override is not None) else None
            snap_axis = axis_override if axis_override is not None else axis_vec
            ex_pts = extra_points if extra_points is not None else getattr(self, "points", [])
            excl = exclude if exclude is not None else ([self._preview_obj] if getattr(self, "_preview_obj", None) else [])
            new_pt = self._apply_snap(
                new_pt, event_dict,
                origin=snap_origin,
                axis_unit_vec=snap_axis,
                extra_points=ex_pts,
                exclude=excl,
                is_modal=False,
            )
        elif new_pt is not None and not allow_snap:
            if getattr(self, "_snap_indicator", None):
                self._snap_indicator.hide()

        return new_pt

    def update_preview(self):
        """Dynamic preview update. Override in subclasses if needed."""
        pass

    def finish(self):
        """Standard 'Accept' behavior. Calls _do_finish() then terminate()."""
        if getattr(self, "_terminated", False):
            return
        self._do_finish()

        # If autorepeat is enabled and we are not in edit mode, terminate and restart the command.
        if getattr(self, "autorepeat", False) and not getattr(self, "_is_editing", False):
            self._finished = True
            self.terminate()
            cmd_id = self.get_command_id()
            if cmd_id:
                from PySide import QtCore
                import FreeCADGui
                QtCore.QTimer.singleShot(0, lambda: FreeCADGui.runCommand(cmd_id))
            return

        self.terminate()

    def cancel(self):
        """Standard 'Cancel' behavior: revert any in-progress edits via
        restore_original(), then terminate() (which also removes unfinished
        preview/temp objects). Single entry point shared by the task panel's
        Reject button and the context menu's Cancel item."""
        if getattr(self, "_terminated", False):
            return
        self.restore_original()
        self.terminate()

    def restore_original(self):
        """Revert the edited object to its pre-edit state on cancel. Override
        in tools that snapshot state in edit_object()."""
        pass

    def _do_finish(self):
        pass  # subclass hook — commit result, clean up preview before terminate

    def is_in_progress(self):
        """Returns True if the tool has active state/points OR is in edit mode."""
        return getattr(self, "state", 0) > 0 or getattr(self, "_is_editing", False)

    def toggle_cutter_mode(self):
         self.is_cutter = not self.is_cutter
         self.manual_mode_override = True
         self.update_material()
         self.view.redraw()

    def update_material(self):
        pass

    def update_ui(self):
        pass

    # ------------------------------------------------------------------
    # Geometry helpers — Delegated to ViewProjector
    # ------------------------------------------------------------------

    def get_mouse_world_pos(self, event_dict, plane_normal=None, plane_point=None):
        """Delegated to ViewProjector (which uses FldInputManager for rays)."""
        return self.projector.get_mouse_world_pos(
            event_dict, plane_normal, plane_point, 
            place_on_geometry=getattr(self, "place_on_geometry", False)
        )

    def get_visible_workplanes(self):
        return self.projector.get_visible_workplanes() if self.projector else []

    def get_base_plane(self, wp_obj=None):
        return self.projector.get_base_plane(wp_obj) if self.projector else None

    def get_mouse_plane_pt(self, event_dict):
        """Delegated to ViewProjector (which uses FldInputManager for rays).

        Automatically excludes any preview/active object from SDF hit testing
        so that creation tools don't hit-test against themselves.
        """
        # Build skip list from preview/active objects to avoid self-intersection
        skip = []
        for attr in ("_preview_obj", "_active_obj"):
            obj = getattr(self, attr, None)
            if obj is not None:
                skip.append(obj)

        active_plane = self._active_working_plane()

        result = self.projector.get_mouse_plane_pt(
            event_dict,
            place_on_geometry=getattr(self, "place_on_geometry", False),
            working_plane=active_plane,
            skip_objects=skip or None
        )
        if isinstance(result, tuple):
            # Handle both 2-tuple and 3-tuple for robustness
            pt = result[0]
            wp = result[1]
            return pt
        return result

    def get_selected_vertex_pos(self):
        if hasattr(self, "_target_obj") and self._target_obj and getattr(self, "_selected_element", None):
            idx, elem_type = self._selected_element
            pts = getattr(self._target_obj, "Points", [])
            h_in = getattr(self._target_obj, "HandleIn", [])
            h_out = getattr(self._target_obj, "HandleOut", [])

            val = None
            if elem_type == "Point" and idx < len(pts):
                val = pts[idx]
            elif elem_type == "HandleIn" and idx < len(h_in):
                val = h_in[idx]
            elif elem_type == "HandleOut" and idx < len(h_out):
                val = h_out[idx]

            if val is not None:
                return self._target_obj.Placement.multVec(val)
            return None
        if getattr(self, "_selected_indices", None):
            # Primitive creator (BoxCreator etc.): a control-point selection
            # with no _target_obj. _gizmo_center() is already exactly
            # "centroid of the selection", which is what G should pivot on.
            return self._gizmo_center()
        return None

    def update_selected_vertex_pos(self, global_pos):
        if hasattr(self, "_target_obj") and self._target_obj and getattr(self, "_selected_element", None):
            local_pos = self._target_obj.Placement.inverse().multVec(global_pos)
            self._update_element(self._selected_element, local_pos)
        elif getattr(self, "_selected_indices", None) and getattr(self, "_transform_origin", None) is not None:
            # global_pos arrives already resolved onto the locked axis/plane --
            # _update_modal_preview's single-axis branch projects onto
            # _modal_axis_vector (global OR local per _axis_lock_space) and its
            # free/plane branch already calls constrain_vector itself;
            # apply_numeric_transform's offset is built the same way. Re-masking
            # here by world-axis-LETTER membership (constrain_vector's only
            # notion of "locked") is a no-op for a global lock but corrupts a
            # local one, whose vector legitimately has components on axes whose
            # letter isn't in _axis_lock. Matches the _target_obj branch above,
            # which also takes global_pos as already-resolved.
            delta = global_pos - self._transform_origin
            self._apply_selection_delta(delta)

    def start_modal_transform(self, mode):
        from freecad.fields.core.fld_settings import (
            get_control_scheme, get_modal_requires_selection)
        if get_control_scheme() == "direct":
            fld_logger.info("Modal transforms are off in the Direct control scheme")
            return
        if not self.SUPPORTS_MODAL:
            fld_logger.warn(f"{type(self).__name__} has no modal transform - drag instead")
            return
        origin = self.get_selected_vertex_pos()
        if origin is None:
            if mode == "GRAB" and get_modal_requires_selection():
                fld_logger.warn("Grab needs a selected point - click a control point first")
                return
            origin = self.get_modal_pivot()
        if origin is None:
            fld_logger.warn(f"{mode.title()} has nothing to act on here")
            return
        self._modal_transform = mode
        self._input_buffer = ""
        self._transform_origin = origin
        self._modal_snapshot = self.snapshot_modal_state()
        self._modal_start_mouse = FldInputManager.get_instance()._last_qt_pos
        self._update_modal_hud()
        fld_logger.info(f"Modal {mode} active")

    def snapshot_modal_state(self):
        if hasattr(self, "_target_obj") and self._target_obj:
            pts = [p.copy() if hasattr(p, "copy") else FreeCAD.Vector(p) for p in getattr(self._target_obj, "Points", [])]
            hin = [p.copy() if hasattr(p, "copy") else FreeCAD.Vector(p) for p in getattr(self._target_obj, "HandleIn", [])]
            hout = [p.copy() if hasattr(p, "copy") else FreeCAD.Vector(p) for p in getattr(self._target_obj, "HandleOut", [])]
            pl = getattr(self._target_obj, "Placement", None)
            pl_copy = pl.copy() if (pl and hasattr(pl, "copy")) else pl
            snap = {
                "Points": pts,
                "HandleIn": hin,
                "HandleOut": hout,
                "Placement": pl_copy,
            }
            for prop in PRIMITIVE_SCALE_PROPS:
                if hasattr(self._target_obj, prop):
                    val = getattr(self._target_obj, prop)
                    if isinstance(val, (int, float)):
                        snap[prop] = float(val)
                    elif hasattr(val, "Value"):
                        snap[prop] = float(val.Value)
            return snap
        elif getattr(self, "_selected_element", None):
            pos = self.get_selected_vertex_pos()
            return {"vertex_pos": pos.copy() if (pos and hasattr(pos, "copy")) else pos}
        elif hasattr(self, "points"):
            # Primitive creator (BoxCreator etc.). Snapshot every point
            # regardless of selection -- GRAB (guarded earlier in
            # start_modal_transform to require a selection) reads the
            # selected subset out of this via _apply_selection_delta, but
            # ROTATE has no selection guard: it always turns the whole
            # primitive about its origin, so it needs all points snapshotted
            # even when nothing is selected. Shared with the gizmo-arrow drag
            # path via _drag_start_points.
            pts = [(p.copy() if hasattr(p, "copy") else FreeCAD.Vector(p)) if p is not None else None
                   for p in getattr(self, "points", [])]
            self._drag_start_points = pts
            snap = {"points": pts}
            wp = getattr(self, "working_plane", None)
            if wp is not None:
                snap["wp_base"] = FreeCAD.Vector(wp.Base)
                snap["wp_rot"] = FreeCAD.Rotation(wp.Rotation)
            return snap
        return None

    def restore_modal_state(self, snap):
        if not snap:
            return
        if hasattr(self, "_target_obj") and self._target_obj:
            if "Points" in snap and hasattr(self._target_obj, "Points"):
                self._target_obj.Points = [p.copy() if hasattr(p, "copy") else FreeCAD.Vector(p) for p in snap["Points"]]
            if "HandleIn" in snap and hasattr(self._target_obj, "HandleIn"):
                self._target_obj.HandleIn = [p.copy() if hasattr(p, "copy") else FreeCAD.Vector(p) for p in snap["HandleIn"]]
            if "HandleOut" in snap and hasattr(self._target_obj, "HandleOut"):
                self._target_obj.HandleOut = [p.copy() if hasattr(p, "copy") else FreeCAD.Vector(p) for p in snap["HandleOut"]]
            if "Placement" in snap and snap["Placement"] is not None:
                self._target_obj.Placement = snap["Placement"].copy() if hasattr(snap["Placement"], "copy") else snap["Placement"]
            for prop in PRIMITIVE_SCALE_PROPS:
                if prop in snap and hasattr(self._target_obj, prop):
                    setattr(self._target_obj, prop, snap[prop])
            obj = self._target_obj
            def _deferred():
                if obj and getattr(obj, "Document", None):
                    obj.Document.recompute()
            QtCore.QTimer.singleShot(0, _deferred)
        elif "vertex_pos" in snap and snap["vertex_pos"] is not None:
            self.update_selected_vertex_pos(snap["vertex_pos"])
        elif "points" in snap:
            self.points = [(p.copy() if hasattr(p, "copy") else FreeCAD.Vector(p)) if p is not None else None
                           for p in snap["points"]]
            self._drag_start_points = None
            wp = getattr(self, "working_plane", None)
            if wp is not None and "wp_base" in snap and "wp_rot" in snap:
                wp.Base = FreeCAD.Vector(snap["wp_base"])
                wp.Rotation = FreeCAD.Rotation(snap["wp_rot"])
            if hasattr(self, "_update_handle_positions"):
                self._update_handle_positions(self.points)
            if hasattr(self, "_refresh_origin"):
                self._refresh_origin()
            if hasattr(self, "update_preview"):
                self.update_preview()
            if hasattr(self, "view") and self.view:
                self.view.redraw()

    def _ensure_modal_snap_guide(self):
        if getattr(self, "_snap_guide", None) is None:
            from freecad.fields.core.input.fld_snap import FldSnapGuide
            from pivy import coin
            self._snap_guide = FldSnapGuide()
            root = getattr(self, "points_root", None)
            if not root and hasattr(self, "view") and self.view and hasattr(self.view, "getSceneGraph") and self.view.getSceneGraph():
                self._snap_guide_root = coin.SoAnnotation() if (coin and hasattr(coin, "SoAnnotation")) else None
                if self._snap_guide_root:
                    self.view.getSceneGraph().addChild(self._snap_guide_root)
                    root = self._snap_guide_root
            if root:
                self._snap_guide.draw(root)
        return self._snap_guide

    def cancel_modal_transform(self):
        if self._modal_snapshot is not None:
            self.restore_modal_state(self._modal_snapshot)
        self._modal_transform = None
        self._axis_lock = None
        self._axis_lock_space = None
        self._input_buffer = ""
        self._transform_origin = None
        self._modal_snapshot = None
        self._modal_start_mouse = None
        if getattr(self, "_snap_guide", None):
            self._snap_guide.hide()
        self._update_modal_hud()
        if hasattr(self, "view") and self.view:
            self.view.redraw()

    def commit_modal_transform(self):
        self._modal_transform = None
        self._axis_lock = None
        self._axis_lock_space = None
        self._input_buffer = ""
        self._transform_origin = None
        self._modal_snapshot = None
        self._modal_start_mouse = None
        if getattr(self, "_snap_guide", None):
            self._snap_guide.hide()
        self._update_modal_hud()
        obj = getattr(self, "_target_obj", None)
        view = getattr(self, "view", None)
        def _deferred_recompute():
            if obj and getattr(obj, "Document", None):
                obj.Document.recompute()
            if view:
                view.redraw()
        QtCore.QTimer.singleShot(0, _deferred_recompute)

    def numeric_value(self):
        """Parsed value of the numeric buffer, or None when empty/incomplete."""
        buf = self._input_buffer
        if buf in ("", "-", ".", "-."):
            return None
        try:
            return float(buf)
        except ValueError:
            fld_logger.warn(f"Bad numeric buffer '{buf}'")
            return None

    def _modal_axis_vector(self, axis_letter):
        """World-space unit vector for a locked GRAB axis ('X'/'Y'/'Z').

        Global unless `_axis_lock_space == 'local'`, in which case it is rotated
        by the working plane (or the curve tool's `_target_obj.Placement`) --
        the same rotation source `AxisConstraintMixin._get_constraint_vectors`
        uses for the ordinary (non-modal) drag constraint, kept separate here
        because the modal axis-lock cycle (on_key_press) has its own space
        state (`_axis_lock_space`), independent of `_constraint_space`.
        """
        import FreeCAD
        rot = None
        if getattr(self, "_axis_lock_space", None) == 'local':
            if getattr(self, "working_plane", None):
                rot = self.working_plane.Rotation
            elif getattr(self, "_target_obj", None) and hasattr(self._target_obj, "Placement"):
                rot = self._target_obj.Placement.Rotation
        base = {
            'X': FreeCAD.Vector(1, 0, 0),
            'Y': FreeCAD.Vector(0, 1, 0),
            'Z': FreeCAD.Vector(0, 0, 1),
        }[axis_letter]
        return rot.multVec(base) if rot is not None else base

    def apply_numeric_transform(self):
        val = self.numeric_value()
        if val is None:
            return

        import FreeCAD
        if self._modal_transform == "GRAB" and self._transform_origin:
            if not self._axis_lock:
                return
            lock = self._axis_lock
            if len(lock) == 1:
                offset = self._modal_axis_vector(lock[0]) * val
            else:
                offset = FreeCAD.Vector(
                    val if "X" in lock else 0.0,
                    val if "Y" in lock else 0.0,
                    val if "Z" in lock else 0.0
                )
            self.update_selected_vertex_pos(self._transform_origin + offset)
        elif self._modal_transform == "ROTATE":
            self.apply_modal_rotate(val)
        elif self._modal_transform == "SCALE":
            self.apply_modal_scale(val)

    def _update_modal_hud(self):
        try:
            import FreeCADGui
            sb = FreeCADGui.getMainWindow().statusBar()
            if not self._modal_transform:
                # No modal running. In edit mode the status bar is the only place
                # the modal hotkeys are advertised, so leave the hint up rather
                # than blanking it -- the keys are invisible otherwise.
                if getattr(self, "_is_editing", False):
                    sb.showMessage(EDIT_MODE_HINT)
                else:
                    sb.clearMessage()
                return
            names = {"GRAB": "Grab", "ROTATE": "Rotate", "SCALE": "Scale", "GIZMO": "Gizmo"}
            axis = "+".join(self._axis_lock) if self._axis_lock else "free"
            # The default space stays silent, matching the HUD before the
            # axis-lock cycle existed; only a lock that deviates from it (the
            # 2nd press in the 3-press cycle) is worth calling out.
            lock_space = getattr(self, "_axis_lock_space", None)
            if (self._axis_lock and lock_space
                    and lock_space != self._default_axis_lock_space):
                axis = f"{axis} ({lock_space})"
            buf = self._input_buffer or "…"
            unit = {"GRAB": "mm", "ROTATE": "°", "SCALE": "×", "GIZMO": ""}[self._modal_transform]
            if self._modal_transform == "GRAB" and not self._axis_lock:
                from freecad.fields.core.input import fld_keymap
                kx = fld_keymap.binding_for("modal.lock_x")
                ky = fld_keymap.binding_for("modal.lock_y")
                kz = fld_keymap.binding_for("modal.lock_z")
                sb.showMessage(f"{names[self._modal_transform]}  |  axis: {axis}  |  {buf} {unit}  (type {kx}/{ky}/{kz} to constrain)")
            else:
                sb.showMessage(f"{names[self._modal_transform]}  |  axis: {axis}  |  {buf} {unit}")
        except Exception as e:
            fld_logger.debug(f"modal HUD update failed: {e}")

    def _update_modal_preview(self, event_dict):
        if self.numeric_value() is not None:
            self.apply_numeric_transform()
            if getattr(self, "_snap_guide", None):
                self._snap_guide.hide()
            return

        if self._modal_transform == "GRAB":
            if not self._transform_origin:
                return

            projector = getattr(self, "projector", None)
            if not projector and hasattr(self, "view") and self.view:
                projector = ViewProjector(self.view)
            if not projector:
                return

            new_world_pos = None
            axis_unit_vec = None
            if self._axis_lock and len(self._axis_lock) == 1:
                axis_name = self._axis_lock[0].upper()
                axis_unit_vec = self._modal_axis_vector(axis_name)
                drag_evt = dict(event_dict)
                if getattr(self, "_modal_start_mouse", None) is not None:
                    drag_evt["DragStart2D"] = self._modal_start_mouse
                new_world_pos = projector.get_projected_point(self._transform_origin, axis_unit_vec, drag_evt)
            elif self._axis_lock and len(self._axis_lock) == 2:
                # Shift+axis plane-lock: intersect with the TRUE locked plane
                # (normal = the excluded axis), not a screen-facing plane --
                # otherwise a front-on view (whose screen plane already has ~0
                # extent along the excluded axis) masks out real motion too and
                # the object barely moves, which read as "not working".
                missing_axis = ({"X", "Y", "Z"} - set(self._axis_lock)).pop()
                plane_normal = self._modal_axis_vector(missing_axis)
                pt = projector.get_mouse_world_pos(
                    event_dict,
                    plane_normal=plane_normal,
                    plane_point=self._transform_origin,
                    place_on_geometry=False,
                )
                if pt:
                    delta = pt - self._transform_origin
                    new_world_pos = self._transform_origin + self.constrain_vector(delta)
            else:
                new_world_pos = self.resolve_world_point(
                    event_dict,
                    base_pt=self._transform_origin,
                    allow_snap=False,
                    extra_points=getattr(self, "points", []),
                    show_guide=False,
                )
                if new_world_pos is not None:
                    delta = new_world_pos - self._transform_origin
                    new_world_pos = self._transform_origin + self.constrain_vector(delta)

            from freecad.fields.core.input import fld_snap
            snapping = fld_snap.snap_active()
            if new_world_pos is not None:
                new_world_pos = self._apply_snap(
                    new_world_pos, event_dict,
                    origin=self._transform_origin,
                    axis_unit_vec=axis_unit_vec,
                    extra_points=getattr(self, "points", []),
                    is_modal=True,
                )

                # The axis/plane-lock guide is a constraint affordance, not a
                # snap-increment indicator -- it must show as soon as X/Shift+X
                # locks the drag, same as the non-modal `resolve_world_point`
                # guide, independent of whether grid-snap quantization (Ctrl)
                # is active. Only the free-move plane guide below stays gated
                # on `snapping`: with no lock at all there's nothing to show
                # unless snap is actively suggesting a plane to land on.
                guide = self._ensure_modal_snap_guide()
                if guide:
                    if self._axis_lock and len(self._axis_lock) == 1:
                        guide.show_axis(self._transform_origin, axis_unit_vec, self.view)
                        lock_space = getattr(self, "_axis_lock_space", None) or getattr(self, "_default_axis_lock_space", 'global')
                        guide.show_space_label(self._transform_origin, lock_space, self.view)
                    elif self._axis_lock and len(self._axis_lock) == 2:
                        missing_axis = ({"X", "Y", "Z"} - set(self._axis_lock)).pop()
                        norm = self._modal_axis_vector(missing_axis)
                        first_axis = sorted(list(self._axis_lock))[0]
                        u_hint = self._modal_axis_vector(first_axis)
                        guide.show_plane(self._transform_origin, norm, self.view, u_hint=u_hint)
                        lock_space = getattr(self, "_axis_lock_space", None) or getattr(self, "_default_axis_lock_space", 'global')
                        guide.show_space_label(self._transform_origin, lock_space, self.view)
                    elif snapping:
                        guide.hide_space_label()
                        norm = None
                        u_hint = None
                        if hasattr(self, "working_plane") and self.working_plane and hasattr(self.working_plane, "Rotation"):
                            norm = self.working_plane.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
                            u_hint = self.working_plane.Rotation.multVec(FreeCAD.Vector(1, 0, 0))
                        elif hasattr(self, "view") and self.view and hasattr(self.view, "getViewDirection"):
                            norm = self.view.getViewDirection()
                        if norm:
                            guide.show_plane(self._transform_origin, norm, self.view, u_hint=u_hint)
                        else:
                            guide.hide()
                    else:
                        guide.hide()

                self.update_selected_vertex_pos(new_world_pos)
                self.update_preview()
        elif self._modal_transform == "ROTATE":
            if getattr(self, "_snap_guide", None):
                self._snap_guide.hide()
            self._update_modal_rotate_preview(event_dict)
        elif self._modal_transform == "SCALE":
            if getattr(self, "_snap_guide", None):
                self._snap_guide.hide()
            self._update_modal_scale_preview(event_dict)

    def _project_modal_pivot_to_screen(self, pivot):
        """Qt-widget-space screen position of a world point, for the
        ROTATE/SCALE angle/distance-around-pivot math below.

        Deliberately NOT view.getPointOnScreen(): confirmed live (comparing
        both against the same world points) that it returns a different pixel
        space from the Qt mouse coordinates FldInputManager hands every other
        tool -- roughly a 1.6x scale with the Y axis flipped -- so it silently
        produced a wrong angle instead of raising, rather than the exception
        the old try/except here was guarding against.
        ViewProjector.project_to_screen() is the algebraic inverse of
        get_ray(), the same math the rest of the input layer's mouse handling
        already goes through, so it's guaranteed to agree with
        FldInputManager's mouse coordinates.
        """
        projector = getattr(self, "projector", None)
        if not projector and hasattr(self, "view") and self.view:
            projector = ViewProjector(self.view)
        if not projector:
            return None
        try:
            return projector.project_to_screen(pivot)
        except Exception as e:
            fld_logger.debug(f"_project_modal_pivot_to_screen: {e}")
            return None

    def _update_modal_rotate_preview(self, event_dict):
        pivot = self.get_modal_pivot()
        if pivot is None:
            return
        im = FldInputManager.get_instance()
        cur = im.get_mouse_pos(event_dict)
        start = self._modal_start_mouse or cur
        pivot_px = self._project_modal_pivot_to_screen(pivot) or start
        angle = math.degrees(
            math.atan2(cur[1] - pivot_px[1], cur[0] - pivot_px[0])
            - math.atan2(start[1] - pivot_px[1], start[0] - pivot_px[0])
        )
        self.apply_modal_rotate(angle)

    def _update_modal_scale_preview(self, event_dict):
        pivot = self.get_modal_pivot()
        if pivot is None:
            return
        im = FldInputManager.get_instance()
        cur = im.get_mouse_pos(event_dict)
        start = self._modal_start_mouse or cur
        pivot_px = self._project_modal_pivot_to_screen(pivot) or start
        cur_dist = math.hypot(cur[0] - pivot_px[0], cur[1] - pivot_px[1])
        start_dist = math.hypot(start[0] - pivot_px[0], start[1] - pivot_px[1])
        factor = cur_dist / max(start_dist, 1.0)
        self.apply_modal_scale(factor)

    def get_modal_pivot(self):
        """Returns the world-space pivot for ROTATE/SCALE transforms."""
        if hasattr(self, "_target_obj") and self._target_obj and hasattr(self._target_obj, "Placement") and self._target_obj.Placement:
            return self._target_obj.Placement.Base
        if hasattr(self, "_origin_point"):
            # Primitive creator (BoxCreator etc.): rotation/scale always pivot
            # on the primitive's own origin, never the control-point selection
            # -- rotating just a selected corner isn't a coherent primitive
            # operation, and the origin is already "a guide for rotations and
            # placement" (see fld_edit_gizmo), matching the gizmo rotation
            # ring's own pivot.
            return self._origin_point()
        return self.get_selected_vertex_pos()

    def apply_modal_rotate(self, angle_deg):
        """Rotate all control points of _target_obj around the pivot from initial snapshot."""
        if not self._modal_snapshot:
            fld_logger.debug("apply_modal_rotate: no snapshot")
            return
        if not hasattr(self, "_target_obj") or not self._target_obj:
            if "points" in self._modal_snapshot:
                self._apply_primitive_modal_rotate(angle_deg)
            else:
                fld_logger.debug("apply_modal_rotate: no target object or snapshot")
            return

        pivot = self.get_modal_pivot()
        if pivot is None:
            return

        import FreeCAD
        # Determine rotation axis
        if self._axis_lock and len(self._axis_lock) == 1:
            axis_name = self._axis_lock[0].upper()
            axis_vec = FreeCAD.Vector(
                1.0 if axis_name == "X" else 0.0,
                1.0 if axis_name == "Y" else 0.0,
                1.0 if axis_name == "Z" else 0.0,
            )
        else:
            if hasattr(self, "view") and self.view and hasattr(self.view, "getViewDirection"):
                axis_vec = self.view.getViewDirection().negative()
            else:
                axis_vec = FreeCAD.Vector(0, 0, 1)

        rot = FreeCAD.Rotation(axis_vec, angle_deg)
        pl = self._modal_snapshot.get("Placement")
        if pl is None:
            pl = getattr(self._target_obj, "Placement", FreeCAD.Placement())
        inv_pl = pl.inverse()

        for attr in ("Points", "HandleIn", "HandleOut"):
            if attr in self._modal_snapshot and hasattr(self._target_obj, attr):
                orig_pts = self._modal_snapshot[attr]
                new_pts = []
                for p_local in orig_pts:
                    p_world = pl.multVec(p_local)
                    p_new_world = pivot + rot.multVec(p_world - pivot)
                    p_new_local = inv_pl.multVec(p_new_world)
                    new_pts.append(p_new_local)
                setattr(self._target_obj, attr, new_pts)

        touched = any(attr in self._modal_snapshot and hasattr(self._target_obj, attr)
                      and self._modal_snapshot[attr]
                      for attr in ("Points", "HandleIn", "HandleOut"))
        if not touched and pl is not None:
            # No control points: the object's orientation lives in its Placement.
            new_pl = FreeCAD.Placement(pl)
            new_pl.Base = pivot + rot.multVec(pl.Base - pivot)
            new_pl.Rotation = rot.multiply(pl.Rotation)
            self._target_obj.Placement = new_pl

        self.update_preview()

    def _apply_primitive_modal_rotate(self, angle_deg):
        """Rotate every point (and the working plane) of a primitive creator
        about its origin, from the frozen pre-modal snapshot.

        Primitives have no meaningful "rotate just the selection" -- the
        origin is a guide for rotation/placement (see fld_edit_gizmo), so R
        always rotates the whole shape about it regardless of what's
        selected. Mirrors the gizmo rotation ring's own math
        (_gizmo_rot_drag_update) exactly, so a point's LOCAL position
        relative to the working plane -- and hence e.g. BoxCreator's
        x_min/x_max bounds -- comes out unchanged.
        """
        pivot = self.get_modal_pivot()
        if pivot is None:
            return

        if self._axis_lock and len(self._axis_lock) == 1:
            axis_name = self._axis_lock[0].upper()
            base_vec = {
                "X": FreeCAD.Vector(1, 0, 0),
                "Y": FreeCAD.Vector(0, 1, 0),
                "Z": FreeCAD.Vector(0, 0, 1),
            }[axis_name]
            if getattr(self, "_axis_lock_space", None) == 'local':
                # Frozen at snapshot time, NOT read live from
                # self.working_plane.Rotation -- this method mutates that
                # rotation every call, so reading it live would chase the
                # object and make the drag run away (the exact trap
                # documented on _gizmo_rot_drag_update for the same reason).
                wp_rot = self._modal_snapshot.get("wp_rot")
                axis_vec = wp_rot.multVec(base_vec) if wp_rot is not None else base_vec
            else:
                axis_vec = base_vec
        else:
            if hasattr(self, "view") and self.view and hasattr(self.view, "getViewDirection"):
                axis_vec = self.view.getViewDirection().negative()
            else:
                axis_vec = FreeCAD.Vector(0, 0, 1)

        rot = FreeCAD.Rotation(axis_vec, angle_deg)
        orig_pts = self._modal_snapshot.get("points", [])
        self.points = [None if p is None else pivot + rot.multVec(p - pivot) for p in orig_pts]

        wp = getattr(self, "working_plane", None)
        if wp is not None and "wp_base" in self._modal_snapshot and "wp_rot" in self._modal_snapshot:
            wp.Base = pivot + rot.multVec(self._modal_snapshot["wp_base"] - pivot)
            wp.Rotation = rot.multiply(self._modal_snapshot["wp_rot"])

        if hasattr(self, "_update_gizmo"):
            self._update_gizmo()
        if hasattr(self, "_update_handle_positions"):
            self._update_handle_positions(self.points)
        if hasattr(self, "update_ui"):
            self.update_ui()
        self.update_preview()
        if hasattr(self, "view") and self.view:
            self.view.redraw()

    def apply_modal_scale(self, factor):
        """Scale all control points of _target_obj around the pivot from initial snapshot."""
        if not hasattr(self, "_target_obj") or not self._target_obj or not self._modal_snapshot:
            fld_logger.debug("apply_modal_scale: no target object or snapshot")
            return

        pivot = self.get_modal_pivot()
        if pivot is None:
            return

        import FreeCAD
        pl = self._modal_snapshot.get("Placement")
        if pl is None:
            pl = getattr(self._target_obj, "Placement", FreeCAD.Placement())
        inv_pl = pl.inverse()

        sx = factor if (not self._axis_lock or "X" in self._axis_lock) else 1.0
        sy = factor if (not self._axis_lock or "Y" in self._axis_lock) else 1.0
        sz = factor if (not self._axis_lock or "Z" in self._axis_lock) else 1.0

        for attr in ("Points", "HandleIn", "HandleOut"):
            if attr in self._modal_snapshot and hasattr(self._target_obj, attr):
                orig_pts = self._modal_snapshot[attr]
                new_pts = []
                for p_local in orig_pts:
                    p_world = pl.multVec(p_local)
                    diff = p_world - pivot
                    scaled_diff = FreeCAD.Vector(diff.x * sx, diff.y * sy, diff.z * sz)
                    p_new_world = pivot + scaled_diff
                    p_new_local = inv_pl.multVec(p_new_world)
                    new_pts.append(p_new_local)
                setattr(self._target_obj, attr, new_pts)

        touched = any(attr in self._modal_snapshot and hasattr(self._target_obj, attr)
                      and self._modal_snapshot[attr]
                      for attr in ("Points", "HandleIn", "HandleOut"))
        if not touched:
            scaled_any = False
            for prop in PRIMITIVE_SCALE_PROPS:
                if prop in self._modal_snapshot and hasattr(self._target_obj, prop):
                    setattr(self._target_obj, prop, self._modal_snapshot[prop] * factor)
                    scaled_any = True

            if pl is not None:
                diff = pl.Base - pivot
                if diff.Length > 1e-6:
                    new_pl = FreeCAD.Placement(pl)
                    new_pl.Base = pivot + diff * factor
                    self._target_obj.Placement = new_pl
                    scaled_any = True

            if not scaled_any:
                fld_logger.warn(f"Cannot scale {getattr(self._target_obj, 'Name', 'object')}: no control points or scale properties found.")
            elif self._axis_lock:
                fld_logger.warn("Non-uniform scale of SDF primitive is not expressible in dimensions; scaling uniformly.")

        self.update_preview()

    # ------------------------------------------------------------------
    # Event loop & Overridable Input Hooks
    # ------------------------------------------------------------------

    def on_mouse_press(self, event_dict):
        btn = event_dict.get("Button")
        if getattr(self, "_modal_transform", None):
            if btn == QtCore.Qt.LeftButton:
                self.commit_modal_transform()
                return True
            if btn == QtCore.Qt.RightButton:
                self.cancel_modal_transform()
                return True
            return False  # MMB always passes through (input-map rule)
        if btn == QtCore.Qt.LeftButton:
            return self.on_button1_down(event_dict)
        elif btn == QtCore.Qt.MiddleButton:
            return self.on_button2_down(event_dict)
        elif btn == QtCore.Qt.RightButton:
            return self.on_button3_down(event_dict)
        return False

    def on_mouse_release(self, event_dict):
        btn = event_dict.get("Button")
        if btn == QtCore.Qt.LeftButton:
            return self.on_button1_up(event_dict)
        elif btn == QtCore.Qt.MiddleButton:
            return self.on_button2_up(event_dict)
        elif btn == QtCore.Qt.RightButton:
            return self.on_button3_up(event_dict)
        return False

    def on_mouse_move(self, event_dict):
        # Modal previews are dispatched here rather than in handle_move: every
        # editor overrides handle_move (or on_mouse_move itself) for its own
        # drag, so a modal branch further down the chain never runs and the
        # axis lock has nothing to act on.
        if getattr(self, "_modal_transform", None):
            self._update_modal_preview(event_dict)
            return False
        self.handle_move(event_dict)

    def on_key_press(self, event_dict):
        key = event_dict.get("Key")
        text = event_dict.get("Text", "").lower()

        from freecad.fields.core.input import fld_keymap
        contexts = [fld_keymap.CTX_TOOL]
        if getattr(self, "_is_editing", False):
            contexts.insert(0, fld_keymap.CTX_EDIT)
        if getattr(self, "_modal_transform", None):
            contexts.insert(0, fld_keymap.CTX_MODAL)
        action = fld_keymap.action_for(event_dict, contexts)
        
        if action == "tool.toggle_edit":
            if getattr(self, "_modal_transform", None):
                return True
            self.finish()
            return True

        # Axis constraints in edit mode (toggled via X/Y/Z) -- but not while a
        # modal transform owns the keyboard; there X/Y/Z mean the modal axis lock.
        if (getattr(self, "_is_editing", False)
                and not getattr(self, "_modal_transform", None)
                and action in ("edit.constrain_x", "edit.constrain_y", "edit.constrain_z",
                               "edit.constrain_yz", "edit.constrain_xz", "edit.constrain_xy")):
            axis_map = {
                "edit.constrain_x": ("x", False),
                "edit.constrain_y": ("y", False),
                "edit.constrain_z": ("z", False),
                "edit.constrain_yz": ("x", True),
                "edit.constrain_xz": ("y", True),
                "edit.constrain_xy": ("z", True),
            }
            axis, is_shift = axis_map[action]
            if is_shift:
                plane = {'x': 'yz', 'y': 'xz', 'z': 'xy'}[axis]
                if getattr(self, "_constraint_plane", None) == plane and getattr(self, "_constraint_space", 'global') == 'global':
                    self._constraint_space = 'local'
                elif getattr(self, "_constraint_plane", None) == plane:
                    self._constraint_axis = None
                    self._constraint_plane = None
                else:
                    self._constraint_plane = plane
                    self._constraint_axis = None
                    self._constraint_space = 'global'
                    self._constraint_last_key = f'shift_{axis}'
            else:
                if getattr(self, "_constraint_axis", None) == axis and getattr(self, "_constraint_space", 'global') == 'global':
                    self._constraint_space = 'local'
                elif getattr(self, "_constraint_axis", None) == axis:
                    self._constraint_axis = None
                    self._constraint_plane = None
                else:
                    self._constraint_axis = axis
                    self._constraint_plane = None
                    self._constraint_space = 'global'
                    self._constraint_last_key = axis
            self._update_constraint_visual()
            return True
            
        # If already in a modal state, check axis/numeric inputs
        if self._modal_transform:
            if action == "modal.cancel":
                self.cancel_modal_transform()
                return True
            elif action == "modal.commit":
                self.commit_modal_transform()
                return True
            
            # Check axis lock
            if action in ("modal.lock_x", "modal.lock_y", "modal.lock_z",
                          "modal.lock_yz", "modal.lock_xz", "modal.lock_xy"):
                plane_map = {
                    "modal.lock_yz": ("X", True),
                    "modal.lock_xz": ("Y", True),
                    "modal.lock_xy": ("Z", True),
                    "modal.lock_x": ("X", False),
                    "modal.lock_y": ("Y", False),
                    "modal.lock_z": ("Z", False),
                }
                axis_letter, is_plane = plane_map[action]
                if is_plane:
                    # Shift+Axis locks to the perpendicular plane. Same
                    # Blender-style 3-press cycle as the single-axis case
                    # below: 1st press locks the plane at the default space;
                    # 2nd press (same plane) flips global<->local for THIS
                    # lock only; 3rd press clears it back to free movement.
                    axes = ["X", "Y", "Z"]
                    axes.remove(axis_letter)
                    plane = tuple(axes)
                    default_space = self._default_axis_lock_space
                    other_space = 'local' if default_space == 'global' else 'global'
                    if self._axis_lock == plane and self._axis_lock_space == default_space:
                        self._axis_lock_space = other_space
                    elif self._axis_lock == plane and self._axis_lock_space == other_space:
                        self._axis_lock = None
                        self._axis_lock_space = None
                    else:
                        self._axis_lock = plane
                        self._axis_lock_space = default_space
                else:
                    # Blender-style 3-press cycle: 1st press locks the axis at the
                    # default space; 2nd press (same axis) flips global<->local for
                    # THIS lock only, without touching the default; 3rd press clears
                    # the lock entirely, back to free movement.
                    default_space = self._default_axis_lock_space
                    other_space = 'local' if default_space == 'global' else 'global'
                    if self._axis_lock == (axis_letter,) and self._axis_lock_space == default_space:
                        self._axis_lock_space = other_space
                    elif self._axis_lock == (axis_letter,) and self._axis_lock_space == other_space:
                        self._axis_lock = None
                        self._axis_lock_space = None
                    else:
                        self._axis_lock = (axis_letter,)
                        self._axis_lock_space = default_space
                self._update_modal_hud()
                fld_logger.info(f"Locked to axis: {self._axis_lock} (space: {self._axis_lock_space})")
                # Re-project immediately against the last known mouse position:
                self._update_modal_preview({})
                return True
                
            # Numeric entry
            if key == QtCore.Qt.Key_Backspace:
                self._input_buffer = self._input_buffer[:-1]
                self.apply_numeric_transform()
                self._update_modal_hud()
                return True
            if text == '-':
                if self._input_buffer.startswith('-'):
                    self._input_buffer = self._input_buffer[1:]
                else:
                    self._input_buffer = '-' + self._input_buffer
                self.apply_numeric_transform()
                self._update_modal_hud()
                return True
            if text in "0123456789" or (text == '.' and '.' not in self._input_buffer):
                self._input_buffer += text
                self.apply_numeric_transform()
                self._update_modal_hud()
                return True
                
            return False

        # Start modal transform
        if action == "edit.grab":
            self.start_modal_transform("GRAB")
            return True
        elif action == "edit.rotate":
            self.start_modal_transform("ROTATE")
            return True
        elif action == "edit.scale":
            self.start_modal_transform("SCALE")
            return True
        return self.handle_keyboard(event_dict)

    def on_key_release(self, event_dict):
        return False

    def on_context_menu(self, event_dict):
        if getattr(self, "_modal_transform", None):
            self.cancel_modal_transform()
            return True
        from freecad.fields.core.input.fld_menu import FldMenuManager
        mgr = FldMenuManager.get_instance()
        if not mgr.is_menu_active():
            items = self.get_context_menu(event_dict)
            if items:
                mgr.trigger_dynamic_menu(items)
            else:
                mgr.show_context_menu()
        return True

    def on_button1_down(self, event_dict):
        return self.handle_click(event_dict)

    def on_button2_down(self, event_dict):
        return False

    def on_button3_down(self, event_dict):
        return True  # Consume press; context menu shown via on_context_menu

    def on_button1_up(self, event_dict):
        return False

    def on_button2_up(self, event_dict):
        return False

    def on_button3_up(self, event_dict):
        return True # Consume release to suppress FreeCAD context menu


    def handle_keyboard(self, event_dict):
        key_code = event_dict.get("Key")
        key_text = str(event_dict.get("Text", "None")).upper()
        
        # ESC to cancel
        if key_code == QtCore.Qt.Key_Escape:
            if hasattr(self, "cancel"):
                self.cancel()
            else:
                self.terminate()
            return True
            
        # ENTER/RETURN to finish
        if key_code in [QtCore.Qt.Key_Enter, QtCore.Qt.Key_Return]:
            self.finish()
            return True
            
        # Toggle Cutter Mode (C)
        if key_text == "C":
             self.toggle_cutter_mode()
             return True

        # Axis Toggles (X, Y, Z) -> Focus Panel
        target_axis = None
        if key_text == "X": target_axis = "x"
        elif key_text == "Y": target_axis = "y"
        elif key_text == "Z": target_axis = "z"
        
        if target_axis:
            self.toggle_axis(target_axis)
            return True
            
        # Reset Tool (R)
        if key_text == "R":
            self.reset_state()
            return True
            
        # Tool Option 0 (Shift)
        if "SHIFT" in key_text:
            self.on_tool_option_0()
            return False
            
        # Tool Option 1 (Ctrl)
        if "CONTROL" in key_text or "CTRL" in key_text:
            self.on_tool_option_1()
            return False
            
        # Snapping Menu (S)
        if key_text == "S":
            if hasattr(self, 'get_snapping_menu'):
                from freecad.fields.core.input.fld_menu import FldMenuManager
                FldMenuManager.get_instance().trigger_dynamic_menu(self.get_snapping_menu())
                return True

        # Tool Menu (D)
        if key_text == "D":
            items = None
            if hasattr(self, 'get_context_menu'):
                items = self.get_context_menu()
            if items:
                from freecad.fields.core.input.fld_menu import FldMenuManager
                FldMenuManager.get_instance().trigger_dynamic_menu(items)
                return True
            self.on_tool_menu()
            return True
            
        return False

    def on_tool_option_0(self):
        pass
        
    def on_tool_option_1(self):
        pass
        
    def on_tool_menu(self):
        pass

    def toggle_autorepeat(self, checked=None):
        if checked is not None:
            self.__class__.autorepeat = checked
        else:
            self.__class__.autorepeat = not self.__class__.autorepeat
        if hasattr(self, "update_ui"):
            self.update_ui()

    def get_context_menu(self, event_dict=None):
        items = []
        if hasattr(self, 'get_snapping_menu'):
            items += [("Snapping", self.get_snapping_menu()), None]
        # Same parent-class entry points as the task panel (accept()/reject())
        items += [
            ("Accept", self.finish),
            ("Cancel", self.cancel),
            None,
            ("Auto Repeat", self.toggle_autorepeat, self.autorepeat),
        ]
        return items

    def reset_state(self):
        """Resets the tool to its initial idle state (state 0)."""
        self.state = ToolState.IDLE
        self.start_point = None
        self.current_point = None
        self.height = 0.0
        self._finish_scheduled = False
        self.on_state_change(self.state)
        self.view.redraw()

    def update_from_locks(self):
        pass

    def handle_click(self, event_dict):
        try:
            btn = event_dict.get("Button")
            
            if btn != QtCore.Qt.LeftButton:
                return False

            # Use get_mouse_plane_pt to respect workplanes and snapping
            result = self.get_mouse_plane_pt(event_dict)
            if isinstance(result, tuple):
                pt, wp_hit = result
            else:
                pt, wp_hit = result, None

            if pt is None:

                return False
                

            if getattr(self, "state", ToolState.IDLE) == ToolState.IDLE:
                self.state = ToolState.ACTIVE # Force state 1 if inadvertently set to 0.
                
            if self.state == 1:
                # Pin 1: Fix the starting point and move to State 2
                self.start_point = pt
                self.current_point = pt
                
                # Setup working_plane if we hit something or use fallback
                if wp_hit:
                    if hasattr(wp_hit, "getGlobalPlacement"):
                        self.working_plane = wp_hit.getGlobalPlacement()
                    elif hasattr(wp_hit, "Placement"):
                        self.working_plane = wp_hit.Placement
                    else:
                        self.working_plane = wp_hit
                elif not self.working_plane:
                    # Fallback to camera facing if nothing hit and no plane set
                    n, o = self.get_base_plane()
                    rot = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), n)
                    self.working_plane = FreeCAD.Placement(pt, rot)
                
                self.state = ToolState.DRAGGING # Note: 2 corresponds to ACTIVE/DRAGGING in some tools
                self.on_state_change(self.state)
                self.update_preview()
            elif self.state == 2:
                # Pin 3: Finish
                self.finish()
            
            if self.current_point:
                fld_logger.info(f"Pin location {self.state}: {self.current_point.x:.2f}, {self.current_point.y:.2f}, {self.current_point.z:.2f}")
            return True
        except Exception as e:
            fld_logger.exception(f"handle_click error: {e}")
            return False

    def handle_move(self, event_dict):
        if getattr(self, "state", 0) == 0:
            # Idle hover: dynamic snapping to surfaces/planes
            self.current_point = self._resolve_wp_click(event_dict)
            self.on_move_state_0(event_dict)
            self.update_preview()
            self.update_ui()
            
        elif self.state == 1:
            self.current_point = self.get_mouse_plane_pt(event_dict)
            self.on_move_state_1(event_dict)
            self.update_preview()
            self.update_ui()
            
        elif self.state == 2:
            self.on_move_state_2(event_dict)
            self.update_preview()
            self.update_ui()

    def on_move_state_0(self, event_dict):
        """Hook for subclasses to update internal parameters in state 0."""
        pass

    def on_move_state_1(self, event_dict):
        """Hook for subclasses to update internal parameters in state 1."""
        pass

    def on_move_state_2(self, event_dict):
        """Hook for subclasses to update internal parameters in state 2."""
        pass

    def on_state_change(self, new_state):
        self.update_ui()
        FreeCADGui.updateGui()

    def to_local(self, p):
        if p is None:
            return None
        if not hasattr(self, "working_plane") or not self.working_plane:
            return p
        mat = self.working_plane.toMatrix()
        mat.invert()
        return mat.multVec(p)

    def to_global(self, p):
        if p is None:
            return None
        if not hasattr(self, "working_plane") or not self.working_plane:
            return p
        return self.working_plane.toMatrix().multVec(p)


# ─────────────────────────────────────────────────────────────────────────────
# NURBSPrimitiveCreator
# ─────────────────────────────────────────────────────────────────────────────

class NURBSPrimitiveCreator(FldBase):
    """
    Base for creators that produce a Fields object.
    Uses the actual FldObject for real-time feedback.
    """

    def __init__(self):
        super().__init__()
        self._active_obj = None    # The FldObject being created/edited
        self._finished = False     # Guard for finalization

        self._last_shape_type = None
        self._last_shape_params = None
        self._last_placement = None
        self._preview_cursor = None # Crosshair object

    # ------------------------------------------------------------------
    # Active Object Updates
    # ------------------------------------------------------------------

    def update_active_object(self, shape_type, params, placement=None):
        """
        Updates the active Fields object or creates it if it doesn't exist.
        """
        if self._terminated:
            return

        self._last_shape_type = shape_type
        self._last_shape_params = params
        
        # Track placement
        active_placement = placement
        if not active_placement and hasattr(self, "working_plane"):
            active_placement = self.working_plane
        self._last_placement = active_placement
        
        # Map world coords to local space if using a placement
        local_params = dict(params)
        if active_placement:
            # Helper to map a point or list of points
            def map_p(obj):
                if obj is None:
                    return None
                if isinstance(obj, (list, tuple)):
                    return [self.to_local(p) for p in obj]
                if hasattr(obj, "x"): # It's a Vector
                    return self.to_local(obj)
                return obj

            for k in ["Position", "Points", "HandleIn", "HandleOut"]:
                if k in local_params and local_params[k] is not None:
                    local_params[k] = map_p(local_params[k])

        # Create or update
        if self._active_obj is None:
            # Guard against re-entrant calls. The updateGui() this named has since
            # moved out of create_fld_object into finalize_new_object(), which this
            # path never calls (IF-016), so the original trigger is gone; the guard
            # stays because nothing here proves it was the only one.
            if getattr(self, "_creating_obj", False):
                return
            self._creating_obj = True
            try:
                from freecad.fields.core.objects.fld_object import create_fld_object
                self._active_obj = create_fld_object("FldObject", shape_type, local_params, placement=active_placement)
                if self._active_obj:
                    self._active_obj.Label = shape_type.capitalize()
            finally:
                self._creating_obj = False
        else:
            # Update properties
            for k, v in local_params.items():
                if hasattr(self._active_obj, k):
                    try:
                        setattr(self._active_obj, k, v)
                    except Exception as e:
                        fld_logger.debug(f"DEBUG: Failed to update property {k}: {e}")
                elif k == "Position" and placement is None:
                    self._active_obj.Placement.Base = v
            
            if active_placement:
                self._active_obj.Placement = active_placement

        # Both branches: create_fld_object() no longer recomputes (IF-016), so the
        # freshly created object needs the same single-object recompute the update
        # branch always did.
        if self._active_obj is not None:
            self._active_obj.touch()
            if self.doc:
                self.doc.recompute([self._active_obj])

        # Note: Legacy DM_Cursor (Part::Feature) has been removed in favor of 
        # more efficient Coin3D overlays or can be re-implemented as a pure 
        # view-side node if needed.
        pass




