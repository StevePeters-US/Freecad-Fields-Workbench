# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/gui/brush_editor_panel.py

Brush Editor Dock Widget Panel (SB-028).
Provides a 2D cross-section visualization, falloff curve plot, library asset management,
and parameter controls for sculpt brushes.
"""
import os
import numpy as np
import FreeCAD
import FreeCADGui
from PySide import QtWidgets, QtCore, QtGui

from freecad.fields.core import fld_logger
from freecad.fields.core.input.fld_gui_utils import DynamicLimitSlider
from freecad.fields.core.sdf.sdf import brush_library
from freecad.fields.core.sdf.sdf.sculpt_brush import SculptBrush
from freecad.fields.core.input.fld_tool_manager import FldToolManager


class FalloffCurveWidget(QtWidgets.QWidget):
    """Plots strength * (1 - t)**falloff over t in [0, 1]."""

    def __init__(self, strength=1.0, falloff=1.0, parent=None):
        super().__init__(parent)
        self._strength = strength
        self._falloff = falloff
        self.setFixedHeight(80)
        self.setMinimumWidth(120)

    def set_params(self, strength, falloff):
        self._strength = max(0.0, min(1.0, float(strength)))
        self._falloff = max(0.01, float(falloff))
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        w, h = self.width(), self.height()
        margin = 8
        pw, ph = w - 2 * margin, h - 2 * margin

        # Background
        painter.fillRect(0, 0, w, h, QtGui.QColor("#0f172a"))

        # Grid lines
        painter.setPen(QtGui.QPen(QtGui.QColor("#334155"), 1, QtCore.Qt.DashLine))
        painter.drawLine(margin, margin + ph // 2, margin + pw, margin + ph // 2)
        painter.drawLine(margin + pw // 2, margin, margin + pw // 2, margin + ph)

        # Plot curve
        painter.setPen(QtGui.QPen(QtGui.QColor("#f59e0b"), 2))
        ts = np.linspace(0.0, 1.0, max(pw, 10))
        ys = self._strength * np.power(np.maximum(1.0 - ts, 0.0), self._falloff)

        points = []
        for t, y in zip(ts, ys):
            px = margin + int(t * pw)
            py = margin + ph - int(y * ph)
            points.append(QtCore.QPoint(px, py))

        if len(points) >= 2:
            for i in range(len(points) - 1):
                painter.drawLine(points[i], points[i + 1])


def render_cross_section_pixmap(brush, size=128):
    """Evaluate brush on z=0 cross-section and return a QPixmap."""
    if brush is None or brush.grid is None:
        pix = QtGui.QPixmap(size, size)
        pix.fill(QtGui.QColor("#0f172a"))
        return pix

    xs = np.linspace(-0.6, 0.6, size)
    ys = np.linspace(-0.6, 0.6, size)
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    pts = np.column_stack((gx.ravel(), gy.ravel(), np.zeros_like(gx.ravel())))

    d = brush.grid.evaluate_grid(pts).reshape((size, size))

    # Construct RGBA buffer
    img_data = np.zeros((size, size, 4), dtype=np.uint8)

    # Shading
    inside = d <= 0.0
    contour = np.abs(d) <= (1.2 / size)
    outside = d > 0.0

    # Inside color: #3b82f6 (blue)
    img_data[inside, 0] = 59
    img_data[inside, 1] = 130
    img_data[inside, 2] = 246
    img_data[inside, 3] = 255

    # Outside shaded: darker blue-grey fading outward
    out_dist = np.clip(d[outside] * 2.0, 0.0, 1.0)
    img_data[outside, 0] = (30 * (1.0 - out_dist)).astype(np.uint8)
    img_data[outside, 1] = (41 * (1.0 - out_dist)).astype(np.uint8)
    img_data[outside, 2] = (59 * (1.0 - out_dist) + 20).astype(np.uint8)
    img_data[outside, 3] = 255

    # Zero contour: white line
    img_data[contour, 0] = 255
    img_data[contour, 1] = 255
    img_data[contour, 2] = 255
    img_data[contour, 3] = 255

    # Create QImage from buffer
    image = QtGui.QImage(img_data.data, size, size, size * 4, QtGui.QImage.Format_RGBA8888)
    return QtGui.QPixmap.fromImage(image)


class BrushEditorPanel(QtWidgets.QDockWidget):
    """Dock widget for sculpting brush assets and parameters."""

    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            mw = FreeCADGui.getMainWindow() if hasattr(FreeCADGui, "getMainWindow") else None
            cls._instance = cls(mw)
            if mw is not None:
                mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, cls._instance)
        return cls._instance

    def __init__(self, parent=None):
        super().__init__("Brush Editor", parent)
        self.setObjectName("Fld_BrushEditorPanel")
        self._current_brush = None
        self._debounce_timer = QtCore.QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(50)
        self._debounce_timer.timeout.connect(self._rebuild_preview)

        self._init_ui()
        self.refresh_brush_list()

    def _init_ui(self):
        container = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # ── 1. Brush Asset Library ────────────────────────────────────────────
        lib_grp = QtWidgets.QGroupBox("Brush Library")
        ll = QtWidgets.QVBoxLayout(lib_grp)
        self.brush_list = QtWidgets.QListWidget()
        self.brush_list.setFixedHeight(100)
        self.brush_list.currentTextChanged.connect(self._on_brush_selected)
        ll.addWidget(self.brush_list)

        btn_row = QtWidgets.QHBoxLayout()
        self.save_as_btn = QtWidgets.QPushButton("Save As...")
        self.save_as_btn.clicked.connect(self._on_save_as)
        btn_row.addWidget(self.save_as_btn)

        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_brush_list)
        btn_row.addWidget(self.refresh_btn)
        ll.addLayout(btn_row)
        layout.addWidget(lib_grp)

        # ── 2. Visual Previews (Cross Section + Falloff) ──────────────────────
        vis_grp = QtWidgets.QGroupBox("Cross Section & Falloff")
        vl = QtWidgets.QHBoxLayout(vis_grp)
        self.cross_section_lbl = QtWidgets.QLabel()
        self.cross_section_lbl.setFixedSize(100, 100)
        self.cross_section_lbl.setScaledContents(True)
        self.cross_section_lbl.setStyleSheet("border: 1px solid #334155;")
        vl.addWidget(self.cross_section_lbl)

        self.falloff_widget = FalloffCurveWidget(1.0, 1.0)
        vl.addWidget(self.falloff_widget)
        layout.addWidget(vis_grp)

        # ── 3. Stroke & Brush Parameters ──────────────────────────────────────
        param_grp = QtWidgets.QGroupBox("Parameters")
        pl = QtWidgets.QFormLayout(param_grp)

        self.radius_slider = DynamicLimitSlider(value=15.0, min_val=1.0, max_val=200.0, step=1.0, decimals=1)
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

        layout.addWidget(param_grp)
        layout.addStretch()
        self.setWidget(container)

    def refresh_brush_list(self):
        current = self.brush_list.currentItem().text() if self.brush_list.currentItem() else ""
        self.brush_list.clear()
        brushes = brush_library.list_brushes()
        for b in brushes:
            self.brush_list.addItem(b)
        if current and current in brushes:
            items = self.brush_list.findItems(current, QtCore.Qt.MatchExactly)
            if items:
                self.brush_list.setCurrentItem(items[0])
        elif brushes:
            self.brush_list.setCurrentRow(0)

    def _on_brush_selected(self, name):
        if not name:
            return
        brush = brush_library.load_brush(name)
        if brush is None:
            brush = brush_library.default_brush()
        self._current_brush = brush

        # Sync sliders
        self.strength_slider.setValue(brush.strength)
        self.falloff_slider.setValue(brush.falloff)
        self.spacing_slider.setValue(brush.spacing)

        # Notify active tool
        active_tool = FldToolManager.get_instance().get_active_tool()
        if active_tool and getattr(active_tool, "get_command_id", lambda: "")() == "Fields_SculptBrush":
            active_tool.set_brush(brush)

        self._debounce_timer.start()

    def _on_param_changed(self):
        if self._current_brush is None:
            return
        self._current_brush.strength = self.strength_slider.value()
        self._current_brush.falloff = self.falloff_slider.value()
        self._current_brush.spacing = self.spacing_slider.value()

        self.falloff_widget.set_params(self._current_brush.strength, self._current_brush.falloff)

        # Sync with active tool
        active_tool = FldToolManager.get_instance().get_active_tool()
        if active_tool and getattr(active_tool, "get_command_id", lambda: "")() == "Fields_SculptBrush":
            active_tool.set_brush(self._current_brush)
            active_tool.set_radius(self.radius_slider.value())

        self._debounce_timer.start()

    def _rebuild_preview(self):
        pix = render_cross_section_pixmap(self._current_brush, size=128)
        self.cross_section_lbl.setPixmap(pix)

    def _on_save_as(self):
        if self._current_brush is None:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Save Brush As", "Enter new brush name:")
        if ok and name.strip():
            name = name.strip()
            # Do not overwrite built-ins
            new_brush = SculptBrush(
                name, self._current_brush.grid,
                strength=self._current_brush.strength,
                falloff=self._current_brush.falloff,
                spacing=self._current_brush.spacing
            )
            if brush_library.save_brush(new_brush):
                self.refresh_brush_list()
                items = self.brush_list.findItems(name, QtCore.Qt.MatchExactly)
                if items:
                    self.brush_list.setCurrentItem(items[0])
