# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from PySide import QtCore
from freecad.fields.core.input.gizmo.models import GizmoFrame, Kind, handle_set, world_axes
from freecad.fields.core.input.gizmo.transform import delta_rotation
from freecad.fields.tools.fld_base import FldBase
from freecad.fields.tools.gizmo_driver import GizmoDriverMixin
from freecad.fields.core import fld_logger


class DirectionGizmoTarget:
    """The universal gizmo's `GizmoTarget` for a DirectionGizmoMixin tool (noise,
    heightmap, array; `universal_gizmo` reference/models.md). Arrows and plane squares
    move the tool's centre, rings turn its direction; no scale cubes. Every apply is a
    total from the snapshot; the tool's `_gizmo_*` hooks say what changes."""

    def __init__(self, tool):
        self.tool = tool
        self._snapshot = None       # the latest, for gizmo_commit

    def gizmo_frame(self):
        t = self.tool
        if getattr(t, "_target_obj", None) is None:
            return None
        o = FreeCAD.Vector(t._gizmo_pivot())
        return GizmoFrame(o, t._gizmo_axes(), FreeCAD.Vector(o))

    def gizmo_handles(self):
        return self.tool._gizmo_handles()

    def gizmo_snapshot(self):
        self._snapshot = self.tool._gizmo_snapshot()
        return self._snapshot

    def gizmo_apply(self, snap, delta):
        t = self.tool
        if delta.handle.kind is Kind.ROTATE:
            t._gizmo_apply_rotation(snap, delta)
        else:
            t._gizmo_apply_translation(snap, delta.translation)
        t._gizmo_changed()

    def gizmo_restore(self, snap):
        self.tool._gizmo_restore(snap)
        self.tool._gizmo_changed()

    def gizmo_commit(self):
        self.tool._gizmo_committed(self._snapshot)

    def gizmo_snap_points(self):
        return []


class DirectionGizmoMixin(GizmoDriverMixin):
    """
    Mixin for SDF modifier tools that expose DirectionX/Y/Z on their target object
    and want a 3-ring rotation gizmo + direction arrow visual. The gizmo is the universal
    gizmo controller (`GizmoDriverMixin` routes the mouse to it).

    Host class requirements:
        self._target_obj        — FreeCAD object with Source link and DirectionX/Y/Z
        self.points_root        — coin.SoAnnotation scene node
        self.view               — the 3D view
        self._commit_changes()  — defined by the concrete tool
        self.panel              — optional; `update_ui()` is called after each apply

    Hooks to override in subclasses: `_gizmo_handles`, `_gizmo_pivot`, `_gizmo_axes`,
    `_gizmo_origin_prop_names`, `_gizmo_translate_target`, `_gizmo_snapshot_props`,
    `_gizmo_apply_rotation`, `_gizmo_committed` and `_on_direction_changed` (called after
    direction updates; the default redraws the direction arrow only).
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

    def _gizmo_translate_target(self):
        """Object whose Placement.Base a translation drag moves, when
        `_gizmo_origin_prop_names()` returns None. Defaults to Source (the
        historical behaviour: dragging moves the wrapped shape), falling back
        to the tool's own object. Override when Center *is* the tool's own
        Placement (e.g. NoiseTool)."""
        import FreeCAD
        obj = getattr(self, "_target_obj", None)
        source = getattr(obj, "Source", None)
        return source if (source and hasattr(source, "Placement")) else obj

    def _gizmo_direction_vector(self):
        """World-space direction arrow drawn by `_draw_direction_arrow()`.
        Defaults to reading DirectionX/Y/Z off the target object; override when
        direction is derived from Placement instead (e.g. NoiseTool)."""
        import FreeCAD
        obj = getattr(self, "_target_obj", None)
        return FreeCAD.Vector(obj.DirectionX, obj.DirectionY, obj.DirectionZ)

    def _gizmo_axes(self):
        """The frame's axes: the Source's rotated X/Y/Z, else global (as the old gizmo)."""
        axes = world_axes()
        source = getattr(self._target_obj, "Source", None)
        if source is not None and hasattr(source, "Placement"):
            rot = source.Placement.Rotation
            axes = {k: rot.multVec(v) for k, v in axes.items()}
        return axes

    def _make_gizmo_target(self):
        return DirectionGizmoTarget(self) if getattr(self, "_target_obj", None) else None

    def _gizmo_handles(self):
        """The handles this tool has; only these are drawn (`handle_set`, INP-088).
        Default: the old gizmo's arrows, plane squares and rings; no scale. A tool that
        needs fewer or more overrides it."""
        return handle_set(move="xyz", planes=("xy", "xz", "yz"), rotate="xyz", free_move=True)

    def _gizmo_snapshot_props(self):
        """The properties a transform may change, which Esc restores: the origin
        properties, if any, and DirectionX/Y/Z."""
        return tuple(self._gizmo_origin_prop_names() or ()) + ("DirectionX", "DirectionY", "DirectionZ")

    def _gizmo_snapshot(self):
        """Those properties, plus the translate target's Placement when there are no
        origin properties (`_placement`, else None)."""
        obj = self._target_obj
        snap = {n: getattr(obj, n) for n in self._gizmo_snapshot_props()}
        target = None if self._gizmo_origin_prop_names() else self._gizmo_translate_target()
        snap["_placement"] = (FreeCAD.Placement(target.Placement)
                              if target is not None and hasattr(target, "Placement") else None)
        return snap

    def _gizmo_restore(self, snap):
        obj = self._target_obj
        for n in self._gizmo_snapshot_props():
            setattr(obj, n, snap[n])
        if snap["_placement"] is not None:
            self._gizmo_translate_target().Placement = FreeCAD.Placement(snap["_placement"])

    def _gizmo_apply_translation(self, snap, tr):
        """Arrow or plane square: the centre = the snapshot's + `tr` (world)."""
        names = self._gizmo_origin_prop_names()
        if names:
            obj = self._target_obj
            for n, c in zip(names, (tr.x, tr.y, tr.z)):
                setattr(obj, n, snap[n] + c)
        elif snap["_placement"] is not None:
            # A whole Placement: `.Placement.Base += tr` would change a copy.
            moved = FreeCAD.Placement(snap["_placement"])
            moved.move(tr)
            self._gizmo_translate_target().Placement = moved

    def _gizmo_apply_rotation(self, snap, delta):
        """Ring: the direction = the snapshot's, turned by the total. A ring about the
        direction itself changes nothing here; NoiseTool keeps that spin as its roll."""
        d = FreeCAD.Vector(snap["DirectionX"], snap["DirectionY"], snap["DirectionZ"])
        if d.Length < 1e-8:
            d = FreeCAD.Vector(0, 0, -1)
        new = delta_rotation(delta).multVec(d)
        new.normalize()
        obj = self._target_obj
        obj.DirectionX, obj.DirectionY, obj.DirectionZ = new.x, new.y, new.z

    def _gizmo_changed(self):
        """After an apply or a restore: the arrow, the panel, the deferred recompute."""
        self._on_direction_changed()
        panel = getattr(self, "panel", None)
        if panel:
            panel.update_ui()
        self._commit_changes()

    def _gizmo_committed(self, snap):
        """After a transform is kept. Nothing by default: every apply already committed."""

    def _on_before_redraw(self):
        """After every commit, the gizmo's or the panel's: the gizmo follows the target."""
        self._update_gizmo()
        super()._on_before_redraw()

    def _direction_arrow_length(self):
        """40% of the Source's bounding-box diagonal, else 50 mm (the old gizmo's length)."""
        source = getattr(self._target_obj, "Source", None)
        if source is not None and hasattr(source, "Proxy") and hasattr(source.Proxy, "get_sdf_field"):
            try:
                field = source.Proxy.get_sdf_field(source)
                if field:
                    bb_min, bb_max = field.bounding_box()
                    diag = (bb_max - bb_min).Length
                    if diag > 1.0:
                        return diag * 0.4
            except Exception as e:
                fld_logger.debug(f"DirectionGizmoMixin._direction_arrow_length failed: {e}")
        return 50.0

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
        dir_vec = self._gizmo_direction_vector()
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
        length = self._direction_arrow_length()
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

    def _on_direction_changed(self):
        """Called after direction updates during gizmo drag. Override to update extra visuals."""
        self._draw_direction_arrow()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def _gizmo_teardown(self):
        self._dispose_gizmo()
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

    #: False for a tool whose own mode already says add or remove (sculpt's Deposit/Carve):
    #: its menu then has no Additive / Subtractive item beside that mode.
    OFFERS_GROUP_TOGGLE = True

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

    def _toggle_group(self):
        """Toggle Additive/Subtractive for active SDF object or default creation group."""
        from freecad.fields.core.input.fld_menu import set_render_group
        obj = self._get_sdf_obj()
        if obj and hasattr(obj, "Group"):
            other = "Subtractive" if getattr(obj, "Group", "Additive") == "Additive" else "Additive"
            set_render_group([obj], other)
            self._after_group_toggle(obj)
        else:
            self._set_default_group(
                "Subtractive" if self._get_default_group() == "Additive" else "Additive"
            )

    def get_context_menu(self, event_dict=None):
        base_items = list(super().get_context_menu(event_dict)) if hasattr(super(), "get_context_menu") else []
        if not self.OFFERS_GROUP_TOGGLE:
            return base_items
        from freecad.fields.core.input.fld_menu_tree import MenuItem
        return [MenuItem("Additive / Subtractive", action=self._toggle_group)] + base_items



class FldSdfModifierToolBase(FldSdfToolBase):
    """
    Shared edit/cancel/commit lifecycle for SDF modifier tools (Twist, Bend, Lattice,
    Noise, Heightmap). Each concrete tool only needs to:
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
            gui_doc = FreeCADGui.getDocument(self.doc.Name)
            FreeCADGui.Control.showDialog(self.panel, gui_doc)
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
                from freecad.fields.core.objects.fld_modifier_stack import get_chain
                # recompute(objs) runs objs and what they depend on, never what depends
                # on them, so the modifiers stacked on obj -- the only visible row --
                # would keep the old shape until a full recompute (PNL-024).
                _, chain = get_chain(obj)
                downstream = chain[chain.index(obj) + 1:] if obj in chain else chain
                parents = get_boolean_parents(obj)
                for o in downstream + parents:
                    o.touch()
                doc = obj.Document
                targets = [obj] + downstream + parents
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
