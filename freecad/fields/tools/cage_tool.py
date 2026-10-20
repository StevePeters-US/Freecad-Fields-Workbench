# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from PySide import QtCore
import numpy as np

from freecad.fields.tools.fld_sdf_tool_base import FldSdfToolBase
from freecad.fields.tools.fld_base import DragTimerMixin
from freecad.fields.tools.tool_mixins import HANDLE_TYPE_COLORS
from freecad.fields.core.input.input_manager import FldInputManager
from freecad.fields.core import fld_logger
from freecad.fields.core import fld_perf
from freecad.fields.core.objects.fld_face_extrude_objects import cage_net_of
from freecad.fields.core.sdf.sdf.cage import CageTopology
from freecad.fields.core.sdf.sdf.cage_limits import (
    CageBudgetError, CageTopologyError, check_face_budget, predict_subdivide_faces,
)
from freecad.fields.core.sdf.sdf.cage_deform import SdfCageDeformField, HandleType
from freecad.fields.core.sdf.sdf.cage_net import CageNet, write_net
from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
from freecad.fields.core.input.fld_menu_tree import MenuItem, SEPARATOR
from freecad.fields.core.input.gizmo.models import HandleId, Kind, AXES, Phase
from freecad.fields.tools.cage_edit_journal import CageEditJournal
from freecad.fields.tools.cage_task_panel import CageTaskPanel
from freecad.fields.tools.cage_net_overlay import CageNetOverlay
from freecad.fields.tools.cage_rebuild import build_replacement_field
from freecad.fields.tools.cage_gizmo import CageGizmoMixin
from freecad.fields.tools.cage_extrude_session import CageExtrudeSession
from freecad.fields.tools.cage_picking import (
    pick_handle, pick_hover_edge_handle, pick_hover_vertex, closest_face,
)

class CageEditTool(CageGizmoMixin, FldSdfToolBase, DragTimerMixin):
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
        self._last_clicked_vertex = None
        self._last_clicked_edge = None
        self._handle_clicked = False
        self._cancelled = False
        self._dialog_open = False
        self._n_verts = 0
        self.points = []
        self._overlay = CageNetOverlay(self.view, self._point_color)

        self._pressed_point = None
        self._dragging_idx = None
        self._selection = []   # ordered control-point indices; last is the anchor
        self._journal = None
        self._extrude = CageExtrudeSession(self)

    def _target_is_cage_field(self):
        """True when the target's field owns cage arrays we may mutate in place."""
        field = getattr(getattr(self._target_obj, "Proxy", None), "SdfField", None)
        return field is not None and hasattr(field, "move_vertex")

    def edit_object(self, obj):
        """Attach CageEditTool to target document object and build visual handles."""
        super().edit_object(obj)
        self._target_obj = obj
        self._is_editing = True
        
        # Session undo. _original_props is replaced by the journal mechanism (CGE-008).
        if self._journal is None:
            self._journal = CageEditJournal(obj.Document)
        self._journal.capture(obj)

        proxy = getattr(obj, "Proxy", None)

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
            field.geometry_version = getattr(field, "geometry_version", 0)

        # obj.Points stores world-space coords (cage uses placement=None).
        local_pts = list(getattr(obj, "Points", []))

        if len(local_pts) < n_handles + 3:
            fld_logger.error(f"CageEditTool: too few points on cage object: len(local_pts)={len(local_pts)}, n_handles={n_handles}")
            self.terminate()
            return

        self._n_verts = len(local_pts) - n_handles
        self.points = [FreeCAD.Vector(p.x, p.y, p.z) for p in local_pts]
        self._selection = []

        # Draw handles
        r = self._compute_handle_radius()
        field = proxy.SdfField if proxy else None
        self._overlay.build(self.points, self._n_verts, r)
        self._update_lines()
        self._init_gizmo()

    def set_xray(self, on):
        """Move the cage net between the always-on-top parent and the depth-tested one."""
        if self._overlay.set_xray(on):
            panel = getattr(self, "panel", None)
            if panel is not None and hasattr(panel, "sync_xray"):
                panel.sync_xray(on)

    def toggle_xray(self):
        """Flip the x-ray state of the cage overlay."""
        self.set_xray(not self._overlay.xray)

    @property
    def points_root(self):
        """The always-on-top overlay root."""
        return self._overlay.points_root

    def _update_point_colors(self, handle_types):
        """Update point and handle colors based on their current types."""
        self._overlay.update_point_colors(self.points, self._n_verts, handle_types)

    def _point_color(self, i):
        """Return the unselected display color for control point i."""
        if i < self._n_verts:
            return HANDLE_TYPE_COLORS["vertex"]
        hi = i - self._n_verts
        proxy = getattr(self._target_obj, "Proxy", None)
        field = getattr(proxy, "SdfField", None) if proxy else None
        ht = field._handle_types[hi] if (field and hasattr(field, "_handle_types") and hi < len(field._handle_types)) else 1
        return HANDLE_TYPE_COLORS.get(ht, HANDLE_TYPE_COLORS["LINKED"])

    def _post_init(self):
        super()._post_init()
        if not getattr(self, "_terminated", False) and self._is_editing:
            self.panel = CageTaskPanel(self)
            gui_doc = FreeCADGui.getDocument(self.doc.Name)
            FreeCADGui.Control.showDialog(self.panel, gui_doc)
            self._dialog_open = True
            if self._target_obj:
                fld_logger.info(f"CageEditTool: editing {self._target_obj.Label}")

    def _report_refusal(self, message):
        """Surface a refused operation where the user is actually looking.

        fld_logger has no user-facing channel, so a warn-and-return refusal is
        indistinguishable from a dead button.
        """
        fld_logger.warn(f"CageEditTool: {message}")
        try:
            FreeCADGui.getMainWindow().statusBar().showMessage(message, 4000)
        except Exception as e:
            fld_logger.debug(f"CageEditTool: status bar unavailable: {e}",
                            category="freecad.fields.tools")

    def on_button1_down(self, event_dict):
        """LMB. A live extrusion commits. Otherwise delegates to SelectionGizmoMixin."""
        if event_dict.get("Button") != QtCore.Qt.LeftButton:
            return False
        if self._extrude.active:
            self._end_extrusion_interaction(commit=True)
            return True
        return super().on_button1_down(event_dict)

    def on_button1_up(self, event_dict):
        """The gizmo's release (a drag ends and commits). Always consumed."""
        self._gizmo_release()
        return True

    def on_button1_double_click(self, event_dict):
        """Double-click on an edge selects its edge loop."""
        edge_idx = self._get_edge_under_mouse(event_dict)
        if edge_idx is None:
            return False

        proxy = getattr(self._target_obj, "Proxy", None)
        field = proxy.SdfField if (proxy and hasattr(proxy, "SdfField")) else None
        topo = getattr(field, "topology", None) if field else None
        if not topo:
            return False

        try:
            loop_edges = topo.edge_loop(edge_idx)
        except Exception as e:
            fld_logger.debug(f"CageEditTool: edge_loop failed on edge {edge_idx}: {e}")
            return False

        verts = []
        for ei in loop_edges:
            if ei < len(topo.edges):
                va, vb = topo.edges[ei]
                if va not in verts:
                    verts.append(va)
                if vb not in verts:
                    verts.append(vb)

        self._set_selection(verts)
        return True

    def on_mouse_move(self, event_dict):
        # Cage drag is handled by the timer, not on_mouse_move. Pass all moves
        # through so FreeCAD's navigation always has a current mouse position —
        # without this, orbit/pan jumps because FreeCAD's last known position
        # is stale from before the cage tool was opened.
        self.handle_move(event_dict)
        return False

    def on_button2_down(self, event_dict):
        # Explicit pass-through so middle mouse orbit/pan is never consumed.
        return False

    def on_button3_down(self, event_dict):
        btn = event_dict.get("Button")
        if btn != QtCore.Qt.RightButton:
            return False

        if self._extrude.active:
            self._end_extrusion_interaction(commit=False)
            return True

        ctl = self.gizmo_controller
        if ctl is not None and ctl.state.phase is not Phase.IDLE:
            self._context_menu_handle = None
            return True     # the context menu that follows steps the transform back (D-54)

        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            self._context_menu_handle = None
            return True

        # Hit test handles only (index >= self._n_verts)
        best_idx = pick_handle(self, ray_p, ray_d, include_vertices=False)
        if best_idx is not None:
            self._context_menu_handle = best_idx - self._n_verts
        else:
            self._context_menu_handle = None

        # Always consume RMB press. The global filter suppresses the matching release
        # to prevent FreeCAD's context menu. If this returns False, FreeCAD sees the
        # press but never the release (release is swallowed by the global rule), which
        # leaves FreeCAD's navigation stuck in "RMB held" state after the tool closes.
        return True

    def on_context_menu(self, event_dict):
        try:
            return super().on_context_menu(event_dict)
        finally:
            self._context_menu_handle = None

    def _set_cage_handle_type(self, hi, new_type):
        obj = self._target_obj
        if not (obj and obj.Proxy and obj.Proxy.SdfField and self._target_is_cage_field()):
            return
        field = obj.Proxy.SdfField
        field._handle_types[hi] = new_type

        if new_type == HandleType.ALIGNED:
            ei = hi // 2
            owner_vi = field._edges[ei][0] if (hi % 2 == 0) else field._edges[ei][1]
            field.project_aligned_handles(owner_vi)

            # Sync positions
            all_verts = [FreeCAD.Vector(p[0], p[1], p[2]) for p in field.vertices]
            all_handles = [FreeCAD.Vector(p[0], p[1], p[2]) for p in field.handles]
            self.points = all_verts + all_handles
            obj.Proxy._suspend_rebuild = True
            try:
                obj.Points = self.points
            finally:
                obj.Proxy._suspend_rebuild = False
            for j, pt in enumerate(self.points):
                self._overlay.update_point(j, pt)

        self._update_point_colors(field._handle_types)
        self._update_lines()
        self._commit_changes()

    def get_context_menu(self, event_dict=None):
        if self._extrude.active:
            return [
                MenuItem("Commit Extrusion", action=lambda: self._end_extrusion_interaction(commit=True), shortcut="Return"),
                MenuItem("Cancel Extrusion", action=lambda: self._end_extrusion_interaction(commit=False), shortcut="Esc"),
            ]

        obj = self._target_obj
        field = obj.Proxy.SdfField if (obj and getattr(obj, "Proxy", None) and getattr(obj.Proxy, "SdfField", None) and self._target_is_cage_field()) else None

        items = []
        if getattr(self, "_context_menu_handle", None) is not None and field is not None:
            hi = self._context_menu_handle
            cur_type = field._handle_types[hi] if hi < len(field._handle_types) else None
            handle_type_children = [
                MenuItem("Linked", action=lambda h=hi: self._set_cage_handle_type(h, HandleType.LINKED), checked=(cur_type == HandleType.LINKED)),
                MenuItem("Free", action=lambda h=hi: self._set_cage_handle_type(h, HandleType.FREE), checked=(cur_type == HandleType.FREE)),
                MenuItem("Aligned", action=lambda h=hi: self._set_cage_handle_type(h, HandleType.ALIGNED), checked=(cur_type == HandleType.ALIGNED)),
            ]
            items.append(MenuItem("Handle Type", children=handle_type_children))

        if field is not None:
            ev = event_dict or {}
            items += [
                MenuItem("Extrude Face", action=lambda ev=ev, f=field: self._key_extrude_face(ev, f), shortcut="E"),
                MenuItem("Insert Edge Loop", action=lambda ev=ev, f=field: self._key_insert_loop(ev, f), shortcut="Ctrl+R"),
                MenuItem("Subdivide", action=self.trigger_subdivide, shortcut="Ctrl+D"),
                MenuItem("Weld", action=lambda ev=ev, f=field: self._key_weld_vertex(ev, f), shortcut="M"),
            ]
        xray_checked = bool(getattr(getattr(self, "_overlay", None), "xray", False))
        items += [
            MenuItem("X-ray", action=self.toggle_xray, checked=xray_checked, shortcut="Alt+Z"),
            SEPARATOR,
        ]

        base_items = super().get_context_menu(event_dict) if hasattr(super(), "get_context_menu") else []
        return items + base_items

    def _drag_update(self):
        """The extrusion's drag while one is live, else the driver's (a gizmo drag)."""
        if self._extrude.active:
            self._extrude.update_drag()
            return
        super()._drag_update()


    def _sync_control_point_visuals(self):
        self._overlay.sync_visuals(self.points, self._selection)
        self._update_lines()

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
                    if self._target_obj and not self._cancelled:
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
                if self._target_obj and not self._cancelled:
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
            obj.Proxy.SdfField = new_field

        label = f"{obj.Document.Name}.{obj.Name}"
        FldSceneVoxelRenderer.get_instance().update_field(label, new_field)

        if self.view:
            self.view.redraw()

    def restore_original(self):
        obj = self._target_obj
        if not obj:
            return

        # Session undo first: this replays the cage net, points, field and
        # placement as they were at edit_object() time, including any topology
        # operation run during the session.
        journal = self._journal
        if journal is not None:
            journal.restore()
            self._journal = None

        label = f"{obj.Document.Name}.{obj.Name}"
        field = getattr(getattr(obj, "Proxy", None), "SdfField", None)
        if field:
            FldSceneVoxelRenderer.get_instance().update_field(label, field)
        if self.view:
            self.view.redraw()

    def cancel(self):
        self._cancelled = True
        super().cancel()

    def finish(self):
        self._dispose_gizmo(commit=True)
        self._commit_changes(final=True, force_high_res=True)
        self._is_editing = False
        self._journal = None
        self.terminate()

    def _do_terminate(self):
        self._dispose_gizmo()
        self._overlay.teardown()
        try:
            self._extrude.dispose()
        except Exception as e:
            fld_logger.warn(f"CageEditTool._do_terminate exception: {e}")
        super()._do_terminate()

    def _update_lines(self):
        if not self._overlay.has_lines or not self._target_obj:
            return

        net = cage_net_of(self._target_obj)
        if net is None or not hasattr(net, "_edges"):
            return
        self._overlay.update_lines(self.points, self._n_verts, net._edges)

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
        return closest_face(field, q)

    def _get_edge_under_mouse(self, event_dict):
        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return None

        best_hi = pick_hover_edge_handle(self, ray_p, ray_d)
        if best_hi is not None:
            return best_hi // 2
        return None

    def _get_vertex_under_mouse(self, event_dict):
        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return None

        return pick_hover_vertex(self, ray_p, ray_d)

    def _rebuild_cage_from_topology(self, new_topo, vertex_map=None, new_source=None,
                                    vertex_overrides=None, extra_writes=None,
                                    handles=None, handle_types=None, edge_sharpness=None):
        field = self._target_obj.Proxy.SdfField
        
        # Budget check. A refused rebuild must be visible -- a bare return here
        # reads to the user as a dead menu item.
        try:
            check_face_budget(len(new_topo.faces), "Cage rebuild")
        except CageBudgetError as e:
            self._report_refusal(str(e))
            return

        journal = self._journal
        if journal is not None:
            journal.capture(self._target_obj)

        new_field, new_types, new_points_fc, face_verts_flat, face_sizes = build_replacement_field(
            field, new_topo, vertex_map=vertex_map, new_source=new_source,
            vertex_overrides=vertex_overrides, handles=handles,
            handle_types=handle_types, edge_sharpness=edge_sharpness)
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
                    if hasattr(obj, "EdgeSharpness"):
                        obj.EdgeSharpness = [float(s) for s in (getattr(new_field, "_edge_sharpness", None) or [])]
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
                if hasattr(obj, "EdgeSharpness"):
                    obj.EdgeSharpness = [float(s) for s in (getattr(new_field, "_edge_sharpness", None) or [])]
                obj.Points = new_points_fc
                obj.Proxy.SdfField = new_field
            
            sr = FldSceneVoxelRenderer.get_instance()
            sr.update_field(f"{obj.Document.Name}.{obj.Name}", new_field)
            
            obj.Document.recompute()
            self.edit_object(obj)
            
        QtCore.QTimer.singleShot(0, update_props)
        return new_field

    def trigger_subdivide(self):
        field = self._target_obj.Proxy.SdfField if (self._target_obj and hasattr(self._target_obj, "Proxy")) else None
        if not field or not hasattr(field, "topology"):
            return

        # Budget first: subdivide_smooth() allocates the whole refined cage, so
        # measuring its output is measuring work we already decided not to keep.
        try:
            check_face_budget(predict_subdivide_faces(field.topology), "Subdivide")
        except CageBudgetError as e:
            self._report_refusal(str(e))
            return

        edge_sharp = getattr(field, "_edge_sharpness", None)
        new_verts, new_handles, new_edges, new_faces_flat, new_types, new_sharp = field.topology.subdivide_smooth(edge_sharpness=edge_sharp)

        n_faces = len(new_faces_flat) // 4
        new_topo = CageTopology(new_faces_flat, [4] * n_faces, new_verts)
        self._rebuild_cage_from_topology(
            new_topo,
            handles=new_handles,
            handle_types=new_types,
            edge_sharpness=new_sharp,
        )

    def _end_extrusion_interaction(self, commit):
        """Leave modal extrusion: commit or cancel it, stop the drag, redraw."""
        if commit:
            self._extrude.active = False
            self._stop_drag_timer()
            self._dragging_idx = None
            self._extrude.commit()
        else:
            self._extrude.cancel()
            self._extrude.active = False
            self._stop_drag_timer()
            self._dragging_idx = None
        self._update_gizmo()
        if self.view:
            self.view.redraw()

    def _key_extrude_face(self, event_dict, field):
        fi = self._get_face_under_mouse(event_dict)
        if fi is None and self._last_clicked_vertex is not None:
            fi = next((f_idx for f_idx, f in enumerate(field._face_verts) if self._last_clicked_vertex in f), None)
        if fi is None and self._last_clicked_edge is not None:
            edge_verts = field._edges[self._last_clicked_edge]
            fi = next((f_idx for f_idx, f in enumerate(field._face_verts) if edge_verts[0] in f and edge_verts[1] in f), None)

        if fi is None:
            fld_logger.warn("CageEditTool: No face selected for extrusion")
            return True

        field = self._target_obj.Proxy.SdfField
        if not isinstance(field, SdfCageDeformField):
            fld_logger.warn("CageEditTool: extrude requires a deform cage")
            return True

        self._extrude.begin(field, fi)
        self._update_gizmo()
        self._start_drag_timer()
        return True

    def _key_insert_loop(self, event_dict, field):
        ei = self._get_edge_under_mouse(event_dict)
        if ei is None and self._last_clicked_edge is not None:
            ei = self._last_clicked_edge

        if ei is not None:
            fld_logger.info(f"CageEditTool: inserting edge loop at edge {ei}")
            try:
                field.topology.insert_edge_loop(ei, 0.5)
            except CageTopologyError as e:
                self._report_refusal(str(e))
                return True
            self._rebuild_cage_from_topology(field.topology)
            return True
        else:
            fld_logger.warn("CageEditTool: No edge selected for loop insertion")
            return True

    def _key_weld_vertex(self, event_dict, field):
        vj = self._get_vertex_under_mouse(event_dict)
        vi = self._last_clicked_vertex

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
