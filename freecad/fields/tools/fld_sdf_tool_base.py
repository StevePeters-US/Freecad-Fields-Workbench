# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCADGui
from PySide import QtCore
from freecad.fields.tools.fld_base import FldBase
from freecad.fields.core import fld_logger


class DirectionGizmoMixin:
    """
    Mixin for SDF modifier tools that expose DirectionX/Y/Z on their target object
    and want a 3-ring rotation gizmo + direction arrow visual.

    Host class requirements:
        self._target_obj        — FreeCAD object with Source link and DirectionX/Y/Z
        self.points_root        — coin.SoAnnotation scene node
        self.projector          — ViewProjector (from FldBase)
        self._compute_handle_radius()   — from FldBase
        self._set_cursor() / _restore_cursor()  — from FldBase
        self._start_drag_timer()        — from DragTimerMixin
        self._commit_changes()          — defined by the concrete tool

    Hooks to override in subclasses:
        _on_direction_changed() — called after direction updates during gizmo drag.
                                  Default redraws the direction arrow only.
    """

    ARROW_COLOR = (0.62, 0.28, 0.95)   # purple

    # ── Initialise / draw ─────────────────────────────────────────────────────

    def _gizmo_pivot(self):
        """Return the world-space center point for the direction gizmo."""
        import FreeCAD
        obj = getattr(self, "_target_obj", None)
        if obj:
            source = getattr(obj, "Source", None)
            if source and hasattr(source, "Placement"):
                return FreeCAD.Vector(source.Placement.Base)
            if hasattr(obj, "Placement"):
                return FreeCAD.Vector(obj.Placement.Base)
        return FreeCAD.Vector(0, 0, 0)

    def _gizmo_origin_prop_names(self):
        """Return tuple of property names (X, Y, Z) to modify on translation drag, or None for Placement.Base."""
        return None

    def _init_gizmo(self):
        import FreeCAD
        from freecad.fields.core.input.fld_gizmo import FldTransformGizmo
        if getattr(self, "_gizmo", None):
            self._gizmo.undraw()
            self._gizmo = None
        obj = self._target_obj
        if not obj:
            return
        self._gizmo = FldTransformGizmo()
        source = getattr(obj, "Source", None)
        pivot = self._gizmo_pivot()
        axes = {"x": FreeCAD.Vector(1, 0, 0), "y": FreeCAD.Vector(0, 1, 0), "z": FreeCAD.Vector(0, 0, 1)}
        if source and hasattr(source, "Placement"):
            rot = source.Placement.Rotation
            axes = {k: rot.multVec(v) for k, v in axes.items()}
        length = 50.0
        if source and hasattr(source, "Proxy") and hasattr(source.Proxy, "get_sdf_field"):
            try:
                field = source.Proxy.get_sdf_field(source)
                if field:
                    bb_min, bb_max = field.bounding_box()
                    diag = (bb_max - bb_min).Length
                    if diag > 1.0:
                        length = diag * 0.4
            except Exception as e:
                from freecad.fields.core import fld_logger
                fld_logger.debug(f"DirectionGizmoMixin._init_gizmo failed: {e}")
        self._gizmo.draw(self.points_root, pivot, axes=axes, length=length, draw_translation=True)

    def _draw_direction_arrow(self):
        import FreeCAD
        from pivy import coin
        from freecad.fields.core.input.fld_gizmo import FldTransformGizmo
        if getattr(self, "_arrow_root", None):
            try:
                self.points_root.removeChild(self._arrow_root)
            except Exception as e:
                from freecad.fields.core import fld_logger
                fld_logger.debug(f"DirectionGizmoMixin._draw_direction_arrow failed to remove child: {e}")
        self._arrow_root = coin.SoSeparator()
        self.points_root.addChild(self._arrow_root)
        obj = self._target_obj
        if not obj:
            return
        origin = self._gizmo_pivot()
        dir_vec = FreeCAD.Vector(obj.DirectionX, obj.DirectionY, obj.DirectionZ)
        if dir_vec.Length < 1e-8:
            dir_vec = FreeCAD.Vector(0, 0, -1)
        else:
            dir_vec.normalize()
        color = self.ARROW_COLOR
        mat = coin.SoMaterial()
        mat.diffuseColor.setValue(*color)
        mat.specularColor.setValue(0.5, 0.5, 0.5)
        mat.shininess.setValue(0.5)
        self._arrow_root.addChild(mat)
        length = self._gizmo._length if getattr(self, "_gizmo", None) else 50.0
        shaft_r = length * 0.03
        cone_r  = length * 0.07
        shaft_h = length * 0.75
        cone_h  = length * 0.25
        sb_rot = FldTransformGizmo._axis_sb_rot(dir_vec)
        shaft_ctr = origin + dir_vec * (shaft_h * 0.5)
        xf_shaft = coin.SoTransform()
        xf_shaft.translation.setValue(shaft_ctr.x, shaft_ctr.y, shaft_ctr.z)
        xf_shaft.rotation.setValue(sb_rot)
        cyl = coin.SoCylinder()
        cyl.radius = shaft_r
        cyl.height = shaft_h
        shaft_sep = coin.SoSeparator()
        shaft_sep.addChild(xf_shaft)
        shaft_sep.addChild(cyl)
        self._arrow_root.addChild(shaft_sep)
        cone_ctr = origin + dir_vec * (shaft_h + cone_h * 0.5)
        xf_cone = coin.SoTransform()
        xf_cone.translation.setValue(cone_ctr.x, cone_ctr.y, cone_ctr.z)
        xf_cone.rotation.setValue(sb_rot)
        cone = coin.SoCone()
        cone.bottomRadius = cone_r
        cone.height = cone_h
        cone_sep = coin.SoSeparator()
        cone_sep.addChild(xf_cone)
        cone_sep.addChild(cone)
        self._arrow_root.addChild(cone_sep)

    # ── Input helpers ─────────────────────────────────────────────────────────

    def _gizmo_handle_move(self, ray_p, ray_d, tol) -> bool:
        """Set a pointing cursor if hovering a gizmo handle (ring or arrow). Returns True if consumed."""
        if not getattr(self, "_gizmo", None):
            return False
        axis = self._gizmo.hit_test(ray_p, ray_d, tol)
        if axis:
            from PySide.QtCore import Qt
            self._set_cursor(Qt.PointingHandCursor)
            return True
        return False

    def _gizmo_try_start_drag(self, ray_p, ray_d, event_dict, tol) -> bool:
        """Hit-test the gizmo (rings or translation arrows) and start a drag if hit. Returns True if consumed."""
        if not getattr(self, "_gizmo", None):
            return False
        axis = self._gizmo.hit_test(ray_p, ray_d, tol)
        if not axis:
            return False
        import FreeCAD
        import math
        self._dragging_idx = f"gizmo_{axis}"
        pivot = self._gizmo_pivot()
        self._edit_pivot = pivot
        if axis.startswith("rot_"):
            ax_key = axis[4:]  # "rot_x" → "x"
            ax_vec = self._gizmo._axes[ax_key]
            from freecad.fields.core.input.fld_gizmo import _perp_pair
            ref, tang = _perp_pair(ax_vec)
            self._rot_ring_ref = ref
            self._rot_ring_tang = tang
            pt = self.projector.get_mouse_world_pos(event_dict, ax_vec, pivot, place_on_geometry=False)
            if pt:
                v = pt - pivot
                self._edit_last_angle = math.atan2(v.dot(ref), v.dot(tang))
            else:
                self._edit_last_angle = 0.0
        elif axis.startswith("plane_"):
            plane_key = axis[6:]  # 'xy', 'xz', or 'yz'
            norm_axis = {'xy': 'z', 'xz': 'y', 'yz': 'x'}[plane_key]
            plane_norm = self._gizmo._axes[norm_axis]
            self._plane_drag_norm = FreeCAD.Vector(plane_norm)
            pt = self.projector.get_mouse_world_pos(event_dict, plane_norm, pivot, place_on_geometry=False)
            self._drag_constraint_base = pt if pt else pivot
        else:
            ax_vec = self._gizmo._axes[axis]
            from freecad.fields.core.input.input_manager import FldInputManager
            click_pt = FldInputManager.get_instance().get_axis_point(self.view, pivot, ax_vec, event_dict)
            self._drag_constraint_base = click_pt if click_pt else pivot
        self._start_drag_timer()
        return True

    def _gizmo_drag_tick(self) -> bool:
        """
        Handle one rotation or translation drag frame. Call from _drag_update.
        Returns True if the tick was for a gizmo drag (caller should return).
        """
        dragging = getattr(self, "_dragging_idx", None)
        if not dragging or not dragging.startswith("gizmo_"):
            return False
        import FreeCAD
        import math
        axis = dragging[len("gizmo_"):]
        gizmo = getattr(self, "_gizmo", None)
        if not gizmo:
            return True
        from freecad.fields.core.input.input_manager import FldInputManager
        mouse_pos = FldInputManager.get_instance()._last_qt_pos

        if axis.startswith("rot_"):
            axis_key = axis[4:]
            if axis_key not in gizmo._axes:
                return True
            ax_vec = gizmo._axes[axis_key]
            pivot = getattr(self, "_edit_pivot", None)
            ref   = getattr(self, "_rot_ring_ref", None)
            tang  = getattr(self, "_rot_ring_tang", None)
            if pivot is None or ref is None or tang is None:
                return True
            view = getattr(self, "view", None)
            if view is not None:
                ray_p, ray_d = FldInputManager.get_instance().get_ray(view, {"Position": mouse_pos})
                if ray_d is not None and abs(ray_d.dot(ax_vec)) < 0.17:
                    return True
            pt = self.projector.get_mouse_world_pos(
                {"Position": mouse_pos}, ax_vec, pivot, place_on_geometry=False)
            if not pt:
                return True
            v = pt - pivot
            angle = math.atan2(v.dot(ref), v.dot(tang))
            da = angle - self._edit_last_angle
            # atan2 wraps at +/-pi, so a ring drag that crosses the branch cut
            # yields a delta a full turn out. Where `rot` is applied as a rotation
            # a full turn is the identity and this never showed; ArrayTool is the
            # first consumer to accumulate it as a scalar (StepAngle), where an
            # unwrapped delta is a ~360 deg jump mid-drag.
            da = (da + math.pi) % (2.0 * math.pi) - math.pi
            da_deg = math.degrees(da)
            if abs(da_deg) < 1e-8:
                return True
            rot = FreeCAD.Rotation(ax_vec, da_deg)
            obj = self._target_obj
            if not obj:
                return True
            self._apply_gizmo_rotation(rot)
            self._edit_last_angle = angle
            self._on_direction_changed()
            if hasattr(self, "panel") and self.panel:
                self.panel.update_ui()
            self._commit_changes()
            return True
        elif axis.startswith("plane_"):
            plane_key = axis[6:]
            if plane_key not in ('xy', 'xz', 'yz'):
                return True
            norm_axis = {'xy': 'z', 'xz': 'y', 'yz': 'x'}[plane_key]
            plane_norm = getattr(self, "_plane_drag_norm", None) or gizmo._axes[norm_axis]
            base_pt = getattr(self, "_drag_constraint_base", None)
            if not base_pt:
                return True
            view = getattr(self, "view", None)
            if view is not None:
                ray_p, ray_d = FldInputManager.get_instance().get_ray(view, {"Position": mouse_pos})
                if ray_d is not None and abs(ray_d.dot(plane_norm)) < 0.08:
                    return True
            current_pt = self.projector.get_mouse_world_pos(
                {"Position": mouse_pos}, plane_norm, base_pt, place_on_geometry=False)
            if not current_pt:
                return True
            delta = current_pt - base_pt
            delta = delta - plane_norm * delta.dot(plane_norm)
            ax1_key, ax2_key = {'xy': ('x', 'y'), 'xz': ('x', 'z'), 'yz': ('y', 'z')}[plane_key]
            u1 = gizmo._axes[ax1_key]
            u2 = gizmo._axes[ax2_key]
            from freecad.fields.core.input import fld_snap
            from freecad.fields.core.fld_settings import (
                get_snap_grid_step, get_snap_during_direct_drag)
            if get_snap_during_direct_drag() and fld_snap.snap_active():
                step = get_snap_grid_step()
                d1 = fld_snap.snap_length(delta.dot(u1), step)
                d2 = fld_snap.snap_length(delta.dot(u2), step)
                delta = u1 * d1 + u2 * d2
            if delta.Length < 1e-8:
                return True
            obj = self._target_obj
            if not obj:
                return True
            prop_names = self._gizmo_origin_prop_names()
            if prop_names:
                px, py, pz = prop_names
                setattr(obj, px, getattr(obj, px, 0.0) + delta.x)
                setattr(obj, py, getattr(obj, py, 0.0) + delta.y)
                setattr(obj, pz, getattr(obj, pz, 0.0) + delta.z)
            else:
                source = getattr(obj, "Source", None)
                target = source if (source and hasattr(source, "Placement")) else obj
                if hasattr(target, "Placement"):
                    pl = FreeCAD.Placement(target.Placement)
                    pl.move(delta)
                    target.Placement = pl
            self._drag_constraint_base = current_pt
            pivot = self._gizmo_pivot()
            self._gizmo.update(pivot, axes=gizmo._axes)
            self._on_direction_changed()
            if hasattr(self, "panel") and self.panel:
                self.panel.update_ui()
            self._commit_changes()
            return True
        else:
            # Translation axis drag: 'x', 'y', or 'z'
            if axis not in gizmo._axes:
                return True
            ax_vec = gizmo._axes[axis]
            base_pt = getattr(self, "_drag_constraint_base", None)
            if not base_pt:
                return True
            current_pt = FldInputManager.get_instance().get_axis_point(
                self.view, base_pt, ax_vec, {"Position": mouse_pos})
            if not current_pt:
                return True
            delta = current_pt - base_pt
            from freecad.fields.core.input import fld_snap
            from freecad.fields.core.fld_settings import (
                get_snap_grid_step, get_snap_during_direct_drag)
            if get_snap_during_direct_drag() and fld_snap.snap_active():
                step = get_snap_grid_step()
                delta_len = delta.dot(ax_vec)
                delta = ax_vec * fld_snap.snap_length(delta_len, step)
            if delta.Length < 1e-8:
                return True
            obj = self._target_obj
            if not obj:
                return True
            prop_names = self._gizmo_origin_prop_names()
            if prop_names:
                px, py, pz = prop_names
                setattr(obj, px, getattr(obj, px, 0.0) + delta.x)
                setattr(obj, py, getattr(obj, py, 0.0) + delta.y)
                setattr(obj, pz, getattr(obj, pz, 0.0) + delta.z)
            else:
                source = getattr(obj, "Source", None)
                target = source if (source and hasattr(source, "Placement")) else obj
                if hasattr(target, "Placement"):
                    # `target.Placement.Base += delta` silently does nothing: the
                    # property getter returns a copy. Assign a whole Placement.
                    pl = FreeCAD.Placement(target.Placement)
                    pl.move(delta)
                    target.Placement = pl
            self._drag_constraint_base = current_pt
            pivot = self._gizmo_pivot()
            self._gizmo.update(pivot, axes=gizmo._axes)
            self._on_direction_changed()
            if hasattr(self, "panel") and self.panel:
                self.panel.update_ui()
            self._commit_changes()
            return True

    def _apply_gizmo_rotation(self, rot):
        """Apply one ring-drag rotation to the target's orientation.

        Base behaviour rotates the direction vector only. A ring whose axis is
        parallel to the current direction is therefore a no-op here — override in
        tools that also store a spin about the direction (see Noise2DTool).
        """
        import FreeCAD
        obj = self._target_obj
        direction = FreeCAD.Vector(obj.DirectionX, obj.DirectionY, obj.DirectionZ)
        if direction.Length < 1e-8:
            direction = FreeCAD.Vector(0, 0, -1)
        new_dir = rot.multVec(direction)
        new_dir.normalize()
        obj.DirectionX = new_dir.x
        obj.DirectionY = new_dir.y
        obj.DirectionZ = new_dir.z

    def _on_direction_changed(self):
        """Called after direction updates during gizmo drag. Override to update extra visuals."""
        self._draw_direction_arrow()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def _gizmo_teardown(self):
        if getattr(self, "_gizmo", None):
            self._gizmo.undraw()
            self._gizmo = None
        if getattr(self, "_arrow_root", None):
            try:
                self.points_root.removeChild(self._arrow_root)
            except Exception as e:
                from freecad.fields.core import fld_logger
                fld_logger.debug(f"DirectionGizmoMixin._gizmo_teardown failed to remove arrow: {e}")
            self._arrow_root = None


class FldSdfToolBase(FldBase):
    """
    Common base for all SDF editing tools (primitives, noise, etc.).
    Provides unified Q key group toggle so all tools behave identically.
    """

    def _get_sdf_obj(self):
        """Return the active SDF FreeCAD object. Override in subclasses."""
        return None

    def _get_default_group(self):
        """Return the default group used when no object exists yet (creation mode)."""
        return "Additive"

    def _set_default_group(self, group):
        """Store the default group for creation mode. Override in subclasses."""
        pass

    def _after_group_toggle(self, obj):
        """Called after an object's group is toggled. Override to update UI."""
        pass

    def edit_object_from_creation(self, mod_obj, source_obj):
        """Launch this tool for a newly-created modifier, setting creation context
        before edit_object() so cancel() can clean up correctly even on init failure."""
        self._came_from_creation = True
        self._source_obj = source_obj
        self.edit_object(mod_obj)

    def handle_keyboard(self, event_dict):
        if event_dict.get("Key") == QtCore.Qt.Key_Q:
            obj = self._get_sdf_obj()
            if obj and hasattr(obj, "Group"):
                obj.Group = "Subtractive" if getattr(obj, "Group", "Additive") == "Additive" else "Additive"
                obj.touch()
                if obj.Document:
                    from freecad.fields.core.sdf.sdf_boolean_compose import get_boolean_parents, update_boolean_parents
                    parents = get_boolean_parents(obj)
                    for p in parents:
                        p.touch()
                    obj.Document.recompute([obj] + parents)
                    
                    from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                    renderer = FldSceneVoxelRenderer.get_instance()
                    renderer.begin_update_batch()
                    try:
                        proxy = getattr(obj, "Proxy", None)
                        if proxy and hasattr(proxy, "SdfField") and proxy.SdfField:
                            label = f"{obj.Document.Name}.{obj.Name}"
                            renderer.update_field(label, proxy.SdfField)
                        update_boolean_parents(obj)
                    finally:
                        renderer.end_update_batch()
                self._after_group_toggle(obj)
            else:
                self._set_default_group(
                    "Subtractive" if self._get_default_group() == "Additive" else "Additive"
                )
            return True
        return super().handle_keyboard(event_dict)


class FldSdfModifierToolBase(FldSdfToolBase):
    """
    Shared edit/cancel/commit lifecycle for SDF modifier tools (Twist, Bend, Lattice,
    Noise3D, Noise2D, Heightmap). Each concrete tool only needs to:
      - set SNAPSHOT_PROPERTIES (and optionally SNAPSHOT_CUSTOM_PREFIX)
      - implement _make_panel()
      - override edit_object()/_after_group_toggle() to add tool-specific visuals,
        always calling super() first
      - override _update_render_field()/_on_before_redraw() for commit-time extras

    Recompute on every property edit is deferred via QTimer to avoid re-entrant
    recompute storms while a slider/gizmo drag is firing _commit_changes() rapidly.
    """

    # (property_name, default_value) pairs snapshotted in edit_object() for restore_original().
    SNAPSHOT_PROPERTIES = []
    # If set, also snapshot every property on the object whose name starts with this prefix
    # (used by the noise tools for user-defined "Custom_*" formula parameters).
    SNAPSHOT_CUSTOM_PREFIX = None

    def __init__(self):
        super().__init__()
        self._target_obj = None
        self._is_editing = False
        self._came_from_creation = False  # True when cmd created the object before opening the tool
        self._source_obj = None           # The original SDF object whose visibility was hidden

    def _make_panel(self):
        """Return a new task panel bound to this tool. Override in subclasses."""
        raise NotImplementedError("modifier tool must implement _make_panel()")

    def _post_init(self):
        super()._post_init()
        if not getattr(self, "_terminated", False) and self._is_editing:
            self.panel = self._make_panel()
            FreeCADGui.Control.showDialog(self.panel)
            self._dialog_open = True

    def _get_sdf_obj(self):
        return self._target_obj

    def _after_group_toggle(self, obj):
        if hasattr(self, "panel"):
            self.panel.group_btn.setText(obj.Group)

    def edit_object(self, obj):
        super().edit_object(obj)
        self._target_obj = obj
        self._is_editing = True
        self._original_props = {
            name: getattr(obj, name, default) for name, default in self.SNAPSHOT_PROPERTIES
        }
        if self.SNAPSHOT_CUSTOM_PREFIX:
            for prop in getattr(obj, "PropertiesList", []):
                if prop.startswith(self.SNAPSHOT_CUSTOM_PREFIX):
                    self._original_props[prop] = getattr(obj, prop)
        fld_logger.info(f"{type(self).__name__} activated for {obj.Label}")

    def restore_original(self):
        obj = self._target_obj
        if not obj or not hasattr(self, "_original_props"):
            return
        for k, v in self._original_props.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        self._commit_changes(force=True)

    def cancel(self):
        """If this tool's object was created by the launching command (rather than
        editing a pre-existing one), cancelling must undo the creation: restore the
        source object's visibility, unregister the live SDF preview, and remove the
        new object — instead of just reverting properties via restore_original()."""
        if getattr(self, "_update_pending", False):
            self._fire_pending_update()
        if not getattr(self, "_came_from_creation", False):
            super().cancel()
            return
        name = type(self).__name__
        self._came_from_creation = False
        src = getattr(self, "_source_obj", None)
        if src and hasattr(src, "ViewObject") and src.ViewObject:
            try:
                src.ViewObject.Visibility = True
            except Exception as e:
                fld_logger.debug(f"{name}.cancel: failed to restore source visibility: {e}")
        obj = self._target_obj
        if obj and obj.Document:
            try:
                from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                FldSceneVoxelRenderer.get_instance().unregister_field(
                    f"{obj.Document.Name}.{obj.Name}")
            except Exception as e:
                fld_logger.debug(f"{name}.cancel: failed to unregister sdf field: {e}")
            try:
                doc = obj.Document
                def _deferred_remove(d=doc, n=obj.Name):
                    d.removeObject(n)
                    d.recompute()
                QtCore.QTimer.singleShot(0, _deferred_remove)
            except Exception as e:
                fld_logger.debug(f"{name}.cancel: failed to schedule deferred object removal: {e}")
        self._target_obj = None
        self._is_editing = False
        self.terminate()

    def handle_click(self, event_dict):
        return True

    def _update_render_field(self, renderer, label, sdf_field):
        """Push the edited field to the live raymarch preview. Override to register
        extra GPU resources (e.g. a heightmap texture) before the base update."""
        renderer.update_field(label, sdf_field)

    def _on_before_redraw(self):
        """Hook called right before view.redraw() in _commit_changes(). Override to
        refresh tool-specific visuals (e.g. a direction arrow) that depend on the
        just-committed property values."""
        pass

    def _commit_changes(self, force=False):
        if force:
            if getattr(self, "_update_pending", False):
                self._update_pending = False
                self._pending_callback = None
            self._do_commit_changes()
        else:
            self._schedule_update(self._do_commit_changes)

    def _do_commit_changes(self):
        obj = self._target_obj
        if obj and hasattr(obj, "Proxy") and hasattr(obj.Proxy, "execute"):
            obj.touch()
            if obj.Document:
                from freecad.fields.core.sdf.sdf_boolean_compose import get_boolean_parents, update_boolean_parents
                parents = get_boolean_parents(obj)
                for p in parents:
                    p.touch()
                doc = obj.Document
                targets = [obj] + parents
                QtCore.QTimer.singleShot(0, lambda d=doc, t=targets: d.recompute(t))
                
                from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                renderer = FldSceneVoxelRenderer.get_instance()
                renderer.begin_update_batch()
                try:
                    proxy = obj.Proxy
                    # get_sdf_field, not proxy.SdfField. The recompute above is
                    # deferred to the next event-loop turn, and every modifier
                    # onChanged now invalidates by setting SdfField = None instead
                    # of rebuilding eagerly. Reading the raw attribute here would
                    # see None in the one case this path exists for -- a property
                    # just changed -- silently skip the renderer update, and leave
                    # the viewport on stale geometry for the whole drag.
                    field = (proxy.get_sdf_field(obj) if hasattr(proxy, "get_sdf_field")
                             else getattr(proxy, "SdfField", None))
                    if field:
                        label = f"{obj.Document.Name}.{obj.Name}"
                        self._update_render_field(renderer, label, field)
                    update_boolean_parents(obj)
                finally:
                    renderer.end_update_batch()
            self._on_before_redraw()
            if self.view:
                self.view.redraw()

    def finish(self):
        if getattr(self, "_update_pending", False):
            self._fire_pending_update()
        self._is_editing = False
        self.terminate()

    def update_preview(self):
        """No-op: modifier tools have no click-drag placement preview to update."""
        pass
