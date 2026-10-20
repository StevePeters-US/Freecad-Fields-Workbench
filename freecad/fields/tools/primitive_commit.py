# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/primitive_commit.py

Everything the primitive creators write to the document: finalising a creation,
commit-and-enter-edit, persisting an edit session, and restoring the original
state on cancel.

Stateless mixin; `finish` itself stays on `PrimitiveCreatorBase` as the dispatcher
because `extrusion_creator.py` and `path_extrude_creator.py` override it and call
`_finalize_object`. `_on_committed` is `FldBase`'s hook, which creators override;
the commit code calls it through `self.`. The mixin must precede `FldSdfToolBase`
in the bases tuple, because `restore_original` exists on `FldBase` too.
"""

import FreeCADGui
from PySide import QtCore
from freecad.fields.core import fld_logger
from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
from freecad.fields.core.objects.fld_object import create_fld_object
from freecad.fields.core.objects.fld_object import apply_shaded_display_mode
from freecad.fields.core.mesh.fld_mesher import mesh_timer
from freecad.fields.tools.fld_base import ToolState

# Cell size for the preview timer label in `_do_commit`, in mm.
_PREVIEW_CELL_SIZE = 20.0


class PrimitiveCommitMixin:
    """The commit half of PrimitiveCreatorBase: finalize, commit-and-edit, persist an edit, restore."""

    def restore_original(self):
        obj = self._preview_obj
        if not obj or not getattr(self, "_is_editing", False) or not hasattr(self, "_original_props"):
            return
        props = self._original_props
        try:
            orig_field = props.get("SdfField")
            if orig_field is not None and hasattr(obj, "Proxy"):
                obj.Proxy.SdfField = orig_field
                label = f"{obj.Document.Name}.{obj.Name}"
                FldSceneVoxelRenderer.get_instance().update_field(label, orig_field)
            if "Points" in props and hasattr(obj, "Points"):
                obj.Points = list(props["Points"])
            if "Placement" in props:
                obj.Placement = props["Placement"]
            if props.get("OriginMode") is not None and hasattr(obj, "OriginMode"):
                obj.OriginMode = props["OriginMode"]
            obj.touch()
        except Exception as e:
            fld_logger.debug(f"PrimitiveCreatorBase.restore_original: {e}")
        view = self.view
        def _deferred_recompute(o=obj, v=view):
            try:
                if o.Document:
                    o.Document.recompute()
            except Exception as e:
                fld_logger.debug(f"PrimitiveCreatorBase.restore_original: {e}")
            if v:
                v.redraw()
        QtCore.QTimer.singleShot(0, _deferred_recompute)

    def _persist_edit(self, obj):
        """Write an edit session's field, points, origin mode and placement back to `obj`.

        The body of `finish()`'s edit branch; the recompute is deferred
        (`_commit_edit_deferred`) because Shape assignment is unsafe mid-event.
        """
        try:
            # The shape may have changed since the mode was chosen, so
            # settle the origin before Points and Placement are frozen.
            self._rebase_to_origin()
            field = self._get_edit_preview_field()
            points = self._get_final_points()
            if field is not None:
                obj.Proxy.SdfField = field
            if points is not None:
                if not hasattr(obj, "Points"):
                    obj.addProperty("App::PropertyVectorList", "Points", "Sdf", "Control Points")
                obj.Points = points
            self._write_origin_mode(obj)
            if self.working_plane:
                if hasattr(self.working_plane, "getGlobalPlacement"):
                    obj.Placement = self.working_plane.getGlobalPlacement()
                elif hasattr(self.working_plane, "Placement"):
                    obj.Placement = self.working_plane.Placement
                else:
                    obj.Placement = self.working_plane
            QtCore.QTimer.singleShot(0, lambda: self._commit_edit_deferred(obj))
        except Exception as e:
            fld_logger.error(f"finish(): failed to commit edit: {e}")

    def _commit_edit_deferred(self, obj):
        """Deferred recompute after editing - required for Shape assignment safety."""
        try:
            obj.touch()
            obj.Document.recompute([obj])
            self._on_committed(obj)
        except Exception as e:
            fld_logger.error(f"_commit_edit_deferred: {e}")

    def _finalize_object(self, name, terminate=True):
        """Commit the preview object as the final result, upgrading its mesh resolution."""
        self._rebase_to_origin()   # obj.Placement lands on the chosen origin
        field = self._get_final_field()
        points = self._get_final_points()
        # Captured here, not read inside the deferred commit: with Auto Repeat on,
        # reset_state() runs synchronously right after this and hands the drawing
        # plane back, so a later read would store points and a placement from two
        # different frames and the solid would jump.
        placement = self._get_placement()
        if field is None:
            if terminate: self.terminate()
            return
        
        # We don't set self._finished = True here if we want to repeat, 
        # because _finished prevents _do_terminate from cleaning up.
        # But we DO want to sever the preview object.
        QtCore.QTimer.singleShot(0, lambda: self._do_commit(name, field, points, placement))
        if terminate:
            self._finished = True
            self.terminate()

    def _do_commit(self, name, field, points, placement=None):
        obj = self._preview_obj
        if obj is None or not obj.Document:
            # fallback: create fresh
            obj = create_fld_object(name=name, shape_type="sdf")

        # We must be careful not to trigger recursive recomputes here if we are
        # already in a recompute loop.
        fld_logger.debug(f"Committing {name}: {len(points) if points else 0} points, placement={self.working_plane}")
        self._write_committed_object(obj, name, field, points, placement, "_do_commit")
        self._on_committed(obj)
        # Print accumulated timer summary now that the tool is accepted
        primitive_name = type(self).__name__.replace("Creator", "")
        mesh_timer.summary(f"{primitive_name} preview ({_PREVIEW_CELL_SIZE}mm)")
        self._preview_obj = None  # Severed; the object is now the user's

    def _commit_and_enter_edit(self, name):
        """Commit creation at full resolution, then immediately enter edit mode on the result."""
        self._rebase_to_origin()   # obj.Placement lands on the chosen origin
        field = self._get_final_field()
        points = self._get_final_points()
        placement = self._get_placement()   # captured with the points, not re-read later
        if field is None:
            self.terminate()
            return

        # Pre-enter edit state so handle_move doesn't clobber tool vars during async delay
        self._is_editing = True
        self.state = ToolState.IDLE
        self._locked_params.clear()   # typed dims are baked into the committed geometry

        QtCore.QTimer.singleShot(0, lambda: self._do_commit_and_edit(name, field, points, placement))

    def _do_commit_and_edit(self, name, field, points, placement=None):
        """Async: commit the object at full resolution then switch to edit mode on it."""
        if getattr(self, "_terminated", False):
            return

        obj = self._preview_obj
        if obj is None or not obj.Document:
            obj = create_fld_object(name=name, shape_type="sdf")

        primitive_name = type(self).__name__.replace("Creator", "")
        self._write_committed_object(obj, name, field, points, placement, "_do_commit_and_edit")
        mesh_timer.summary(f"{primitive_name} → edit mode")

        # Clear creation visuals before entering edit mode
        self._undraw_creation_handles()

        # Enter edit mode on the committed object.
        # Mark that this edit session began from creation so cancel() knows to delete it.
        self._came_from_creation = True
        self.edit_object(obj)
        FreeCADGui.updateGui()
        if self.view:
            self.view.redraw()

    def _write_committed_object(self, obj, name, field, points, placement, log_tag):
        """Stamp a committed primitive's properties, field and placement, then recompute.

        Shared by the finalize path (`_do_commit`) and the commit-and-edit path
        (`_do_commit_and_edit`); `log_tag` keeps their log lines distinguishable.
        """
        try:
            obj.Label = name
        except Exception as e:
            fld_logger.debug(f"{log_tag}: failed to set Label on object: {e}")

        if points is not None:
            try:
                if not hasattr(obj, "Points"):
                    obj.addProperty("App::PropertyVectorList", "Points", "Sdf", "Control Points")
                obj.Points = points
            except Exception as e:
                fld_logger.error(f"{log_tag}: failed to set Points on '{getattr(obj, 'Label', '?')}': {e}")

        sdf_type = self.get_sdf_type()
        if sdf_type:
            try:
                if not hasattr(obj, "SdfType"):
                    obj.addProperty("App::PropertyString", "SdfType", "Sdf", "SDF primitive type")
                obj.SdfType = sdf_type
            except Exception as e:
                fld_logger.debug(f"{log_tag}: failed to set SdfType on object: {e}")

        self._write_origin_mode(obj)

        source_curve = self._get_source_curve_link()
        if source_curve is not None:
            try:
                if not hasattr(obj, "SourceCurveLink"):
                    obj.addProperty("App::PropertyLink", "SourceCurveLink", "Sdf", "Source curve")
                obj.SourceCurveLink = source_curve
            except Exception as e:
                fld_logger.error(f"{log_tag}: failed to set SourceCurveLink: {e}")

        # Store source surface link for surface-based extrusions
        if hasattr(self, "_surface_obj") and self._surface_obj is not None:
            try:
                if not hasattr(obj, "SourceSurfaceLink"):
                    obj.addProperty("App::PropertyLink", "SourceSurfaceLink", "Sdf", "Source surface")
                obj.SourceSurfaceLink = self._surface_obj
            except Exception as e:
                fld_logger.error(f"{log_tag}: failed to set SourceSurfaceLink: {e}")

        obj.Proxy.SdfField = field

        # Set placement BEFORE calling edit_object so edit_object reads the correct
        # placement and reconstructs world-space handle positions from local obj.Points.
        # The frame the points were expressed in -- see _finalize_object and
        # _commit_and_enter_edit.
        if placement is None:
            placement = self._get_placement()
        if placement is not None:
            obj.Placement = placement

        obj.touch()
        obj.Document.recompute([obj])
        # create_fld_object() no longer sets this -- it needs a computed Shape,
        # and commit is the first point on this path that has one (IF-016).
        apply_shaded_display_mode(obj)
