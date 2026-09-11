# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/sculpt_brush_tool.py

SculptBrushTool -- interactive volumetric brush tool for sculpting SDF objects
via bounded sub-box stamping into discrete sculpt layers.
"""
import FreeCAD
import FreeCADGui
from PySide import QtWidgets, QtCore

from freecad.fields.core import fld_logger
from freecad.fields.tools.fld_sdf_tool_base import FldSdfModifierToolBase
from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
from freecad.fields.core.sdf.sdf.brush_library import default_brush
from freecad.fields.core.sdf.sdf import brush_library
from freecad.fields.core.sdf.sdf.sculpt_brush import SculptBrush
from freecad.fields.core.objects.fld_sculpt_layer import find_or_create_layer, FldSculptLayerProxy
from freecad.fields.core.objects.fld_surface_id import stamp_surface_id
from freecad.fields.core.objects.fld_modifier_stack import get_chain
from freecad.fields.core.input.fld_gui_utils import DynamicLimitSlider
from freecad.fields.core.gui.brush_editor_panel import FalloffCurveWidget, render_cross_section_pixmap


class SculptBrushTaskPanel:
    """Task panel for SculptBrushTool inside FreeCAD's Tasks view."""

    def __init__(self, tool):
        self.tool = tool
        self.form = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.form)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── 1. Brush Library ──────────────────────────────────────────────
        brush_grp = QtWidgets.QGroupBox("Brush Library")
        bl = QtWidgets.QVBoxLayout(brush_grp)

        self.brush_combo = QtWidgets.QComboBox()
        self.brush_combo.currentTextChanged.connect(self._on_brush_selected)
        bl.addWidget(self.brush_combo)

        btn_row = QtWidgets.QHBoxLayout()
        self.save_as_btn = QtWidgets.QPushButton("Save As...")
        self.save_as_btn.clicked.connect(self._on_save_as)
        btn_row.addWidget(self.save_as_btn)

        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_brush_list)
        btn_row.addWidget(self.refresh_btn)
        bl.addLayout(btn_row)
        layout.addWidget(brush_grp)

        # ── 2. Visual Previews (Cross Section + Falloff) ──────────────────
        vis_grp = QtWidgets.QGroupBox("Cross Section & Falloff")
        vl = QtWidgets.QHBoxLayout(vis_grp)
        self.cross_section_lbl = QtWidgets.QLabel()
        self.cross_section_lbl.setFixedSize(80, 80)
        self.cross_section_lbl.setScaledContents(True)
        self.cross_section_lbl.setStyleSheet("border: 1px solid #334155; background: #0f172a;")
        vl.addWidget(self.cross_section_lbl)

        self.falloff_widget = FalloffCurveWidget(1.0, 1.0)
        vl.addWidget(self.falloff_widget)
        layout.addWidget(vis_grp)

        # ── 3. Stroke & Brush Parameters ──────────────────────────────────
        param_grp = QtWidgets.QGroupBox("Parameters")
        pl = QtWidgets.QFormLayout(param_grp)

        self.radius_slider = DynamicLimitSlider(value=tool._radius, min_val=1.0, max_val=200.0, step=1.0, decimals=1)
        self.radius_slider.valueChanged.connect(self._on_param_changed)
        pl.addRow("Radius (mm):", self.radius_slider)

        self.strength_slider = DynamicLimitSlider(value=1.0, min_val=0.01, max_val=1.0, step=0.05, decimals=2)
        self.strength_slider.valueChanged.connect(self._on_param_changed)
        pl.addRow("Strength:", self.strength_slider)

        self.falloff_slider = DynamicLimitSlider(value=1.0, min_val=0.1, max_val=4.0, step=0.1, decimals=2)
        self.falloff_slider.valueChanged.connect(self._on_param_changed)
        pl.addRow("Falloff:", self.falloff_slider)

        self.spacing_slider = DynamicLimitSlider(value=0.35, min_val=0.05, max_val=1.0, step=0.05, decimals=2)
        self.spacing_slider.valueChanged.connect(self._on_param_changed)
        pl.addRow("Spacing:", self.spacing_slider)

        self.smooth_k_slider = DynamicLimitSlider(value=0.0, min_val=0.0, max_val=20.0, step=0.5, decimals=1)
        self.smooth_k_slider.valueChanged.connect(self._on_param_changed)
        pl.addRow("Smooth K:", self.smooth_k_slider)

        layout.addWidget(param_grp)

        # ── 4. Instructions ───────────────────────────────────────────────
        info_lbl = QtWidgets.QLabel(
            "<b>Controls:</b><br>"
            "• <b>Left Drag:</b> Sculpt (Deposit)<br>"
            "• <b>Ctrl + Drag:</b> Carve (Subtract)<br>"
            "• <b>[ / ]:</b> Adjust Radius<br>"
            "• <b>Enter:</b> Commit | <b>Esc:</b> Cancel"
        )
        info_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; padding: 4px;")
        layout.addWidget(info_lbl)

        layout.addStretch()

        self.refresh_brush_list()
        self.update_ui()

    def refresh_brush_list(self):
        brushes = brush_library.list_brushes()
        self.brush_combo.blockSignals(True)
        self.brush_combo.clear()
        for b in brushes:
            self.brush_combo.addItem(b)
        active_name = getattr(self.tool._brush, "name", "")
        if active_name and active_name in brushes:
            idx = self.brush_combo.findText(active_name)
            if idx >= 0:
                self.brush_combo.setCurrentIndex(idx)
        self.brush_combo.blockSignals(False)
        self._update_preview()

    def update_ui(self):
        brush = self.tool._brush
        if brush:
            self.strength_slider.blockSignals(True)
            self.falloff_slider.blockSignals(True)
            self.spacing_slider.blockSignals(True)
            self.radius_slider.blockSignals(True)
            self.smooth_k_slider.blockSignals(True)

            self.strength_slider.setValue(brush.strength)
            self.falloff_slider.setValue(brush.falloff)
            self.spacing_slider.setValue(brush.spacing)
            self.radius_slider.setValue(self.tool._radius)
            self.smooth_k_slider.setValue(self.tool._smooth_k)

            self.strength_slider.blockSignals(False)
            self.falloff_slider.blockSignals(False)
            self.spacing_slider.blockSignals(False)
            self.radius_slider.blockSignals(False)
            self.smooth_k_slider.blockSignals(False)

            self.falloff_widget.set_params(brush.strength, brush.falloff)
            self._update_preview()

    def _on_brush_selected(self, name):
        if not name:
            return
        brush = brush_library.load_brush(name)
        if brush is None:
            brush = brush_library.default_brush()
        self.tool.set_brush(brush)
        self.update_ui()

    def _on_param_changed(self):
        brush = self.tool._brush
        if brush:
            brush.strength = self.strength_slider.value()
            brush.falloff = self.falloff_slider.value()
            brush.spacing = self.spacing_slider.value()
            self.falloff_widget.set_params(brush.strength, brush.falloff)
        self.tool.set_radius(self.radius_slider.value())
        self.tool.set_smooth_k(self.smooth_k_slider.value())
        self._update_preview()

    def _update_preview(self):
        brush = self.tool._brush
        if brush:
            pix = render_cross_section_pixmap(brush, size=80)
            self.cross_section_lbl.setPixmap(pix)

    def _on_save_as(self):
        brush = self.tool._brush
        if not brush:
            return
        name, ok = QtWidgets.QInputDialog.getText(self.form, "Save Brush As", "Enter new brush name:")
        if ok and name.strip():
            name = name.strip()
            new_brush = SculptBrush(
                name, brush.grid,
                strength=brush.strength,
                falloff=brush.falloff,
                spacing=brush.spacing
            )
            if brush_library.save_brush(new_brush):
                self.refresh_brush_list()
                idx = self.brush_combo.findText(name)
                if idx >= 0:
                    self.brush_combo.setCurrentIndex(idx)

    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def accept(self):
        self.tool._dialog_open = False
        self.tool.finish()
        return True

    def reject(self):
        self.tool.cancel()
        return True


class SculptBrushTool(FldSdfModifierToolBase):
    """Interactive sculpting brush tool."""

    SUPPORTS_MODAL = False
    _active_brush = None  # Class variable holding last chosen brush across sessions

    def __init__(self):
        super().__init__()
        if SculptBrushTool._active_brush is None:
            SculptBrushTool._active_brush = default_brush()
        self._brush = SculptBrushTool._active_brush
        self._radius = 15.0
        self._smooth_k = 0.0
        self._is_dragging = False
        self._drag_timer = None
        self._last_dab_pos = None
        self._current_layer = None
        self._ghost_label = None
        self._ctrl_down = False
        self._target = None

    def get_command_id(self):
        return "Fields_SculptBrush"

    def get_handled_types(self):
        return ["FldObjectProxy", "FldVoxelFieldProxy", "FldSculptLayerProxy", "FldModifierProxyBase"]

    def _make_panel(self):
        return SculptBrushTaskPanel(self)

    def edit_object(self, obj):
        super().edit_object(obj)
        base, chain = get_chain(obj)
        self._target = chain[-1] if chain else (base or obj)
        doc = getattr(self._target, "Document", None) or FreeCAD.activeDocument()
        doc_name = doc.Name if doc else "Doc"
        self._ghost_label = f"{doc_name}.__brush_ghost"
        self._update_status()

    def set_brush(self, brush):
        """Set active brush asset for this tool and the class session."""
        self._brush = brush
        SculptBrushTool._active_brush = brush
        if getattr(self, "panel", None) and hasattr(self.panel, "update_ui"):
            self.panel.update_ui()

    def set_radius(self, radius):
        self._radius = max(1.0, float(radius))
        if getattr(self, "panel", None) and hasattr(self.panel, "update_ui"):
            self.panel.update_ui()
        self._update_status()

    def set_smooth_k(self, smooth_k):
        self._smooth_k = max(0.0, float(smooth_k))
        if getattr(self, "panel", None) and hasattr(self.panel, "update_ui"):
            self.panel.update_ui()

    def _is_ctrl_pressed(self, event_dict=None):
        if isinstance(event_dict, dict):
            if event_dict.get("ctrl", False):
                return True
            mods = event_dict.get("Modifiers", 0)
            try:
                if bool(int(mods) & int(QtCore.Qt.ControlModifier)):
                    return True
            except Exception:
                pass
            key_code = event_dict.get("Key", 0)
            if key_code == QtCore.Qt.Key_Control:
                return True
            key_str = event_dict.get("key", "")
            if key_str in ("Control", "ctrl", "Ctrl"):
                return True
        if FreeCAD.GuiUp:
            try:
                return bool(QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ControlModifier)
            except Exception:
                pass
        return False

    def _update_status(self):
        msg = f"Sculpt Brush | Radius: {self._radius:.1f} mm | Mode: {'Carve (Ctrl / Subtractive)' if self._ctrl_down else 'Deposit (Additive)'}"
        if FreeCAD.GuiUp:
            mw = FreeCADGui.getMainWindow()
            if mw and mw.statusBar():
                mw.statusBar().showMessage(msg, 3000)

    # -- Hover Ghost -----------------------------------------------------------

    def _hide_ghost(self):
        if self._ghost_label:
            try:
                FldSceneVoxelRenderer.get_instance().unregister_field(self._ghost_label)
            except Exception as e:
                fld_logger.debug(f"SculptBrushTool._hide_ghost failed: {e}")

    def _update_ghost(self, hit_pos):
        if hit_pos is None or self._brush is None or not self._ghost_label:
            self._hide_ghost()
            return
        try:
            ghost_field = self._brush.stamp_field(hit_pos, self._radius)
            stamp_surface_id(self._target, ghost_field)
            renderer = FldSceneVoxelRenderer.get_instance()
            renderer.update_field(self._ghost_label, ghost_field)
        except Exception as e:
            fld_logger.debug(f"SculptBrushTool._update_ghost failed: {e}")

    def _get_mouse_hit(self, event_dict):
        """Where on the surface the cursor is, or None when it is over nothing."""
        if not self.projector:
            return None
        return self.projector.get_surface_hit(event_dict)

    def handle_move(self, event_dict):
        if self._is_dragging:
            return True
        is_ctrl = self._is_ctrl_pressed(event_dict)
        if is_ctrl != self._ctrl_down:
            self._ctrl_down = is_ctrl
            self._update_status()
        hit = self._get_mouse_hit(event_dict)
        self._update_ghost(hit)
        return True

    # -- Hotkeys ---------------------------------------------------------------

    def handle_keyboard(self, event_dict):
        key_code = event_dict.get("Key", 0) if isinstance(event_dict, dict) else 0
        key = event_dict.get("key", "") if isinstance(event_dict, dict) else ""
        text = event_dict.get("Text", "") if isinstance(event_dict, dict) else ""
        type_str = event_dict.get("type", "") if isinstance(event_dict, dict) else ""

        is_ctrl = self._is_ctrl_pressed(event_dict)
        if is_ctrl != self._ctrl_down:
            self._ctrl_down = is_ctrl
            self._update_status()

        is_down = type_str == "key_down" or key_code != 0 or bool(text)
        if is_down:
            if key == "[" or text == "[":
                self.set_radius(self._radius * 0.9)
                return True
            elif key == "]" or text == "]":
                self.set_radius(self._radius * 1.1)
                return True
            elif key in ("Escape", "escape") or key_code == QtCore.Qt.Key_Escape:
                self.cancel()
                return True
            elif key in ("Return", "Enter") or key_code in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                self.finish()
                return True
        return super().handle_keyboard(event_dict)

    # -- Stroke Drag Loop ------------------------------------------------------
    # _start_drag_timer/_stop_drag_timer are no longer overridden here (CR-050)
    # -- FldSdfModifierToolBase -> FldSdfToolBase -> FldBase already mixes in
    # DragTimerMixin, so this class had it available the whole time; the bare
    # 16ms QTimer methods that used to live here just shadowed it, which meant
    # sculpt strokes neither participated in render-state switching
    # (_RS_INTERACTIVE/_RS_QUALITY) nor logged perf metrics the way every other
    # drag tool does. The mixin also drives its tick off the user's
    # configurable DragTickRate (default 30Hz) rather than the shadowed 16ms --
    # a real, user-visible change to the default sculpt responsiveness, not
    # just a rename; confirm interactively.

    def on_button1_down(self, event_dict):
        self._ctrl_down = self._is_ctrl_pressed(event_dict)
        pos = self._get_mouse_hit(event_dict)
        if pos is None and isinstance(event_dict, dict):
            pos = event_dict.get("pos", None)
        if pos is None:
            return False

        self._hide_ghost()

        doc = getattr(self._target, "Document", None) or FreeCAD.activeDocument()
        if doc and hasattr(doc, "openTransaction"):
            doc.openTransaction("Sculpt Brush")

        self._current_layer = find_or_create_layer(self._target, subtractive=self._ctrl_down, smooth_k=self._smooth_k)
        if hasattr(self._current_layer, "SmoothK"):
            self._current_layer.SmoothK = float(self._smooth_k)

        if hasattr(self._current_layer.Proxy, "begin_stroke"):
            self._current_layer.Proxy.begin_stroke(self._current_layer)

        self._last_dab_pos = None
        # _start_drag_timer() (DragTimerMixin) sets _is_dragging itself, after its
        # own internal _stop_drag_timer() safety-reset -- setting it True here
        # first would make that internal reset see a stale _is_dragging=True and
        # log a bogus perf summary (with the previous stroke's tick count) as
        # this new stroke begins.
        self._start_drag_timer()
        self._dab_at(pos)
        return True

    def _dab_at(self, pos):
        if self._brush is None or self._current_layer is None:
            return
        if self._last_dab_pos is not None:
            dist = (pos - self._last_dab_pos).Length
            spacing_dist = max(self._brush.spacing * self._radius, 0.2)
            if dist < spacing_dist:
                return

        stamp = self._brush.stamp_field(pos, self._radius)
        layer = self._current_layer
        proxy = layer.Proxy
        bb_min, bb_max = stamp.bounding_box()

        if hasattr(proxy, "ensure_covers"):
            proxy.ensure_covers(layer, bb_min, bb_max)
        if hasattr(proxy, "record_dab"):
            proxy.record_dab(layer, stamp)

        # For a FldSculptLayer modifier, the grid is a delta volume composed onto Source.
        # - Additive layer: UnionField(source, grid) -> grid holds added material (op="add")
        # - Subtractive layer: SubtractionField(source, grid) -> grid holds cutter volume (op="add")
        # For a direct/standalone voxel field without a modifier wrapper, carving uses op="cut".
        if isinstance(proxy, FldSculptLayerProxy):
            op = "add"
            smooth_k = 0.0
        else:
            op = "cut" if self._ctrl_down else "add"
            smooth_k = self._smooth_k

        grid = proxy._grid_field(layer)
        box = grid.stamp_local(stamp, operation=op, smooth_k=smooth_k)
        if box is not None:
            self._last_dab_pos = pos
            proxy.touch_grid(layer)
            renderer = FldSceneVoxelRenderer.get_instance()
            doc_name = layer.Document.Name if getattr(layer, "Document", None) else "Doc"
            lbl = f"{doc_name}.{layer.Name}"
            fld = proxy.get_sdf_field(layer)
            if fld is not None:
                renderer.update_field(lbl, fld)
                renderer._mark_dirty_region((bb_min, bb_max))

    def _drag_update(self):
        """Per-tick stroke update; called by DragTimerMixin._drag_update_wrapper."""
        if FreeCAD.GuiUp:
            buttons = QtWidgets.QApplication.mouseButtons()
            if not (buttons & QtCore.Qt.LeftButton):
                self._end_stroke()
                return
            self._ctrl_down = bool(QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ControlModifier)
        from freecad.fields.core.input.input_manager import FldInputManager
        event_dict = {"Position": FldInputManager.get_instance()._last_qt_pos}
        pos = self._get_mouse_hit(event_dict)
        if pos is not None:
            self._dab_at(pos)

    def on_button1_up(self, event_dict):
        self._end_stroke()
        return True

    def _end_stroke(self):
        if not self._is_dragging:
            return
        # _stop_drag_timer() (DragTimerMixin) owns clearing _is_dragging itself --
        # it only logs the perf-session summary while _is_dragging is still True,
        # so clearing it here first would silently suppress that log every time,
        # the same way every other DragTimerMixin tool leaves this to it.
        self._stop_drag_timer()
        layer = self._current_layer
        self._current_layer = None
        self._last_dab_pos = None

        if layer and hasattr(layer.Proxy, "end_stroke"):
            def _commit(l=layer):
                l.Proxy.end_stroke(l)
                doc = getattr(l, "Document", None)
                if doc:
                    if hasattr(doc, "commitTransaction"):
                        doc.commitTransaction()
                    doc.recompute()
            QtCore.QTimer.singleShot(0, _commit)

    # -- Termination / Cleanup -------------------------------------------------

    def _do_terminate(self):
        self._stop_drag_timer()
        self._hide_ghost()
        super()._do_terminate()

    def restore_original(self):
        self._hide_ghost()
        super().restore_original()
