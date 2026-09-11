# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/gui/node_editor/node_scene.py

QGraphicsScene subclass managing node cards, wire connections, and interactive wiring.
"""
import uuid
from PySide import QtWidgets, QtGui, QtCore
from freecad.fields.core.gui.node_editor.node_items import (
    NodeCardItem, NodeSocketItem, NodeWireItem
)
from freecad.fields.core.gui.node_editor.node_definitions import (
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

        if not self._is_loading:
            self.graphChanged.emit()
        return wire

    def remove_wire(self, wire_item):
        if wire_item in self.wires:
            self.wires.remove(wire_item)
        if wire_item.start_socket and wire_item in wire_item.start_socket.wires:
            wire_item.start_socket.wires.remove(wire_item)
            wire_item.start_socket.update()
        if wire_item.end_socket and wire_item in wire_item.end_socket.wires:
            wire_item.end_socket.wires.remove(wire_item)
            wire_item.end_socket.update()
        self.removeItem(wire_item)
        if not self._is_loading:
            self.graphChanged.emit()

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

    def get_graph_data(self):
        nodes_data = [card.to_dict() for card in self.nodes.values()]
        wires_data = []
        for w in self.wires:
            if w.start_socket and w.end_socket:
                wires_data.append({
                    "from_node": w.start_socket.parent_node.node_id,
                    "from_socket": w.start_socket.socket_def.name,
                    "to_node": w.end_socket.parent_node.node_id,
                    "to_socket": w.end_socket.socket_def.name,
                })
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

        for wdata in data.get("wires", []):
            fn = self.nodes.get(wdata.get("from_node"))
            tn = self.nodes.get(wdata.get("to_node"))
            fs_name = wdata.get("from_socket")
            ts_name = wdata.get("to_socket")
            if fn and tn:
                fs = next((s for s in fn.output_sockets if s.socket_def.name == fs_name), None)
                ts = next((s for s in tn.input_sockets if s.socket_def.name == ts_name), None)
                if fs and ts:
                    self.connect_sockets(fs, ts)

        self._is_loading = False
        self.graphChanged.emit()

    # ── Interactive Wire Dragging ──────────────────────────────────────────────

    def _find_socket_at(self, scene_pos, exclude_socket=None):
        """Finds any NodeSocketItem near scene_pos within a comfortable snapping radius."""
        try:
            nearby_items = self.items(QtCore.QRectF(scene_pos.x() - 12, scene_pos.y() - 12, 24, 24))
            for item in nearby_items:
                if isinstance(item, NodeSocketItem) and item != exclude_socket:
                    return item
        except Exception:
            pass

        try:
            item = self.itemAt(scene_pos, QtGui.QTransform())
            if isinstance(item, NodeSocketItem) and item != exclude_socket:
                return item
        except Exception:
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
            except Exception:
                pass

        self._press_sock = sock
        self._press_wire = wire_item
        self._press_pos = event.scenePos()
        self._press_modifiers = modifiers

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
                else:
                    # Input socket
                    if sock.wires:
                        existing_wire = sock.wires[-1]
                        source_out = existing_wire.start_socket
                        if (modifiers & ctrl_mod):
                            # Ctrl + Drag: Branch connection from same output to a new input!
                            if source_out:
                                self._drag_start_socket = source_out
                                self._drag_wire = NodeWireItem(start_socket=source_out, temp_end_pos=event.scenePos())
                                self.addItem(self._drag_wire)
                                event.accept()
                                return
                        else:
                            # Normal drag: pull-off wire to move/reroute
                            if source_out:
                                self.remove_wire(existing_wire)
                                self._drag_start_socket = source_out
                                self._drag_wire = NodeWireItem(start_socket=source_out, temp_end_pos=event.scenePos())
                                self.addItem(self._drag_wire)
                                event.accept()
                                return
                    else:
                        # Unconnected input: drag from input
                        self._drag_start_socket = sock
                        self._drag_wire = NodeWireItem(start_socket=sock, temp_end_pos=event.scenePos())
                        self.addItem(self._drag_wire)
                        event.accept()
                        return

            # ── 2. Wire Click / Drag ───────────────────────────────────────────
            if wire_item is not None:
                if (modifiers & ctrl_mod):
                    # Ctrl + Drag from wire: Branch connection from wire's source output!
                    if wire_item.start_socket:
                        self._drag_start_socket = wire_item.start_socket
                        self._drag_wire = NodeWireItem(start_socket=wire_item.start_socket, temp_end_pos=event.scenePos())
                        self.addItem(self._drag_wire)
                        event.accept()
                        return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
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

        # If it was a Ctrl + Click without dragging (< 6px), break connections!
        if (modifiers & ctrl_mod) and moved_dist < 6:
            press_sock = getattr(self, "_press_sock", None)
            press_wire = getattr(self, "_press_wire", None)
            handled = False
            if press_sock is not None and press_sock.wires:
                for w in list(press_sock.wires):
                    self.remove_wire(w)
                handled = True
            elif press_wire is not None and press_wire in self.wires:
                self.remove_wire(press_wire)
                handled = True

            if self._drag_wire:
                self.removeItem(self._drag_wire)
                self._drag_wire = None
                self._drag_start_socket = None

            if handled:
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
            except Exception:
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
                except Exception:
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
                except Exception:
                    pass

        for w in to_remove:
            self.remove_wire(w)
        return len(to_remove)

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            for item in self.selectedItems():
                if isinstance(item, NodeCardItem):
                    self.remove_node(item)
                elif isinstance(item, NodeWireItem):
                    self.remove_wire(item)
            event.accept()
            return
        super().keyPressEvent(event)
