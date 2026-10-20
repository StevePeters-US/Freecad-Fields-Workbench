# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Base classes for Fields interactive tools.

Event Ownership Contract (Single-Owner Qt-Native)
================================================
- Qt filter (FldInputManager): owns all events — state tracking, global hotkeys,
  viewport detection, and dispatch to the active tool via on_mouse_press/move/etc.
- Tool methods (on_button1_down, handle_move, on_key_press, …): ALL tool logic.
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
from freecad.fields.core.input.fld_menu_tree import MenuItem, SEPARATOR

# ─────────────────────────────────────────────────────────────────────────────
# DragTimerMixin (re-exported from freecad.fields.tools.tool_mixins)
# ─────────────────────────────────────────────────────────────────────────────

from freecad.fields.tools.tool_mixins import (  # noqa: F401
    DragTimerMixin, ControlCageDrawMixin, SnapPreferencesMixin,
)


# ─────────────────────────────────────────────────────────────────────────────
# PrimitiveCreatorBase
# ─────────────────────────────────────────────────────────────────────────────

from enum import IntEnum


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


class FldBase(DragTimerMixin, ControlCageDrawMixin, SnapPreferencesMixin):
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
        self.panel = None
        self._dialog_open = False
        self._finish_scheduled = False

        self._cursor_active = False
        self._last_btn3_time = 0.0
        self._update_pending = False
        self._pending_callback = None

        self._is_editing = False

        self._snap_guide = None        # FldSnapGuide; primitives draw one, _do_terminate removes it

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

        Enforces the Workplane Fallback Rule: a plane the tool merely
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
            from freecad.fields.core.fld_settings import get_interactive_throttle_interval
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
                    FreeCADGui.updateGui()
                    return

            FreeCADGui.updateGui()
                    
        except Exception as e:
            fld_logger.exception(f"{type(self).__name__}: entering edit mode failed")
            self._is_editing = False
            self.terminate()
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
            fld_logger.exception(f"{type(self).__name__}: selecting the workplane failed")

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
                gui_doc = FreeCADGui.getDocument(self.doc.Name)
                FreeCADGui.Control.closeDialog(gui_doc)
                self._dialog_open = False

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

            FreeCADGui.updateGui()

        except Exception as e:
            fld_logger.debug(f"FldBase._do_terminate: Cleanup failed: {e}")

    def _apply_snap(self, pt, event_dict, *, origin=None, axis_unit_vec=None,
                    frame_axes=None, extra_points=(), exclude=()):
        """Quantize `pt`. Vertex/origin/surface candidates win over the grid.

        `origin` + `axis_unit_vec` make this snap the DELTA along a locked axis
        rather than the absolute point -- which is what a lock means: the result
        must stay on the axis, and quantizing x/y/z independently would leave it.
        `origin` + `frame_axes` (three orthonormal vectors, e.g. a working plane's)
        quantize the delta in that frame instead of in world X/Y/Z, so a grid on a
        rotated plane lands on the plane's own grid lines.
        """
        from freecad.fields.core.input import fld_snap
        from freecad.fields.core.fld_settings import get_snap_grid_step

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
            elif frame_axes is not None:
                snapped = FreeCAD.Vector(origin)
                for ax in frame_axes:
                    snapped = snapped + ax * fld_snap.snap_length(delta.dot(ax), step)
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

        Order: axis override -> projection (explicit plane, else workplane, else camera plane)
        -> snap -> guide. Every tool's drag path goes through here; nothing else in the
        workbench may call fld_snap directly.

        allow_snap=False is for paths that are already quantised by something else,
        not for opting a tool out -- Ctrl gates snapping, in _apply_snap().
        """
        from freecad.fields.core.input.input_manager import FldInputManager
        new_pt = None

        axis_override = getattr(self, '_drag_axis_override', None)
        if axis_override is not None:
            if base_pt is not None:
                new_pt = FldInputManager.get_instance().get_axis_point(
                    self.view, base_pt, axis_override, event_dict)
                if show_guide and getattr(self, "_snap_guide", None) and self.view:
                    self._snap_guide.show_axis(base_pt, axis_override, self.view)

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
            snap_origin = base_pt if axis_override is not None else None
            snap_axis = axis_override
            ex_pts = extra_points if extra_points is not None else getattr(self, "points", [])
            excl = exclude if exclude is not None else ([self._preview_obj] if getattr(self, "_preview_obj", None) else [])
            new_pt = self._apply_snap(
                new_pt, event_dict,
                origin=snap_origin,
                axis_unit_vec=snap_axis,
                extra_points=ex_pts,
                exclude=excl,
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

    # ------------------------------------------------------------------
    # Event loop & Overridable Input Hooks
    # ------------------------------------------------------------------

    def on_mouse_press(self, event_dict):
        btn = event_dict.get("Button")
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

    def on_mouse_double_click(self, event_dict):
        btn = event_dict.get("Button")
        if btn == QtCore.Qt.LeftButton:
            return self.on_button1_double_click(event_dict)
        return False

    def on_button1_double_click(self, event_dict):
        return False

    def on_mouse_move(self, event_dict):
        self.handle_move(event_dict)

    def on_key_press(self, event_dict):
        """A key runs the tool menu's leaf whose shortcut it is (INP-B07). True = consumed."""
        from freecad.fields.core.input import fld_shortcuts
        item, matched = fld_shortcuts.find(self.get_context_menu(event_dict) or [], event_dict)
        if item is not None and item.enabled:
            item.action()
        return matched

    def on_key_release(self, event_dict):
        return False

    def on_ctrl_wheel(self, notches):
        """Ctrl + wheel: step the grid snap. A tool driving a GizmoController overrides
        this to step the increment of its active handle instead (universal_gizmo)."""
        from freecad.fields.core.input import fld_snap
        new = fld_snap.step_snap("grid", notches)
        try:
            FreeCADGui.getMainWindow().statusBar().showMessage(
                fld_snap.snap_step_label("grid", new), 2000)
        except Exception as e:
            fld_logger.debug(f"on_ctrl_wheel: status message failed: {e}")

    def on_context_menu(self, event_dict):
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
            items += [MenuItem("Snapping", children=self.get_snapping_menu()), SEPARATOR]
        # Same parent-class entry points as the task panel (accept()/reject())
        items += [
            MenuItem("Accept", action=self.finish, shortcut="Return"),
            MenuItem("Cancel", action=self.cancel, shortcut="Esc"),
        ]
        if not getattr(self, "_is_editing", False):
            items.append(MenuItem("Reset", action=self.reset_state))
        items += [
            SEPARATOR,
            MenuItem("Auto Repeat", action=self.toggle_autorepeat, checked=self.autorepeat),
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




