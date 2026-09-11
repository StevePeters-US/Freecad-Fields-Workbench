# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/gui/node_editor/node_items.py

QGraphicsItem implementations for Node cards, Sockets, and Bezier Wires.
"""
from PySide import QtWidgets, QtGui, QtCore
from freecad.fields.core.gui.node_editor.node_definitions import (
    SocketType, SOCKET_TYPE_NAMES, SOCKET_TYPE_SHORT_LABELS, NODE_REGISTRY
)

SOCKET_COLORS = {
    SocketType.FLOAT: QtGui.QColor("#94a3b8"),  # Slate gray (1D Scalar)
    SocketType.VEC2: QtGui.QColor("#a855f7"),   # Violet (2D Vector)
    SocketType.VEC3: QtGui.QColor("#3b82f6"),   # Electric Blue (3D Vector)
    SocketType.BOOL: QtGui.QColor("#ec4899"),   # Pink / Magenta (Boolean)
}
DEFAULT_SOCKET_COLOR = QtGui.QColor("#94a3b8")

WIRE_WIDTHS = {
    SocketType.FLOAT: 2.0,   # 1D Float
    SocketType.VEC2: 2.8,    # 2D Vector
    SocketType.VEC3: 3.6,    # 3D Vector
    SocketType.BOOL: 2.0,    # Boolean
}


class NodeSocketItem(QtWidgets.QGraphicsItem):
    """Circular socket port on a node card."""
    RADIUS = 6.0

    def __init__(self, socket_def, is_input, parent_node):
        super().__init__(parent_node)
        self.socket_def = socket_def
        self.is_input = is_input
        self.parent_node = parent_node
        self.wires = []
        self.color = SOCKET_COLORS.get(socket_def.socket_type, DEFAULT_SOCKET_COLOR)
        self.setAcceptHoverEvents(True)
        self._is_hovered = False
        self.setZValue(2.0)

        stype = getattr(socket_def, "socket_type", SocketType.FLOAT)
        type_name = SOCKET_TYPE_NAMES.get(stype, "1D Float")
        dir_label = "Input" if is_input else "Output"
        self.setToolTip(f"{socket_def.name} ({dir_label}, {type_name})")

    def setToolTip(self, text):
        self._tooltip_text = text
        try:
            super().setToolTip(text)
        except Exception:
            pass

    def toolTip(self):
        return getattr(self, "_tooltip_text", "")

    def boundingRect(self):
        r = self.RADIUS + 3.0
        return QtCore.QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        r = self.RADIUS
        if self._is_hovered:
            r += 1.5
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1.5))
        else:
            painter.setPen(QtGui.QPen(QtGui.QColor("#1e293b"), 1.2))

        if self.wires or self._is_hovered:
            painter.setBrush(QtGui.QBrush(self.color))
        else:
            painter.setBrush(QtGui.QBrush(QtGui.QColor("#0f172a")))
            painter.drawEllipse(QtCore.QPointF(0, 0), r, r)
            painter.setBrush(QtGui.QBrush(self.color))
            r = self.RADIUS * 0.55

        painter.drawEllipse(QtCore.QPointF(0, 0), r, r)

    def hoverEnterEvent(self, event):
        self._is_hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._is_hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def setPos(self, *args):
        if len(args) == 1:
            self._local_pos = args[0]
        elif len(args) >= 2:
            self._local_pos = QtCore.QPointF(float(args[0]), float(args[1]))
        try:
            super().setPos(*args)
        except Exception:
            pass

    def pos(self):
        try:
            p = super().pos()
            if hasattr(p, "x") and isinstance(p.x(), (int, float)):
                return p
        except Exception:
            pass
        return getattr(self, "_local_pos", QtCore.QPointF(0.0, 0.0))

    def get_center_scene_pos(self):
        try:
            pt = self.mapToScene(QtCore.QPointF(0, 0))
            if hasattr(pt, "x") and isinstance(pt.x(), (int, float)):
                return pt
        except Exception:
            pass
        # Fallback when mapToScene is not implemented (e.g. mock)
        try:
            parent_pos = self.parent_node.pos() if self.parent_node else QtCore.QPointF(0, 0)
            my_pos = self.pos()
            return QtCore.QPointF(float(parent_pos.x() + my_pos.x()), float(parent_pos.y() + my_pos.y()))
        except Exception:
            return QtCore.QPointF(0.0, 0.0)


class NodeWireItem(QtWidgets.QGraphicsPathItem):
    """Cubic bezier wire connecting an output socket to an input socket."""

    def __init__(self, start_socket=None, end_socket=None, temp_end_pos=None):
        super().__init__()
        self.start_socket = start_socket
        self.end_socket = end_socket
        self.temp_end_pos = temp_end_pos
        self.setZValue(-1.0)
        self.color = start_socket.color if start_socket else QtGui.QColor("#94a3b8")
        flag_sel = getattr(QtWidgets.QGraphicsItem, "ItemIsSelectable", None)
        if flag_sel is not None:
            try:
                self.setFlag(flag_sel, True)
            except Exception:
                pass
        self.update_path()

    def shape(self):
        try:
            stroker = QtGui.QPainterPathStroker()
            stroker.setWidth(14.0)
            cap = getattr(QtCore.Qt, "RoundCap", None)
            if cap is not None:
                try:
                    stroker.setCapStyle(cap)
                except Exception:
                    pass
            stroke = stroker.createStroke(self.path())
            if stroke is not None and hasattr(stroke, "boundingRect"):
                return stroke
        except Exception:
            pass
        return super().shape()

    def paint(self, painter, option, widget=None):
        try:
            pen = QtGui.QPen(self.pen())
            if self.isSelected():
                pen.setColor(QtGui.QColor("#ffffff"))
                pen.setWidthF(pen.widthF() + 1.2)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setPen(pen)
            painter.drawPath(self.path())
        except Exception:
            try:
                super().paint(painter, option, widget)
            except Exception:
                pass

    def update_path(self):
        if not self.start_socket:
            return

        p1 = self.start_socket.get_center_scene_pos()
        if self.end_socket:
            p2 = self.end_socket.get_center_scene_pos()
        elif self.temp_end_pos:
            p2 = self.temp_end_pos
        else:
            p2 = p1

        try:
            x1, y1 = float(p1.x()), float(p1.y())
            x2, y2 = float(p2.x()), float(p2.y())
        except Exception:
            x1, y1, x2, y2 = 0.0, 0.0, 0.0, 0.0

        dx = abs(x2 - x1)
        offset = max(40.0, dx * 0.5)

        # Output extends right, input approaches from left
        ctrl1 = QtCore.QPointF(x1 + offset, y1)
        ctrl2 = QtCore.QPointF(x2 - offset, y2)

        path = QtGui.QPainterPath(QtCore.QPointF(x1, y1))
        path.cubicTo(ctrl1, ctrl2, QtCore.QPointF(x2, y2))
        self.setPath(path)

        stype1 = getattr(getattr(self.start_socket, "socket_def", None), "socket_type", SocketType.FLOAT)
        stype2 = getattr(getattr(self.end_socket, "socket_def", None), "socket_type", SocketType.FLOAT) if self.end_socket else stype1
        w1 = WIRE_WIDTHS.get(stype1, 2.0)
        w2 = WIRE_WIDTHS.get(stype2, 2.0)
        base_width = max(w1, w2)

        c1 = self.start_socket.color if self.start_socket else QtGui.QColor("#94a3b8")
        c2 = self.end_socket.color if self.end_socket else c1

        if self.end_socket and c1 != c2:
            grad = QtGui.QLinearGradient(x1, y1, x2, y2)
            grad.setColorAt(0.0, c1)
            grad.setColorAt(1.0, c2)
            pen = QtGui.QPen(QtGui.QBrush(grad), base_width)
        else:
            pen = QtGui.QPen(c1, base_width)

        cap = getattr(QtCore.Qt, "RoundCap", None)
        if cap is not None:
            try:
                pen.setCapStyle(cap)
            except Exception:
                pass
        self.setPen(pen)

        # Dynamic tooltip on wire
        try:
            t1 = SOCKET_TYPE_NAMES.get(stype1, "1D Float")
            if self.end_socket:
                t2 = SOCKET_TYPE_NAMES.get(stype2, "1D Float")
                p1_name = getattr(getattr(self.start_socket, "parent_node", None), "node_type", "Node")
                p2_name = getattr(getattr(self.end_socket, "parent_node", None), "node_type", "Node")
                s1_name = getattr(getattr(self.start_socket, "socket_def", None), "name", "")
                s2_name = getattr(getattr(self.end_socket, "socket_def", None), "name", "")
                if stype1 == stype2:
                    self.setToolTip(f"Wire: {t1} ({p1_name}.{s1_name} → {p2_name}.{s2_name})")
                else:
                    self.setToolTip(f"Wire: {t1} → {t2} ({p1_name}.{s1_name} → {p2_name}.{s2_name})")
            else:
                self.setToolTip(f"Wire: {t1}")
        except Exception:
            pass

    def setToolTip(self, text):
        self._tooltip_text = text
        try:
            super().setToolTip(text)
        except Exception:
            pass

    def toolTip(self):
        return getattr(self, "_tooltip_text", "")


class NodeCardItem(QtWidgets.QGraphicsItem):
    """Visual card widget for a node on the canvas."""
    WIDTH = 190.0
    HEADER_HEIGHT = 28.0
    ROW_HEIGHT = 22.0

    def __init__(self, node_id, node_type, params=None):
        super().__init__()
        self.node_id = node_id
        self.node_type = node_type
        self.node_cls = NODE_REGISTRY.get(node_type)
        self.node_instance = self.node_cls() if self.node_cls else None
        self.params = dict(self.node_instance.default_params if self.node_instance else {})
        if params:
            self.params.update(params)

        self.input_sockets = []
        self.output_sockets = []
        self._proxy_widgets = []
        self.on_changed_cb = None

        for flag_name in ("ItemIsMovable", "ItemIsSelectable", "ItemSendsGeometryChanges"):
            flag_val = getattr(QtWidgets.QGraphicsItem, flag_name, None)
            if flag_val is not None:
                try:
                    self.setFlag(flag_val, True)
                except Exception:
                    pass
        self.WIDTH = 220.0 if node_type == "custom_param" else 190.0
        self._card_pos = QtCore.QPointF(0.0, 0.0)
        self._build_ui()

    def setPos(self, *args):
        if len(args) == 1:
            self._card_pos = args[0]
        elif len(args) >= 2:
            self._card_pos = QtCore.QPointF(float(args[0]), float(args[1]))
        try:
            super().setPos(*args)
        except Exception:
            pass

    def pos(self):
        try:
            p = super().pos()
            if hasattr(p, "x") and isinstance(p.x(), (int, float)):
                return p
        except Exception:
            pass
        return getattr(self, "_card_pos", QtCore.QPointF(0.0, 0.0))

    def scenePos(self):
        try:
            p = super().scenePos()
            if hasattr(p, "x") and isinstance(p.x(), (int, float)):
                return p
        except Exception:
            pass
        return self.pos()

    def scene(self):
        try:
            s = super().scene()
            if s is not None and type(s).__name__ != "MockClass":
                return s
        except Exception:
            pass
        return getattr(self, "_scene_ref", None)

    def _build_ui(self):
        if not self.node_instance:
            return

        # Create sockets
        for sdef in self.node_instance.inputs:
            s_item = NodeSocketItem(sdef, is_input=True, parent_node=self)
            self.input_sockets.append(s_item)

        for sdef in self.node_instance.outputs:
            s_item = NodeSocketItem(sdef, is_input=False, parent_node=self)
            self.output_sockets.append(s_item)

        # Embedded internal controls
        num_inputs = len(self.input_sockets)
        num_outputs = len(self.output_sockets)
        num_rows = max(num_inputs, num_outputs)

        y_offset = self.HEADER_HEIGHT + 8.0

        # Position sockets
        for i, s in enumerate(self.input_sockets):
            s.setPos(0, y_offset + i * self.ROW_HEIGHT + self.ROW_HEIGHT * 0.5)

        for i, s in enumerate(self.output_sockets):
            s.setPos(self.WIDTH, y_offset + i * self.ROW_HEIGHT + self.ROW_HEIGHT * 0.5)

        # Add embedded param widget if needed
        self._setup_internal_widgets(y_offset + num_rows * self.ROW_HEIGHT)

    def _setup_internal_widgets(self, start_y):
        ntype = self.node_type
        dark_combo_style = """
            QComboBox {
                background-color: #0f172a;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 3px;
                padding: 1px 4px;
                font-size: 11px;
            }
            QComboBox QAbstractItemView {
                background-color: #1e293b;
                color: #f8fafc;
                selection-background-color: #0284c7;
            }
        """

        if ntype == "math":
            combo = QtWidgets.QComboBox()
            combo.setStyleSheet(dark_combo_style)
            from freecad.fields.core.gui.node_editor.node_definitions import MathNode
            combo.addItems(list(MathNode.OPS.keys()))
            cur_op = self.params.get("op", "Add (+)")
            idx = combo.findText(cur_op)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(lambda _idx, c=combo: self._on_math_op_changed(c.currentText()))
            self._add_embedded_widget(combo, start_y, 24)

        elif ntype in ("trig", "function", "min_max"):
            combo = QtWidgets.QComboBox()
            combo.setStyleSheet(dark_combo_style)
            from freecad.fields.core.gui.node_editor.node_definitions import (
                TrigNode, FunctionNode, MinMaxNode
            )
            if ntype == "trig":
                items = TrigNode.FNS
            elif ntype == "function":
                items = FunctionNode.FNS
            else:
                items = ["min", "max"]
            combo.addItems(items)
            cur_fn = self.params.get("fn", items[0])
            idx = combo.findText(cur_fn)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(lambda _idx, c=combo: self._on_fn_changed(c.currentText()))
            self._add_embedded_widget(combo, start_y, 24)

        elif ntype == "constant":
            spin = QtWidgets.QDoubleSpinBox()
            spin.setStyleSheet("""
                QDoubleSpinBox {
                    background-color: #0f172a;
                    color: #f8fafc;
                    border: 1px solid #334155;
                    border-radius: 3px;
                    padding: 1px 4px;
                    font-size: 11px;
                }
            """)
            spin.setRange(-99999.0, 99999.0)
            spin.setSingleStep(0.1)
            spin.setDecimals(3)
            spin.setValue(float(self.params.get("val", 1.0)))
            spin.valueChanged.connect(self._on_const_changed)
            self._add_embedded_widget(spin, start_y, 24)

        elif ntype == "custom_param":
            container = QtWidgets.QWidget()
            flag_trans = getattr(QtCore.Qt, "WA_TranslucentBackground", None)
            if flag_trans is not None:
                try:
                    container.setAttribute(flag_trans, True)
                except Exception:
                    pass
            container.setStyleSheet("""
                QWidget {
                    background: transparent;
                    color: #cbd5e1;
                    font-size: 10px;
                }
                QLabel {
                    background: transparent;
                    color: #94a3b8;
                    font-size: 10px;
                }
                QLineEdit, QComboBox, QDoubleSpinBox {
                    background-color: #0f172a;
                    color: #f8fafc;
                    border: 1px solid #334155;
                    border-radius: 3px;
                    padding: 1px 3px;
                    min-height: 18px;
                    max-height: 20px;
                    font-size: 10px;
                }
                QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {
                    border: 1px solid #38bdf8;
                }
                QComboBox::drop-down {
                    border: none;
                    width: 12px;
                }
                QComboBox QAbstractItemView {
                    background-color: #1e293b;
                    color: #f8fafc;
                    selection-background-color: #0284c7;
                }
            """)
            layout = QtWidgets.QFormLayout(container)
            layout.setContentsMargins(2, 2, 2, 2)
            layout.setSpacing(3)

            name_edit = QtWidgets.QLineEdit(str(self.params.get("name", "param")))
            name_edit.editingFinished.connect(lambda: self._on_custom_param_field("name", name_edit.text().strip()))
            name_edit.textChanged.connect(lambda t: self._on_custom_param_field("name", t.strip()))
            layout.addRow("Name:", name_edit)

            type_combo = QtWidgets.QComboBox()
            type_combo.addItems(["float", "slider", "int", "bool"])
            t_idx = type_combo.findText(str(self.params.get("ptype", "float")))
            if t_idx >= 0:
                type_combo.setCurrentIndex(t_idx)
            type_combo.currentIndexChanged.connect(lambda idx: self._on_custom_param_field("ptype", type_combo.currentText()))
            layout.addRow("Type:", type_combo)

            def_spin = QtWidgets.QDoubleSpinBox()
            def_spin.setRange(-99999.0, 99999.0)
            def_spin.setValue(float(self.params.get("default", 1.0)))
            def_spin.valueChanged.connect(lambda v: self._on_custom_param_field("default", v))

            def_chk = QtWidgets.QCheckBox("Default On")
            def_chk.setStyleSheet("color: #f8fafc; font-size: 10px;")
            cur_def_bool = self.params.get("default", 1) in (1, 1.0, True, "1", "true", "True")
            def_chk.setChecked(bool(cur_def_bool))
            def_chk.toggled.connect(lambda checked: self._on_custom_param_field("default", 1 if checked else 0))

            def_container = QtWidgets.QWidget()
            def_layout = QtWidgets.QHBoxLayout(def_container)
            def_layout.setContentsMargins(0, 0, 0, 0)
            def_layout.addWidget(def_spin)
            def_layout.addWidget(def_chk)
            layout.addRow("Default:", def_container)

            min_spin = QtWidgets.QDoubleSpinBox()
            min_spin.setRange(-99999.0, 99999.0)
            min_spin.setValue(float(self.params.get("min", 0.0)))
            min_spin.valueChanged.connect(lambda v: self._on_custom_param_field("min", v))
            min_spin.setMinimumWidth(45)

            max_spin = QtWidgets.QDoubleSpinBox()
            max_spin.setRange(-99999.0, 99999.0)
            max_spin.setValue(float(self.params.get("max", 5.0)))
            max_spin.valueChanged.connect(lambda v: self._on_custom_param_field("max", v))
            max_spin.setMinimumWidth(45)

            range_widget = QtWidgets.QWidget()
            range_layout = QtWidgets.QHBoxLayout(range_widget)
            range_layout.setContentsMargins(0, 0, 0, 0)
            range_layout.setSpacing(3)
            range_layout.addWidget(min_spin)
            dash_lbl = QtWidgets.QLabel("-")
            dash_lbl.setStyleSheet("color: #94a3b8; font-weight: bold;")
            range_layout.addWidget(dash_lbl)
            range_layout.addWidget(max_spin)
            layout.addRow("Range:", range_widget)

            def _update_spin_mode(ptype):
                is_bool = (ptype == "bool")
                def_spin.setVisible(not is_bool)
                def_chk.setVisible(is_bool)
                range_widget.setVisible(not is_bool)
                # Find and toggle visibility of range label in form layout
                lbl = layout.labelForField(range_widget)
                if lbl:
                    lbl.setVisible(not is_bool)

                # Update output socket type and color
                if self.output_sockets:
                    s_item = self.output_sockets[0]
                    target_stype = SocketType.BOOL if is_bool else SocketType.FLOAT
                    s_item.socket_def.socket_type = target_stype
                    s_item.color = SOCKET_COLORS.get(target_stype, DEFAULT_SOCKET_COLOR)
                    stype_name = SOCKET_TYPE_NAMES.get(target_stype, target_stype)
                    s_item.setToolTip(f"{s_item.socket_def.name} ({stype_name})")
                    s_item.update()
                    self.update()

                if ptype == "int":
                    def_spin.setDecimals(0)
                    def_spin.setSingleStep(1)
                    min_spin.setDecimals(0)
                    min_spin.setSingleStep(1)
                    max_spin.setDecimals(0)
                    max_spin.setSingleStep(1)
                elif not is_bool:
                    def_spin.setDecimals(3)
                    def_spin.setSingleStep(0.1)
                    min_spin.setDecimals(2)
                    min_spin.setSingleStep(0.5)
                    max_spin.setDecimals(2)
                    max_spin.setSingleStep(0.5)

            _update_spin_mode(type_combo.currentText())
            type_combo.currentIndexChanged.connect(lambda idx: _update_spin_mode(type_combo.currentText()))

            self._add_embedded_widget(container, start_y, 98)

        elif ntype == "radial_projection":
            container = QtWidgets.QWidget()
            flag_trans = getattr(QtCore.Qt, "WA_TranslucentBackground", None)
            if flag_trans is not None:
                try:
                    container.setAttribute(flag_trans, True)
                except Exception:
                    pass
            c_layout = QtWidgets.QVBoxLayout(container)
            c_layout.setContentsMargins(4, 2, 4, 2)
            c_layout.setSpacing(4)

            chk = QtWidgets.QCheckBox("Radial Mode")
            chk.setStyleSheet("color: #f8fafc; font-size: 10px; font-weight: bold;")
            chk.setChecked(bool(self.params.get("radial", True)))
            chk.toggled.connect(lambda v: self._on_radial_toggled(v))
            c_layout.addWidget(chk)

            btn = QtWidgets.QPushButton("Edit Subgraph ⤢")
            btn.setStyleSheet(
                "font-weight: bold; background-color: #334155; color: #38bdf8; "
                "border: 1px solid #0284c7; border-radius: 4px; padding: 2px;"
            )
            btn.setToolTip("Open and edit the internal node network of this subgraph (or double-click node)")
            btn.clicked.connect(self._on_open_subgraph)
            c_layout.addWidget(btn)

            self._add_embedded_widget(container, start_y, 48)

        elif getattr(self.node_instance, "is_subgraph", False):
            btn = QtWidgets.QPushButton("Edit Subgraph ⤢")
            btn.setStyleSheet(
                "font-weight: bold; background-color: #334155; color: #38bdf8; "
                "border: 1px solid #0284c7; border-radius: 4px; padding: 2px;"
            )
            btn.setToolTip("Open and edit the internal node network of this subgraph (or double-click node)")
            btn.clicked.connect(self._on_open_subgraph)
            self._add_embedded_widget(btn, start_y, 26)

    def _on_radial_toggled(self, val):
        self.params["radial"] = bool(val)
        self._notify_changed()

    def _on_open_subgraph(self):
        sc = self.scene()
        if sc and hasattr(sc, "open_subgraph_requested"):
            sc.open_subgraph_requested.emit(self.node_id)

    def mouseDoubleClickEvent(self, event):
        if getattr(self.node_instance, "is_subgraph", False):
            self._on_open_subgraph()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _add_embedded_widget(self, widget, y_pos, height):
        proxy = QtWidgets.QGraphicsProxyWidget(self)
        proxy.setWidget(widget)
        proxy.setGeometry(QtCore.QRectF(10, y_pos + 2, self.WIDTH - 20, height))
        self._proxy_widgets.append((proxy, height + 8))

    def _on_math_op_changed(self, val):
        if isinstance(val, str):
            self.params["op"] = val
        elif hasattr(val, "currentText"):
            self.params["op"] = val.currentText()
        elif hasattr(self, "sender"):
            s = self.sender()
            if s and hasattr(s, "currentText"):
                self.params["op"] = s.currentText()
        self._notify_changed()

    def _on_fn_changed(self, val):
        if isinstance(val, str):
            self.params["fn"] = val
        elif hasattr(val, "currentText"):
            self.params["fn"] = val.currentText()
        elif hasattr(self, "sender"):
            s = self.sender()
            if s and hasattr(s, "currentText"):
                self.params["fn"] = s.currentText()
        self._notify_changed()

    def _on_const_changed(self, val):
        self.params["val"] = val
        self._notify_changed()

    def _on_custom_param_field(self, key, val):
        self.params[key] = val
        if key == "default":
            pname = str(self.params.get("name", "")).strip()
            if pname:
                prop_name = "Custom_" + pname
                sc = self.scene()
                dlg = None
                if sc and hasattr(sc, "parent"):
                    p = sc.parent()
                    if hasattr(p, "target_obj"):
                        dlg = p
                if not dlg and sc and hasattr(sc, "views"):
                    v = sc.views()
                    if isinstance(v, (list, tuple)) and len(v) > 0 and hasattr(v[0], "window"):
                        dlg = v[0].window()
                target_obj = getattr(dlg, "target_obj", None) if dlg else None
                if target_obj:
                    ptype = self.params.get("ptype", "float")
                    if ptype == "bool":
                        new_val = bool(val in (1, 1.0, True, "1", "true", "True"))
                        prop_type = "App::PropertyBool"
                    elif ptype == "int":
                        new_val = int(val)
                        prop_type = "App::PropertyInteger"
                    else:
                        new_val = float(val)
                        prop_type = "App::PropertyFloat"

                    if not hasattr(target_obj, prop_name) and hasattr(target_obj, "addProperty"):
                        try:
                            target_obj.addProperty(prop_type, prop_name, "Custom Params", f"Custom parameter {prop_name}")
                        except Exception:
                            pass
                    try:
                        setattr(target_obj, prop_name, new_val)
                    except Exception:
                        pass
        self._notify_changed()

    def _notify_changed(self):
        if self.on_changed_cb:
            self.on_changed_cb()

    def total_height(self):
        num_rows = max(len(self.input_sockets), len(self.output_sockets))
        h = self.HEADER_HEIGHT + 12.0 + num_rows * self.ROW_HEIGHT
        for _, extra_h in self._proxy_widgets:
            h += extra_h
        return max(h, 60.0)

    def boundingRect(self):
        return QtCore.QRectF(0, 0, self.WIDTH, self.total_height())

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.boundingRect()

        # Background card
        bg_color = QtGui.QColor("#1e293b")
        if self.isSelected():
            border_color = QtGui.QColor("#38bdf8")
            border_width = 2.0
        else:
            border_color = QtGui.QColor("#334155")
            border_width = 1.0

        painter.setBrush(QtGui.QBrush(bg_color))
        painter.setPen(QtGui.QPen(border_color, border_width))
        painter.drawRoundedRect(rect, 8.0, 8.0)

        # Header bar
        header_rect = QtCore.QRectF(0, 0, self.WIDTH, self.HEADER_HEIGHT)
        header_path = QtGui.QPainterPath()
        header_path.addRoundedRect(header_rect, 8.0, 8.0)
        # Flatten bottom corners of header
        header_path.addRect(QtCore.QRectF(0, self.HEADER_HEIGHT - 8.0, self.WIDTH, 8.0))

        cat_colors = {
            "Inputs": QtGui.QColor("#0ea5e9"),
            "Vector": QtGui.QColor("#6366f1"),
            "Math": QtGui.QColor("#8b5cf6"),
            "Trig": QtGui.QColor("#ec4899"),
            "Functions": QtGui.QColor("#f59e0b"),
            "Range": QtGui.QColor("#10b981"),
            "Generators": QtGui.QColor("#f97316"),
            "Subgraph": QtGui.QColor("#14b8a6"),
            "Output": QtGui.QColor("#ef4444"),
        }
        head_color = cat_colors.get(self.node_instance.category if self.node_instance else "", QtGui.QColor("#475569"))

        painter.setClipRect(header_rect)
        painter.setBrush(QtGui.QBrush(head_color))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawRoundedRect(header_rect, 8.0, 8.0)
        painter.setClipping(False)

        # Header title
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff")))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(9)
        painter.setFont(font)
        title = self.node_instance.title if self.node_instance else self.node_type
        painter.drawText(QtCore.QRectF(10, 0, self.WIDTH - 20, self.HEADER_HEIGHT),
                         QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, title)

        # Socket labels and type badges
        font.setBold(False)
        font.setPointSize(8)
        painter.setFont(font)

        badge_font = QtGui.QFont(font)
        badge_font.setPointSize(7)
        badge_font.setBold(True)

        y_offset = self.HEADER_HEIGHT + 8.0
        for i, s in enumerate(self.input_sockets):
            y = y_offset + i * self.ROW_HEIGHT
            stype = getattr(s.socket_def, "socket_type", SocketType.FLOAT)
            stype_color = SOCKET_COLORS.get(stype, DEFAULT_SOCKET_COLOR)
            stype_badge = SOCKET_TYPE_SHORT_LABELS.get(stype, "1D")

            # Input socket name
            painter.setFont(font)
            painter.setPen(QtGui.QPen(QtGui.QColor("#cbd5e1")))
            w_name = self.WIDTH * 0.5 - 38 if len(self.output_sockets) > 0 else self.WIDTH - 48
            painter.drawText(QtCore.QRectF(12, y, w_name, self.ROW_HEIGHT),
                             QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, s.socket_def.name)

            # Input socket type badge
            painter.setFont(badge_font)
            painter.setPen(QtGui.QPen(stype_color))
            bx = self.WIDTH * 0.5 - 34 if len(self.output_sockets) > 0 else self.WIDTH - 36
            painter.drawText(QtCore.QRectF(bx, y, 30, self.ROW_HEIGHT),
                             QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, stype_badge)

        for i, s in enumerate(self.output_sockets):
            y = y_offset + i * self.ROW_HEIGHT
            stype = getattr(s.socket_def, "socket_type", SocketType.FLOAT)
            stype_color = SOCKET_COLORS.get(stype, DEFAULT_SOCKET_COLOR)
            stype_badge = SOCKET_TYPE_SHORT_LABELS.get(stype, "1D")

            # Output socket type badge
            painter.setFont(badge_font)
            painter.setPen(QtGui.QPen(stype_color))
            bx = self.WIDTH * 0.5 + 4 if len(self.input_sockets) > 0 else 12
            painter.drawText(QtCore.QRectF(bx, y, 30, self.ROW_HEIGHT),
                             QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, stype_badge)

            # Output socket name
            painter.setFont(font)
            painter.setPen(QtGui.QPen(QtGui.QColor("#cbd5e1")))
            nx = self.WIDTH * 0.5 + 36 if len(self.input_sockets) > 0 else 44
            nw = self.WIDTH * 0.5 - 48 if len(self.input_sockets) > 0 else self.WIDTH - 56
            painter.drawText(QtCore.QRectF(nx, y, nw, self.ROW_HEIGHT),
                             QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, s.socket_def.name)

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemPositionChange:
            # Update all attached wires
            for s in self.input_sockets + self.output_sockets:
                for w in s.wires:
                    w.update_path()
        return super().itemChange(change, value)

    def to_dict(self):
        try:
            px, py = float(self.pos().x()), float(self.pos().y())
        except Exception:
            px, py = 0.0, 0.0
        return {
            "id": self.node_id,
            "type": self.node_type,
            "pos": [px, py],
            "params": dict(self.params),
        }
