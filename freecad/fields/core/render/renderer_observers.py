# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/renderer_observers.py

The three observers that drive FldSceneVoxelRenderer from outside: the Qt event
filter that switches quality state during navigation, the selection observer
that keeps `_selected_labels` current, and the App document observer that
releases a field when its object goes away.

All three hold a back-reference to the renderer and reach into its attributes;
they are its collaborators, not a public API, and are split out only because
they are self-contained.
"""
import FreeCADGui

from freecad.fields.core import fld_logger
from freecad.fields.core.render.render_state import _RS_QUALITY, _RS_INTERACTIVE

try:
    from PySide.QtCore import QObject, QEvent, QTimer
except ImportError:
    from PySide.QtCore import QObject, QEvent, QTimer


class _FldNavFilter(QObject):
    """App-level event filter that drives the renderer state machine.

    MouseButtonPress  → _RS_INTERACTIVE (downscaled, fewer march steps)
    MouseButtonRelease → _RS_QUALITY    (native res, full march steps) + view.redraw()
    Wheel             → _RS_INTERACTIVE + 200ms debounce timer → _RS_QUALITY
    """

    def __init__(self, renderer):
        super().__init__()
        self._r = renderer
        self._wheel_timer = QTimer(self)
        self._wheel_timer.setSingleShot(True)
        self._wheel_timer.timeout.connect(self._on_wheel_end)

    def _redraw(self):
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            if view:
                view.redraw()
        except Exception as e:
            fld_logger.render_debug(f"_FldNavFilter._redraw failed: {e}")

    def _on_wheel_end(self):
        self._r._render_state = _RS_QUALITY
        self._redraw()

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.MouseButtonPress:
            self._wheel_timer.stop()
            self._r._render_state = _RS_INTERACTIVE
        elif t == QEvent.MouseButtonRelease:
            self._wheel_timer.stop()
            self._r._render_state = _RS_QUALITY
            self._redraw()
        elif t == QEvent.Wheel:
            self._r._render_state = _RS_INTERACTIVE
            self._wheel_timer.start(200)   # restart; 200ms after last scroll → quality
        return False   # never consume events

class _FldSelectionObserver:
    def __init__(self, renderer):
        self._renderer = renderer

    def addSelection(self, doc_name, obj_name, sub_name, pnt):
        label = f"{doc_name}.{obj_name}"
        self._renderer._selected_labels.add(label)
        self._renderer._field_meta_dirty = True
        self._redraw()

    def removeSelection(self, doc_name, obj_name, sub_name):
        label = f"{doc_name}.{obj_name}"
        self._renderer._selected_labels.discard(label)
        self._renderer._field_meta_dirty = True
        self._redraw()

    def clearSelection(self, doc_name):
        self._renderer._selected_labels.clear()
        self._renderer._field_meta_dirty = True
        self._redraw()

    def _redraw(self):
        try:
            if FreeCADGui.activeView():
                FreeCADGui.activeView().redraw()
        except Exception as e:
            fld_logger.debug(
                f"_FldSelectionObserver._redraw: activeView().redraw() failed "
                f"({e}); the selection highlight will refresh on the next frame."
            )


class _FldDocumentObserver:
    """Releases a scene field when its document object goes away.

    Every deletion path ends in App's document, but only one of them used to reach
    the renderer. `FldViewProvider.onDelete` does not fire for `doc.removeObject`,
    which is what the tree's Delete action calls on a modifier, and nothing fires
    at all when a document is closed.

    Measured 2026-08-26 (MS-010): after deleting a modifier from the tree,
    `doc.getObject("Sphere_Noise")` was `None` while `_registry` still listed
    `MS9.Sphere_Noise` and `_last_analytical_data` still carried it with a live
    bbox -- a ghost that was still being baked, not a stale dictionary key. The
    same purge found `NB001.Box` and `NB001.Noise` still registered after
    `FreeCAD.closeDocument("NB001")`.

    Observing App is the one place all three paths pass through, so it is the only
    place this is done -- the onDelete copy was removed rather than kept beside it.
    """

    def __init__(self, renderer):
        self._renderer = renderer

    def slotDeletedObject(self, obj):
        doc = getattr(obj, "Document", None)
        name = getattr(obj, "Name", None)
        if doc is None or not name:
            return
        try:
            self._renderer.unregister_field(f"{doc.Name}.{name}")
        except Exception as e:
            fld_logger.render_debug(f"_FldDocumentObserver.slotDeletedObject failed: {e}")

    def slotDeletedDocument(self, doc):
        # The document's objects are still reachable here -- verified live: the slot
        # fires before the teardown, and no per-object slot follows a close.
        try:
            names = [getattr(o, "Name", None) for o in (getattr(doc, "Objects", None) or [])]
            for name in names:
                if name:
                    self._renderer.unregister_field(f"{doc.Name}.{name}")
        except Exception as e:
            fld_logger.render_debug(f"_FldDocumentObserver.slotDeletedDocument failed: {e}")

