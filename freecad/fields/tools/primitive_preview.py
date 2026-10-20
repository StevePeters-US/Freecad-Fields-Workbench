# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/primitive_preview.py

The live preview of the primitive creators and the typed-dimension locks that feed
it: the throttled field update, the boolean-parent renderer push, `update_ui`
(task-panel sync), and `set_parameters` / `_apply_param_locks`.

Stateless mixin. `update_preview` exists on `FldBase` and `FldSdfModifierToolBase`
too, so this mixin must precede `FldSdfToolBase` in the bases tuple or the primitive
override silently stops running (`test_primitive_creator_mro.py`). The subclass hook
stubs (`get_parameters`, `_apply_parameters`, `_update_ghost_visuals`) stay on
`PrimitiveCreatorBase`.
"""

from PySide import QtCore
from freecad.fields.core import fld_logger
from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
from freecad.fields.core.objects.fld_object import create_fld_object


class PrimitivePreviewMixin:
    """The live-preview half of PrimitiveCreatorBase: field push, boolean parents, panel sync, typed locks."""

    def update_preview(self):
        """Called on every mouse move by FldBase.handle_move. Updates the live mesh."""
        if getattr(self, "_terminated", False):
            return
        # In edit mode, use the edit-mode field builder which reads from self.points
        if getattr(self, "_is_editing", False):
            field = self._get_edit_preview_field()
        else:
            # _get_preview_field() rebuilds geometry from the creation state (mouse),
            # so any dimension the user typed has to be re-imposed on top of it.
            field = self._get_preview_field()
            if self._apply_param_locks():
                field = self._get_preview_field() or field
        # Panel mirrors the live geometry (position + dimensions) on every frame.
        self.update_ui()
        if field is None:
            return

        if self._preview_obj is None:
            if getattr(self, "_creating_obj", False):
                return
            self._creating_obj = True
            try:
                # Create the preview object for the first time
                # Use last part of class name without 'Creator' suffix
                name = type(self).__name__.replace("Creator", "")
                self._preview_obj = create_fld_object(name=name, shape_type="sdf")
                sdf_type = self.get_sdf_type()
                if sdf_type:
                    if not hasattr(self._preview_obj, "SdfType"):
                        self._preview_obj.addProperty("App::PropertyString", "SdfType", "Sdf", "SDF primitive type")
                    self._preview_obj.SdfType = sdf_type
                if hasattr(self._preview_obj, "Group"):
                    self._preview_obj.Group = getattr(self, "_create_group", "Additive")
            finally:
                self._creating_obj = False

        self._schedule_update(lambda: self._do_full_preview_update(field))

    def _do_full_preview_update(self, field=None):
        """Throttled update of both the mesh and the ghost visuals."""
        if field is None:
            field = (self._get_edit_preview_field()
                     if getattr(self, "_is_editing", False)
                     else self._get_preview_field())
        renderer = FldSceneVoxelRenderer.get_instance()
        renderer.begin_update_batch()
        try:
            self._apply_preview_field(field)
            self._update_ghost_visuals()
            if getattr(self, "_is_editing", False) and self._preview_obj and self._preview_obj.Document:
                obj = self._preview_obj
                # Always push recomposed boolean fields to renderer so the result updates live.
                self._update_boolean_parents_in_renderer()
                # Full FreeCAD doc recompute only on drag release (too slow to do every frame).
                if not getattr(self, "_is_dragging", False):
                    QtCore.QTimer.singleShot(0, lambda: self._recompute_boolean_parents(obj))
        finally:
            renderer.end_update_batch()
        if self.view:
            self.view.redraw()

    def _update_boolean_parents_in_renderer(self):
        """Push recomposed boolean fields directly to the renderer for real-time updates.
        Called every preview frame including during drags — avoids FreeCAD doc recompute.
        """
        if self._preview_obj is None or not self._preview_obj.Document:
            return
        try:
            from freecad.fields.core.sdf.sdf_boolean_compose import _recompose_boolean
            doc = self._preview_obj.Document
            renderer = FldSceneVoxelRenderer.get_instance()
            renderer.begin_update_batch()
            try:
                for obj in doc.Objects:
                    if getattr(obj, "ShapeType", None) != "sdf":
                        continue
                    inputs = getattr(obj, "BooleanInputs", None) or []
                    if self._preview_obj not in inputs:
                        continue
                    new_field = _recompose_boolean(obj)
                    if new_field is not None:
                        if hasattr(obj, "Proxy"):
                            obj.Proxy.SdfField = new_field
                        label = f"{doc.Name}.{obj.Name}"
                        renderer.update_field(label, new_field)
            finally:
                renderer.end_update_batch()
        except Exception as e:
            fld_logger.debug(f"Boolean parent renderer update error: {e}")

    def _recompute_boolean_parents(self, obj):
        """Deferred: recompute the primitive and its boolean parent dependents."""
        if getattr(self, "_terminated", False):
            return
        try:
            if obj and obj.Document:
                obj.touch()
                obj.Document.recompute()
        except Exception as e:
            fld_logger.debug(f"Boolean parent recompute error: {e}")

    def _apply_preview_field(self, field):
        if self._preview_obj is None or not self._preview_obj.Document:
            return
        try:
            proxy = self._preview_obj.Proxy
            if proxy is None:
                return
            proxy.SdfField = field

            # Direct GPU update - skip FreeCAD recompute cycle
            label = f"{self._preview_obj.Document.Name}.{self._preview_obj.Name}"
            FldSceneVoxelRenderer.get_instance().update_field(label, field)
        except Exception as e:
            fld_logger.debug(f"PrimitiveCreatorBase preview update error: {e}")
        finally:
            self._update_pending = False

    def update_ui(self):
        """Push the tool's current state into the task panel (no-op without one)."""
        panel = getattr(self, "panel", None)
        if panel is None:
            return
        try:
            panel.update_ui()
        except Exception as e:
            fld_logger.debug(f"{type(self).__name__}.update_ui: {e}")

    def set_parameters(self, params, changed=None):
        """Task panel entry point for typed dimensions.

        `changed` is the key the user actually edited; during creation only that
        key is pinned, so the mouse keeps driving the other dimensions.
        """
        if not self._apply_parameters(params):
            return False
        if not getattr(self, "_is_editing", False):
            if changed is None:
                self._locked_params.update(params)
            elif changed in params:
                self._locked_params[changed] = params[changed]
        if self.points:
            self._update_handle_positions(self.points)
        self._refresh_origin()
        self.update_preview()
        self.update_ui()
        if self.view:
            self.view.redraw()
        return True

    def _apply_param_locks(self):
        """Re-impose dimensions typed during creation. True if geometry changed."""
        if not self._locked_params or getattr(self, "_is_editing", False):
            return False
        params = self.get_parameters()
        if not params:
            return False
        merged = dict(params)
        dirty = False
        for key, val in self._locked_params.items():
            if key in merged and abs(merged[key] - val) > 1e-9:
                merged[key] = val
                dirty = True
        if not dirty:
            return False
        return bool(self._apply_parameters(merged))
