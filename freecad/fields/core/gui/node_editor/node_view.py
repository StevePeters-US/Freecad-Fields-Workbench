# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/gui/node_editor/node_view.py

QGraphicsView with smooth pan, zoom, grid rendering, and rubberband selection.
"""
from PySide import QtWidgets, QtGui, QtCore
from freecad.fields.core import fld_logger
from freecad.fields.core.gui.node_editor.node_items import NodeCardItem, NodeSocketItem


class NodeGraphView(QtWidgets.QGraphicsView):
    """Interactive canvas view for editing the node graph."""

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(
            QtGui.QPainter.Antialiasing |
            QtGui.QPainter.TextAntialiasing |
            QtGui.QPainter.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)

        self._is_panning = False
        self._pan_start = QtCore.QPoint()
        self._rmb_press_pos = None
        self._rmb_dragged = False
        self._is_knife_cutting = False
        self._knife_points = []
        self._suppress_next_context_menu = False
        self._zoom = 1.0
        self._min_zoom = 0.2
        self._max_zoom = 3.0

        # Style background
        self.setBackgroundBrush(QtGui.QBrush(QtGui.QColor("#0f172a")))

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        # Draw background grid dots
        grid_size = 24.0
        left = int(rect.left()) - (int(rect.left()) % int(grid_size))
        top = int(rect.top()) - (int(rect.top()) % int(grid_size))

        painter.setPen(QtGui.QPen(QtGui.QColor("#1e293b"), 2.0))
        points = []
        x = left
        while x < rect.right():
            y = top
            while y < rect.bottom():
                points.append(QtCore.QPointF(x, y))
                y += grid_size
            x += grid_size
        painter.drawPoints(points)

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        # Blender-style knife cut line
        if getattr(self, "_is_knife_cutting", False) and len(getattr(self, "_knife_points", [])) >= 2:
            painter.save()
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            pen = QtGui.QPen(QtGui.QColor("#ef4444"), 2.0, QtCore.Qt.DashLine)
            painter.setPen(pen)
            path = QtGui.QPainterPath()
            pts = self._knife_points
            path.moveTo(pts[0])
            for pt in pts[1:]:
                path.lineTo(pt)
            painter.drawPath(path)
            painter.restore()

    def wheelEvent(self, event):
        try:
            delta = event.angleDelta().y()
        except AttributeError:
            delta = event.delta()

        factor = 1.15 if delta > 0 else 1.0 / 1.15
        new_zoom = self._zoom * factor
        if self._min_zoom <= new_zoom <= self._max_zoom:
            self._zoom = new_zoom
            self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event):
        if getattr(self, "_is_knife_cutting", False):
            # A new gesture starting while the knife flag is still set means
            # every "button came up" signal was missed (e.g. focus lost mid-
            # drag) -- clear the stale state rather than let it eat every
            # future move/click as a knife cut. `None` end point: an abandoned
            # gesture must not retroactively slice everything between where it
            # stopped and wherever the user happens to click next.
            self._finish_knife_cut(None)
        # A new gesture starts here, so any suppression left over from the last
        # one is spent: this bounds the flag's life to a single gesture and
        # stops it swallowing an unrelated context menu later on.
        self._suppress_next_context_menu = False

        get_mods = getattr(QtWidgets.QApplication, "keyboardModifiers", lambda: 0)
        modifiers = get_mods()
        if event.button() == QtCore.Qt.MiddleButton or (event.button() == QtCore.Qt.LeftButton and modifiers & QtCore.Qt.AltModifier):
            self._is_panning = True
            self._pan_start = event.pos()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return

        if event.button() == QtCore.Qt.RightButton:
            if modifiers & QtCore.Qt.ControlModifier:
                # Blender knife cut tool!
                self._is_knife_cutting = True
                self._knife_points = [self.mapToScene(event.pos())]
                self.viewport().update()
                event.accept()
                return
            self._rmb_press_pos = event.pos()
            self._rmb_dragged = False
            event.accept()
            return

        super().mousePressEvent(event)

    def _finish_knife_cut(self, end_pt):
        """End an in-progress knife gesture, cutting the final segment.

        Every signal that can mean "the right button came up" routes here:
        mouseReleaseEvent, a move that reports no buttons held, and
        contextMenuEvent. Which of those actually arrives is platform- and
        timing-dependent -- on Windows the release alone proved unreliable,
        which left the knife live forever once it was the only exit (NE-017).
        Committing the last segment from each of them, instead of discarding
        the gesture, makes the order they arrive in stop mattering: whichever
        is first ends the cut, and the rest no-op.
        """
        if not getattr(self, "_is_knife_cutting", False):
            return
        if self._knife_points and end_pt is not None:
            scene = self.scene()
            if hasattr(scene, "cut_wires_intersecting"):
                scene.cut_wires_intersecting(self._knife_points[-1], end_pt)
        self._is_knife_cutting = False
        self._knife_points = []
        # Windows delivers QContextMenuEvent on right-button RELEASE, so the menu
        # would open on top of the finished cut. Swallow the next one. Cleared
        # again on the next press, so it can never eat a later, unrelated menu.
        self._suppress_next_context_menu = True
        self.viewport().update()

    def mouseMoveEvent(self, event):
        get_buttons = getattr(event, "buttons", lambda: 0)
        buttons = get_buttons()
        if buttons == 0:
            if self._is_panning:
                self._is_panning = False
                self.setCursor(QtCore.Qt.ArrowCursor)
            if getattr(self, "_is_knife_cutting", False):
                # No buttons held: the release happened, whether or not its
                # event reached us. Commit here -- clearing without cutting is
                # what used to drop the final segment (NE-017).
                self._finish_knife_cut(self.mapToScene(event.pos()))
                event.accept()
                return

        if getattr(self, "_is_knife_cutting", False):
            curr_pt = self.mapToScene(event.pos())
            if self._knife_points:
                prev_pt = self._knife_points[-1]
                scene = self.scene()
                if hasattr(scene, "cut_wires_intersecting"):
                    scene.cut_wires_intersecting(prev_pt, curr_pt)
            self._knife_points.append(curr_pt)
            self.viewport().update()
            event.accept()
            return

        # `buttons` (not just `_rmb_press_pos`) gates this: _rmb_press_pos is no
        # longer cleared on a stray zero-buttons move (see above), so without
        # this check a leftover press position from an already-finished
        # gesture would spuriously kick off a pan on the next real move.
        if buttons != 0 and self._rmb_press_pos is not None and not self._is_panning:
            if (event.pos() - self._rmb_press_pos).manhattanLength() > 5:
                self._rmb_dragged = True
                self._is_panning = True
                self._pan_start = event.pos()
                self.setCursor(QtCore.Qt.ClosedHandCursor)

        if self._is_panning:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._is_panning:
            self._is_panning = False
            self.setCursor(QtCore.Qt.ArrowCursor)

        if event.button() == QtCore.Qt.RightButton:
            if getattr(self, "_is_knife_cutting", False):
                self._finish_knife_cut(self.mapToScene(event.pos()))
                event.accept()
                return

            was_dragged = self._rmb_dragged
            self._rmb_press_pos = None
            self._rmb_dragged = False

            # A knife gesture that some earlier signal already finished must not
            # fall through to the menu here: _rmb_press_pos was never set for a
            # Ctrl+RMB press, so `was_dragged` is False and this would read a
            # finished cut as a plain right-click (NE-017).
            if not was_dragged and not getattr(self, "_suppress_next_context_menu", False):
                self._show_context_menu(event.pos(), event.globalPos())
                event.accept()
                return

        self._rmb_press_pos = None
        self._rmb_dragged = False
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        # Windows delivers this on right-button RELEASE, so a knife gesture that
        # is still open when it arrives means this event IS the release -- it can
        # come before, or instead of, mouseReleaseEvent. Commit the cut from it
        # rather than only swallowing the menu, which left the knife live on the
        # cursor with no way to put it down (NE-017).
        if getattr(self, "_is_knife_cutting", False):
            self._finish_knife_cut(self.mapToScene(event.pos()))
            # This very event is the one _finish_knife_cut wanted suppressed.
            self._suppress_next_context_menu = False
            event.accept()
            return
        # Suppression is keyed on the gesture, not on the modifier still being held:
        # by the time this arrives Ctrl may already be up, and _show_context_menu
        # would then wipe the knife state.
        if getattr(self, "_suppress_next_context_menu", False):
            self._suppress_next_context_menu = False
            event.accept()
            return
        get_mods = getattr(QtWidgets.QApplication, "keyboardModifiers", lambda: 0)
        if get_mods() & QtCore.Qt.ControlModifier:
            event.accept()
            return
        self._show_context_menu(event.pos(), event.globalPos())
        event.accept()

    def _show_context_menu(self, pos, global_pos):
        self._is_panning = False
        self._rmb_press_pos = None
        self._rmb_dragged = False
        self._is_knife_cutting = False
        self._knife_points = []
        self._suppress_next_context_menu = False
        self.setCursor(QtCore.Qt.ArrowCursor)
        parent_dialog = self.window()
        if not hasattr(parent_dialog, "show_canvas_context_menu"):
            parent_dialog = self.parent()
        if hasattr(parent_dialog, "show_canvas_context_menu"):
            parent_dialog.show_canvas_context_menu(pos, global_pos)

    def zoom_to_fit(self):
        """Fit all nodes into the current viewport with a comfortable margin."""
        rect = self.scene().itemsBoundingRect()
        if not rect.isEmpty():
            self.fitInView(rect.adjusted(-60, -60, 60, 60), QtCore.Qt.KeepAspectRatio)
            try:
                self._zoom = max(self._min_zoom, min(self._max_zoom, self.transform().m11()))
            except Exception as exc:  # safe: transform().m11() query is best-effort cache update
                fld_logger.debug_throttled("node_view:zoom_to_fit", f"[node_view] zoom_to_fit transform query failed: {exc}")
                pass
