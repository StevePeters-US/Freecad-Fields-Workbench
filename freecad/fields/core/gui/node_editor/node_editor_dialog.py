# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/gui/node_editor/node_editor_dialog.py

Main modeless dialog for the Visual Noise Formula Node Editor.
"""
import json
from PySide import QtWidgets, QtGui, QtCore

from freecad.fields.core import fld_logger
from freecad.fields.core.gui.node_editor.node_scene import NodeGraphScene
from freecad.fields.core.gui.node_editor.node_view import NodeGraphView
from freecad.fields.core.gui.node_editor.node_definitions import (
    get_available_node_classes, compile_graph, get_template_graph
)


class FldNoiseNodeEditorDialog(QtWidgets.QDialog):
    """Visual Node Editor Dialog for procedural noise formulas."""

    def __init__(self, target_obj=None, tool=None, is_2d=False, parent=None):
        super().__init__(parent)
        self.target_obj = target_obj
        self.tool = tool
        self.is_2d = is_2d
        self._is_updating = False
        self.current_subgraph_node_id = None
        self.root_graph_data = None

        self.setWindowTitle("Fields - Noise Formula Node Editor")
        self.resize(920, 620)
        self.setMinimumSize(600, 400)

        # Allow non-modal floating window
        win_flag = getattr(QtCore.Qt, "Window", None)
        if win_flag is not None:
            self.setWindowFlags(self.windowFlags() | win_flag)

        self._build_ui()
        self.load_from_object()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # ── Top Toolbar ────────────────────────────────────────────────────────
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)

        # Add Node menu
        self.add_node_btn = QtWidgets.QPushButton("+ Add Node")
        self.add_node_btn.setStyleSheet("font-weight: bold; padding: 4px 10px;")
        self.add_node_menu = QtWidgets.QMenu(self)
        self._populate_add_node_menu()
        self.add_node_btn.setMenu(self.add_node_menu)
        toolbar.addWidget(self.add_node_btn)

        # Templates menu
        self.templates_btn = QtWidgets.QPushButton("Templates")
        self.templates_menu = QtWidgets.QMenu(self)
        self._populate_templates_menu()
        self.templates_btn.setMenu(self.templates_menu)
        toolbar.addWidget(self.templates_btn)

        toolbar.addStretch()

        # Live Update toggle
        self.live_update_chk = QtWidgets.QCheckBox("Live 3D Update")
        self.live_update_chk.setChecked(True)
        self.live_update_chk.setToolTip("Automatically update the 3D viewport in real time as nodes change")
        toolbar.addWidget(self.live_update_chk)

        # Apply button
        self.apply_btn = QtWidgets.QPushButton("Apply Formula")
        self.apply_btn.setStyleSheet("font-weight: bold; padding: 4px 14px; background-color: #0284c7; color: white;")
        self.apply_btn.clicked.connect(self.save_to_object)
        toolbar.addWidget(self.apply_btn)

        # Clear button
        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.setToolTip("Clear all nodes")
        clear_btn.clicked.connect(self._on_clear)
        toolbar.addWidget(clear_btn)

        main_layout.addLayout(toolbar)

        # ── Breadcrumb / Subgraph Navigation Bar ──────────────────────────────
        breadcrumb_bar = QtWidgets.QHBoxLayout()
        breadcrumb_bar.setContentsMargins(4, 2, 4, 2)
        breadcrumb_bar.setSpacing(8)

        self.back_btn = QtWidgets.QPushButton("◀ Back to Root Graph")
        self.back_btn.setStyleSheet(
            "font-weight: bold; padding: 3px 10px; background-color: #0369a1; color: white; border-radius: 4px;"
        )
        self.back_btn.setToolTip("Return to parent graph (changes to this subgraph are preserved)")
        self.back_btn.setVisible(False)
        self.back_btn.clicked.connect(self.close_subgraph)
        breadcrumb_bar.addWidget(self.back_btn)

        self.breadcrumb_lbl = QtWidgets.QLabel("📁 Root Graph")
        self.breadcrumb_lbl.setStyleSheet("font-weight: bold; color: #cbd5e1; font-size: 10pt;")
        breadcrumb_bar.addWidget(self.breadcrumb_lbl)
        breadcrumb_bar.addStretch()

        # Visual Wire & Socket Type Legend
        types_info = [
            ("1D Float", "#94a3b8"),
            ("2D Vector", "#a855f7"),
            ("3D Vector", "#3b82f6"),
            ("Boolean", "#ec4899"),
        ]
        legend_widget = QtWidgets.QWidget()
        legend_layout = QtWidgets.QHBoxLayout(legend_widget)
        legend_layout.setContentsMargins(0, 0, 0, 0)
        legend_layout.setSpacing(10)
        t_lbl = QtWidgets.QLabel("Types:")
        t_lbl.setStyleSheet("color: #64748b; font-size: 9px; font-weight: bold;")
        legend_layout.addWidget(t_lbl)
        for tname, tcolor in types_info:
            lbl = QtWidgets.QLabel(f"● {tname}")
            lbl.setStyleSheet(f"color: {tcolor}; font-size: 9px; font-weight: bold;")
            lbl.setToolTip(f"{tname} socket and wire type")
            legend_layout.addWidget(lbl)
        breadcrumb_bar.addWidget(legend_widget)
        breadcrumb_bar.addSpacing(12)

        self.zoom_fit_btn = QtWidgets.QToolButton()
        self.zoom_fit_btn.setText("⤢ Fit View")
        self.zoom_fit_btn.setToolTip("Fit all nodes into view")
        self.zoom_fit_btn.clicked.connect(lambda: self.view.zoom_to_fit())
        breadcrumb_bar.addWidget(self.zoom_fit_btn)

        main_layout.addLayout(breadcrumb_bar)

        # ── Canvas ─────────────────────────────────────────────────────────────
        self.scene = NodeGraphScene(self)
        self.scene.graphChanged.connect(self._on_graph_changed)
        self.scene.open_subgraph_requested.connect(self.open_subgraph)
        self.view = NodeGraphView(self.scene, self)
        main_layout.addWidget(self.view, 1)

        # ── Bottom Status & Formula Preview ────────────────────────────────────
        bottom_box = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QVBoxLayout(bottom_box)
        bottom_layout.setContentsMargins(4, 4, 4, 4)
        bottom_layout.setSpacing(4)

        status_row = QtWidgets.QHBoxLayout()
        self.status_lbl = QtWidgets.QLabel("Status: Ready")
        self.status_lbl.setStyleSheet("font-weight: bold; color: #38bdf8;")
        status_row.addWidget(self.status_lbl)
        status_row.addStretch()

        copy_btn = QtWidgets.QPushButton("Copy Formula")
        copy_btn.setStyleSheet("padding: 2px 8px; font-size: 9pt;")
        copy_btn.setToolTip("Copy generated GLSL formula to clipboard")
        copy_btn.clicked.connect(self._on_copy_formula)
        status_row.addWidget(copy_btn)

        toggle_preview_btn = QtWidgets.QToolButton()
        toggle_preview_btn.setText("Formula Code Preview")
        toggle_preview_btn.setCheckable(True)
        toggle_preview_btn.setChecked(True)
        status_row.addWidget(toggle_preview_btn)
        bottom_layout.addLayout(status_row)

        self.formula_preview = QtWidgets.QPlainTextEdit()
        self.formula_preview.setReadOnly(True)
        self.formula_preview.setMaximumHeight(65)
        font = QtGui.QFont("Monospace")
        font.setPointSize(8)
        self.formula_preview.setFont(font)
        bottom_layout.addWidget(self.formula_preview)

        toggle_preview_btn.toggled.connect(self.formula_preview.setVisible)
        main_layout.addWidget(bottom_box)

    def _populate_add_node_menu(self, in_subgraph=False):
        self._populate_add_menu(self.add_node_menu, in_subgraph=in_subgraph)

    def _populate_add_menu(self, menu, target_pos=None, in_subgraph=False):
        menu.clear()
        classes = get_available_node_classes(self.is_2d, in_subgraph=in_subgraph)

        # Group by category
        categories = {}
        for cls in classes:
            cat = cls.category
            categories.setdefault(cat, []).append(cls)

        cat_order = ["Inputs", "Vector", "Math", "Trig", "Functions", "Range", "Generators", "Subgraph", "Output"]
        for cat in cat_order:
            if cat not in categories:
                continue
            submenu = menu.addMenu(cat)
            for cls in categories[cat]:
                act = submenu.addAction(cls.title)
                if target_pos is not None:
                    act.triggered.connect(lambda checked=False, t=cls.node_type, p=target_pos: self._add_node_with_pending_connection(t, pos=p))
                else:
                    act.triggered.connect(lambda checked=False, t=cls.node_type: self._add_node_to_center(t))

    def _find_compatible_socket(self, source_sock, candidate_sockets):
        """Finds the best matching socket by socket_type, or returns the first socket."""
        if not candidate_sockets:
            return None
        stype = getattr(getattr(source_sock, "socket_def", None), "socket_type", None)
        for s in candidate_sockets:
            if getattr(getattr(s, "socket_def", None), "socket_type", None) == stype:
                return s
        return candidate_sockets[0]

    def _auto_connect_pending_socket(self, pending_sock, card):
        """Auto-connects a pending dragged wire to the new node card."""
        if not pending_sock or not card:
            return
        if not getattr(pending_sock, "is_input", False):
            # Wire dragged from an output: connect to new node's input socket
            target = self._find_compatible_socket(pending_sock, card.input_sockets)
            if target:
                self.scene.connect_sockets(pending_sock, target)
        else:
            # Wire dragged from an input: connect from new node's output socket
            target = self._find_compatible_socket(pending_sock, card.output_sockets)
            if target:
                self.scene.connect_sockets(target, pending_sock)

    def _add_node_with_pending_connection(self, node_type, pos=None):
        """Adds a node and attaches any pending dragged wire to it."""
        card = self.scene.add_node(node_type, pos=pos)
        pending = getattr(self.scene, "_pending_connection_socket", None)
        if pending is not None and card is not None:
            self._auto_connect_pending_socket(pending, card)
            self.scene._pending_connection_socket = None
        return card

    def show_canvas_context_menu(self, view_pos, global_pos):
        """Shows the Add Node context menu at the right-click position."""
        scene_pos = self.view.mapToScene(view_pos)
        target_pos = [scene_pos.x() - 95, scene_pos.y() - 25]

        # Check if right-clicked on an existing node card
        item = self.scene.itemAt(scene_pos, QtGui.QTransform())
        from freecad.fields.core.gui.node_editor.node_items import NodeCardItem
        target_card = None
        curr = item
        while curr:
            if isinstance(curr, NodeCardItem):
                target_card = curr
                break
            curr = curr.parentItem()

        menu = QtWidgets.QMenu(self)

        if target_card:
            del_act = menu.addAction(f"Delete '{target_card.node_instance.title}'")
            del_act.triggered.connect(lambda: self.scene.remove_node(target_card))
            if getattr(target_card.node_instance, "is_subgraph", False):
                open_act = menu.addAction("Edit Subgraph ⤢")
                open_act.triggered.connect(lambda: self.open_subgraph(target_card.node_id))
            menu.addSeparator()

        add_menu_target = menu.addMenu("+ Add Node") if target_card else menu
        self._populate_add_menu(
            add_menu_target,
            target_pos=target_pos,
            in_subgraph=(self.current_subgraph_node_id is not None)
        )

        menu.exec_(global_pos)
        QtCore.QTimer.singleShot(0, lambda: setattr(self.scene, "_pending_connection_socket", None))

    def _populate_templates_menu(self):
        self.templates_menu.clear()
        if not self.is_2d:
            templates = ["3D: Simple Sine", "3D: Multi-Frequency Waves"]
        else:
            templates = ["2D: Dual Waves", "2D: Radial Ripple", "2D: Square Wave"]

        for tname in templates:
            act = self.templates_menu.addAction(tname)
            act.triggered.connect(lambda checked=False, tn=tname: self._load_template(tn))

    def _add_node_to_center(self, node_type):
        center_pos = self.view.mapToScene(self.view.viewport().rect().center())
        self._add_node_with_pending_connection(node_type, pos=[center_pos.x() - 95, center_pos.y() - 40])

    def open_subgraph(self, node_id):
        """Open the internal node graph of a subgraph node (e.g. Rotated Wave 2D)."""
        if self.current_subgraph_node_id:
            self.close_subgraph()

        card = self.scene.nodes.get(node_id)
        if not card:
            return

        self.root_graph_data = self.scene.get_graph_data()
        self.current_subgraph_node_id = node_id

        subgraph_data = card.params.get("subgraph_data")
        if not subgraph_data or not subgraph_data.get("nodes"):
            from freecad.fields.core.gui.node_editor.node_definitions import get_default_rotated_wave_2d_subgraph
            subgraph_data = get_default_rotated_wave_2d_subgraph()
            card.params["subgraph_data"] = subgraph_data

        title = card.node_instance.title if card.node_instance else "Subgraph"
        self.breadcrumb_lbl.setText(f"📁 Root Graph > 📦 {title} ({node_id})")
        self.back_btn.setVisible(True)
        self.templates_btn.setEnabled(False)
        self._populate_add_node_menu(in_subgraph=True)

        self._is_updating = True
        try:
            self.scene.load_graph_data(subgraph_data)
        finally:
            self._is_updating = False

        QtCore.QTimer.singleShot(50, self.view.zoom_to_fit)

    def close_subgraph(self):
        """Return from subgraph view back to root graph view."""
        if not self.current_subgraph_node_id or not self.root_graph_data:
            return

        # Sync active subgraph changes into root_graph_data
        curr_subgraph_data = self.scene.get_graph_data()
        for n in self.root_graph_data.get("nodes", []):
            if n.get("id") == self.current_subgraph_node_id:
                if "params" not in n:
                    n["params"] = {}
                n["params"]["subgraph_data"] = curr_subgraph_data
                break

        self.current_subgraph_node_id = None
        self.breadcrumb_lbl.setText("📁 Root Graph")
        self.back_btn.setVisible(False)
        self.templates_btn.setEnabled(True)
        self._populate_add_node_menu(in_subgraph=False)

        self._is_updating = True
        try:
            self.scene.load_graph_data(self.root_graph_data)
        finally:
            self._is_updating = False

        QtCore.QTimer.singleShot(50, self.view.zoom_to_fit)
        self._on_graph_changed()

    def _load_template(self, template_name):
        if self.current_subgraph_node_id:
            self.close_subgraph()
        data = get_template_graph(template_name, self.is_2d)
        self.scene.load_graph_data(data)
        self.save_to_object()

    def _on_clear(self):
        res = QtWidgets.QMessageBox.question(
            self, "Clear Graph", "Are you sure you want to clear all nodes?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if res == QtWidgets.QMessageBox.Yes:
            self.scene.clear_graph()

    def _on_copy_formula(self):
        txt = self.formula_preview.toPlainText().strip()
        if txt:
            QtWidgets.QApplication.clipboard().setText(txt)
            self.status_lbl.setText("Status: Formula copied to clipboard!")

    def _on_graph_changed(self):
        if self._is_updating:
            return

        if self.current_subgraph_node_id and self.root_graph_data:
            curr_subgraph_data = self.scene.get_graph_data()
            for n in self.root_graph_data.get("nodes", []):
                if n.get("id") == self.current_subgraph_node_id:
                    if "params" not in n:
                        n["params"] = {}
                    n["params"]["subgraph_data"] = curr_subgraph_data
                    break
            eval_data = self.root_graph_data
        else:
            eval_data = self.scene.get_graph_data()

        formula, comments, err = compile_graph(eval_data)

        if err:
            self.status_lbl.setText(f"Status: {err}")
            self.status_lbl.setStyleSheet("font-weight: bold; color: #f87171;")
            self.formula_preview.setPlainText("")
        else:
            self.status_lbl.setText("Status: Valid Graph")
            self.status_lbl.setStyleSheet("font-weight: bold; color: #4ade80;")
            self.formula_preview.setPlainText(formula)

            if self.live_update_chk.isChecked():
                self.save_to_object()

    def load_from_object(self):
        self._is_updating = True
        try:
            loaded = False
            if self.target_obj and hasattr(self.target_obj, "NodeGraphJson"):
                json_str = getattr(self.target_obj, "NodeGraphJson", "")
                if json_str:
                    try:
                        data = json.loads(json_str)
                        if data and "nodes" in data and len(data["nodes"]) > 0:
                            self.scene.load_graph_data(data)
                            loaded = True
                    except Exception as e:
                        fld_logger.warn(f"Failed to parse NodeGraphJson: {e}")

            if not loaded:
                # Load default template for mode
                default_tmpl = "2D: Dual Waves" if self.is_2d else "3D: Simple Sine"
                data = get_template_graph(default_tmpl, self.is_2d)
                self.scene.load_graph_data(data)
        finally:
            self._is_updating = False
        self._on_graph_changed()

    def save_to_object(self):
        if not self.target_obj:
            return

        if self.current_subgraph_node_id and self.root_graph_data:
            curr_subgraph_data = self.scene.get_graph_data()
            for n in self.root_graph_data.get("nodes", []):
                if n.get("id") == self.current_subgraph_node_id:
                    if "params" not in n:
                        n["params"] = {}
                    n["params"]["subgraph_data"] = curr_subgraph_data
                    break
            gdata = self.root_graph_data
        else:
            gdata = self.scene.get_graph_data()

        formula, comments, err = compile_graph(gdata)
        if err or not formula:
            return

        # Ensure NodeGraphJson property exists
        if not hasattr(self.target_obj, "NodeGraphJson"):
            try:
                self.target_obj.addProperty(
                    "App::PropertyString", "NodeGraphJson", "Noise", "Serialized node graph"
                )
            except Exception as e:
                fld_logger.debug(f"save_to_object: could not add NodeGraphJson ({e})")

        try:
            self.target_obj.NodeGraphJson = json.dumps(gdata, default=str)
            self.target_obj.Formula = formula
            from freecad.fields.core.sdf.sdf.noise import parse_custom_params
            parsed_params = parse_custom_params(formula)
            for p_info in parsed_params:
                p_name = p_info["name"]
                p_type = p_info["type"]
                p_def = p_info["default"]
                prop_name = "Custom_" + p_name
                if not hasattr(self.target_obj, prop_name) and hasattr(self.target_obj, "addProperty"):
                    if p_type == "bool":
                        prop_type = "App::PropertyBool"
                    elif p_type == "int":
                        prop_type = "App::PropertyInteger"
                    else:
                        prop_type = "App::PropertyFloat"
                    try:
                        self.target_obj.addProperty(prop_type, prop_name, "Custom Params", f"Custom parameter {prop_name}")
                        setattr(self.target_obj, prop_name, bool(p_def) if p_type == "bool" else p_def)
                    except Exception:
                        pass
        except Exception as e:
            fld_logger.warn(f"Failed to set Formula or NodeGraphJson: {e}")

        # If tool is open, trigger commit and update panel's text edit
        panel = None
        if self.tool:
            panel = getattr(self.tool, "panel", getattr(self.tool, "_panel", None))
        if not panel and hasattr(self, "parent"):
            p = self.parent()
            if hasattr(p, "_update_custom_widgets"):
                panel = p

        if panel:
            if hasattr(panel, "_formula_edit"):
                panel._formula_edit.blockSignals(True)
                panel._formula_edit.setPlainText(formula)
                panel._formula_edit.blockSignals(False)
            if hasattr(panel, "_update_custom_widgets"):
                panel._update_custom_widgets()

        if self.tool:
            self.tool._commit_changes()
