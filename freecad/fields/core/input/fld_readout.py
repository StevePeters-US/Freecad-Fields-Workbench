# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""`FldReadout`: the HUD strip at the bottom of a 3D view (universal_gizmo interaction.md HUD, D-53).

A QLabel on the view's own GL surface, `view.graphicsView().viewport()`, which is the
widget `ViewProjector` measures in. Never the input layer's `_cached_viewport`: every key
event resets that to None (`input_manager.py:218-219`), so typed values never reached
the box (INP-052).
"""
from PySide import QtCore, QtWidgets

from freecad.fields.core import fld_logger
from freecad.fields.core.input.input_manager import _qt_alive


class FldReadout:
    """One value strip, centred on the bottom edge of the view, MARGIN_PX up (D-53). It
    never narrows while it stays up, so changing values don't make it jitter. It never
    takes a click or focus. When the view has no GL surface it hides rather than leaving
    old text up."""

    MARGIN_PX = 8
    STYLE = ("QLabel { background: rgba(30, 30, 30, 220); color: white; "
             "border: 1px solid #888; border-radius: 3px; padding: 2px 6px; "
             "font-size: 12pt; }")

    def __init__(self):
        self._label = None
        self._width = 0     # the widest it has been since it last appeared

    def show(self, view, text):
        widget = self._surface(view)
        if widget is None:
            self.hide()
            return
        lbl = self._label_on(widget)
        lbl.setText(text)
        lbl.adjustSize()
        self._width = max(self._width, lbl.width())
        lbl.resize(self._width, lbl.height())
        x = max(0, int((widget.width() - self._width) / 2))
        y = max(0, widget.height() - lbl.height() - self.MARGIN_PX)
        lbl.move(x, y)
        lbl.show()
        lbl.raise_()

    def hide(self):
        self._width = 0
        if self._label is not None and _qt_alive(self._label):
            self._label.hide()

    def dispose(self):
        """Delete the label, if there is one."""
        lbl, self._label = self._label, None
        self._width = 0
        if lbl is not None and _qt_alive(lbl):
            lbl.hide()
            lbl.deleteLater()

    @staticmethod
    def _surface(view):
        """The view's GL surface, or None."""
        try:
            w = view.graphicsView().viewport()
        except Exception as e:
            fld_logger.debug(f"FldReadout: no GL surface for the view: {e}")
            return None
        return w if _qt_alive(w) else None

    def _label_on(self, widget):
        """The label, a child of `widget`; made on first use, remade for another view."""
        lbl = self._label
        if lbl is not None and _qt_alive(lbl) and lbl.parent() == widget:
            return lbl
        self.dispose()
        lbl = QtWidgets.QLabel(widget)
        lbl.setObjectName("FldGizmoReadout")
        lbl.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        lbl.setFocusPolicy(QtCore.Qt.NoFocus)
        lbl.setStyleSheet(self.STYLE)
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        self._label = lbl
        return lbl
