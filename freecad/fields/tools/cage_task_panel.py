# SPDX-License-Identifier: CC-BY-NC-SA-4.0
from PySide import QtWidgets

from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip


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
            "• <b>Ctrl + D</b>: Subdivide Smooth<br>"
            "• <b>Alt + Z</b>: Toggle X-ray (see the net through the surface)"
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addSpacing(15)
        
        self.xray_check = QtWidgets.QCheckBox("X-ray (draw net through the surface)")
        self.xray_check.setChecked(tool._overlay.xray)
        self.xray_check.setToolTip(
            rich_tooltip(
                "CageTaskPanel",
                QT_TRANSLATE_NOOP(
                    "CageTaskPanel",
                    "Draws the control net through the surface so control points and curves stay visible.\nShortcut: Alt+Z.",
                ),
            )
        )
        self.xray_check.toggled.connect(self.tool.set_xray)
        layout.addWidget(self.xray_check)

        self.subdivide_btn = QtWidgets.QPushButton("Subdivide Smooth")
        self.subdivide_btn.clicked.connect(self._on_subdivide)
        layout.addWidget(self.subdivide_btn)
        
        layout.addStretch()

    def _on_subdivide(self):
        self.tool.trigger_subdivide()

    def sync_xray(self, on):
        """Reflect a hotkey-driven x-ray change without re-emitting toggled()."""
        was = self.xray_check.blockSignals(True)
        self.xray_check.setChecked(bool(on))
        self.xray_check.blockSignals(was)

    def accept(self):
        self.tool._dialog_open = False
        self.tool.finish()
        return True

    def reject(self):
        self.tool._dialog_open = False
        self.tool.cancel()
        return True
