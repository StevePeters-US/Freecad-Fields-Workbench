# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCADGui
from PySide import QtCore, QtGui, QtWidgets
from freecad.fields.core import fld_logger


def _run_command_action(command):
    """A menu-item callable that runs `command` via `FreeCADGui.runCommand`.

    Every `_build_*_menu`/`_build_*_submenu` method redefined this identical
    one-line closure locally as `cmd(c)` (CR-036); hoisted here so it lands once.
    """
    return lambda checked=False, command=command: FreeCADGui.runCommand(command)


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
        self._ignore_hotkeys = False

    def is_menu_active(self):
        return self._menu_open

    def _build_dynamic_menu(self, menu, items):
        for item in items:
            if item == "-" or item is None:
                menu.addSeparator()
            elif isinstance(item, tuple):
                if len(item) == 2:
                    name, action = item
                    if isinstance(action, list):
                        submenu = menu.addMenu(name)
                        self._build_dynamic_menu(submenu, action)
                    else:
                        menu.addAction(name, action)
                elif len(item) == 3:
                    name, action, is_checked = item
                    act = menu.addAction(name)
                    act.setCheckable(True)
                    act.setChecked(is_checked)
                    act.toggled.connect(action)

    def _on_menu_hide(self):
        self._menu_open = False
        self._active_menu = None
        self._ignore_hotkeys = True
        QtCore.QTimer.singleShot(150, lambda: setattr(self, '_ignore_hotkeys', False))

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
        return [
            ("WorkPlane",  _run_command_action("Fields_WorkPlane")),
            "-",
            ("Primitives", self._build_primitives_submenu()),
            ("NURBS",      self._build_nurbs_submenu()),
            ("SDF Ops",    self._build_sdf_ops_submenu()),
            "-",
            ("Settings",   _run_command_action("Fields_Settings")),
        ]

    # ─── Selection menu ───────────────────────────────────────────────────────

    def _build_selection_menu(self, sel):
        obj = sel[0]
        label = getattr(obj, "Label", "Object")

        items = [
            (f"Edit  {label}",      _run_command_action("Fields_EditObject")),
            (f"Translate  {label}", _run_command_action("Fields_Translate")),
            ("Operations",          self._build_operations_submenu()),
        ]

        if len(sel) >= 1 and all(getattr(o, "ShapeType", None) in ("surface", "curve") for o in sel):
            items.append(("Cage from Surfaces", _run_command_action("Fields_CageFromSurfaces")))

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
                    items.append(("Add to Cage", lambda checked=False: add_to_assembly(cage_obj, to_add)))
                if to_remove:
                    items.append(("Remove from Cage", lambda checked=False: remove_from_assembly(cage_obj, to_remove)))

        extrudes = [o for o in sel if hasattr(o, "ExtrudeDirection")]
        if extrudes:
            items.append(("Extrude Direction", self._build_extrude_direction_submenu(extrudes)))

        props = self._build_properties_submenu(obj)
        if props:
            items.append(("Properties", props))

        items += [
            "-",
            ("WorkPlane", _run_command_action("Fields_WorkPlane")),
            ("Primitives", self._build_primitives_submenu()),
            "-",
            ("Settings",  _run_command_action("Fields_Settings")),
        ]
        return items

    # ─── Shared submenu builders ──────────────────────────────────────────────

    def _build_primitives_submenu(self):
        return [
            ("Box",      _run_command_action("Fields_CreateBox")),
            ("Sphere",   _run_command_action("Fields_CreateSphere")),
            ("Cylinder", _run_command_action("Fields_CreateCylinder")),
            ("Torus",    _run_command_action("Fields_CreateTorus")),
            ("Prism",    _run_command_action("Fields_ExtrudeCurve")),
            ("Convert to Deform Cage", _run_command_action("Fields_DeformCageFromPrimitive")),
        ]

    def _build_nurbs_submenu(self):
        return [
            ("Point",      _run_command_action("Fields_CreatePoint")),
            ("Curve",      _run_command_action("Fields_CreateCurve")),
            ("Fill Curve", _run_command_action("Fields_FillCurve")),
        ]

    def _build_sdf_ops_submenu(self):
        return [
            ("Extrude",   _run_command_action("Fields_ExtrudeCurve")),
            ("Pipe",      _run_command_action("Fields_CurvePipe")),
            ("Noise",     _run_command_action("Fields_CreateNoiseModifier")),
            ("Heightmap", _run_command_action("Fields_CreateHeightmapModifier")),
        ]

    def _build_operations_submenu(self):
        return [
            ("Fuse",   _run_command_action("Fields_Add")),
            ("Cut",    _run_command_action("Fields_Subtract")),
            ("Common", _run_command_action("Fields_Intersection")),
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
            def setter(checked=False, m=mode):
                if not checked:
                    return          # the toggled(False) half of switching modes
                self._set_extrude_direction(objs, m)
            return setter

        return [(m, make_setter(m), m == current) for m in modes]

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

        def toggle_closed(checked=False):
            obj.Closed = not bool(getattr(obj, "Closed", False))
            obj.touch()
            if obj.Document:
                doc = obj.Document
                QtCore.QTimer.singleShot(0, doc.recompute)

        return [
            (f"Closed: {state_label}", toggle_closed, closed),
        ]

    def _build_sdf_properties(self, obj, sdf_type):
        def open_edit(checked=False):
            FreeCADGui.runCommand("Fields_EditObject")

        if sdf_type == "box":
            return [("Edit Shape (roundness, size)", open_edit)]
        elif sdf_type in ("sphere", "cylinder", "torus"):
            return [("Edit Shape (radius)", open_edit)]
        else:
            return [("Edit Object", open_edit)]
