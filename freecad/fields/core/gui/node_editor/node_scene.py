# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/gui/node_editor/node_scene.py

QGraphicsScene subclass managing node cards, wire connections, and interactive wiring.
"""
import json
import uuid
from PySide import QtWidgets, QtGui, QtCore
from freecad.fields.core import fld_logger
from freecad.fields.core.gui.node_editor.node_items import (
    NodeCardItem, NodeSocketItem, NodeWireItem
)
from freecad.fields.core.gui.node_editor.nodes import (
    NODE_REGISTRY, compile_graph
)


class NodeGraphScene(QtWidgets.QGraphicsScene):
    """Scene managing graph nodes and interactive wiring."""
    graphChanged = QtCore.Signal()
    open_subgraph_requested = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(-3000, -3000, 6000, 6000)
        self.nodes = {}  # node_id -> NodeCardItem
        self.wires = []  # list of NodeWireItem
        self._drag_wire = None
        self._drag_start_socket = None
        self._pending_connection_socket = None
        self._press_pos = None
        self._press_sock = None
        self._press_wire = None
        self._ctrl_promoted = False
        self._is_loading = False

    def add_node(self, node_type, pos=None, params=None, node_id=None):
        if not node_id:
            node_id = f"node_{uuid.uuid4().hex[:6]}"
        card = NodeCardItem(node_id, node_type, params)
        if pos:
            card.setPos(QtCore.QPointF(pos[0], pos[1]))
        card.on_changed_cb = self._on_node_changed
        card._scene_ref = self
        self.addItem(card)
        self.nodes[node_id] = card
        if not self._is_loading:
            self.graphChanged.emit()
        return card

    def remove_node(self, node_item):
        if isinstance(node_item, str):
            node_item = self.nodes.get(node_item)
        if not node_item:
            return

        # Remove all attached wires
        for s in node_item.input_sockets + node_item.output_sockets:
            for w in list(s.wires):
                self.remove_wire(w)

        nid = node_item.node_id
        if nid in self.nodes:
            del self.nodes[nid]
        self.removeItem(node_item)
        if not self._is_loading:
            self.graphChanged.emit()

    def connect_sockets(self, from_socket, to_socket):
        """Connect an output socket to an input socket."""
        if from_socket.is_input and not to_socket.is_input:
            from_socket, to_socket = to_socket, from_socket

        if from_socket.is_input or not to_socket.is_input:
            return None  # Cannot connect in-to-in or out-to-out

        if from_socket.parent_node == to_socket.parent_node:
            return None  # Cannot connect node to itself

        # Input socket can only have one connection: remove existing
        for w in list(to_socket.wires):
            self.remove_wire(w)

        wire = NodeWireItem(start_socket=from_socket, end_socket=to_socket)
        self.addItem(wire)
        self.wires.append(wire)
        from_socket.wires.append(wire)
        to_socket.wires.append(wire)
        wire.update_path()
        from_socket.update()
        to_socket.update()
        self._notify_socket_wire_changed(from_socket)
        self._notify_socket_wire_changed(to_socket)

        if not self._is_loading:
            self.graphChanged.emit()
        return wire

    def remove_wire(self, wire_item):
        if wire_item in self.wires:
            self.wires.remove(wire_item)
        if wire_item.start_socket and wire_item in wire_item.start_socket.wires:
            wire_item.start_socket.wires.remove(wire_item)
            wire_item.start_socket.update()
            self._notify_socket_wire_changed(wire_item.start_socket)
        if wire_item.end_socket and wire_item in wire_item.end_socket.wires:
            wire_item.end_socket.wires.remove(wire_item)
            wire_item.end_socket.update()
            self._notify_socket_wire_changed(wire_item.end_socket)
        self.removeItem(wire_item)
        if not self._is_loading:
            self.graphChanged.emit()

    def _notify_socket_wire_changed(self, socket_item):
        node = getattr(socket_item, "parent_node", None)
        if node is not None and hasattr(node, "on_socket_wire_changed"):
            node.on_socket_wire_changed(socket_item)

    def _on_node_changed(self):
        if not self._is_loading:
            self.graphChanged.emit()

    def clear_graph(self):
        self._is_loading = True
        for w in list(self.wires):
            self.remove_wire(w)
        for n in list(self.nodes.values()):
            self.remove_node(n)
        self.nodes.clear()
        self.wires.clear()
        self._is_loading = False
        self.graphChanged.emit()

    @staticmethod
    def _wire_to_dict(w):
        return {
            "from_node": w.start_socket.parent_node.node_id,
            "from_socket": w.start_socket.socket_def.name,
            "to_node": w.end_socket.parent_node.node_id,
            "to_socket": w.end_socket.socket_def.name,
        }

    def _connect_wires_from_data(self, wires_data, id_map=None):
        for wdata in wires_data:
            fn_id = wdata.get("from_node")
            tn_id = wdata.get("to_node")
            if id_map is not None:
                fn_id = id_map.get(fn_id)
                tn_id = id_map.get(tn_id)
            fn = self.nodes.get(fn_id)
            tn = self.nodes.get(tn_id)
            if not (fn and tn):
                continue
            fs_name = wdata.get("from_socket")
            ts_name = wdata.get("to_socket")
            fs = next((s for s in fn.output_sockets if s.socket_def.name == fs_name), None)
            ts = next((s for s in tn.input_sockets if s.socket_def.name == ts_name), None)
            if fs and ts:
                self.connect_sockets(fs, ts)

    def get_graph_data(self):
        nodes_data = [card.to_dict() for card in self.nodes.values()]
        wires_data = [self._wire_to_dict(w) for w in self.wires if w.start_socket and w.end_socket]
        return {"version": 1, "nodes": nodes_data, "wires": wires_data}

    def load_graph_data(self, data):
        self.clear_graph()
        self._is_loading = True

        for ndata in data.get("nodes", []):
            nid = ndata.get("id")
            ntype = ndata.get("type")
            pos = ndata.get("pos", [0, 0])
            params = ndata.get("params", {})
            self.add_node(ntype, pos, params, node_id=nid)

        self._connect_wires_from_data(data.get("wires", []))

        self._is_loading = False
        self.graphChanged.emit()

    CLIPBOARD_PREFIX = "FIELDS_NODE_CLIPBOARD:\n"

    def copy_selection(self, set_clipboard=True):
        """Serialize selected nodes and internal wires. If set_clipboard is True, writes to system clipboard."""
        selected_nodes = [item for item in self.selectedItems() if isinstance(item, NodeCardItem)]
        if not selected_nodes:
            return None
        selected_ids = {node.node_id for node in selected_nodes}
        nodes_data = [card.to_dict() for card in selected_nodes]
        wires_data = [
            self._wire_to_dict(w) for w in self.wires
            if w.start_socket and w.end_socket
            and w.start_socket.parent_node.node_id in selected_ids
            and w.end_socket.parent_node.node_id in selected_ids
        ]
        data = {"version": 1, "nodes": nodes_data, "wires": wires_data}
        if set_clipboard:
            cb = QtWidgets.QApplication.clipboard()
            if cb is not None:
                cb.setText(self.CLIPBOARD_PREFIX + json.dumps(data))
        return data

    def cut_selection(self):
        """Copies selection to clipboard and removes the selected nodes and wires."""
        data = self.copy_selection(set_clipboard=True)
        if not data:
            return None
        selected_nodes = [item for item in self.selectedItems() if isinstance(item, NodeCardItem)]
        for node in selected_nodes:
            self.remove_node(node)
        return data

    def paste(self, data=None, offset=QtCore.QPointF(30, 30)):
        """Paste node graph data, reminting IDs, offsetting positions, and selecting newly pasted nodes."""
        if data is None:
            cb = QtWidgets.QApplication.clipboard()
            if cb is None:
                return []
            text = cb.text()
            if not text or not text.startswith(self.CLIPBOARD_PREFIX):
                return []
            try:
                data = json.loads(text[len(self.CLIPBOARD_PREFIX):])
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                from freecad.fields.core import fld_logger
                fld_logger.render_debug(f"NodeGraphScene.paste: invalid clipboard data: {e}")
                return []

        nodes_data = data.get("nodes", [])
        if not nodes_data:
            return []

        self._is_loading = True

        # Remint node IDs: f"{old_id}_copy{n}" until unused
        id_map = {}
        new_nodes = []
        for ndata in nodes_data:
            old_id = ndata.get("id", "node")
            n = 1
            cand = f"{old_id}_copy"
            while cand in self.nodes or cand in id_map.values():
                n += 1
                cand = f"{old_id}_copy{n}"
            id_map[old_id] = cand

            ntype = ndata.get("type")
            pos = ndata.get("pos", [0, 0])
            new_pos = [pos[0] + offset.x(), pos[1] + offset.y()]
            params = ndata.get("params", {})
            card = self.add_node(ntype, new_pos, params, node_id=cand)
            new_nodes.append(card)

        # Wire connections using mapped IDs
        self._connect_wires_from_data(data.get("wires", []), id_map=id_map)

        # Clear selection and select newly pasted nodes
        self.clearSelection()
        for card in new_nodes:
            card.setSelected(True)

        self._is_loading = False
        self.graphChanged.emit()
        return new_nodes

    def duplicate_selection(self, offset=QtCore.QPointF(30, 30)):
        """Ctrl+D: duplicates selection without modifying system clipboard."""
        data = self.copy_selection(set_clipboard=False)
        if not data:
            return []
        return self.paste(data=data, offset=offset)

    # ── Interactive Wire Dragging ──────────────────────────────────────────────

    def _find_socket_at(self, scene_pos, exclude_socket=None):
        """Finds any NodeSocketItem near scene_pos within a comfortable snapping radius."""
        try:
            nearby_items = self.items(QtCore.QRectF(scene_pos.x() - 12, scene_pos.y() - 12, 24, 24))
            for item in nearby_items:
                if isinstance(item, NodeSocketItem) and item != exclude_socket:
                    return item
        except Exception as exc:  # safe: items(QRectF) is best-effort spatial query
            fld_logger.debug(f"[node_scene] find_socket_at items query failed: {exc}")
            pass

        try:
            item = self.itemAt(scene_pos, QtGui.QTransform())
            if isinstance(item, NodeSocketItem) and item != exclude_socket:
                return item
        except Exception as exc:  # safe: itemAt fallback query is best-effort
            fld_logger.debug(f"[node_scene] find_socket_at itemAt fallback failed: {exc}")
            pass
        return None

    def mousePressEvent(self, event):
        # If already dragging a wire, a click drops/connects/cancels it
        if self._drag_wire and self._drag_start_socket:
            target_sock = self._find_socket_at(event.scenePos(), self._drag_start_socket)
            self.removeItem(self._drag_wire)
            self._drag_wire = None
            start_sock = self._drag_start_socket
            self._drag_start_socket = None

            if target_sock is not None:
                self.connect_sockets(start_sock, target_sock)
            event.accept()
            return

        btn = getattr(event, "button", lambda: QtCore.Qt.NoButton)()
        event_mods = getattr(event, "modifiers", lambda: QtCore.Qt.NoModifier)()
        app_mods = getattr(QtWidgets.QApplication, "keyboardModifiers", lambda: QtCore.Qt.NoModifier)()
        modifiers = event_mods | app_mods
        ctrl_mod = getattr(QtCore.Qt, "ControlModifier", 0x04000000)
        left_btn = getattr(QtCore.Qt, "LeftButton", 0x00000001)

        sock = self._find_socket_at(event.scenePos())
        item = self.itemAt(event.scenePos(), QtGui.QTransform())
        if sock is None and isinstance(item, NodeSocketItem):
            sock = item

        wire_item = item if isinstance(item, NodeWireItem) else None
        if not wire_item and item is None:
            try:
                nearby = self.items(QtCore.QRectF(event.scenePos().x() - 10, event.scenePos().y() - 10, 20, 20))
                for it in nearby:
                    if isinstance(it, NodeWireItem):
                        wire_item = it
                        break
            except Exception as exc:  # safe: nearby wire hit-test query is best-effort
                fld_logger.debug(f"[node_scene] mousePressEvent wire hit-test failed: {exc}")
                pass

        self._press_sock = sock
        self._press_wire = wire_item
        self._press_pos = event.scenePos()
        self._press_modifiers = modifiers
        self._ctrl_promoted = False

        if btn == left_btn:
            # ── 1. Socket Click / Drag ─────────────────────────────────────────
            if sock is not None:
                if not sock.is_input:
                    # Output socket: start wire drag
                    self._drag_start_socket = sock
                    self._drag_wire = NodeWireItem(start_socket=sock, temp_end_pos=event.scenePos())
                    self.addItem(self._drag_wire)
                    event.accept()
                    return
                elif not sock.wires:
                    # Unconnected input: drag from input
                    self._drag_start_socket = sock
                    self._drag_wire = NodeWireItem(start_socket=sock, temp_end_pos=event.scenePos())
                    self.addItem(self._drag_wire)
                    event.accept()
                    return
                # Linked input: a plain click/drag here must NOT touch the
                # existing wire (that used to unplug it on an ordinary click --
                # NE-017). Ctrl turns the gesture into a pick-up, but the actual
                # detach happens later -- in mouseMoveEvent once real movement
                # clears a threshold, or in mouseReleaseEvent for a stationary
                # Ctrl+click -- never eagerly here, where a click and the start
                # of a drag look identical.

            # ── 2. Wire Click / Drag ───────────────────────────────────────────
            # Same deal for clicking a wire's body directly: only Ctrl detaches
            # it, decided by mouseMoveEvent/mouseReleaseEvent below, not here.

        super().mousePressEvent(event)

    def _find_press_wire(self, require_input_sock):
        """Resolve the wire (if any) that mousePressEvent recorded under the cursor."""
        press_sock = getattr(self, "_press_sock", None)
        press_wire = getattr(self, "_press_wire", None)
        if press_sock is not None and press_sock.wires and (not require_input_sock or press_sock.is_input):
            return press_sock.wires[-1]
        if press_wire is not None and press_wire in self.wires:
            return press_wire
        return None

    def _detach_wire_to_drag(self, wire, event_pos):
        """Remove `wire` and start a new drag wire from its source socket."""
        source_out = wire.start_socket
        self.remove_wire(wire)
        self._drag_start_socket = source_out
        self._drag_wire = NodeWireItem(start_socket=source_out, temp_end_pos=event_pos)
        self.addItem(self._drag_wire)

    def mouseMoveEvent(self, event):
        if self._drag_wire is None:
            # A Ctrl-drag that started on a linked input socket or directly on
            # a wire's body detaches that wire once the drag clears a small
            # threshold -- a stationary Ctrl+click is handled by
            # mouseReleaseEvent instead, so a single click can pick a wire up
            # and hold it for a later drop (see mousePressEvent above, NE-017).
            ctrl_mod = getattr(QtCore.Qt, "ControlModifier", 0x04000000)
            get_mods = getattr(QtWidgets.QApplication, "keyboardModifiers", lambda: QtCore.Qt.NoModifier)
            press_pos = getattr(self, "_press_pos", None)
            if (get_mods() & ctrl_mod) and press_pos is not None:
                dx = event.scenePos().x() - press_pos.x()
                dy = event.scenePos().y() - press_pos.y()
                if (dx * dx + dy * dy) ** 0.5 >= 6:
                    wire = self._find_press_wire(require_input_sock=True)
                    if wire is not None and wire.start_socket is not None:
                        self._detach_wire_to_drag(wire, event.scenePos())
                        self._ctrl_promoted = True
                        event.accept()
                        return

        if self._drag_wire and self._drag_start_socket:
            self._drag_wire.temp_end_pos = event.scenePos()
            self._drag_wire.update_path()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        btn = getattr(event, "button", lambda: QtCore.Qt.NoButton)()
        event_mods = getattr(event, "modifiers", lambda: QtCore.Qt.NoModifier)()
        app_mods = getattr(QtWidgets.QApplication, "keyboardModifiers", lambda: QtCore.Qt.NoModifier)()
        modifiers = event_mods | app_mods
        ctrl_mod = getattr(QtCore.Qt, "ControlModifier", 0x04000000)

        press_pos = getattr(self, "_press_pos", None)
        moved_dist = 0
        if press_pos is not None:
            dx = event.scenePos().x() - press_pos.x()
            dy = event.scenePos().y() - press_pos.y()
            moved_dist = (dx * dx + dy * dy) ** 0.5

        # Ctrl + click (no drag) detaches the connection and leaves it on the cursor.
        # The next click drops it -- mousePressEvent's first branch handles that. Delete
        # is Del / Backspace on a selected wire, or the Ctrl+RMB knife; Ctrl+click used to
        # delete, which made re-routing a wire a delete-then-redraw.
        # `not _ctrl_promoted` excludes a real Ctrl-drag that mouseMoveEvent already
        # detached -- without it, a drag that ends up back within 6px of the press
        # point (e.g. dragged out and back) would re-detach a wire that's already
        # gone, or clobber the in-progress drag wire outright.
        if (modifiers & ctrl_mod) and moved_dist < 6 and not self._ctrl_promoted:
            wire = self._find_press_wire(require_input_sock=False)

            if self._drag_wire:
                self.removeItem(self._drag_wire)
                self._drag_wire = None
                self._drag_start_socket = None

            if wire is not None and wire.start_socket is not None:
                self._detach_wire_to_drag(wire, event.scenePos())
                event.accept()
                return

        if self._drag_wire and self._drag_start_socket:
            start_sock = self._drag_start_socket
            target_sock = self._find_socket_at(event.scenePos(), start_sock)
            if target_sock is not None:
                self.removeItem(self._drag_wire)
                self._drag_wire = None
                self._drag_start_socket = None
                self.connect_sockets(start_sock, target_sock)
                event.accept()
                return

            # Releasing in empty space:
            wire_start = start_sock.get_center_scene_pos()
            dx = event.scenePos().x() - wire_start.x()
            dy = event.scenePos().y() - wire_start.y()
            if (dx * dx + dy * dy) > 100:  # dragged > 10px into empty space
                self.removeItem(self._drag_wire)
                self._drag_wire = None
                self._drag_start_socket = None

                # Store pending connection socket for auto-attach to new node
                self._pending_connection_socket = start_sock

                # Request context menu at this position
                if self.views():
                    view = self.views()[0]
                    dlg = view.window()
                    if hasattr(dlg, "show_canvas_context_menu"):
                        vpos = view.mapFromScene(event.scenePos())
                        gpos = view.mapToGlobal(vpos)
                        QtCore.QTimer.singleShot(0, lambda vp=vpos, gp=gpos: dlg.show_canvas_context_menu(vp, gp))
                event.accept()
                return
            else:
                self.removeItem(self._drag_wire)
                self._drag_wire = None
                self._drag_start_socket = None
                event.accept()
                return

        super().mouseReleaseEvent(event)

    def cut_wires_intersecting(self, p1, p2):
        """Knife cut: removes any wires that intersect line segment (p1, p2)."""
        seg_path = QtGui.QPainterPath(p1)
        seg_path.lineTo(p2)
        stroker = QtGui.QPainterPathStroker()
        stroker.setWidth(6.0)
        seg_stroke = stroker.createStroke(seg_path)

        to_remove = []
        for w in list(self.wires):
            try:
                if hasattr(w, "collidesWithPath") and w.collidesWithPath(seg_stroke):
                    to_remove.append(w)
                elif hasattr(w, "shape") and hasattr(w.shape(), "intersects") and w.shape().intersects(seg_stroke):
                    to_remove.append(w)
                elif hasattr(w, "path") and hasattr(w.path(), "intersects") and w.path().intersects(seg_stroke):
                    to_remove.append(w)
            except Exception as exc:  # safe: wire path collision query is best-effort
                fld_logger.debug(f"[node_scene] _cut_wires_along_segment collision query failed: {exc}")
                pass

        # Fallback segment intersection test
        if not to_remove and hasattr(p1, "x") and hasattr(p2, "x"):
            def ccw(A, B, C):
                return (C.y() - A.y()) * (B.x() - A.x()) > (B.y() - A.y()) * (C.x() - A.x())

            def seg_intersect(A, B, C, D):
                return ccw(A, C, D) != ccw(B, C, D) and ccw(A, B, C) != ccw(A, B, D)

            for w in list(self.wires):
                if w in to_remove:
                    continue
                try:
                    if w.start_socket and w.end_socket:
                        wp1 = w.start_socket.get_center_scene_pos()
                        wp2 = w.end_socket.get_center_scene_pos()
                        if seg_intersect(p1, p2, wp1, wp2):
                            to_remove.append(w)
                            continue
                except Exception as exc:  # safe: wire endpoint intersection test is best-effort
                    fld_logger.debug(f"[node_scene] _cut_wires_along_segment endpoint intersection failed: {exc}")
                    pass

                try:
                    path = w.path()
                    if hasattr(path, "pointAtPercent"):
                        num_samples = 10
                        pts = [path.pointAtPercent(i / num_samples) for i in range(num_samples + 1)]
                        for i in range(num_samples):
                            if seg_intersect(p1, p2, pts[i], pts[i + 1]):
                                to_remove.append(w)
                                break
                except Exception as exc:  # safe: wire path sampling intersection test is best-effort
                    fld_logger.debug(f"[node_scene] _cut_wires_along_segment path sampling intersection failed: {exc}")
                    pass

        for w in to_remove:
            self.remove_wire(w)
        return len(to_remove)

    def keyPressEvent(self, event):
        # A focused embedded widget (the Name field on a Custom Parameter node, say)
        # is being typed into: Backspace there is a character, not "delete the
        # selected node". Only super() forwards the key to it, so this must run
        # before the Delete/Backspace branch, not after.
        focus_item = self.focusItem()
        if isinstance(focus_item, QtWidgets.QGraphicsProxyWidget):
            super().keyPressEvent(event)
            return
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            for item in self.selectedItems():
                if isinstance(item, NodeCardItem):
                    self.remove_node(item)
                elif isinstance(item, NodeWireItem):
                    self.remove_wire(item)
            event.accept()
            return
        super().keyPressEvent(event)
