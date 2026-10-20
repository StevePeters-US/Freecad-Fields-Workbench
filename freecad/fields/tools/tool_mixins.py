# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Tool mixins and polling helpers.

Split out of tools/fld_base.py (ST-008). Provides QTimer-based drag polling
and performance metrics logging for interactive tools.
"""
import FreeCAD
import FreeCADGui
from PySide import QtCore
from freecad.fields.core import fld_logger
from freecad.fields.core.input.input_manager import FldInputManager
from freecad.fields.core.input.fld_menu_tree import MenuItem, SEPARATOR
from freecad.fields.core.render.field_appearance import DEFAULT_ADDITIVE_COLOR

HANDLE_TYPE_COLORS = {
    "vertex": (1.0, 1.0, 1.0),
    "FREE": DEFAULT_ADDITIVE_COLOR,
    "ALIGNED": (0.3, 0.6, 1.0),
    "LINKED": (0.6, 0.6, 0.6),
    0: DEFAULT_ADDITIVE_COLOR,  # FREE
    1: (0.6, 0.6, 0.6),        # LINKED
    2: (0.3, 0.6, 1.0),        # ALIGNED
}


class DragTimerMixin:
    """Consolidated QTimer-based polling for tool dragging with timing metrics."""
    def _start_drag_timer(self, interval_ms=None):
        import time
        self._stop_drag_timer()
        self._drag_session_ticks = 0
        self._drag_session_start_t = time.perf_counter()
        self._drag_session_total_update_time = 0.0
        self._drag_session_metrics = {}
        try:
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer, _RS_INTERACTIVE
            FldSceneVoxelRenderer.get_instance()._render_state = _RS_INTERACTIVE
            FldSceneVoxelRenderer.get_instance()._drag_session_rebuild_time = 0.0
            FldSceneVoxelRenderer.get_instance()._perf.session_reset()
            from freecad.fields.core.sdf import field_eval
            field_eval.eval_stats_reset()
            from freecad.fields.core.fld_settings import get_simplify_cage_drag
            simplify = get_simplify_cage_drag()
            eff_quad_iters = 4 if simplify else 6
            fld_logger.debug(f"Drag started: setting interactive render state (effective u_cage_quad_iters = {eff_quad_iters})")
        except Exception as e:
            fld_logger.debug(f"Failed to set interactive render state: {e}")

        self._drag_timer = QtCore.QTimer()
        self._drag_timer.timeout.connect(self._drag_update_wrapper)
        if interval_ms is None:
            from freecad.fields.core.fld_settings import get_drag_tick_rate
            hz = get_drag_tick_rate()
            hz = max(1, min(120, hz))
            interval_ms = int(1000 / hz)
        self._drag_timer.start(interval_ms)
        self._is_dragging = True

    def _stop_drag_timer(self):
        if getattr(self, "_is_dragging", False):
            self._log_drag_session_summary()
        if hasattr(self, "_drag_timer") and self._drag_timer:
            self._drag_timer.stop()
            self._drag_timer = None
        self._is_dragging = False

        try:
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer, _RS_QUALITY
            FldSceneVoxelRenderer.get_instance()._render_state = _RS_QUALITY
            view = FreeCADGui.activeView()
            if view:
                view.redraw()
        except Exception as e:
            fld_logger.debug(f"Failed to restore quality render state: {e}")

    def _drag_update_wrapper(self):
        import time
        t_start = time.perf_counter()
        self._drag_update()
        duration = time.perf_counter() - t_start
        self._drag_session_ticks = getattr(self, "_drag_session_ticks", 0) + 1
        self._drag_session_total_update_time = getattr(self, "_drag_session_total_update_time", 0.0) + duration

    def _log_drag_session_summary(self):
        import time
        ticks = getattr(self, "_drag_session_ticks", 0)
        if ticks == 0:
            return
        total_time = time.perf_counter() - getattr(self, "_drag_session_start_t", time.perf_counter())
        avg_update = getattr(self, "_drag_session_total_update_time", 0.0) / ticks
        
        rebuild_time = 0.0
        avg_rebuild = 0.0
        render_stages = {}
        render_counts = {}
        render_frames = 0
        try:
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
            sr = FldSceneVoxelRenderer.get_instance()
            rebuild_time = getattr(sr, "_drag_session_rebuild_time", 0.0)
            avg_rebuild = rebuild_time / ticks
            render_stages, render_counts, render_frames = sr._perf.session_snapshot()
        except Exception as e:
            fld_logger.debug(f"DragTimerMixin._log_drag_session_summary: Failed to get rebuild time: {e}")

        metrics = getattr(self, "_drag_session_metrics", {})
        fps = ticks / total_time if total_time > 0 else 0.0

        lines = [
            f"{self.__class__.__name__} Drag completed. Ticks: {ticks} | Total Session Time: {total_time*1000:.1f}ms | Avg Framerate: {fps:.1f} FPS",
            "┌──────────────────────┬─────────────┬─────────────┐",
            "│ Step / Function      │ Total Time  │ Avg Time    │",
            "├──────────────────────┼─────────────┼─────────────┤"
        ]

        candidate_order = [
            "get_mouse_pos",
            "mutate_cage",
            "commit_field",
            "rebuild",
            "redraw_view",
            "update_handles",
            "uh_vec_conv",
            "uh_set_points",
            "uh_coin_sync",
            "uh_colors",
        ]
        steps = []
        for key in candidate_order:
            if key == "rebuild":
                if rebuild_time > 0:
                    steps.append(("rebuild", rebuild_time))
            elif key in metrics:
                indent = "  " if key.startswith("uh_") else ""
                display_name = f"{indent}{key}"
                steps.append((display_name, metrics[key]))

        for key, val in metrics.items():
            if key not in candidate_order and key != "ticks":
                steps.append((key, val))

        for name, duration in steps:
            avg = duration / ticks if ticks > 0 else 0.0
            lines.append(f"│ {name:<20} │ {duration*1000:>9.1f}ms │ {avg*1000:>9.2f}ms │")

        lines.append("├──────────────────────┼─────────────┼─────────────┤")
        lines.append(f"│ total update         │ {getattr(self, '_drag_session_total_update_time', 0.0)*1000:>9.1f}ms │ {avg_update*1000:>9.2f}ms │")
        lines.append("└──────────────────────┴─────────────┴─────────────┘")

        lines.extend(self._render_session_lines(
            render_stages, render_counts, render_frames, total_time))
        fld_logger.info("\n" + "\n".join(lines))

    def _render_session_lines(self, stages, counts, frames, total_time):
        """The other side of the drag budget: the render callback."""
        if not frames:
            return []
        cb_total = stages.get("callback_total", 0.0)
        out = [
            f"Render callback: {frames} frame(s) over {total_time*1000:.1f}ms "
            f"— {cb_total*1000:.1f}ms inside the callback "
            f"({cb_total/total_time*100 if total_time > 0 else 0.0:.0f}% of the session)",
            "┌──────────────────────┬─────────────┬─────────────┬────────┐",
            "│ Render stage         │ Total Time  │ Avg/render  │ Calls  │",
            "├──────────────────────┼─────────────┼─────────────┼────────┤",
        ]
        # "volume_bake" was here until 2026-08-10 and nothing has ever written
        # it -- the counter is "voxel_bake" -- so the single most expensive
        # stage of an extrude drag was silently absent from this table.
        for name in ("inherited_queue", "callback_total", "tex3d_upload", "ssbo_pack",
                     "voxel_bake", "voxel_bake_full", "voxel_bake_region",
                     "pass1_dispatch", "pass2_ssao", "pass3_blur",
                     "pass4_composite", "rebuild_total"):
            t = stages.get(name, 0.0)
            n = counts.get(name, 0)
            if n == 0:
                continue
            out.append(f"│ {name:<20} │ {t*1000:>9.1f}ms │ "
                       f"{t/frames*1000:>9.2f}ms │ {n:>6} │")
        out.append("└──────────────────────┴─────────────┴─────────────┴────────┘")
        other = total_time - cb_total - getattr(self, "_drag_session_total_update_time", 0.0)
        pct = (other / total_time * 100.0) if total_time > 0 else 0.0
        out.append(f"Outside callback and ticks: {other*1000:.1f}ms ({pct:.0f}% of session) — Qt idle, event handling")

        try:
            from freecad.fields.core.sdf import field_eval
            out.append("CPU field evaluation this session: "
                       + field_eval.eval_stats_line())
            by_field = field_eval.eval_stats_snapshot()["by_field"]
            for cls, (calls, points, seconds) in sorted(
                    by_field.items(), key=lambda kv: -kv[1][2])[:5]:
                out.append(f"    {cls:<32} {seconds*1000:>8.1f}ms  "
                           f"{calls:>5} call(s)  {points:>9} pts")
        except Exception as e:
            fld_logger.debug(f"DragTimerMixin: field eval stats unavailable: {e}")
        return out

    def _drag_update(self):
        """Override in subclasses."""
        pass

    def _drag_check_lmb_released(self):
        """Returns True if LMB was released, stopping the timer and resetting state."""
        if not FldInputManager.get_instance().is_left_mouse_down():
            self._stop_drag_timer()
            from freecad.fields.tools.fld_base import ToolState
            self.state = getattr(self, '_drag_return_state', ToolState.IDLE)
            if hasattr(self, "_dragging_idx"): self._dragging_idx = None
            
            # Trigger final full update if supported (e.g. PrimitiveCreatorBase)
            if hasattr(self, "_do_full_preview_update"):
                self._do_full_preview_update()
            
            return True
        return False


class ControlCageDrawMixin:
    """SoSphere handle + SoLineSet cage overlay for NURBS-style tools.

    Moved verbatim out of FldBase (ST-008). `_compute_handle_radius` has callers in eight other
    tool modules, all of them FldBase subclasses calling it through self.
    """

    def _compute_handle_radius(self, ref_pt=None):
        """Sphere radius in world units — sized to look ~8 px on screen."""
        try:
            cam = self.view.getCameraNode()
            viewer = self.view.getViewer()
            vp_h = 800.0
            try:
                if hasattr(viewer, "getGlxSize"):
                    sz = viewer.getGlxSize(); vp_h = float(sz[1])
                elif hasattr(viewer, "getSize"):
                    sz = viewer.getSize()
                    vp_h = float(sz[1] if isinstance(sz, (list, tuple)) else sz.height())
            except Exception as e:
                fld_logger.debug(f"FldBase._compute_handle_radius: Failed to get viewport size: {e}")
            if hasattr(cam, "height"):
                half_world_h = cam.height.getValue() / 2.0
            elif hasattr(cam, "heightAngle"):
                cam_vals = cam.position.getValue()
                cam_pos_v = FreeCAD.Vector(cam_vals[0], cam_vals[1], cam_vals[2])
                ref = ref_pt if ref_pt is not None else FreeCAD.Vector(0, 0, 0)
                depth = (ref - cam_pos_v).Length
                half_world_h = depth * math.tan(cam.heightAngle.getValue() / 2.0)
            else:
                half_world_h = 100.0
            px_per_world = (vp_h / 2.0) / max(half_world_h, 1e-6)
            from freecad.fields.core.fld_settings import get_point_size
            pt_sz = get_point_size()
            return max(0.01, (pt_sz * 1.33) / px_per_world)
        except Exception as e:
            fld_logger.debug(f"FldBase._compute_handle_radius failed: {e}")
            return 5.0

    def _draw_control_cage(self, points, n_verts, handle_types=None, edges=None):
        """Draw control points (vertices) and handles in the viewport, and connect them with lines."""
        from freecad.fields.core.objects.fld_point import FldPoint
        from freecad.fields.core.objects.fld_line import FldLineSet
        
        self._n_verts = n_verts
        self.points = list(points)
        
        # Clean up existing points and lines first
        self._clear_control_cage()
        
        self.fld_points = []
        r = self._compute_handle_radius()
        
        for i, pt in enumerate(self.points):
            is_vert = i < self._n_verts
            fld_pt = FldPoint(pt)
            if is_vert:
                pt_color = HANDLE_TYPE_COLORS["vertex"]
            else:
                hi = i - self._n_verts
                ht = handle_types[hi] if (handle_types and hi < len(handle_types)) else 1
                pt_color = HANDLE_TYPE_COLORS.get(ht, HANDLE_TYPE_COLORS["LINKED"])
            fld_pt.draw_point(self.points_root, r * (1.0 if is_vert else 0.6), color=pt_color)
            self.fld_points.append(fld_pt)
            
        # Draw lines to handles
        self._handle_lines = FldLineSet(self.points_root, color=HANDLE_TYPE_COLORS["LINKED"], width=1.0, pattern=0x0F0F)
        self._update_handle_lines(edges)

    def _update_point_colors(self, handle_types):
        """Update point and handle colors based on their current types."""
        if not hasattr(self, "fld_points") or not self.fld_points:
            return
        for i in range(len(self.points)):
            is_vert = i < self._n_verts
            if is_vert:
                pt_color = HANDLE_TYPE_COLORS["vertex"]
            else:
                hi = i - self._n_verts
                ht = handle_types[hi] if (handle_types and hi < len(handle_types)) else 1
                pt_color = HANDLE_TYPE_COLORS.get(ht, HANDLE_TYPE_COLORS["LINKED"])
            if i < len(self.fld_points):
                self.fld_points[i].set_color(pt_color)

    def _update_handle_lines(self, edges):
        """Refresh lines connecting vertices to their handles."""
        if not hasattr(self, "_handle_lines") or not self._handle_lines or not edges:
            return
        
        line_pts = []
        segments = []
        for hi in range(24):
            ei = hi // 2
            if ei >= len(edges):
                continue
            owner_vi = edges[ei][0] if (hi % 2 == 0) else edges[ei][1]
            h_idx = self._n_verts + hi
            if owner_vi < len(self.points) and h_idx < len(self.points):
                v_pos = self.points[owner_vi]
                h_pos = self.points[h_idx]
                if (h_pos - v_pos).Length > 1e-4:
                    line_pts.append(v_pos)
                    line_pts.append(h_pos)
                    segments.append(2)
        
        self._handle_lines.update_lines(line_pts, segments)

    def _clear_control_cage(self):
        """Undraw and clear control points and handle connection lines."""
        if hasattr(self, "fld_points") and self.fld_points:
            for fld_pt in self.fld_points:
                try:
                    fld_pt.undraw()
                except Exception as e:
                    fld_logger.debug(f"FldBase._clear_control_cage: Failed to undraw point: {e}")
            self.fld_points = []

        if hasattr(self, "_handle_lines") and self._handle_lines:
            try:
                self._handle_lines.undraw()
            except Exception as e:
                fld_logger.debug(f"FldBase._clear_control_cage: Failed to undraw handle lines: {e}")
            self._handle_lines = None


class SnapPreferencesMixin:
    """Snap toggles + increment menus, shared by every tool's RMB menu and the S menu.

    Split out of FldBase (CR-063) following the same pattern ControlCageDrawMixin
    already used in this file. Lives here rather than on
    PrimitiveCreatorBase because the tools that most need to change an increment
    mid-transform -- Translate, Rotate, Scale -- are FldBase subclasses and had no
    snapping menu at all before this existed. FldBase.get_context_menu() still calls
    self.get_snapping_menu() -- unchanged, since mixin inheritance resolves it the same
    way a same-class method would.
    """

    def get_snapping_menu(self):
        """Snap toggles + increments. Subclasses with projection modes prepend their own."""
        return self.get_snap_pref_menu()

    def get_snap_pref_menu(self):
        """The three on/off toggles and the two increment submenus.

        Snapping applies while Ctrl is held; these toggles choose what it snaps to.
        Grid/Vertices/Angle are independent -- turning one off leaves the others live.
        """
        from freecad.fields.core.fld_settings import get_snap_step, get_snap_vertex_enabled
        return [
            MenuItem("Snap to Grid", action=lambda: self._toggle_snap_pref('grid'),
                     checked=get_snap_step('grid') > 0),
            MenuItem("Grid Step", children=self._build_snap_step_menu('grid')),
            MenuItem("Snap to Vertices", action=lambda: self._toggle_snap_pref('vertex'),
                     checked=get_snap_vertex_enabled()),
            MenuItem("Snap to Angle", action=lambda: self._toggle_snap_pref('angle'),
                     checked=get_snap_step('angle') > 0),
            MenuItem("Angle Step", children=self._build_snap_step_menu('angle')),
            MenuItem("Snap to Scale", action=lambda: self._toggle_snap_pref('scale'),
                     checked=get_snap_step('scale') > 0),
            MenuItem("Scale Step", children=self._build_snap_step_menu('scale')),
        ]

    def _build_snap_step_menu(self, kind):
        """Preset increments for 'grid' (mm), 'angle' (degrees) or 'scale', plus a Custom entry.

        The current value is checked. If it is not one of the presets -- set from the
        settings dialog or from Custom -- it is added to the list so the menu always
        shows what is actually in force rather than silently checking nothing.
        """
        from freecad.fields.core.fld_settings import get_snap_step
        from freecad.fields.core.input import fld_snap
        cur = get_snap_step(kind)
        fmt = fld_snap.SNAP_STEP_FORMATS[kind]
        values = list(fld_snap.SNAP_STEP_PRESETS[kind])
        if cur > 0 and not any(abs(cur - v) < 1e-9 for v in values):
            values = sorted(values + [cur])
        items = [MenuItem(fmt.format(v),
                          action=lambda _v=v: self._set_snap_step(kind, _v),
                          checked=abs(cur - v) < 1e-9)
                 for v in values]
        items.append(SEPARATOR)
        items.append(MenuItem("Custom\u2026", action=lambda: self._prompt_snap_step(kind)))
        return items

    def _set_snap_step(self, kind, value):
        """Set a snap increment. Any non-zero value also turns that snap on,
        because `step > 0` IS the enabled state -- there is no separate flag to set."""
        from freecad.fields.core.fld_settings import set_snap_step
        set_snap_step(kind, value)
        if getattr(self, "view", None):
            self.view.redraw()

    def _prompt_snap_step(self, kind):
        """Ask for an increment outside the preset list. 0 disables that snap."""
        from PySide import QtWidgets
        from freecad.fields.core.fld_settings import get_snap_step
        title, label, hi, dec = {
            'grid': ("Grid Step", "Translation increment (mm), 0 to disable:", 1000.0, 3),
            'angle': ("Angle Step", "Rotation increment (degrees), 0 to disable:", 180.0, 2),
            'scale': ("Scale Step", "Scale increment (factor), 0 to disable:", 10.0, 3),
        }[kind]
        cur = get_snap_step(kind)
        try:
            val, ok = QtWidgets.QInputDialog.getDouble(
                FreeCADGui.getMainWindow(), title, label, float(cur), 0.0, hi, dec)
        except Exception as e:
            fld_logger.error(f"Snap step dialog failed: {e}")
            return
        if ok:
            self._set_snap_step(kind, val)

    def _toggle_snap_pref(self, kind):
        """Toggle grid/vertex/angle/scale snapping; the last non-zero step is a
        parameter (INP-023), so it survives off and on."""
        from freecad.fields.core.fld_settings import (
            get_snap_step, set_snap_step, get_last_snap_step,
            get_snap_vertex_enabled, set_snap_vertex_enabled,
        )
        if kind == 'vertex':
            set_snap_vertex_enabled(not get_snap_vertex_enabled())
        elif get_snap_step(kind) > 0:
            set_snap_step(kind, 0.0)
        else:
            set_snap_step(kind, get_last_snap_step(kind))
