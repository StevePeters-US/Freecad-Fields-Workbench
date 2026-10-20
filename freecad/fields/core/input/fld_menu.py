# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCADGui
from PySide import QtCore, QtGui, QtWidgets
from freecad.fields.core import fld_logger
from freecad.fields.core.input.fld_menu_tree import MenuItem, SEPARATOR


def _run_command_action(command):
    """A menu-item callable that runs `command` via `FreeCADGui.runCommand`.

    Every `_build_*_menu`/`_build_*_submenu` method redefined this identical
    one-line closure locally as `cmd(c)` (CR-036); hoisted here so it lands once.
    """
    return lambda checked=False, command=command: FreeCADGui.runCommand(command)


def _command_item(label, command):
    """A leaf that runs `command`, greyed out when FreeCAD says it can't run now."""
    try:
        enabled = bool(FreeCADGui.Command.get(command).isActive())
    except Exception:
        enabled = True
    return MenuItem(label, action=_run_command_action(command), enabled=enabled)


_RENDER_GROUPS = ("Additive", "Subtractive")


def has_render_group(obj):
    """Whether `obj` carries Fields' Additive/Subtractive `Group` enumeration.

    FreeCAD's own groups (App::Part, Body, DocumentObjectGroup) also have a
    `Group` property, but it is a link list, so `hasattr` is not enough.
    """
    return getattr(obj, "Group", None) in _RENDER_GROUPS


def set_render_group(objs, mode):
    """Set Additive/Subtractive to `mode` on each object in `objs` that has one.

    The mode is applied along the object's whole modifier chain (`get_chain`), so a
    modifier never disagrees with its base. Objects without the enumeration are
    skipped. The tail's recompute is deferred to the next event-loop turn
    (`QTimer.singleShot(0, ...)`) and then pushes the field to the voxel renderer.

    Args:
        objs: Document objects, normally the current selection.
        mode: "Additive" or "Subtractive".

    Returns:
        True if at least one object was changed.
    """
    if mode not in _RENDER_GROUPS:
        raise ValueError(f"unknown render group: {mode!r}")
    from freecad.fields.core.objects.fld_modifier_stack import get_chain
    from freecad.fields.core.sdf.sdf_boolean_compose import get_boolean_parents, update_boolean_parents
    changed_any = False
    for obj in objs:
        if not has_render_group(obj):
            continue
        base, modifiers = get_chain(obj)
        all_chain = ([base] + modifiers) if base is not None else [obj]
        for o in all_chain:
            if has_render_group(o):
                o.Group = mode
        tail = modifiers[-1] if modifiers else obj
        if tail.Document:
            parents = get_boolean_parents(tail)
            for p in parents:
                if hasattr(p, "touch"):
                    p.touch()

            def _deferred_recompute(o=tail, pts=parents):
                try:
                    if o.Document:
                        o.Document.recompute([o] + pts)
                        from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                        renderer = FldSceneVoxelRenderer.get_instance()
                        renderer.begin_update_batch()
                        try:
                            proxy = getattr(o, "Proxy", None)
                            if proxy and hasattr(proxy, "SdfField") and proxy.SdfField:
                                label = f"{o.Document.Name}.{o.Name}"
                                renderer.update_field(label, proxy.SdfField)
                            update_boolean_parents(o)
                        finally:
                            renderer.end_update_batch()
                except Exception as ex:
                    fld_logger.debug(f"set_render_group: deferred recompute error: {ex}")
            QtCore.QTimer.singleShot(0, _deferred_recompute)
        changed_any = True
    return changed_any


class FldMenuManager:
    """Handles dynamic context menus and UI interactions."""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = FldMenuManager()
        return cls._instance

    def __init__(self):
        self._menu_open = False
        self._active_menu = None

    def is_menu_active(self):
        return self._menu_open

    def _build_dynamic_menu(self, menu, items):
        """Render a menu tree into `menu`. Items are MenuItem or SEPARATOR."""
        for item in items:
            if item is SEPARATOR:
                menu.addSeparator()
                continue
            if not isinstance(item, MenuItem):
                raise TypeError(f"menu entry must be MenuItem or SEPARATOR, got {item!r}")
            if item.is_submenu:
                if not item.children:
                    continue                      # an empty submenu is not shown
                sub = menu.addMenu(item.label)
                sub.setEnabled(item.enabled)
                if item.icon:
                    sub.setIcon(QtGui.QIcon(item.icon))
                self._build_dynamic_menu(sub, item.children)
                continue
            act = menu.addAction(item.label)
            act.setEnabled(item.enabled)
            if item.icon:
                act.setIcon(QtGui.QIcon(item.icon))
            if item.checked is not None:
                act.setCheckable(True)
                act.setChecked(item.checked)
            if item.shortcut:
                act.setShortcut(QtGui.QKeySequence(item.shortcut))
                act.setShortcutVisibleInContextMenu(True)
            act.triggered.connect(lambda checked=False, fn=item.action: fn())

    def _on_menu_hide(self):
        self._menu_open = False
        self._active_menu = None

    def trigger_dynamic_menu(self, items):
        if not items or self._menu_open:
            return False

        try:
            self._active_menu = QtWidgets.QMenu()
            self._build_dynamic_menu(self._active_menu, items)
            self._active_menu.aboutToHide.connect(self._on_menu_hide)
            self._menu_open = True
            QtCore.QTimer.singleShot(0, lambda: self._active_menu.exec_(QtGui.QCursor.pos()))
            return True
        except Exception as e:
            fld_logger.error(f"Error triggering dynamic menu: {e}")
            self._menu_open = False
            return False

    # ─── Public entry point ───────────────────────────────────────────────────

    def show_context_menu(self):
        """Shows the right-click menu. Content depends on current FreeCAD selection."""
        try:
            sel = FreeCADGui.Selection.getSelection()
            if sel:
                items = self._build_selection_menu(sel)
            else:
                items = self._build_idle_menu()
            self.trigger_dynamic_menu(items)
        except Exception as e:
            fld_logger.error(f"Error showing context menu: {e}")

    # ─── Idle menu (nothing selected) ────────────────────────────────────────

    def _build_idle_menu(self):
        """The Scene menu: nothing is selected, so the only thing to do is add."""
        return [
            MenuItem("Add", children=self._build_add_submenu([])),
            SEPARATOR,
            MenuItem("Settings", action=_run_command_action("Fields_Settings")),
        ]

    # ─── Selection menu ───────────────────────────────────────────────────────

    def _build_selection_menu(self, sel):
        obj = sel[0]
        label = getattr(obj, "Label", "Object")

        items = [
            MenuItem(f"Edit  {label}", action=_run_command_action("Fields_EditObject")),
        ]
        if len(sel) >= 2:
            items.append(MenuItem("Operations", children=self._build_operations_submenu()))

        # One entry per mode the selection isn't wholly in already.
        grouped = [o for o in sel if has_render_group(o)]
        for mode in _RENDER_GROUPS:
            if any(o.Group != mode for o in grouped):
                items.append(MenuItem(f"Make {mode}",
                                      action=lambda m=mode: set_render_group(grouped, m)))

        if len(sel) >= 1 and all(getattr(o, "ShapeType", None) in ("surface", "curve") for o in sel):
            items.append(MenuItem("Cage from Surfaces", action=_run_command_action("Fields_CageFromSurfaces")))

        cages = [o for o in sel if getattr(o, "ShapeType", None) == "sdf" and getattr(o, "SdfType", None) == "cage" and hasattr(o, "Members")]
        if len(cages) == 1:
            cage_obj = cages[0]
            others = [o for o in sel if o != cage_obj and getattr(o, "ShapeType", None) in ("surface", "curve")]
            if others:
                members_set = set(cage_obj.Members)
                to_add = [o for o in others if o not in members_set]
                to_remove = [o for o in others if o in members_set]
                from freecad.fields.commands.cmd_cage_from_surfaces import add_to_assembly, remove_from_assembly
                if to_add:
                    items.append(MenuItem("Add to Cage", action=lambda: add_to_assembly(cage_obj, to_add)))
                if to_remove:
                    items.append(MenuItem("Remove from Cage", action=lambda: remove_from_assembly(cage_obj, to_remove)))

        extrudes = [o for o in sel if hasattr(o, "ExtrudeDirection")]
        if extrudes:
            items.append(MenuItem("Extrude Direction",
                                  children=self._build_extrude_direction_submenu(extrudes)))

        props = self._build_properties_submenu(obj)
        if props:
            items.append(MenuItem("Properties", children=props))

        items += [
            SEPARATOR,
            MenuItem("Add", children=self._build_add_submenu(sel)),
            SEPARATOR,
            MenuItem("Settings", action=_run_command_action("Fields_Settings")),
        ]
        return items

    # ─── Shared submenu builders ──────────────────────────────────────────────

    def _build_add_submenu(self, sel):
        """Everything that adds to the document, in one entry so the top level stays short.

        New objects always; a modifier for selected SDF objects; builds from selected curves.
        """
        items = self._build_add_object_items()
        extra = []
        if any(getattr(o, "ShapeType", None) == "sdf" for o in sel):
            extra.append(MenuItem("Modifier", children=self._build_add_modifier_submenu()))
        if sel and all(getattr(o, "ShapeType", None) == "curve" for o in sel):
            extra.append(MenuItem("From Curves", children=self._build_from_curves_submenu()))
        return items + ([SEPARATOR] + extra if extra else [])

    def _build_add_object_items(self):
        """New geometry, independent of the selection."""
        return [
            _command_item("WorkPlane", "Fields_WorkPlane"),
            _command_item("Point", "Fields_CreatePoint"),
            _command_item("Curve", "Fields_CreateCurve"),
            MenuItem("Primitive", children=[
                _command_item("Box", "Fields_CreateBox"),
                _command_item("Sphere", "Fields_CreateSphere"),
                _command_item("Cylinder", "Fields_CreateCylinder"),
                _command_item("Torus", "Fields_CreateTorus"),
            ]),
        ]

    def _build_add_modifier_submenu(self):
        """Modifiers stacked on the selected SDF objects."""
        return [
            _command_item("Noise", "Fields_CreateNoiseModifier"),
            _command_item("Heightmap", "Fields_CreateHeightmapModifier"),
            SEPARATOR,
            _command_item("Twist", "Fields_Twist"),
            _command_item("Bend", "Fields_Bend"),
            _command_item("Lattice", "Fields_Lattice"),
            _command_item("Array", "Fields_Array"),
            _command_item("Panel", "Fields_Panel"),
            SEPARATOR,
            _command_item("Deform Cage", "Fields_DeformCageFromPrimitive"),
        ]

    def _build_from_curves_submenu(self):
        """Objects built from the selected curves."""
        return [
            _command_item("Extrude", "Fields_ExtrudeCurve"),
            _command_item("Pipe", "Fields_CurvePipe"),
            _command_item("Fill", "Fields_FillCurve"),
        ]

    def _build_operations_submenu(self):
        return [
            MenuItem("Fuse", action=_run_command_action("Fields_Add")),
            MenuItem("Cut", action=_run_command_action("Fields_Subtract")),
            MenuItem("Common", action=_run_command_action("Fields_Intersection")),
        ]

    # ─── Face-extrude direction submenu ──────────────────────────────────────

    def _build_extrude_direction_submenu(self, objs):
        """Checkable mode list for the selected face extrusions.

        Control Point Normal sweeps the face's own normal (a straight prism);
        Surface Normal follows each vertex's surface normal, which flares on a
        curved face; Defined Normal uses the object's Direction vector.
        """
        try:
            modes = list(objs[0].getEnumerationsOfProperty("ExtrudeDirection"))
        except Exception:
            modes = ["Surface Normal", "Control Point Normal", "Defined Normal"]

        current = getattr(objs[0], "ExtrudeDirection", None)

        def make_setter(mode):
            return lambda m=mode: self._set_extrude_direction(objs, m)
        return [MenuItem(m, action=make_setter(m), checked=(m == current)) for m in modes]

    def _set_extrude_direction(self, objs, mode):
        from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
        sr = FldSceneVoxelRenderer.get_instance()
        for obj in objs:
            try:
                obj.ExtrudeDirection = mode      # onChanged rebuilds the field
                field = getattr(getattr(obj, "Proxy", None), "SdfField", None)
                if field is not None:
                    sr.update_field(f"{obj.Document.Name}.{obj.Name}", field)
            except Exception as e:
                fld_logger.error(f"Could not set ExtrudeDirection on {obj.Name}: {e}")
        view = FreeCADGui.activeView()
        if view:
            view.redraw()

    # ─── Per-object properties submenu ───────────────────────────────────────

    def _build_properties_submenu(self, obj):
        shape_type = getattr(obj, "ShapeType", None)
        sdf_type = getattr(obj, "SdfType", None)

        if shape_type == "curve":
            return self._build_curve_properties(obj)
        elif shape_type == "sdf":
            return self._build_sdf_properties(obj, sdf_type)
        return []

    def _build_curve_properties(self, obj):
        closed = bool(getattr(obj, "Closed", False))
        state_label = "On" if closed else "Off"

        def toggle_closed():
            obj.Closed = not bool(getattr(obj, "Closed", False))
            obj.touch()
            if obj.Document:
                doc = obj.Document
                QtCore.QTimer.singleShot(0, doc.recompute)

        return [
            MenuItem(f"Closed: {state_label}", action=toggle_closed, checked=closed),
        ]

    def _build_sdf_properties(self, obj, sdf_type):
        def open_edit():
            FreeCADGui.runCommand("Fields_EditObject")

        if sdf_type == "box":
            return [MenuItem("Edit Shape (roundness, size)", action=open_edit)]
        elif sdf_type in ("sphere", "cylinder", "torus"):
            return [MenuItem("Edit Shape (radius)", action=open_edit)]
        else:
            return [MenuItem("Edit Object", action=open_edit)]
