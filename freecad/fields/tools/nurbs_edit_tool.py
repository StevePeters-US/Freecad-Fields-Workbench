# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/nurbs_edit_tool.py

Interactive editing tool for traditional NURBS curves, points, and surface
control cages (control-point manipulation, projection, dragging).
"""
import FreeCAD
import FreeCADGui
import Part
from PySide import QtCore
from freecad.fields.tools.fld_base import FldBase, DragTimerMixin
from freecad.fields.core import fld_logger
from freecad.fields.core.input.fld_menu_tree import MenuItem, SEPARATOR
from freecad.fields.core.input.input_manager import FldInputManager


from freecad.fields.tools.curve_gizmo import CurveGizmoMixin, padded
from pivy import coin


class NurbsEditTool(CurveGizmoMixin, FldBase, DragTimerMixin):
    """
    Interactive tool for editing lattice-driven SDF objects.
    """
    def get_command_id(self):
        return "Fields_EditObject"

    def __init__(self):
        super().__init__()
        self._target_obj = None
        self._is_editing = True
        self._wp_drag_start = None
        self._selection = []
        self._sel_markers = []
        self.points_root = None

    def activate(self, obj=None):
        if obj is None:
            sel = FreeCADGui.Selection.getSelection()
            if not sel:
                fld_logger.error("No object selected to edit.")
                self.terminate()
                return
            obj = sel[0]

        from freecad.fields.core.objects.fld_object_proxy import FldObjectProxy

        proxy = getattr(obj, "Proxy", None)
        if proxy is not None and isinstance(proxy, FldObjectProxy):
            shape_type = getattr(obj, "ShapeType", None)
            if shape_type in ("curve", "point"):
                self._target_obj = obj
            else:
                fld_logger.error("Selected object is not an editable curve or point.")
                self.terminate()
                return
        else:
            fld_logger.error("Selected object is not a Fields object.")
            self.terminate()
            return
            
        fld_logger.info(f"NurbsEditTool activated for {self._target_obj.Label}")

        shape_type = getattr(self._target_obj, "ShapeType", None)
        self._created_node_names = []
        if shape_type == "curve":
            nodes = [n for n in (getattr(self._target_obj, "ControlNodes", []) or []) if n is not None]
            self.linked_nodes = list(nodes)
            # Snapshot original properties for cancel restoration
            self._original_props = {
                "Points": list(getattr(self._target_obj, "Points", [])),
                "HandleIn": list(getattr(self._target_obj, "HandleIn", [])),
                "HandleOut": list(getattr(self._target_obj, "HandleOut", [])),
                "HandleTypes": list(getattr(self._target_obj, "HandleTypes", [])),
                "PointTypes": list(getattr(self._target_obj, "PointTypes", [])),
            }
            if self.linked_nodes:
                self._original_props["Nodes"] = [(n.Name, FreeCAD.Vector(n.Coordinates)) for n in nodes]
        else:
            self.linked_nodes = []
            self._original_props = {
                "Coordinates": FreeCAD.Vector(getattr(self._target_obj, "Coordinates", self._target_obj.Position))
            }

        # Toggle EditMode ON and force the control cage to rebuild directly —
        # updateData may not fire reliably for the EditMode property change.
        self._target_obj.EditMode = True
        vp = self._target_obj.ViewObject
        vp_proxy = getattr(vp, "Proxy", None)
        renderer = getattr(vp_proxy, "renderer", None) if vp_proxy else None
        if renderer:
            renderer.rebuild_control_cage(self._target_obj)
        vp.show()
        if self._target_obj.Document:
            doc = self._target_obj.Document
            QtCore.QTimer.singleShot(0, doc.recompute)

        if coin is not None and hasattr(coin, "SoAnnotation"):
            self.points_root = coin.SoAnnotation()
            if getattr(self, "view", None) and self.view.getSceneGraph():
                self.view.getSceneGraph().addChild(self.points_root)
        else:
            self.points_root = None
        self._init_gizmo()

    def _get_ray(self, event_dict):
        """Delegate ray acquisition to FldInputManager."""
        return FldInputManager.get_instance().get_ray(self.view, event_dict)

    def _hit_test(self, ray_p, ray_d):
        if not self._target_obj or not ray_p or not ray_d: return None
        
        shape_type = getattr(self._target_obj, "ShapeType", None)
        if shape_type == "point":
            inv_plac = self._target_obj.Placement.inverse()
            p = getattr(self._target_obj, "Coordinates", self._target_obj.Position)
            p_local = inv_plac.multVec(p)
            local_ray_p = inv_plac.multVec(ray_p)
            local_ray_d = inv_plac.Rotation.multVec(ray_d)
            idx, dist = self._hit_test_perp(local_ray_p, local_ray_d, [p_local])
            if idx is not None:
                return (0, "Point")
            return None

        pts = self._local_points()
        h_in = getattr(self._target_obj, "HandleIn", [])
        h_out = getattr(self._target_obj, "HandleOut", [])
        
        inv_plac = self._target_obj.Placement.inverse()
        local_ray_p = inv_plac.multVec(ray_p)
        local_ray_d = inv_plac.Rotation.multVec(ray_d)
        
        elements = []
        for i, p in enumerate(pts):
            elements.append((i, "Point", p))
            if i < len(h_in): elements.append((i, "HandleIn", h_in[i]))
            if i < len(h_out): elements.append((i, "HandleOut", h_out[i]))
            
        points = [p[2] for p in elements if p[2] is not None]
        idx, dist = self._hit_test_perp(local_ray_p, local_ray_d, points)
        
        if idx is not None:
            # Re-map filtered points back to original elements list
            valid_elements = [e for e in elements if e[2] is not None]
            best_elem = (valid_elements[idx][0], valid_elements[idx][1])
            return best_elem
                
        return None

    def _hit_test_edge(self, event_dict):
        """The point on the curve nearest the mouse ray, or None when the ray passes the curve
        farther than the hover tolerance there."""
        ray_p, ray_d = self._get_ray(event_dict)
        obj = self._target_obj
        if obj is None or ray_p is None or ray_d is None:
            return None
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            return None
        d = FreeCAD.Vector(ray_d).normalize()
        far = shape.BoundBox.DiagonalLength + (ray_p - shape.BoundBox.Center).Length + 1.0
        ray = Part.LineSegment(ray_p - d * far, ray_p + d * far).toShape()
        dist, pairs, _info = shape.distToShape(ray)
        if not pairs:
            return None
        on_curve = pairs[0][0]
        if dist > 1.5 * self._compute_handle_radius(on_curve):
            return None
        return on_curve

    def on_button1_double_click(self, event_dict):
        """A double-click on the curve inserts a point there, but not when hitting a control point/handle."""
        ray_p, ray_d = self._get_ray(event_dict)
        if ray_p is not None and ray_d is not None and self._hit_test(ray_p, ray_d) is not None:
            return True
        hit = self._hit_test_edge(event_dict)
        if hit is None:
            return False
        self._insert_point(hit)
        return True

    def _tool_hover(self, event_dict):
        ray_p, ray_d = self._get_ray(event_dict)
        if self.working_plane and ray_p and ray_d:
            wp_o = self.working_plane.Base
            if self._hit_test_perp(ray_p, ray_d, [wp_o], tolerance=self._compute_handle_radius(wp_o))[0] is not None:
                self._set_cursor(QtCore.Qt.SizeAllCursor)
                return True
        return super()._tool_hover(event_dict)

    def _tool_press(self, event_dict):
        ray_p, ray_d = self._get_ray(event_dict)
        if self.working_plane and ray_p and ray_d:
            wp_o = self.working_plane.Base
            if self._hit_test_perp(ray_p, ray_d, [wp_o], tolerance=self._compute_handle_radius(wp_o))[0] is not None:
                self._wp_drag_start = wp_o
                self._dragging_idx = "wp"
                self._set_cursor(QtCore.Qt.SizeAllCursor)
                self._start_drag_timer()
                return True
        return super()._tool_press(event_dict)

    def _tool_drag_update(self):
        if getattr(self, "_dragging_idx", None) == "marquee":
            return super()._tool_drag_update()
        if self._drag_check_lmb_released():
            return
        if getattr(self, "_dragging_idx", None) == "wp":
            mouse_pos = FldInputManager.get_instance()._last_qt_pos
            event_dict = {"Position": mouse_pos}
            snap_result = self.projector.get_mouse_plane_pt(
                event_dict, place_on_geometry=True,
                working_plane=self.working_plane)
            pt_global = snap_result[0] if snap_result else None
            if pt_global:
                delta = pt_global - self._wp_drag_start
                self.working_plane.Base += delta
                self._wp_drag_start = pt_global
                if self._target_obj:
                    plac = self._target_obj.Placement
                    plac.Base = plac.Base + delta
                    self._target_obj.Placement = plac
                    self._target_obj.touch()
                    vp = self._target_obj.ViewObject
                    if vp and hasattr(vp, "Proxy") and vp.Proxy:
                        vp.Proxy.updateData(self._target_obj, "Points")
                    if self.view:
                        self.view.redraw()
            self._update_gizmo()
            self._selection_changed()
            return
        super()._tool_drag_update()

    def on_button1_up(self, _event_dict):
        if getattr(self, "_dragging_idx", None) == "wp":
            self._stop_drag_timer()
            self._dragging_idx = None
            self._wp_drag_start = None
            if self._target_obj and self._target_obj.Document:
                doc = self._target_obj.Document
                QtCore.QTimer.singleShot(0, doc.recompute)
        self._gizmo_release()
        return False

    def on_button2_down(self, event_dict):
        # Allow middle mouse for view rotation
        return False

    def _update_element(self, element, new_pos):
        idx, elem_type = element
        shape_type = getattr(self._target_obj, "ShapeType", None)
        if shape_type == "point":
            if elem_type == "Point":
                world_pos = self._target_obj.Placement.multVec(new_pos)
                if hasattr(self._target_obj, "Coordinates"):
                    self._target_obj.Coordinates = world_pos
                if hasattr(self._target_obj, "Position"):
                    self._target_obj.Position = world_pos
                self._target_obj.touch()
                if not getattr(self, "_is_dragging", False):
                    if self._target_obj.Document:
                        doc = self._target_obj.Document
                        QtCore.QTimer.singleShot(0, doc.recompute)
                else:
                    if self.view:
                        self.view.redraw()
            return

        pts = list(getattr(self._target_obj, "Points", []))
        h_in = list(getattr(self._target_obj, "HandleIn", []))
        h_out = list(getattr(self._target_obj, "HandleOut", []))
        p_types = padded(getattr(self._target_obj, "PointTypes", []), len(pts), 0)
        h_types = padded(getattr(self._target_obj, "HandleTypes", []), 2 * len(pts), 0)
        
        if elem_type == "Point":
            delta = new_pos - pts[idx]
            pts[idx] = new_pos
            if idx < len(h_in): h_in[idx] += delta
            if idx < len(h_out): h_out[idx] += delta
            if hasattr(self, "linked_nodes") and self.linked_nodes and idx < len(self.linked_nodes):
                node = self.linked_nodes[idx]
                if node is not None:
                    node.Coordinates = self._target_obj.Placement.multVec(new_pos)
                    node.touch()
        elif elem_type in ("HandleIn", "HandleOut"):
            from freecad.fields.tools.curve_gizmo import follow_handle
            (h_in if elem_type == "HandleIn" else h_out)[idx] = new_pos
            follow_handle(pts, h_in, h_out, p_types, h_types, idx, elem_type)
                    
        self._target_obj.Points = pts
        self._target_obj.HandleIn = h_in
        self._target_obj.HandleOut = h_out
        self._target_obj.HandleTypes = h_types
        self._target_obj.touch()

        if not getattr(self, "_is_dragging", False):
            if self._target_obj.Document:
                doc = self._target_obj.Document
                QtCore.QTimer.singleShot(0, doc.recompute)
        else:
            # Interactive update during drag: rebuild shape + control cage
            vp = self._target_obj.ViewObject
            if vp and hasattr(vp, "Proxy") and vp.Proxy:
                vp.Proxy.updateData(self._target_obj, "Points")
            if self.view:
                self.view.redraw()

    def _insert_point(self, pos_global):
        """Insert a point into the curve at the given global position."""
        obj = self._target_obj
        if obj is None:
            return

        pos_local = obj.Placement.inverse().multVec(pos_global)
        pts = list(self._local_points())

        if len(pts) < 2:
            idx = len(pts)
        else:
            closed = bool(getattr(obj, "Closed", False))
            num_segs = len(pts) if closed else len(pts) - 1
            best_idx = 1
            min_d = float('inf')
            for i in range(num_segs):
                p1 = pts[i]
                p2 = pts[(i + 1) % len(pts)]
                v = p2 - p1
                w = pos_local - p1
                v_dot_v = v.dot(v)
                if v_dot_v > 1e-12:
                    t = w.dot(v) / v_dot_v
                    t = max(0.0, min(1.0, t))
                else:
                    t = 0.0
                proj = p1 + v * t
                d = (pos_local - proj).Length
                if d < min_d:
                    min_d = d
                    best_idx = len(pts) if (closed and i == len(pts) - 1) else i + 1
            idx = best_idx

        doc = getattr(self, "doc", None) or getattr(obj, "Document", None) or FreeCAD.ActiveDocument
        if doc and hasattr(doc, "openTransaction"):
            doc.openTransaction("Insert Point")

        # Node curve:
        if getattr(obj, "ControlNodes", None):
            node_name = "Node_0"
            cnt = 0
            if doc and hasattr(doc, "getObject"):
                while doc.getObject(node_name):
                    cnt += 1
                    node_name = f"Node_{cnt}"
            try:
                from freecad.fields.core.objects.fld_object import create_fld_object
                node = create_fld_object(node_name, "point", params={"Coordinates": pos_global})
            except Exception as e:
                fld_logger.debug(f"create_fld_object fallback in _insert_point: {e}")
                node = None
            if node is not None and doc and hasattr(doc, "recompute"):
                doc.recompute([node])
            nodes = list(obj.ControlNodes)
            nodes.insert(idx, node)
            obj.ControlNodes = nodes
            self.linked_nodes = list(obj.ControlNodes)
            if hasattr(self, "_created_node_names") and node is not None and hasattr(node, "Name"):
                self._created_node_names.append(node.Name)

        # Every list:
        points = list(getattr(obj, "Points", []))
        points.insert(idx, pos_local)
        obj.Points = points

        h_in = list(getattr(obj, "HandleIn", []))
        h_in.insert(idx, FreeCAD.Vector(pos_local))
        obj.HandleIn = h_in

        h_out = list(getattr(obj, "HandleOut", []))
        h_out.insert(idx, FreeCAD.Vector(pos_local))
        obj.HandleOut = h_out

        p_types = list(getattr(obj, "PointTypes", []))
        p_types.insert(idx, 0)
        obj.PointTypes = p_types

        h_types = list(getattr(obj, "HandleTypes", []))
        h_types.insert(2 * idx, 0)
        h_types.insert(2 * idx + 1, 0)
        obj.HandleTypes = h_types

        self._apply_handle_type(idx, 0)

        if doc and hasattr(doc, "commitTransaction"):
            doc.commitTransaction()

        obj.touch()
        self._set_selection([(idx, "Point")])
        self._update_gizmo()

        if doc and hasattr(doc, "recompute"):
            QtCore.QTimer.singleShot(0, doc.recompute)

        fld_logger.info("Inserted new point into curve")

    def _delete_points(self, indices):
        obj = self._target_obj
        if obj is None or not indices:
            return
        pts = list(getattr(obj, "Points", []))
        unique_indices = sorted(set(int(i) for i in indices))
        if len(pts) - len(unique_indices) < 2:
            fld_logger.warn("Cannot delete point: curve must have at least 2 points.")
            return

        doc = getattr(self, "doc", None) or getattr(obj, "Document", None) or FreeCAD.ActiveDocument
        if doc and hasattr(doc, "openTransaction"):
            doc.openTransaction("Delete Point")

        has_nodes = bool(getattr(obj, "ControlNodes", None))
        nodes = list(obj.ControlNodes) if has_nodes else []
        points = list(getattr(obj, "Points", []))
        h_in = list(getattr(obj, "HandleIn", []))
        h_out = list(getattr(obj, "HandleOut", []))
        p_types = list(getattr(obj, "PointTypes", []))
        h_types = list(getattr(obj, "HandleTypes", []))

        h_types = padded(h_types, 2 * len(points), 0)
        p_types = padded(p_types, len(points), 0)

        removed_node_objs = []
        for i in reversed(unique_indices):
            if i < 0 or i >= len(points):
                continue
            if has_nodes and i < len(nodes):
                removed_node = nodes.pop(i)
                removed_node_objs.append(removed_node)
            if i < len(points):
                points.pop(i)
            if i < len(h_in):
                h_in.pop(i)
            if i < len(h_out):
                h_out.pop(i)
            if i < len(p_types):
                p_types.pop(i)
            if 2 * i + 1 < len(h_types):
                h_types.pop(2 * i + 1)
            if 2 * i < len(h_types):
                h_types.pop(2 * i)

        if has_nodes:
            obj.ControlNodes = nodes
            self.linked_nodes = list(nodes)
            for node in removed_node_objs:
                if not getattr(node, "InList", None):
                    if doc and hasattr(doc, "removeObject") and hasattr(node, "Name"):
                        doc.removeObject(node.Name)

        obj.Points = points
        obj.HandleIn = h_in
        obj.HandleOut = h_out
        obj.PointTypes = p_types
        obj.HandleTypes = h_types

        if doc and hasattr(doc, "commitTransaction"):
            doc.commitTransaction()

        obj.touch()
        self._set_selection([])
        self._update_gizmo()

        if doc and hasattr(doc, "recompute"):
            QtCore.QTimer.singleShot(0, doc.recompute)

        fld_logger.info(f"Deleted points at indices {unique_indices}")

    def on_context_menu(self, event_dict):
        ray_p, ray_d = self._get_ray(event_dict)
        hit = self._hit_test(ray_p, ray_d)
        self._context_menu_hit = hit if hit else None

        self._context_menu_pos = None
        if self._target_obj and not hit:
            try:
                vd = self.view.getViewDirection()
                n = FreeCAD.Vector(-vd.x, -vd.y, -vd.z)
                o = self._target_obj.Placement.Base
                self._context_menu_pos = self.get_mouse_world_pos(event_dict, n, o)
            except Exception as e:
                fld_logger.debug(f"NurbsEditTool.on_context_menu: could not get cursor pos: {e}")

        try:
            return super().on_context_menu(event_dict)
        finally:
            self._context_menu_hit = None
            self._context_menu_pos = None

    def get_context_menu(self, event_dict=None):
        hit = getattr(self, "_context_menu_hit", None)
        shape_type = getattr(self._target_obj, "ShapeType", None) if self._target_obj else None
        items = []

        if shape_type == "curve" and hit is not None:
            idx, _ = hit
            h_types = list(getattr(self._target_obj, "HandleTypes", []))
            cur = h_types[2 * idx] if len(h_types) > 2 * idx else 0
            items += [
                MenuItem("Handle Type", children=[
                    MenuItem("Auto",    action=lambda i=idx: self._set_handle_type(i, 0), checked=(cur == 0)),
                    MenuItem("Vector",  action=lambda i=idx: self._set_handle_type(i, 1), checked=(cur == 1)),
                    MenuItem("Aligned", action=lambda i=idx: self._set_handle_type(i, 2), checked=(cur == 2)),
                    MenuItem("Free",    action=lambda i=idx: self._set_handle_type(i, 3), checked=(cur == 3)),
                ]),
                SEPARATOR,
            ]
        elif shape_type == "curve":
            pos = getattr(self, "_context_menu_pos", None)
            if pos is not None:
                items += [MenuItem("Insert Point Here", action=lambda p=pos: self._insert_point(p))]

        if shape_type == "curve":
            hit_idx = hit[0] if hit else None
            sel_keys = getattr(self, "_selection", set())
            sel_indices = [k[0] if isinstance(k, tuple) else k for k in sel_keys]
            if hit_idx is not None and hit_idx not in sel_indices:
                del_targets = [hit_idx]
            else:
                del_targets = sel_indices
            items += [
                MenuItem(
                    "Delete",
                    action=lambda targets=list(del_targets): self._delete_points(targets),
                    enabled=bool(del_targets),
                    shortcut="Del",
                )
            ]

        items += [
            SEPARATOR,
            MenuItem("Accept", action=self.finish, shortcut="Return"),
            MenuItem("Cancel", action=self.cancel, shortcut="Esc"),
        ]
        return self._with_transform_space(items)

    def _set_handle_type(self, idx, val):
        pts_len = len(getattr(self._target_obj, "Points", []))
        h_types = padded(getattr(self._target_obj, "HandleTypes", []), 2 * pts_len, 0)
        h_types[2 * idx] = val
        h_types[2 * idx + 1] = val
        self._target_obj.HandleTypes = h_types
        
        p_types = padded(getattr(self._target_obj, "PointTypes", []), pts_len, 0)
        if val == 3:
            p_types[idx] = 2
        elif val == 2:
            p_types[idx] = 1
        else:
            p_types[idx] = 0
        self._target_obj.PointTypes = p_types
        
        self._apply_handle_type(idx, val)
        self._target_obj.touch()
        obj = self._target_obj
        view = self.view
        def _deferred_recompute():
            if obj.Document:
                obj.Document.recompute()
            vp = obj.ViewObject
            if vp and hasattr(vp, "Proxy") and vp.Proxy:
                vp.Proxy.updateData(obj, "Points")
            if view:
                view.redraw()
        QtCore.QTimer.singleShot(0, _deferred_recompute)

    def _apply_handle_type(self, idx, handle_type):
        if not self._target_obj:
            return
        pts = list(getattr(self._target_obj, "Points", []))
        h_in = list(getattr(self._target_obj, "HandleIn", []))
        h_out = list(getattr(self._target_obj, "HandleOut", []))
        n = len(pts)
        if idx >= n or idx >= len(h_in) or idx >= len(h_out):
            return
            
        p = pts[idx]
        is_closed = getattr(self._target_obj, "Closed", False)
        if is_closed:
            prev_p = pts[(idx - 1) % n]
            next_p = pts[(idx + 1) % n]
        else:
            prev_p = pts[max(0, idx - 1)]
            next_p = pts[min(n - 1, idx + 1)]

        if handle_type == 1:  # Vector
            h_out[idx] = p + (next_p - p) * (1.0 / 3.0)
            h_in[idx]  = p + (prev_p - p) * (1.0 / 3.0)
        elif handle_type == 0:  # Auto
            chord_out = (next_p - p).Length
            chord_in  = (p - prev_p).Length
            tangent = next_p - prev_p
            tlen = tangent.Length
            if tlen > 1e-6:
                tangent = tangent * (1.0 / tlen)
            else:
                tangent = FreeCAD.Vector(0, 0, 0)
            h_out[idx] = p + tangent * (chord_out / 3.0)
            h_in[idx]  = p - tangent * (chord_in  / 3.0)

        self._target_obj.HandleIn = h_in
        self._target_obj.HandleOut = h_out


    def restore_original(self):
        if not self._target_obj or not hasattr(self, "_original_props"):
            return
        shape_type = getattr(self._target_obj, "ShapeType", None)
        if shape_type == "point":
            orig = self._original_props["Coordinates"]
            if hasattr(self._target_obj, "Coordinates"):
                self._target_obj.Coordinates = orig
            if hasattr(self._target_obj, "Position"):
                self._target_obj.Position = orig
            self._target_obj.touch()
        else:
            doc = getattr(self, "doc", None) or getattr(self._target_obj, "Document", None) or FreeCAD.ActiveDocument
            if "Nodes" in self._original_props:
                from freecad.fields.core.objects.fld_object import create_fld_object
                restored_nodes = []
                for name, coord in self._original_props["Nodes"]:
                    node = doc.getObject(name) if doc and hasattr(doc, "getObject") else None
                    if node is None:
                        try:
                            node = create_fld_object(name, "point", params={"Coordinates": coord})
                        except Exception as e:
                            fld_logger.debug(f"restore_original create_fld_object fallback: {e}")
                            node = None
                        if node is not None and doc and hasattr(doc, "recompute"):
                            doc.recompute([node])
                    if node is not None:
                        node.Coordinates = coord
                        node.touch()
                    restored_nodes.append(node)
                self._target_obj.ControlNodes = restored_nodes
                self.linked_nodes = list(restored_nodes)
            if hasattr(self, "_created_node_names") and self._created_node_names:
                for name in list(self._created_node_names):
                    node = doc.getObject(name) if doc and hasattr(doc, "getObject") else None
                    if node is None:
                        continue
                    in_list = getattr(node, "InList", []) or []
                    linked = [o for o in in_list if hasattr(o, "ControlNodes") and o.ControlNodes and node in o.ControlNodes]
                    if not linked:
                        if doc and hasattr(doc, "removeObject") and hasattr(node, "Name"):
                            doc.removeObject(node.Name)
                self._created_node_names = []
            self._target_obj.Points = self._original_props["Points"]
            self._target_obj.HandleIn = self._original_props["HandleIn"]
            self._target_obj.HandleOut = self._original_props["HandleOut"]
            self._target_obj.HandleTypes = self._original_props["HandleTypes"]
            self._target_obj.PointTypes = self._original_props["PointTypes"]
        self._target_obj.touch()
        obj = self._target_obj
        view = self.view
        def _deferred_recompute():
            if obj.Document:
                obj.Document.recompute()
            vp = obj.ViewObject
            if vp and hasattr(vp, "Proxy") and vp.Proxy:
                if shape_type == "curve":
                    vp.Proxy.updateData(obj, "Points")
            if view:
                view.redraw()
        QtCore.QTimer.singleShot(0, _deferred_recompute)

    def cancel(self):
        if self._target_obj:
            self._target_obj.EditMode = False
        self._is_editing = False
        self._restore_cursor()
        super().cancel()

    def finish(self):
        self._dispose_gizmo(commit=True)
        if self._target_obj:
            self._target_obj.EditMode = False
        self._is_editing = False
        self._restore_cursor()
        self.terminate()

    def _do_terminate(self):
        try:
            self._dispose_gizmo()
            if hasattr(self, "_sel_markers"):
                for m in self._sel_markers:
                    m.undraw()
                self._sel_markers = []
            if getattr(self, "points_root", None) is not None:
                if getattr(self, "view", None) and self.view.getSceneGraph():
                    self.view.getSceneGraph().removeChild(self.points_root)
                self.points_root = None
            if self._target_obj:
                self._target_obj.EditMode = False
        except Exception as e:
            fld_logger.debug(f"NurbsEditTool._do_terminate exception: {e}")
        self._restore_cursor()
        super()._do_terminate()


