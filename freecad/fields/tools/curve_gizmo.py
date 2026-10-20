# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""The curve editor's universal gizmo: the handle-follow rule and `CurveGizmoMixin`'s hooks (Round 15)."""

import FreeCAD
from FreeCAD import Vector
from PySide import QtCore

from freecad.fields.core.input.gizmo.models import ALL_HANDLES, handle_set
from freecad.fields.core.input.gizmo.transform import point_map
from freecad.fields.core.objects.fld_point import FldPoint
from freecad.fields.tools.selection_gizmo import SelectionGizmoMixin, SelectionGizmoTarget

MOVE_HANDLES = handle_set(move="xyz", planes=("xy", "xz", "yz"), free_move=True)


def padded(values, n, fill=0):
    """`values` as a list of at least `n` entries, `fill` appended."""
    values = list(values)
    return values + [fill] * (n - len(values))


def follow_handle(pts, h_in, h_out, p_types, h_types, idx, which):
    """After handle `which` ("HandleIn" or "HandleOut") of point `idx` moved on its own, its
    partner follows the point's type and both handle types change, as a hand drag of that
    handle does. Tangent (PointTypes 0) mirrors the partner through the point; Split (1) turns
    it opposite, keeping its length; Custom (2) leaves it. Both HandleTypes become Free (3)
    for Custom, else Aligned (2). Edits the lists in place; `p_types` and `h_types` must
    already be padded to the curve's length (`_update_element` pads them)."""
    moved, other = (h_in, h_out) if which == "HandleIn" else (h_out, h_in)
    pt_type = p_types[idx]
    delta = moved[idx] - pts[idx]
    if pt_type == 0:
        other[idx] = pts[idx] - delta
    elif pt_type == 1 and delta.Length > 1e-6:
        length = (other[idx] - pts[idx]).Length
        other[idx] = pts[idx] - delta * (length / delta.Length)
    new_type = 3 if pt_type == 2 else 2
    h_types[2 * idx] = new_type
    h_types[2 * idx + 1] = new_type


class CurveGizmoTarget(SelectionGizmoTarget):
    def offered(self):
        sel = self.tool._selection
        if len(sel) == 1:
            key = sel[0]
            if isinstance(key, tuple):
                idx, elem_type = key
                if elem_type in ("HandleIn", "HandleOut"):
                    return MOVE_HANDLES
                if elem_type == "Point":
                    obj = self.tool._target_obj
                    h_in = getattr(obj, "HandleIn", [])
                    h_out = getattr(obj, "HandleOut", [])
                    if idx >= len(h_in) and idx >= len(h_out):
                        return MOVE_HANDLES
            else:
                # Point object key (or non-tuple)
                return MOVE_HANDLES
        return ALL_HANDLES

    def geometry(self):
        obj = self.tool._target_obj
        if obj is None:
            return {}
        data = {"placement": FreeCAD.Placement(obj.Placement)}
        shape_type = getattr(obj, "ShapeType", None)
        if shape_type == "point":
            p = getattr(obj, "Coordinates", getattr(obj, "Position", Vector()))
            data["coords"] = Vector(p)
        else:
            pts = list(self.tool._local_points())
            h_in = [Vector(v) for v in getattr(obj, "HandleIn", [])]
            h_out = [Vector(v) for v in getattr(obj, "HandleOut", [])]
            p_types = padded(getattr(obj, "PointTypes", []), len(pts), 0)
            h_types = padded(getattr(obj, "HandleTypes", []), 2 * len(pts), 0)
            data["points"] = pts
            data["h_in"] = h_in
            data["h_out"] = h_out
            data["p_types"] = p_types
            data["h_types"] = h_types
        return data

    def gizmo_apply(self, snap, delta):
        obj = self.tool._target_obj
        if obj is None:
            return
        h = point_map(delta)
        pl = snap["placement"]
        inv = pl.inverse()

        def g(v):
            return inv.multVec(h(pl.multVec(v)))

        shape_type = getattr(obj, "ShapeType", None)
        if shape_type == "point":
            new_coords = h(snap["coords"])
            self.tool._write_elements({"coords": new_coords}, moved_points=[])
            return

        pts = [Vector(v) for v in snap["points"]]
        h_in = [Vector(v) for v in snap["h_in"]]
        h_out = [Vector(v) for v in snap["h_out"]]
        p_types = list(snap["p_types"])
        h_types = list(snap["h_types"])

        selected_points = set()
        selected_handles = {}  # idx -> set of ("HandleIn", "HandleOut")

        sel = snap["selection"]
        for key in sel:
            if isinstance(key, tuple):
                idx, elem_type = key
                if elem_type == "Point":
                    selected_points.add(idx)
                elif elem_type in ("HandleIn", "HandleOut"):
                    selected_handles.setdefault(idx, set()).add(elem_type)

        # Map each selected Point i, pts[i], h_in[i], h_out[i]
        for i in selected_points:
            if i < len(pts):
                pts[i] = g(pts[i])
            if i < len(h_in):
                h_in[i] = g(h_in[i])
            if i < len(h_out):
                h_out[i] = g(h_out[i])

        # Loose handles (handles whose point is not selected)
        for idx, handle_set_types in selected_handles.items():
            if idx in selected_points:
                continue
            if "HandleIn" in handle_set_types and "HandleOut" in handle_set_types:
                # Both handles selected, no point: both map on their own, neither follows
                if idx < len(h_in):
                    h_in[idx] = g(h_in[idx])
                if idx < len(h_out):
                    h_out[idx] = g(h_out[idx])
                pt_type = p_types[idx] if idx < len(p_types) else 0
                new_type = 3 if pt_type == 2 else 2
                h_types[2 * idx] = new_type
                h_types[2 * idx + 1] = new_type
            elif "HandleIn" in handle_set_types:
                if idx < len(h_in):
                    h_in[idx] = g(h_in[idx])
                    follow_handle(pts, h_in, h_out, p_types, h_types, idx, "HandleIn")
            elif "HandleOut" in handle_set_types:
                if idx < len(h_out):
                    h_out[idx] = g(h_out[idx])
                    follow_handle(pts, h_in, h_out, p_types, h_types, idx, "HandleOut")

        lists = {
            "points": pts,
            "h_in": h_in,
            "h_out": h_out,
            "p_types": p_types,
            "h_types": h_types,
        }
        self.tool._write_elements(lists, moved_points=list(selected_points))

    def gizmo_restore(self, snap):
        shape_type = getattr(self.tool._target_obj, "ShapeType", None)
        if shape_type == "point":
            self.tool._write_elements({"coords": snap["coords"]}, moved_points=[])
        else:
            sel = snap["selection"]
            selected_points = [k[0] for k in sel if isinstance(k, tuple) and k[1] == "Point"]
            lists = {
                "points": [Vector(v) for v in snap["points"]],
                "h_in": [Vector(v) for v in snap["h_in"]],
                "h_out": [Vector(v) for v in snap["h_out"]],
                "p_types": list(snap["p_types"]),
                "h_types": list(snap["h_types"]),
            }
            self.tool._write_elements(lists, moved_points=selected_points)
        doc = getattr(self.tool._target_obj, "Document", None)
        if doc is not None:
            QtCore.QTimer.singleShot(0, doc.recompute)

    def gizmo_commit(self):
        doc = getattr(self.tool._target_obj, "Document", None)
        if doc is not None:
            QtCore.QTimer.singleShot(0, doc.recompute)


class CurveGizmoMixin(SelectionGizmoMixin):
    GIZMO_TARGET = CurveGizmoTarget
    MARQUEE = True

    @property
    def _selected_element(self):
        return self._selection[-1] if self._selection else None

    @_selected_element.setter
    def _selected_element(self, val):
        self._selection = [] if val is None else [val]

    @property
    def _selected_point_idx(self):
        el = self._selected_element
        if el is None:
            return None
        if isinstance(el, tuple):
            return el[0]
        return el

    def _selection_changed(self):
        self._sel_point_count = len(self._local_points())
        markers = getattr(self, "_sel_markers", None)
        if markers is not None:
            for m in markers:
                m.undraw()
            self._sel_markers = []
        else:
            self._sel_markers = []

        root = getattr(self, "points_root", None)
        if root is None:
            return

        for k in self._selection:
            pos = self._point_world(k)
            if pos is not None:
                r = 1.3 * self._compute_handle_radius(pos)
                pt = FldPoint(pos)
                pt.draw_point(root, radius=r, color=(1.0, 1.0, 1.0))
                self._sel_markers.append(pt)

    def _local_points(self):
        obj = self._target_obj
        if obj is None:
            return []
        if hasattr(obj, "ControlNodes") and obj.ControlNodes:
            inv = obj.Placement.inverse()
            return [inv.multVec(n.Coordinates) for n in obj.ControlNodes if n is not None]
        return list(getattr(obj, "Points", []))

    def _gizmo_editable(self):
        return self._target_obj is not None

    def _pick_point(self, ray_p, ray_d):
        return self._hit_test(ray_p, ray_d)

    def _point_world(self, key):
        obj = self._target_obj
        if obj is None:
            return Vector()
        shape_type = getattr(obj, "ShapeType", None)
        if shape_type == "point":
            return getattr(obj, "Coordinates", getattr(obj, "Position", Vector()))
        if isinstance(key, tuple):
            idx, elem_type = key
            pl = obj.Placement
            if elem_type == "Point":
                pts = self._local_points()
                if 0 <= idx < len(pts):
                    return pl.multVec(pts[idx])
            elif elem_type == "HandleIn":
                h_in = getattr(obj, "HandleIn", [])
                if 0 <= idx < len(h_in):
                    return pl.multVec(h_in[idx])
            elif elem_type == "HandleOut":
                h_out = getattr(obj, "HandleOut", [])
                if 0 <= idx < len(h_out):
                    return pl.multVec(h_out[idx])
        return Vector()

    def _selectable_keys(self):
        """The marquee's keys: the curve's points. Handles are click-only."""
        obj = self._target_obj
        if obj is None:
            return []
        if getattr(obj, "ShapeType", None) == "point":
            return [(0, "Point")]
        return [(i, "Point") for i in range(len(self._local_points()))]

    def _gizmo_unselected_centre(self):
        return None

    def _key_exists(self, key):
        obj = self._target_obj
        if obj is None:
            return False
        if getattr(obj, "ShapeType", None) == "point":
            return key == (0, "Point")
        if not isinstance(key, tuple) or len(key) != 2:
            return False
        idx, elem_type = key
        if elem_type == "Point":
            return 0 <= idx < len(self._local_points())
        elif elem_type == "HandleIn":
            return 0 <= idx < len(getattr(obj, "HandleIn", []))
        elif elem_type == "HandleOut":
            return 0 <= idx < len(getattr(obj, "HandleOut", []))
        return False

    def _reload_after_undo(self):
        obj = self._target_obj
        if obj is not None and hasattr(obj, "ControlNodes"):
            self.linked_nodes = list(obj.ControlNodes or [])
        if len(self._local_points()) != getattr(self, "_sel_point_count", None):
            self._set_selection([])
        super()._reload_after_undo()

    def _gizmo_exclude(self):
        if self._target_obj is None:
            return []
        res = [self._target_obj]
        if hasattr(self, "linked_nodes") and self.linked_nodes:
            res.extend(n for n in self.linked_nodes if n is not None)
        elif hasattr(self._target_obj, "ControlNodes") and self._target_obj.ControlNodes:
            res.extend(n for n in self._target_obj.ControlNodes if n is not None)
        return res

    def _write_elements(self, data, moved_points=None):
        obj = self._target_obj
        if obj is None:
            return
        shape_type = getattr(obj, "ShapeType", None)
        if shape_type == "point":
            coords = data["coords"]
            if hasattr(obj, "Coordinates"):
                obj.Coordinates = coords
            if hasattr(obj, "Position"):
                obj.Position = coords
            obj.touch()
        else:
            obj.Points = data["points"]
            obj.HandleIn = data["h_in"]
            obj.HandleOut = data["h_out"]
            obj.HandleTypes = data["h_types"]
            obj.PointTypes = data["p_types"]
            pl = obj.Placement
            if moved_points:
                nodes = getattr(self, "linked_nodes", getattr(obj, "ControlNodes", []))
                for i in moved_points:
                    if nodes and i < len(nodes):
                        node = nodes[i]
                        if node is not None:
                            node.Coordinates = pl.multVec(data["points"][i])
                            node.touch()
            obj.touch()
            vp = getattr(obj, "ViewObject", None)
            if vp is not None and hasattr(vp, "Proxy") and hasattr(vp.Proxy, "updateData"):
                vp.Proxy.updateData(obj, "Points")
        self._selection_changed()
        if getattr(self, "view", None) is not None:
            self.view.redraw()
