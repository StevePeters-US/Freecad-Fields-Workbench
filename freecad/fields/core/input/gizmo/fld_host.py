# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""`FldGizmoHost`: the Fields services behind the universal gizmo controller.

A `GizmoHost` (see `gizmo/models.py`) over `FldInputManager`, `ViewProjector`,
`fld_snap` and `fld_settings`. A tool constructs one with its view and the Coin node
the gizmo draws under.
"""
import FreeCAD
import FreeCADGui

from freecad.fields.core import fld_logger, fld_settings
from freecad.fields.core.input import fld_snap
from freecad.fields.core.input.input_manager import FldInputManager
from freecad.fields.core.input.fld_readout import FldReadout
from freecad.fields.core.input.view_projector import ViewProjector
from freecad.fields.core.input.gizmo.models import SnapSettings


class FldGizmoHost:
    """Fields' `GizmoHost`. `view` is the 3D view; `root` the Coin node to draw under."""

    def __init__(self, view, root, exclude=()):
        self.view = view
        self.root = root
        self.exclude = list(exclude)   # objects whose vertices never snap
        self._layer = None             # the SoAnnotation the gizmo draws under, made on demand
        self._readout = FldReadout()   # the HUD box, its label made on first use
        self._doc = None               # the document a transaction was opened on
        self._tx = None                # FreeCAD's (name, id) for it, from getActiveTransaction

    # --- screen and rays ------------------------------------------------------------

    def ray(self, pos):
        """Screen position -> world ray `(point, direction)`, or None."""
        p, d = FldInputManager.get_instance().get_ray(self.view, {"Position": pos})
        if p is None or d is None:
            return None
        return p, d

    def mouse_pos(self):
        """The latest mouse position, viewport logical px."""
        return FldInputManager.get_instance()._last_qt_pos

    def to_screen(self, p):
        """World point -> `(x, y)` logical px, or None."""
        return ViewProjector(self.view).project_to_screen(p)

    def handle_scale(self, at):
        """World units per screen pixel at `at`."""
        return fld_snap.world_per_px(self.view, at)

    # --- snapping -------------------------------------------------------------------

    def ctrl_held(self):
        return fld_snap.snap_active()

    def shift_held(self):
        from PySide import QtCore, QtWidgets
        return bool(QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ShiftModifier)

    def snap_settings(self):
        return SnapSettings(
            fld_settings.get_snap_step("grid"),
            fld_settings.get_snap_step("angle"),
            fld_settings.get_snap_step("scale"),
            fld_settings.get_snap_vertex_enabled(),
            fld_settings.get_snap_pixel_radius())

    def step_snap(self, kind, notches):
        """Step the `kind` increment through its presets; returns the new step."""
        return fld_snap.step_snap(kind, notches)

    def vertex_candidates(self, near):
        """The scene vertex nearest `near` within the capture radius, as a list. Never
        one of `exclude`'s."""
        res = fld_snap.snap_world_point(self.view, near, exclude=self.exclude)
        return [res.point] if res.kind else []

    # --- drawing and feedback -------------------------------------------------------

    def scene_root(self):
        """An SoAnnotation under `root`, so geometry never hides the gizmo
        (universal_gizmo geometry.md). Made on first use; `dispose` removes it."""
        if self._layer is None and self.root is not None:
            from pivy import coin
            self._layer = coin.SoAnnotation()
            self.root.addChild(self._layer)
        return self._layer

    def dispose(self):
        """Remove the annotation layer from `root` and the readout label. Detach the
        controller first."""
        self._readout.dispose()
        if self._layer is not None and self.root is not None:
            idx = self.root.findChild(self._layer)
            if idx >= 0:
                self.root.removeChild(idx)
        self._layer = None

    def status(self, text):
        """Show `text` in the status bar; None clears it. Best-effort."""
        try:
            sb = FreeCADGui.getMainWindow().statusBar()
            if text is None:
                sb.clearMessage()
            else:
                sb.showMessage(text)
        except Exception as e:
            fld_logger.debug(f"FldGizmoHost.status: status bar failed: {e}")

    def readout(self, text):
        """The HUD strip at the bottom of the view (D-53); None hides it. See `FldReadout`."""
        if text is None:
            self._readout.hide()
        else:
            self._readout.show(self.view, text)

    def redraw(self):
        if self.view is not None:
            self.view.redraw()

    # --- undo -----------------------------------------------------------------------

    def open_transaction(self, name):
        """Open `name` on the active document, remembering which one it was."""
        self._doc = FreeCAD.ActiveDocument
        if self._doc is not None:
            self._doc.openTransaction(name)

    def commit_transaction(self):
        """Commit on the document the transaction was opened on, not the active one now."""
        doc, self._doc = self._doc, None
        if doc is not None:
            doc.commitTransaction()

    def abort_transaction(self):
        doc, self._doc = self._doc, None
        if doc is not None:
            doc.abortTransaction()
