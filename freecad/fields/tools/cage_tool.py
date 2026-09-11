# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from PySide import QtCore, QtWidgets
from pivy import coin
import numpy as np

from freecad.fields.tools.fld_sdf_tool_base import FldSdfToolBase
from freecad.fields.tools.fld_base import DragTimerMixin
from freecad.fields.tools.tool_mixins import HANDLE_TYPE_COLORS
from freecad.fields.core.objects.fld_point import FldPoint
from freecad.fields.core.objects.fld_line import FldLineSet
from freecad.fields.core.input.input_manager import FldInputManager
from freecad.fields.core import fld_logger
from freecad.fields.core import fld_perf
from freecad.fields.core.sdf.sdf_face_extrude import newell_normal
from freecad.fields.core.objects.fld_face_extrude_objects import ring_handles, ring_rest_handles, carry_ring_handles_to_cap, cage_net_of
from freecad.fields.core.objects.fld_deform_objects import push_extrusion, set_last_extrusion_top, pop_extrusion
from freecad.fields.core.sdf.sdf.cage import CageTopology, SdfCageField, MAX_CAGE_FACES, _closest_on_face, _closest_on_tri_face, rebuild_handles_and_types, remap_handle_displacements
from freecad.fields.core.sdf.sdf.cage_deform import SdfCageDeformField, HandleType
from freecad.fields.core.sdf.sdf.cage_net import CageNet, read_net, write_net
from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
from freecad.fields.tools.extrude_gizmo import ExtrudeGizmo

class CageTaskPanel:
    def __init__(self, tool):
        self.tool = tool
        self.form = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.form)
        layout.setContentsMargins(10, 10, 10, 10)
        
        label = QtWidgets.QLabel(
            "<b>Cage Editing Controls:</b><br><br>"
            "• Drag <b>Vertices</b> (white in viewport) to reshape.<br>"
            "• Drag or Right-click <b>Handles</b> to change control:<br><br>"
            "<b>Handle Types:</b><br>"
            "• <span style='color: #888888;'><b>LINKED (grey)</b></span>: Tracks vertex movement.<br>"
            "• <span style='color: #ff8000;'><b>FREE (orange)</b></span>: Moves independently (broken link).<br>"
            "• <span style='color: #0088ff;'><b>ALIGNED (blue)</b></span>: Constrains tangent plane.<br><br>"
            "<b>Hotkeys:</b><br>"
            "• <b>E</b>: Extrude face under mouse<br>"
            "• <b>Ctrl + R</b>: Insert loop at edge under mouse<br>"
            "• <b>M</b>: Weld last clicked vertex to hovered vertex<br>"
            "• <b>Ctrl + D</b>: Subdivide Smooth"
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addSpacing(15)
        
        self.subdivide_btn = QtWidgets.QPushButton("Subdivide Smooth")
        self.subdivide_btn.clicked.connect(self._on_subdivide)
        layout.addWidget(self.subdivide_btn)
        
        layout.addStretch()

    def _on_subdivide(self):
        self.tool.trigger_subdivide()

    def accept(self):
        self.tool._dialog_open = False
        self.tool.finish()
        return True

    def reject(self):
        self.tool._dialog_open = False
        self.tool.cancel()
        return True


class CageEditTool(FldSdfToolBase, DragTimerMixin):
    """Interactive edit tool for deform cage control nets (vertices and handles)."""

    def get_command_id(self):
        """Return FreeCAD command identifier string."""
        return "Fields_EditObject"

    def get_handled_types(self):
        """Return handled document object types."""
        return ["sdf"]

    def update_preview(self):
        """Update live preview during tool active state."""
        pass

    def _do_full_preview_update(self, field=None):
        """Called on drag release by DragTimerMixin to commit final high-res state."""
        self._commit_changes(final=True)

    def __init__(self):
        """Initialize CageEditTool and Coin3D scene graph nodes."""
        super().__init__()
        self._target_obj = None
        self._is_editing = False
        self.fld_points = []
        self.points_root = coin.SoAnnotation() if (coin and hasattr(coin, "SoAnnotation")) else None
        if self.points_root and self.view and self.view.getSceneGraph():
            self.view.getSceneGraph().addChild(self.points_root)
        
        self._dragging_idx = None
        self._modal_sel_idx = None
        self._drag_plane_n = None
        self._drag_plane_o = None
        self._original_props = {}
        self._edge_curves = None
        self._handle_arms = None
        self._last_mesh_time = 0.0
        self._dirty_faces = set()
        self._dirty_box = None
        self._previous_mesh = None
        self.extrude_gizmo = None

    def _target_is_cage_field(self):
        """True when the target's field owns cage arrays we may mutate in place."""
        field = getattr(getattr(self._target_obj, "Proxy", None), "SdfField", None)
        return field is not None and hasattr(field, "move_vertex")

    def edit_object(self, obj):
        """Attach CageEditTool to target document object and build visual handles."""
        super().edit_object(obj)
        self._target_obj = obj
        self._is_editing = True
        
        proxy = getattr(obj, "Proxy", None)
        self._original_props = {
            "Net": CageNet.from_properties(obj),
            "Points": list(getattr(obj, "Points", [])),
            "SdfField": getattr(proxy, "SdfField", None),
            "Placement": FreeCAD.Placement(obj.Placement.Base, obj.Placement.Rotation) if obj.Placement else None,
        }

        from freecad.fields.core.objects.fld_face_extrude_objects import cage_net_of
        net = cage_net_of(obj)
        if net is None or not hasattr(net, "_edges"):
            fld_logger.error("CageEditTool: target object has no cage net")
            self.terminate()
            return
        n_handles = 2 * len(net._edges)

        if hasattr(obj, "HandleTypes") and obj.HandleTypes:
            h_types = list(obj.HandleTypes)
        elif hasattr(net, "_handle_types"):
            h_types = list(net._handle_types)
        else:
            h_types = [1] * n_handles

        field = proxy.SdfField if proxy else None
        if field and hasattr(field, "_handle_types"):
            field._handle_types = h_types
            # Initialize hot faces on edit start
            mouse_pos = FldInputManager.get_instance()._last_qt_pos
            fi = None
            if mouse_pos:
                fi = self._get_face_under_mouse({"Position": mouse_pos})
            if fi is None:
                fi = 0
            field.hot_faces = frozenset([fi])
            field.geometry_version = getattr(field, "geometry_version", 0)

        # obj.Points stores world-space coords (cage uses placement=None).
        local_pts = list(getattr(obj, "Points", []))

        if len(local_pts) < n_handles + 3:
            fld_logger.error(f"CageEditTool: too few points on cage object: len(local_pts)={len(local_pts)}, n_handles={n_handles}")
            self.terminate()
            return

        self._n_verts = len(local_pts) - n_handles
        self.points = [FreeCAD.Vector(p.x, p.y, p.z) for p in local_pts]

        # Draw handles
        r = self._compute_handle_radius()
        self.fld_points = []
        field = proxy.SdfField if proxy else None
        for i, pt in enumerate(self.points):
            is_vert = i < self._n_verts
            fld_pt = FldPoint(pt)
            if is_vert:
                pt_color = HANDLE_TYPE_COLORS["vertex"]
            else:
                hi = i - self._n_verts
                ht = field._handle_types[hi] if (field and hasattr(field, "_handle_types") and hi < len(field._handle_types)) else 1
                pt_color = HANDLE_TYPE_COLORS.get(ht, HANDLE_TYPE_COLORS["LINKED"])
            fld_pt.draw_point(self.points_root, r * (1.0 if is_vert else 0.6), color=pt_color)
            self.fld_points.append(fld_pt)

        # Initialize line sets for curves and arms
        self._edge_curves = FldLineSet(self.points_root, color=(0.0, 0.8, 1.0), width=2.0)
        self._handle_arms = FldLineSet(self.points_root, color=(0.6, 0.6, 0.6), width=1.2, pattern=0x0F0F)
        self._update_lines()

    def _post_init(self):
        super()._post_init()
        if not getattr(self, "_terminated", False) and self._is_editing:
            self.panel = CageTaskPanel(self)
            FreeCADGui.Control.showDialog(self.panel)
            self._dialog_open = True
            if self._target_obj:
                fld_logger.info(f"CageEditTool: editing {self._target_obj.Label}")

    def _faces_touching(self, field, idx):
        """Face indices whose vertex loop contains the control point at idx."""
        if idx < self._n_verts:
            verts = {idx}
        else:
            ei = (idx - self._n_verts) // 2
            if hasattr(field, "_edges") and ei < len(field._edges):
                verts = set(field._edges[ei])
            else:
                verts = set()
        return {fi for fi, face in enumerate(getattr(field, "_face_verts", [])) if verts & set(face)}

    def on_button1_down(self, event_dict):
        btn = event_dict.get("Button")
        if btn != QtCore.Qt.LeftButton:
            return False

        if getattr(self, "_modal_extrusion_active", False):
            self._modal_extrusion_active = False
            self._stop_drag_timer()
            self._dragging_idx = None
            self._commit_extrusion()
            if self.view:
                self.view.redraw()
            return True

        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return True

        r = self._compute_handle_radius()
        
        # Hit test all handles
        best_idx = None
        best_dist = float('inf')
        n_verts = getattr(self, '_n_verts', 8)
        for i, pt in enumerate(self.points):
            tolerance = r * (1.0 if i < n_verts else 0.6)
            idx, dist = self._hit_test_perp(ray_p, ray_d, [pt], tolerance=tolerance)
            if idx is not None and dist < best_dist:
                best_dist = dist
                best_idx = i

        if best_idx is not None:
            self._dragging_idx = best_idx
            self._drag_plane_o = self.points[best_idx]
            self._drag_constraint_base = self.points[best_idx]
            self._dirty_box = None
            self._previous_mesh = None

            # Initialize dirty faces to the faces containing the clicked vertex/handle
            proxy = getattr(self._target_obj, "Proxy", None)
            field = proxy.SdfField if (proxy and hasattr(proxy, "SdfField")) else None
            if field:
                self._dirty_faces = self._faces_touching(field, best_idx)
                field.hot_faces = frozenset(self._dirty_faces)
                label = f"{self._target_obj.Document.Name}.{self._target_obj.Name}"
                FldSceneVoxelRenderer.get_instance().update_field(label, field)

            # Modal G/R/S act on the last control point the user clicked.
            self._modal_sel_idx = best_idx

            # Record last clicked vertex or edge for topology tools fallback
            if best_idx < self._n_verts:
                self._last_clicked_vertex = best_idx
            else:
                self._last_clicked_edge = (best_idx - self._n_verts) // 2
            
            # Setup drag plane normal: default to viewport direction
            vd = self.view.getViewDirection()
            self._drag_plane_n = FreeCAD.Vector(-vd.x, -vd.y, -vd.z).normalize()
            
            # If target obj has placement, try to use its local Z axis
            if self._target_obj and self._target_obj.Placement:
                local_z = self._target_obj.Placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
                if abs(self._drag_plane_n.dot(local_z)) >= 0.15:
                    self._drag_plane_n = local_z
                    
            self._update_constraint_visual()
            self._start_drag_timer()
            return True
        
        return True

    def on_button1_up(self, event_dict):
        self._stop_drag_timer()
        self._clear_constraint_visual()
        if self._dragging_idx is not None:
            self._dragging_idx = None
            self._commit_changes(final=True)
        return True

    def on_mouse_move(self, event_dict):
        # A modal transform is mouse-driven and has no drag timer behind it, so
        # it must reach FldBase's dispatcher.
        if getattr(self, "_modal_transform", None):
            return super().on_mouse_move(event_dict)
        # Cage drag is handled by the timer, not on_mouse_move. Pass all moves
        # through so FreeCAD's navigation always has a current mouse position —
        # without this, orbit/pan jumps because FreeCAD's last known position
        # is stale from before the cage tool was opened.
        return False

    def on_button2_down(self, event_dict):
        # Explicit pass-through so middle mouse orbit/pan is never consumed.
        return False

    def on_button3_down(self, event_dict):
        btn = event_dict.get("Button")
        if btn != QtCore.Qt.RightButton:
            return False

        if getattr(self, "_modal_extrusion_active", False):
            self._cancel_modal_extrusion()
            self._modal_extrusion_active = False
            self._stop_drag_timer()
            self._dragging_idx = None
            self.view.redraw()
            return True

        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            self._handle_clicked = False
            return True

        r = self._compute_handle_radius()
        
        # Hit test handles only (index >= self._n_verts)
        best_idx = None
        best_dist = float('inf')
        for i, pt in enumerate(self.points):
            if i < self._n_verts:
                continue # Skip vertices
            tolerance = r * 0.6
            idx, dist = self._hit_test_perp(ray_p, ray_d, [pt], tolerance=tolerance)
            if idx is not None and dist < best_dist:
                best_dist = dist
                best_idx = i

        if best_idx is not None:
            self._handle_clicked = True
            hi = best_idx - self._n_verts
            obj = self._target_obj
            if obj and obj.Proxy and obj.Proxy.SdfField and self._target_is_cage_field():
                field = obj.Proxy.SdfField
                from freecad.fields.core.sdf.sdf.cage import HandleType
                cur_type = field._handle_types[hi]
                if cur_type == HandleType.LINKED:
                    new_type = HandleType.FREE
                elif cur_type == HandleType.FREE:
                    new_type = HandleType.ALIGNED
                else:
                    new_type = HandleType.LINKED

                field._handle_types[hi] = new_type

                if new_type == HandleType.ALIGNED:
                    ei = hi // 2
                    owner_vi = field._edges[ei][0] if (hi % 2 == 0) else field._edges[ei][1]
                    field.project_aligned_handles(owner_vi)

                    # Sync positions
                    all_verts = [FreeCAD.Vector(p[0], p[1], p[2]) for p in field.vertices]
                    all_handles = [FreeCAD.Vector(p[0], p[1], p[2]) for p in field.handles]
                    self.points = all_verts + all_handles
                    # Batch-writer contract: the live field is the authority here
                    # -- the handle-type cycle above mutated `field._handle_types`
                    # and HandleTypes is not written back. FldDeformCageProxy's
                    # onChanged invalidates SdfField on a Points write, so without
                    # this the field is discarded, the type change is lost to a
                    # rebuild from stale properties, and _commit_changes below
                    # early-returns on the now-None field without committing.
                    obj.Proxy._suspend_rebuild = True
                    try:
                        obj.Points = self.points
                    finally:
                        obj.Proxy._suspend_rebuild = False
                    for j, pt in enumerate(self.points):
                        self.fld_points[j].position = pt
                        self.fld_points[j].update_draw()

                self._update_point_colors(field._handle_types)
                self._update_lines()
                self._commit_changes()
        else:
            self._handle_clicked = False

        # Always consume RMB press. The global filter suppresses the matching release
        # to prevent FreeCAD's context menu. If this returns False, FreeCAD sees the
        # press but never the release (release is swallowed by the global rule), which
        # leaves FreeCAD's navigation stuck in "RMB held" state after the tool closes.
        return True

    def on_context_menu(self, event_dict):
        if getattr(self, "_handle_clicked", False):
            self._handle_clicked = False
            return True
        return super().on_context_menu(event_dict)

    def _accumulate_dirty_box(self, field, faces):
        """Grow self._dirty_box to cover the current AABBs of the given faces."""
        for fi in faces:
            if fi < len(field._face_aabbs):
                bmin, bmax = field._face_aabbs[fi]
                if self._dirty_box is None:
                    self._dirty_box = (bmin.copy(), bmax.copy())
                else:
                    self._dirty_box = (np.minimum(self._dirty_box[0], bmin),
                                       np.maximum(self._dirty_box[1], bmax))

    def _drag_update(self):
        if getattr(self, "_modal_extrusion_active", False):
            # An extrude drag returns here, before the vertex-drag path below,
            # so without its own timing every row of the drag summary reads 0.0
            # for the one interaction we most need to account for.
            import time
            metrics = getattr(self, "_drag_session_metrics", None)
            if metrics is not None:
                metrics["ticks"] = metrics.get("ticks", 0) + 1

            t0 = time.perf_counter()
            pt = self.projector.get_mouse_world_pos(
                {"Position": FldInputManager.get_instance()._last_qt_pos},
                self._modal_extrusion_plane_normal,
                self._modal_extrusion_plane_origin,
                place_on_geometry=False,
            )
            if metrics is not None:
                metrics["get_mouse_pos"] = metrics.get("get_mouse_pos", 0.0) + (time.perf_counter() - t0)
            if pt is None:
                return
            dist = float((pt - self._modal_extrusion_world_start).dot(self._modal_extrusion_normal))
            sweep = np.array([self._modal_extrusion_normal.x,
                              self._modal_extrusion_normal.y,
                              self._modal_extrusion_normal.z]) * dist
            # Only the analytic E is rebuilt per tick. The topology grows once, at
            # commit -- rebuilding the cage every tick is the 17 ms/tick regression.
            t0 = time.perf_counter()
            new_field = set_last_extrusion_top(
                self._target_obj, self._modal_extrusion_base_ring + sweep)
            if metrics is not None:
                metrics["mutate_cage"] = metrics.get("mutate_cage", 0.0) + (time.perf_counter() - t0)
            if new_field is not None:
                t0 = time.perf_counter()
                from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                label = f"{self._target_obj.Document.Name}.{self._target_obj.Name}"
                FldSceneVoxelRenderer.get_instance().update_field(label, new_field)
                if metrics is not None:
                    metrics["commit_field"] = metrics.get("commit_field", 0.0) + (time.perf_counter() - t0)

            if self.points_root:
                if self.extrude_gizmo is None:
                    self.extrude_gizmo = ExtrudeGizmo(self.points_root)
                field = getattr(getattr(self._target_obj, "Proxy", None), "SdfField", None)
                if field is not None and getattr(self, "_modal_extrusion_face", None) is not None:
                    ring = list(field._face_verts[self._modal_extrusion_face])
                    # The gizmo is drawn, so every point of it must be in the CURRENT
                    # frame. The ring's vertices and handles are cage control points, so
                    # their deformed positions are read directly -- no MVC needed. Taking
                    # the handles from the rest frame instead is what made the preview
                    # curves swoop off the surface.
                    base_ring = [FreeCAD.Vector(*p) for p in field.vertices[ring]]
                    base_h = ring_handles(field, ring, frame="current")
                    # Forward-MVC image of (rest ring + sweep) for exact top ring on deformed cages
                    if hasattr(field, "mvc_forward_map") and not getattr(field, "_is_identity", True):
                        rest_verts = field.rest_vertices[ring]
                        rest_h = ring_handles(field, ring, frame="rest")
                        top_pts = field.mvc_forward_map(rest_verts + sweep)
                        top_h_pts = field.mvc_forward_map(rest_h + sweep)
                        top_ring = [FreeCAD.Vector(*p) for p in top_pts]
                        top_h = top_h_pts
                    else:
                        top_ring = [FreeCAD.Vector(*(p + sweep)) for p in field.vertices[ring]]
                        top_h = base_h + sweep
                    r = self._compute_handle_radius()
                    self.extrude_gizmo.update(base_ring, top_ring, base_h, top_h, handle_radius=r)

            if self.view:
                t0 = time.perf_counter()
                self.view.redraw()
                if metrics is not None:
                    metrics["redraw_view"] = metrics.get("redraw_view", 0.0) + (time.perf_counter() - t0)
            return

        if self._drag_check_lmb_released():
            return
        if self._dragging_idx is None:
            return

        import time
        metrics = getattr(self, "_drag_session_metrics", None)
        if metrics is not None:
            metrics["ticks"] = metrics.get("ticks", 0) + 1

        mouse_pos = FldInputManager.get_instance()._last_qt_pos
        event_dict = {"Position": mouse_pos}
        
        # 1. Resolver resolve_world_point
        t0 = time.perf_counter()
        pt_global = self.resolve_world_point(
            event_dict,
            base_pt=self._drag_plane_o,
            plane_normal=self._drag_plane_n,
            plane_origin=self._drag_plane_o,
            exclude=[self._target_obj] if getattr(self, "_target_obj", None) else [],
            place_on_geometry=False,
        )
        if metrics is not None:
            metrics["get_mouse_pos"] = metrics.get("get_mouse_pos", 0.0) + (time.perf_counter() - t0)

        if pt_global:
            self._move_control_point(self._dragging_idx, pt_global)

    def _move_control_point(self, idx, pt_global):
        """Move control point `idx` to `pt_global`, resyncing field, handles and view.

        Shared by the drag timer and by modal grab, so both write the cage
        through exactly one path.
        """
        obj = self._target_obj
        if not obj or not obj.Proxy or not obj.Proxy.SdfField:
            return

        if not self._target_is_cage_field():
            if idx < len(self.points):
                self.points[idx] = pt_global
                obj.Points = self.points
                if idx < len(self.fld_points):
                    self.fld_points[idx].position = pt_global
                    self.fld_points[idx].update_draw()
                self._update_lines()
                self._commit_changes()
            return

        field = obj.Proxy.SdfField
        color_changed = False
        dirty_faces = self._faces_touching(field, idx)
        self._dirty_faces.update(dirty_faces)
        self._accumulate_dirty_box(field, dirty_faces)

        if idx < self._n_verts:
            # Dragging a vertex
            vi = idx
            old_pos = FreeCAD.Vector(field.vertices[vi][0], field.vertices[vi][1], field.vertices[vi][2])
            delta = pt_global - old_pos
            field.move_vertex(vi, delta)
        else:
            # Dragging a handle
            hi = idx - self._n_verts
            field.handles[hi] = np.array([pt_global.x, pt_global.y, pt_global.z], dtype=np.float64)
            if field._handle_types[hi] == HandleType.LINKED:
                field._handle_types[hi] = HandleType.FREE
                color_changed = True
            ei = hi // 2
            if getattr(field, "_edge_straight", None) and field._edge_straight[ei]:
                field._edge_straight[ei] = False
            field.geometry_version = getattr(field, "geometry_version", 0) + 1
            owner_vi = field.handle_owner(hi)
            field._handle_offsets[hi] = field.handles[hi] - field.vertices[owner_vi]

            # Project handles if aligned
            if field._handle_types[hi] == HandleType.ALIGNED:
                field.project_aligned_handles(owner_vi)
            else:
                field.recompute_face_aabbs()
        self._accumulate_dirty_box(field, dirty_faces)

        all_verts = [FreeCAD.Vector(p[0], p[1], p[2]) for p in field.vertices]
        all_handles = [FreeCAD.Vector(p[0], p[1], p[2]) for p in field.handles]
        self.points = all_verts + all_handles

        # Coin3D drawing sync
        modified_indices = {idx}
        if idx < self._n_verts:
            vi = idx
            if field and hasattr(field, "_vert_handles"):
                for hi in field._vert_handles.get(vi, []):
                    modified_indices.add(self._n_verts + hi)
        else:
            hi = idx - self._n_verts
            owner_vi = field.handle_owner(hi)
            if field and hasattr(field, "_vert_handles"):
                for h_idx in field._vert_handles.get(owner_vi, []):
                    modified_indices.add(self._n_verts + h_idx)

        for pi in modified_indices:
            if pi < len(self.points) and pi < len(self.fld_points):
                self.fld_points[pi].position = self.points[pi]
                self.fld_points[pi].update_draw()

        if color_changed:
            self._update_point_colors(field._handle_types)

        self._update_lines()
        self._commit_changes()

    # ------------------------------------------------------------------
    # Modal transform hooks (G / R / S)
    #
    # A cage's control points live on the SdfField, not in obj.Points, so
    # FldBase's implementations -- which snapshot and rewrite Points/HandleIn/
    # HandleOut -- have nothing to move here. These overrides hand the modal
    # transforms the cage's own data model.
    # ------------------------------------------------------------------

    def _cage_field(self):
        """The target's cage field, or None when it is not one we may mutate."""
        proxy = getattr(self._target_obj, "Proxy", None)
        field = getattr(proxy, "SdfField", None) if proxy else None
        if field is None or not self._target_is_cage_field():
            return None
        return field

    def get_selected_vertex_pos(self):
        idx = getattr(self, "_modal_sel_idx", None)
        if idx is None or idx >= len(self.points):
            return None
        p = self.points[idx]
        return FreeCAD.Vector(p.x, p.y, p.z)

    def update_selected_vertex_pos(self, global_pos):
        idx = getattr(self, "_modal_sel_idx", None)
        if idx is None or global_pos is None:
            return
        self._move_control_point(idx, global_pos)

    def get_modal_pivot(self):
        """Centroid of the cage's control points.

        The cage stores world-space points against an identity Placement, so
        FldBase's Placement.Base pivot would spin the cage about the document
        origin instead of about itself.
        """
        field = self._cage_field()
        if field is not None and len(field.vertices):
            c = np.mean(field.vertices, axis=0)
            return FreeCAD.Vector(float(c[0]), float(c[1]), float(c[2]))
        if self.points:
            acc = FreeCAD.Vector(0, 0, 0)
            for p in self.points:
                acc = acc + p
            n = float(len(self.points))
            return FreeCAD.Vector(acc.x / n, acc.y / n, acc.z / n)
        return None

    def snapshot_modal_state(self):
        field = self._cage_field()
        if field is not None:
            return {"vertices": field.vertices.copy(), "handles": field.handles.copy()}
        return {"points": [FreeCAD.Vector(p.x, p.y, p.z) for p in self.points]}

    def restore_modal_state(self, snap):
        if not snap:
            return
        if "vertices" in snap:
            self._set_all_control_points(snap["vertices"].copy(), snap["handles"].copy())
        elif "points" in snap:
            self.points = [FreeCAD.Vector(p.x, p.y, p.z) for p in snap["points"]]
            if self._target_obj:
                self._target_obj.Points = self.points
            self._sync_control_point_visuals()
        self._commit_changes(final=True)

    def commit_modal_transform(self):
        was_modal = getattr(self, "_modal_transform", None)
        super().commit_modal_transform()
        if was_modal and self._target_obj:
            self._commit_changes(final=True)

    def _set_all_control_points(self, verts, handles):
        """Bulk-write cage geometry, refresh derived state and resync the view."""
        field = self._cage_field()
        if field is None:
            return
        field.vertices[:] = verts
        field.handles[:] = handles
        for hi in range(len(field.handles)):
            field._handle_offsets[hi] = field.handles[hi] - field.vertices[field.handle_owner(hi)]
        # Mirror what each field's own move_vertex does: SdfCageField rebuilds
        # its topology from the moved vertices, while the deform field's
        # topology lives in the rest frame and must not be rebuilt from the
        # deformed ones.
        if not isinstance(field, SdfCageDeformField):
            topo = getattr(field, "topology", None)
            if topo is not None and hasattr(topo, "rebuild_from_geometry"):
                topo.rebuild_from_geometry(field.vertices)
        field.recompute_face_aabbs()

        # A whole-cage transform moves every face, so no region survives.
        self._dirty_faces = set(range(len(getattr(field, "_face_verts", []))))
        field.hot_faces = frozenset(self._dirty_faces)
        self._dirty_box = None

        self.points = ([FreeCAD.Vector(p[0], p[1], p[2]) for p in field.vertices]
                       + [FreeCAD.Vector(p[0], p[1], p[2]) for p in field.handles])
        self._sync_control_point_visuals()
        self._commit_changes()

    def _sync_control_point_visuals(self):
        for i, pt in enumerate(self.points):
            if i < len(self.fld_points):
                self.fld_points[i].position = pt
                self.fld_points[i].update_draw()
        self._update_lines()

    def _modal_pivot_array(self):
        # Taken from the snapshot, so a preview that is re-applied every mouse
        # tick keeps pivoting about where the cage started.
        snap = getattr(self, "_modal_snapshot", None)
        if snap and "vertices" in snap and len(snap["vertices"]):
            return snap["vertices"].mean(axis=0)
        pivot = self.get_modal_pivot()
        if pivot is None:
            return None
        return np.array([pivot.x, pivot.y, pivot.z], dtype=np.float64)

    def apply_modal_rotate(self, angle_deg):
        snap = getattr(self, "_modal_snapshot", None)
        piv = self._modal_pivot_array()
        if not snap or "vertices" not in snap or piv is None:
            return

        if self._axis_lock and len(self._axis_lock) == 1:
            name = self._axis_lock[0].upper()
            axis_vec = FreeCAD.Vector(1.0 if name == "X" else 0.0,
                                      1.0 if name == "Y" else 0.0,
                                      1.0 if name == "Z" else 0.0)
        elif self.view and hasattr(self.view, "getViewDirection"):
            axis_vec = self.view.getViewDirection().negative()
        else:
            axis_vec = FreeCAD.Vector(0, 0, 1)

        # Rotation matrix from the images of the basis vectors, so the whole
        # cage transforms as one numpy expression per tick.
        r = FreeCAD.Rotation(axis_vec, angle_deg)
        cols = [r.multVec(FreeCAD.Vector(1, 0, 0)),
                r.multVec(FreeCAD.Vector(0, 1, 0)),
                r.multVec(FreeCAD.Vector(0, 0, 1))]
        rot = np.array([[c.x for c in cols],
                        [c.y for c in cols],
                        [c.z for c in cols]], dtype=np.float64)
        self._set_all_control_points(
            (snap["vertices"] - piv) @ rot.T + piv,
            (snap["handles"] - piv) @ rot.T + piv)

    def apply_modal_scale(self, factor):
        snap = getattr(self, "_modal_snapshot", None)
        piv = self._modal_pivot_array()
        if not snap or "vertices" not in snap or piv is None:
            return

        lock = self._axis_lock
        s = np.array([factor if (not lock or "X" in lock) else 1.0,
                      factor if (not lock or "Y" in lock) else 1.0,
                      factor if (not lock or "Z" in lock) else 1.0], dtype=np.float64)
        self._set_all_control_points(
            (snap["vertices"] - piv) * s + piv,
            (snap["handles"] - piv) * s + piv)

    def _commit_changes(self, final=False, force_high_res=False):
        obj = self._target_obj
        if not obj or not obj.Proxy or not obj.Proxy.SdfField:
            return

        if not self._target_is_cage_field():
            obj.Points = self.points
            label = f"{obj.Document.Name}.{obj.Name}"
            if obj.Proxy and obj.Proxy.SdfField:
                FldSceneVoxelRenderer.get_instance().update_field(label, obj.Proxy.SdfField)
            if final:
                def deferred_write():
                    if self._target_obj and not getattr(self, "_cancelled", False):
                        o = self._target_obj
                        o.touch()
                        o.Document.recompute([o])
                QtCore.QTimer.singleShot(0, deferred_write)
            if self.view:
                self.view.redraw()
            return

        field = obj.Proxy.SdfField

        if final:
            new_field = field.clone_bumped()
            obj.Proxy.SdfField = new_field

            # Defer property/document writes to QTimer.singleShot per event-safety rules
            def deferred_write():
                if self._target_obj and not getattr(self, "_cancelled", False):
                    with fld_perf.phase("drag end: write net + recompute",
                                       category="cage"):
                        o = self._target_obj
                        net = CageNet.from_field(new_field)
                        disp = getattr(new_field, "displacements", None)
                        write_net(o, net, points=net.control_points, displacements=disp)
                        o.touch()
                        o.Document.recompute([o])
            QtCore.QTimer.singleShot(0, deferred_write)
        else:
            new_field = field
            new_field.simplify = True
            new_field.hot_faces = frozenset(getattr(self, "_dirty_faces", []))
            obj.Proxy.SdfField = new_field

        label = f"{obj.Document.Name}.{obj.Name}"
        FldSceneVoxelRenderer.get_instance().update_field(label, new_field)

        if self.view:
            self.view.redraw()

    def restore_original(self):
        obj = self._target_obj
        if not obj:
            return

        proxy = getattr(obj, "Proxy", None)
        orig_prim = getattr(proxy, "_original_primitive_props", None) if proxy else None

        if orig_prim is not None:
            obj.SdfType = orig_prim["SdfType"]
            obj.Points = [FreeCAD.Vector(p.x, p.y, p.z) for p in orig_prim["Points"]]
            if orig_prim["Placement"]:
                obj.Placement = orig_prim["Placement"]
            proxy.SdfField = orig_prim["SdfField"]

            for prop in orig_prim.get("AddedProperties", []):
                if hasattr(obj, prop):
                    try:
                        obj.removeProperty(prop)
                    except Exception as e:
                        fld_logger.debug(f"restore_original: could not remove property {prop}: {e}")

            if hasattr(proxy, "_original_primitive_props"):
                delattr(proxy, "_original_primitive_props")

            label = f"{obj.Document.Name}.{obj.Name}"
            FldSceneVoxelRenderer.get_instance().update_field(label, orig_prim["SdfField"])
            obj.touch()
            obj.Document.recompute([obj])
            if self.view:
                self.view.redraw()
            return

        if not self._original_props:
            return

        orig_net = self._original_props.get("Net")
        orig_field = self._original_props.get("SdfField")
        if orig_net is not None:
            write_net(obj, orig_net, points=self._original_props.get("Points"))
        if self._original_props.get("Placement"):
            obj.Placement = self._original_props["Placement"]
        if orig_field is not None:
            obj.Proxy.SdfField = orig_field

        label = f"{obj.Document.Name}.{obj.Name}"
        if orig_field:
            FldSceneVoxelRenderer.get_instance().update_field(label, orig_field)
        obj.touch()
        obj.Document.recompute([obj])
        if self.view:
            self.view.redraw()

    def _cancel_modal_extrusion(self):
        """Discard the pending extrusion record and restore the pre-extrude field."""
        obj = self._target_obj
        if obj is not None and getattr(self, "_modal_extrusion_face", None) is not None:
            new_field = pop_extrusion(obj)
            self._modal_extrusion_face = None
            if new_field is not None:
                from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                label = f"{obj.Document.Name}.{obj.Name}"
                FldSceneVoxelRenderer.get_instance().update_field(label, new_field)
        if getattr(self, "extrude_gizmo", None):
            self.extrude_gizmo.clear()
        if self.view:
            self.view.redraw()

    def cancel(self):
        self._cancelled = True
        super().cancel()

    def finish(self):
        self._commit_changes(final=True, force_high_res=True)
        self._is_editing = False
        proxy = getattr(self._target_obj, "Proxy", None) if self._target_obj else None
        if proxy and hasattr(proxy, "_original_primitive_props"):
            delattr(proxy, "_original_primitive_props")
        self.terminate()

    def _do_terminate(self):
        try:
            # Clean up Coin3D handles
            for fld_pt in self.fld_points:
                try:
                    fld_pt.undraw()
                except Exception as e:
                    fld_logger.warn(f"CageEditTool._do_terminate: failed to undraw point: {e}")
            self.fld_points = []
            
            # Clean up lines
            if hasattr(self, "_edge_curves") and self._edge_curves:
                self._edge_curves.undraw()
                self._edge_curves = None
            if hasattr(self, "_handle_arms") and self._handle_arms:
                self._handle_arms.undraw()
                self._handle_arms = None

            if getattr(self, "extrude_gizmo", None):
                self.extrude_gizmo.clear()
                self.extrude_gizmo = None

            if self.points_root and self.view and self.view.getSceneGraph():
                self.view.getSceneGraph().removeChild(self.points_root)
            self.points_root = None
        except Exception as e:
            fld_logger.warn(f"CageEditTool._do_terminate exception: {e}")
        super()._do_terminate()

    def _update_lines(self):
        if not self._edge_curves or not self._handle_arms or not self._target_obj:
            return



        from freecad.fields.core.objects.fld_face_extrude_objects import cage_net_of
        net = cage_net_of(self._target_obj)
        if net is None or not hasattr(net, "_edges"):
            return
        edges = net._edges

        curve_pts = []
        curve_segments = []
        arm_pts = []
        arm_segments = []

        n_samples = 16
        t_vals = np.linspace(0.0, 1.0, n_samples)

        for i, (vi, vj) in enumerate(edges):
            if vi >= len(self.points) or vj >= len(self.points) or (self._n_verts + 2*i + 1) >= len(self.points):
                continue
            p0 = self.points[vi]
            p1 = self.points[self._n_verts + 2*i]
            p2 = self.points[self._n_verts + 2*i + 1]
            p3 = self.points[vj]

            # Convert to numpy arrays for calculation
            v0 = np.array([p0.x, p0.y, p0.z])
            v1 = np.array([p1.x, p1.y, p1.z])
            v2 = np.array([p2.x, p2.y, p2.z])
            v3 = np.array([p3.x, p3.y, p3.z])

            # Sample cubic Bezier
            for t in t_vals:
                u = 1.0 - t
                pt = u*u*u*v0 + 3.0*u*u*t*v1 + 3.0*u*t*t*v2 + t*t*t*v3
                curve_pts.append(FreeCAD.Vector(float(pt[0]), float(pt[1]), float(pt[2])))
            curve_segments.append(n_samples)

            # Handle arms
            arm_pts.extend([p0, p1, p3, p2])
            arm_segments.extend([2, 2])

        self._edge_curves.update_lines(curve_pts, curve_segments)
        self._handle_arms.update_lines(arm_pts, arm_segments)

    def _get_face_under_mouse(self, event_dict):
        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return None
        
        field = self._target_obj.Proxy.SdfField if (self._target_obj and hasattr(self._target_obj, "Proxy")) else None
        if not field:
            return None
        hit = field.ray_march(ray_p, ray_d)
        if hit is None:
            return None
        
        hit_pos, _ = hit
        q = np.array([hit_pos.x, hit_pos.y, hit_pos.z], dtype=np.float64)
        ctrl_cache = getattr(field, "_face_cached_ctrl_pts", None) or []

        best_fi, best_dist = None, float("inf")
        for fi in range(len(field._face_verts)):
            ctrl = ctrl_cache[fi] if fi < len(ctrl_cache) else None
            if ctrl is None:
                # No cached patch (n-gon): fall back to corner polygon centroid
                dist = float(np.linalg.norm(field.vertices[field._face_verts[fi]].mean(axis=0) - q))
            elif len(field._face_verts[fi]) == 4:
                dist = _closest_on_face(*ctrl, q, simplify=True)[2]
            else:
                dist = _closest_on_tri_face(ctrl, q, simplify=True)[2]
            if dist < best_dist:
                best_dist, best_fi = dist, fi
        return best_fi

    def _get_edge_under_mouse(self, event_dict):
        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return None
        
        best_hi = None
        best_dist = float('inf')
        for i in range(self._n_verts, len(self.points)):
            pt = self.points[i]
            idx, dist = self._hit_test_perp(ray_p, ray_d, [pt], tolerance=1000.0)
            if idx is not None and dist < best_dist:
                best_dist = dist
                best_hi = i - self._n_verts
        
        if best_hi is not None:
            return best_hi // 2
        return None

    def _get_vertex_under_mouse(self, event_dict):
        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return None
        
        best_vi = None
        best_dist = float('inf')
        for i in range(self._n_verts):
            pt = self.points[i]
            idx, dist = self._hit_test_perp(ray_p, ray_d, [pt], tolerance=1000.0)
            if idx is not None and dist < best_dist:
                best_dist = dist
                best_vi = i
        return best_vi

    def _rebuild_cage_from_topology(self, new_topo, vertex_map=None, new_source=None,
                                    vertex_overrides=None, extra_writes=None):
        from freecad.fields.core.sdf.sdf.cage import rebuild_handles_and_types, remap_handle_displacements
        from freecad.fields.core.sdf.sdf.cage_deform import SdfCageDeformField
        field = self._target_obj.Proxy.SdfField
        
        # Check MAX_FACES shader cap (CP-014 / CP-021)
        if len(new_topo.faces) > MAX_CAGE_FACES:
            fld_logger.warn(f"CageEditTool: operation refused — {len(new_topo.faces)} "
                           f"faces exceeds the MAX_FACES shader cap ({MAX_CAGE_FACES})")
            return

        v_map_fn = vertex_map if callable(vertex_map) else (lambda v: vertex_map.get(v, v) if isinstance(vertex_map, dict) else v)
        
        new_faces = []
        for f in new_topo.faces:
            he_start = f.half_edge
            he = he_start
            f_verts = []
            while True:
                f_verts.append(he.prev.vertex.idx)
                he = he.next
                if he == he_start:
                    break
            new_faces.append(f_verts)
        face_verts_flat = [v for f in new_faces for v in f]
        face_sizes = [len(f) for f in new_faces]

        if isinstance(field, SdfCageDeformField):
            rest_verts = np.array([v.pos for v in new_topo.vertices], dtype=np.float64)
            class _RestProxy:
                pass
            rest_proxy = _RestProxy()
            rest_proxy.vertices = field.rest_vertices
            rest_proxy.handles = field.rest_handles
            rest_proxy._edges = field._edges
            rest_proxy._handle_types = field._handle_types
            rest_handles, new_types = rebuild_handles_and_types(rest_proxy, new_topo, v_map_fn)

            # Preserve handle displacements frame-consistently. new_topo lives in
            # the REST frame while field.handles live in the CURRENT (deformed)
            # frame; rebuilding absolute curr_handles by mixing the two (the
            # patch-cage path) leaks the vertex displacement into every handle and
            # blows the cage into spikes on the second extrude. Carry the
            # frame-independent handle displacement instead. (Handle types are
            # frame-independent, so take them from the rest rebuild above.)
            old_handle_disp = field.displacements[len(field.vertices):]
            disp_h = remap_handle_displacements(
                field._edges, old_handle_disp, new_topo.edges, v_map_fn)
            curr_handles = rest_handles + disp_h

            curr_verts = rest_verts.copy()
            for vi_new in range(len(new_topo.vertices)):
                if vertex_overrides and vi_new in vertex_overrides:
                    curr_verts[vi_new] = vertex_overrides[vi_new]
                    continue
                old_v = v_map_fn(vi_new)
                if old_v < len(field.vertices):
                    curr_verts[vi_new] = field.vertices[old_v]

            disp_v = curr_verts - rest_verts
            displacements = np.vstack([disp_v, disp_h])

            new_field = SdfCageDeformField(
                source=(new_source if new_source is not None else field.source),
                vertices=curr_verts,
                handles=curr_handles,
                face_verts=new_faces,  # list-of-lists: SdfCageDeformField has no face_sizes param
                edges=new_topo.edges,
                handle_types=new_types,
                edge_straight=getattr(field, "_edge_straight", None),
                rest_vertices=rest_verts,
                rest_handles=rest_handles,
                displacements=displacements,
                placement=field.placement,
            )
            new_field.sign_mode = getattr(field, "sign_mode", "closest")
            new_points_list = list(curr_verts) + list(curr_handles)
            new_points_fc = [FreeCAD.Vector(p[0], p[1], p[2]) for p in new_points_list]
        else:
            new_verts = np.array([v.pos for v in new_topo.vertices], dtype=np.float64)
            new_handles, new_types = rebuild_handles_and_types(field, new_topo, v_map_fn)
            from freecad.fields.core.sdf.sdf.cage import SdfCageField
            new_field = SdfCageField(
                vertices=new_verts,
                handles=new_handles,
                face_verts=face_verts_flat,
                face_sizes=face_sizes,
                edges=new_topo.edges,
                placement=field.placement,
                handle_types=new_types
            )
            new_field.sign_mode = getattr(field, "sign_mode", "closest")
            new_points_list = list(new_verts) + list(new_handles)
            new_points_fc = [FreeCAD.Vector(p[0], p[1], p[2]) for p in new_points_list]
        
        self._target_obj.Proxy.SdfField = new_field

        def update_props():
            with fld_perf.phase("cage rebuild: property write + recompute",
                               category="cage"):
                update_props_inner()

        def update_props_inner():
            obj = self._target_obj
            if isinstance(field, SdfCageDeformField):
                obj.Proxy._suspend_rebuild = True
                try:
                    if hasattr(obj, "Vertices"):
                        obj.Vertices = [FreeCAD.Vector(*v) for v in new_field.rest_vertices]
                    if hasattr(obj, "Handles"):
                        obj.Handles = [FreeCAD.Vector(*h) for h in new_field.rest_handles]
                    obj.FaceVertices = face_verts_flat
                    obj.FaceSizes = face_sizes
                    if hasattr(obj, "EdgeVertices"):
                        obj.EdgeVertices = [v for edge in new_topo.edges for v in edge]
                    obj.HandleTypes = new_types
                    if hasattr(obj, "EdgeStraight"):
                        obj.EdgeStraight = [1 if es else 0 for es in new_field._edge_straight]
                    if hasattr(obj, "Displacements"):
                        obj.Displacements = [FreeCAD.Vector(*d) for d in new_field.displacements]
                    obj.Points = ([FreeCAD.Vector(*v) for v in new_field.vertices] +
                                  [FreeCAD.Vector(*h) for h in new_field.handles])
                    if extra_writes is not None:
                        extra_writes(obj)
                finally:
                    obj.Proxy._suspend_rebuild = False
                obj.Proxy.SdfField = new_field
            else:
                obj.FaceVertices = face_verts_flat
                obj.FaceSizes = face_sizes
                obj.HandleTypes = new_types
                if hasattr(obj, "EdgeStraight"):
                    obj.EdgeStraight = [1 if es else 0 for es in getattr(new_field, "_edge_straight", [])]
                obj.Points = new_points_fc
                obj.Proxy.SdfField = new_field
            
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
            sr = FldSceneVoxelRenderer.get_instance()
            sr.update_field(f"{obj.Document.Name}.{obj.Name}", new_field)
            
            obj.Document.recompute()
            self.edit_object(obj)
            
        QtCore.QTimer.singleShot(0, update_props)
        return new_field

    def _commit_extrusion(self):
        """Grow the active deform cage over the extrusion committed to its properties."""
        # Committing is the one place the cage's topology grows, and it is the
        # start of the quiet stretch between one extrude and the next.
        with fld_perf.phase(
                f"commit extrusion (face {getattr(self, '_modal_extrusion_face', None)})",
                category="cage"):
            self._commit_extrusion_inner()

    def _commit_extrusion_inner(self):
        if getattr(self, "extrude_gizmo", None):
            self.extrude_gizmo.clear()
        from freecad.fields.core.sdf.sdf.cage_deform import SdfCageDeformField
        obj = self._target_obj
        fi = getattr(self, "_modal_extrusion_face", None)
        field = getattr(getattr(obj, "Proxy", None), "SdfField", None)
        if fi is None or not isinstance(field, SdfCageDeformField):
            return

        base_ring = self._modal_extrusion_base_ring
        base_handles = self._modal_extrusion_base_handles
        from freecad.fields.core.objects.fld_deform_objects import FldDeformCageProxy
        records = FldDeformCageProxy._extrusion_records(obj)
        if not records:
            return
        top_ring = records[-1][1]
        sweep = top_ring - base_ring
        k = len(base_ring)
        ring = list(field._face_verts[fi])

        # Grow a COPY: CageTopology.extrude_face re-inits the object it is called on
        # (cage.py:208), and field.topology is shared with the field that is about to
        # become the union's source.
        topo = CageTopology(*field.topology.net_arrays())
        old_n = len(topo.vertices)
        topo.extrude_face(fi, 1.0)
        n0 = len(topo.vertices) - k
        for i in range(k):
            topo.vertices[n0 + i].pos = top_ring[i].copy()

        # New cap vertex i came from ring[i]: it inherits that vertex's displacement,
        # so curr = rest + disp = (rest_base + sweep) + (curr_base - rest_base).
        v_map_dict = {n0 + i: ring[i] for i in range(k)}
        v_map = lambda v: v_map_dict.get(v, v)
        cap_curr = {n0 + i: field.vertices[ring[i]] + sweep[i] for i in range(k)}

        new_field = self._rebuild_cage_from_topology(
            topo, vertex_map=v_map, vertex_overrides=cap_curr)

        # Cap curvature: the cap ring inherits the source face's arc handles, carried
        # along the sweep. Same transform build_extrusion_cage uses -- one helper.
        if new_field is not None and base_handles is not None:
            for handles in (new_field.rest_handles, new_field.handles):
                carry_ring_handles_to_cap(
                    handles, topo.edges, base_handles, base_ring, top_ring,
                    top_offset=n0, edge_straight=new_field._edge_straight)
            new_field.displacements[len(new_field.vertices):] = (
                new_field.handles - new_field.rest_handles)
            new_field.recompute_face_aabbs()

        self._modal_extrusion_face = None
        self._modal_extrusion_base_ring = None
        self._modal_extrusion_base_handles = None

    def trigger_subdivide(self):
        field = self._target_obj.Proxy.SdfField if (self._target_obj and hasattr(self._target_obj, "Proxy")) else None
        if not field or not hasattr(field, "topology"):
            return
        
        # Call subdivide_smooth on the topology
        new_verts, new_handles, new_edges, new_faces_flat, new_types = field.topology.subdivide_smooth()
        
        # Check MAX_FACES shader cap
        n_faces = len(new_faces_flat) // 4
        if n_faces > MAX_CAGE_FACES:
            fld_logger.warn(f"CageEditTool: Subdivide refused — {n_faces} "
                           f"faces exceeds the MAX_FACES shader cap ({MAX_CAGE_FACES}).")
            return
            
        from freecad.fields.core.sdf.sdf.cage import CageTopology
        new_topo = CageTopology(new_faces_flat, [4] * n_faces, new_verts)
        self._rebuild_cage_from_topology(new_topo)

    def handle_keyboard(self, event_dict):
        key = event_dict.get("Key")
        mods = event_dict.get("Modifiers", 0)
        ctrl_mod = getattr(QtCore.Qt, "ControlModifier", 0)
        is_ctrl = bool(mods & ctrl_mod) if isinstance(ctrl_mod, int) else False
        
        if getattr(self, "_modal_extrusion_active", False):
            if key == QtCore.Qt.Key_Escape:
                self._cancel_modal_extrusion()
                self._modal_extrusion_active = False
                self._stop_drag_timer()
                self._dragging_idx = None
                if self.view:
                    self.view.redraw()
                return True
            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                self._modal_extrusion_active = False
                self._stop_drag_timer()
                self._dragging_idx = None
                self._commit_extrusion()
                if self.view:
                    self.view.redraw()
                return True
        
        field = self._target_obj.Proxy.SdfField if (self._target_obj and hasattr(self._target_obj, "Proxy")) else None
        if not field:
            return super().handle_keyboard(event_dict)
            
        if key == QtCore.Qt.Key_E:
            fi = self._get_face_under_mouse(event_dict)
            if fi is None and hasattr(self, "_last_clicked_vertex") and self._last_clicked_vertex is not None:
                fi = next((f_idx for f_idx, f in enumerate(field._face_verts) if self._last_clicked_vertex in f), None)
            if fi is None and hasattr(self, "_last_clicked_edge") and self._last_clicked_edge is not None:
                edge_verts = field._edges[self._last_clicked_edge]
                fi = next((f_idx for f_idx, f in enumerate(field._face_verts) if edge_verts[0] in f and edge_verts[1] in f), None)
            
            if fi is None:
                fld_logger.warn("CageEditTool: No face selected for extrusion")
                return True

            field = self._target_obj.Proxy.SdfField
            from freecad.fields.core.sdf.sdf.cage_deform import SdfCageDeformField
            if not isinstance(field, SdfCageDeformField):
                fld_logger.warn("CageEditTool: extrude requires a deform cage")
                return True

            fld_logger.info(f"CageEditTool: extruding face {fi} (analytic face extrude)")
            self._modal_extrusion_face = fi

            # Rest-frame ring geometry. The ring vertices ARE cage vertices, so their
            # rest positions are known exactly -- no inverse-MVC needed. The drag is
            # applied in the rest frame, and the cage's own deformation then carries
            # the prism along with the surface.
            ring = list(field._face_verts[fi])
            base_ring = field.rest_vertices[ring]
            base_handles = ring_rest_handles(field, ring)

            # Outward rest-frame normal: face winding carries no outward guarantee,
            # and an inward sweep would extrude into the solid.
            normal = newell_normal(base_ring)
            if np.dot(normal, base_ring.mean(axis=0) - field.rest_vertices.mean(axis=0)) < 0.0:
                normal = -normal
            self._modal_extrusion_normal = FreeCAD.Vector(*normal)
            self._modal_extrusion_base_ring = base_ring
            self._modal_extrusion_base_handles = base_handles

            centroid = field.vertices[ring].mean(axis=0)
            view_dir = self.view.getViewDirection()
            self._modal_extrusion_plane_normal = FreeCAD.Vector(
                -view_dir.x, -view_dir.y, -view_dir.z).normalize()
            self._modal_extrusion_plane_origin = FreeCAD.Vector(*centroid)
            self._modal_extrusion_world_start = self.projector.get_mouse_world_pos(
                {"Position": FldInputManager.get_instance()._last_qt_pos},
                self._modal_extrusion_plane_normal,
                self._modal_extrusion_plane_origin,
                place_on_geometry=False,
            ) or self._modal_extrusion_plane_origin

            with fld_perf.phase(f"push extrusion record (face {fi})", category="cage"):
                push_extrusion(self._target_obj, base_ring, base_ring, base_handles)
            self._modal_extrusion_active = True
            self._start_drag_timer()
            return True
                
        elif key == QtCore.Qt.Key_R and is_ctrl:
            ei = self._get_edge_under_mouse(event_dict)
            if ei is None and hasattr(self, "_last_clicked_edge") and self._last_clicked_edge is not None:
                ei = self._last_clicked_edge
            
            if ei is not None:
                fld_logger.info(f"CageEditTool: inserting edge loop at edge {ei}")
                field.topology.insert_edge_loop(ei, 0.5)
                self._rebuild_cage_from_topology(field.topology)
                return True
            else:
                fld_logger.warn("CageEditTool: No edge selected for loop insertion")
                return True
                
        elif key == QtCore.Qt.Key_M:
            vj = self._get_vertex_under_mouse(event_dict)
            vi = getattr(self, "_last_clicked_vertex", None)
            
            if vi is not None and vj is not None and vi != vj:
                fld_logger.info(f"CageEditTool: welding vertex {vi} to {vj}")
                mapped_vj = vj - 1 if vj > vi else vj
                def v_map(v):
                    if v > vi:
                        return v - 1
                    elif v == vi:
                        return mapped_vj
                    else:
                        return v
                        
                field.topology.weld_vertices(vi, vj)
                self._rebuild_cage_from_topology(field.topology, vertex_map=v_map)
                self._last_clicked_vertex = None
                return True
            else:
                fld_logger.warn("CageEditTool: Weld requires two distinct vertices")
                return True

        elif key == QtCore.Qt.Key_D and is_ctrl:
            self.trigger_subdivide()
            return True
                
        return super().handle_keyboard(event_dict)

    def _log_drag_session_summary(self):
        super()._log_drag_session_summary()
        self._report_drag_metrics()

    def _report_drag_metrics(self):
        ticks = getattr(self, "_drag_session_ticks", 0)
        if ticks == 0:
            return
        total_update = getattr(self, "_drag_session_total_update_time", 0.0)
        avg_total = total_update / ticks
        
        # If the average total time per tick exceeds the 16 ms budget, log a warning
        if avg_total > 0.016:
            fld_logger.warn(
                f"CageEditTool: drag tick budget exceeded! "
                f"Avg tick: {avg_total*1000:.2f}ms (budget 16.0ms), ticks: {ticks}"
            )
        else:
            fld_logger.debug(
                f"CageEditTool: drag tick budget okay. "
                f"Avg tick: {avg_total*1000:.2f}ms (budget 16.0ms), ticks: {ticks}"
            )
