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

    It also owns the catch-up redraw that follows a frame the 30 fps quality
    throttle skipped (RF-001): render_frame books it, a full frame cancels it.
    """

    def __init__(self, renderer):
        super().__init__()
        self._r = renderer
        self._wheel_timer = QTimer(self)
        self._wheel_timer.setSingleShot(True)
        self._wheel_timer.timeout.connect(self._on_wheel_end)
        self._catch_up_timer = QTimer(self)
        self._catch_up_timer.setSingleShot(True)
        self._catch_up_timer.timeout.connect(self._redraw)

    def _redraw(self):
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            if view:
                view.redraw()
        except Exception as e:
            fld_logger.render_debug(f"_FldNavFilter._redraw failed: {e}")

    def request_catch_up(self, delay_s):
        """Redraw once, `delay_s` from now, unless a catch-up is already armed.

        A throttled frame composites the previous G-buffer; this redraw is what
        makes the frame after it run Passes 1-3. An armed timer is left alone,
        so a stream of throttled frames can't keep pushing it back. The +2 ms
        lands it past the window even if Qt's coarse timer fires a little early.
        """
        if not self._catch_up_timer.isActive():
            self._catch_up_timer.start(int(delay_s * 1000.0) + 2)

    def cancel_catch_up(self):
        """A full frame just drew (or tried to draw) the latest state."""
        self._catch_up_timer.stop()

    def stop_timers(self):
        """Called by detach: no timer may fire for a renderer that left the scene."""
        self._wheel_timer.stop()
        self._catch_up_timer.stop()

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
    A close releases by document prefix over the registry, not over `doc.Objects`,
    so a label whose object had already gone is released too, and inside one update
    batch so the releases finish before the scene is rebuilt without them.
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
        # By prefix, not by doc.Objects: a label whose object already left by a
        # route that missed slotDeletedObject has no object here to match, and
        # with its document gone nothing else would ever release it.
        #
        # Batched, because the document is already half torn down here: the first
        # `unregister_field` rebuilds the scene, the rebuild asks the appearance
        # about every label still registered, and for the dying document's own
        # labels that raises NameError ("Unknown document"). Unbatched, this
        # released one label and abandoned the rest (measured live 2026-10-04).
        r = self._renderer
        try:
            prefix = f"{doc.Name}."
            r.begin_update_batch()
            try:
                for label in [l for l in r._registry.fields if l.startswith(prefix)]:
                    r.unregister_field(label)
            finally:
                r.end_update_batch()
            r._matcap_cache.drop_document(doc.Name)
            # Dropping a document shifts every later document's variant layers down;
            # FieldMeta holds layer indices resolved before the drop (ML-013).
            r.refresh_appearance()
        except Exception as e:
            fld_logger.render_debug(f"_FldDocumentObserver.slotDeletedDocument failed: {e}")


    def slotUndoDocument(self, doc):
        self._defer_matcap_refresh(doc)

    def slotRedoDocument(self, doc):
        self._defer_matcap_refresh(doc)

    def _defer_matcap_refresh(self, doc):
        """Undo and redo roll the Matcap choice lists (view properties) back without
        touching doc.Meta, which decides them. Re-derive once FreeCAD has finished
        applying the step: writing properties from inside the slot would race it."""
        import FreeCAD
        name = doc.Name

        def refresh():
            try:
                live = FreeCAD.listDocuments().get(name)
                if live is None:
                    return                       # closed since
                from freecad.fields.core.objects.fld_view_provider import (
                    refresh_document_matcap_choices)
                refresh_document_matcap_choices(live)
                self._renderer.refresh_appearance()
            except Exception as e:
                fld_logger.render_debug(f"_FldDocumentObserver matcap refresh failed: {e}")

        QTimer.singleShot(0, refresh)


class _FldViewActivationObserver:
    """Makes the activated 3D view the one that draws Fields geometry.

    Hooked to the main window's `QMdiArea.subWindowActivated`, which fires for a new
    view (Std_ViewCreate), a click between two views of one document, and a switch
    of document, with `ActiveDocument.ActiveView` already the activated view
    (measured live 2026-10-05). One compiled scene serves every view, so it is the
    active document's, and the render guard lets only the active view draw it.
    Switching documents costs one scene-shader build and one volume bake: each
    field's GLSL is cached per label, so nothing is marked dirty here. Switching
    between views of one document rebuilds nothing.
    """

    def __init__(self, renderer):
        self._renderer = renderer
        self._area = None

    def connect(self):
        from PySide import QtWidgets
        area = FreeCADGui.getMainWindow().findChild(QtWidgets.QMdiArea)
        if area is None:
            return False
        area.subWindowActivated.connect(self.slotViewActivated)
        self._area = area
        return True

    def disconnect(self):
        area, self._area = self._area, None
        if area is not None:
            area.subWindowActivated.disconnect(self.slotViewActivated)

    def slotViewActivated(self, sub_window=None):
        r = self._renderer
        try:
            gui_doc = FreeCADGui.ActiveDocument
            view = gui_doc.ActiveView if gui_doc else None
            graph = view.getSceneGraph() if view else None
            name = gui_doc.Document.Name if gui_doc else None
        except Exception as e:
            fld_logger.render_debug(f"_FldViewActivationObserver: no active 3D view ({e})")
            return
        if graph is None or name is None:
            return
        r._active_graph = graph
        new_document = name != r._scene_doc
        if new_document:
            r._scene_doc = name
            r._volume_dirty = True
            r._dirty_region_full = True
        try:
            r._attach()
            if new_document:
                r._rebuild()
            view.redraw()
        except Exception as e:
            fld_logger.render_debug(f"_FldViewActivationObserver.slotViewActivated failed: {e}")
