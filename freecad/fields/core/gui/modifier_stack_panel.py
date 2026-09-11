# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Modifier Stack Dock Widget Panel.

Provides a Blender-like modifier stack UI showing the chain of modifiers on
the currently selected SDF object, with controls to toggle bypass (Enabled),
reorder (move up/down), remove/splice, and add new modifiers.
"""
import FreeCAD
import FreeCADGui
from PySide import QtWidgets, QtCore

from freecad.fields.core import fld_logger
from freecad.fields.core.objects.fld_modifier_stack import (
    get_chain, is_deform_cage, move_up, move_down, remove
)


class _StackSelectionObserver:
    def __init__(self, panel):
        self._panel = panel

    def addSelection(self, doc_name, obj_name, sub_name, pnt):
        self._panel._on_selection_event()

    def removeSelection(self, doc_name, obj_name, sub_name):
        self._panel._on_selection_event()

    def clearSelection(self, doc_name):
        self._panel._on_selection_event()


class ModifierStackPanel(QtWidgets.QDockWidget):
    """Dock widget showing the modifier stack for the selected object."""

    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            mw = FreeCADGui.getMainWindow() if hasattr(FreeCADGui, "getMainWindow") else None
            cls._instance = cls(mw)
            if mw is not None:
                mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, cls._instance)
        return cls._instance

    def __init__(self, parent=None):
        super().__init__("Modifier Stack", parent)
        self.setObjectName("Fld_ModifierStackPanel")
        self._updating = False
        self._sel_observer = None
        self._current_base = None
        self._current_chain = []

        self._init_ui()

    def _init_ui(self):
        container = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header / Add button bar
        btn_bar = QtWidgets.QHBoxLayout()
        self._add_btn = QtWidgets.QPushButton("+ Add Modifier", container)
        self._add_menu = QtWidgets.QMenu(self._add_btn)
        self._build_add_menu()
        self._add_btn.setMenu(self._add_menu)
        btn_bar.addWidget(self._add_btn)
        btn_bar.addStretch()

        self._refresh_btn = QtWidgets.QPushButton("Refresh", container)
        self._refresh_btn.setToolTip("Refresh modifier stack")
        self._refresh_btn.clicked.connect(self.refresh)
        btn_bar.addWidget(self._refresh_btn)

        layout.addLayout(btn_bar)

        # Scroll area for rows
        self._scroll = QtWidgets.QScrollArea(container)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.StyledPanel)

        self._list_widget = QtWidgets.QWidget()
        self._list_layout = QtWidgets.QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(2, 2, 2, 2)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch()

        self._scroll.setWidget(self._list_widget)
        layout.addWidget(self._scroll)

        self.setWidget(container)

    def _build_add_menu(self):
        self._add_menu.clear()
        modifiers = [
            ("Twist", "Twist", "Twist deformation around an axis"),
            ("Bend", "Bend", "Bend deformation around an axis"),
            ("Lattice", "Lattice", "Free-form 3D lattice deformation"),
            ("Array", "Array", "Linear or radial repetition"),
            ("Noise", "Noise (3D)", "Procedural 3D noise displacement"),
            ("DeformCage", "Deform Cage", "FFD deform cage modifier"),
        ]
        for mod_type, label, tooltip in modifiers:
            act = self._add_menu.addAction(label)
            act.setToolTip(tooltip)
            act.triggered.connect(lambda checked=False, t=mod_type: self._on_add_modifier(t))

    def showEvent(self, event):
        super().showEvent(event)
        if self._sel_observer is None and getattr(FreeCADGui, "Selection", None):
            try:
                self._sel_observer = _StackSelectionObserver(self)
                FreeCADGui.Selection.addObserver(self._sel_observer)
            except Exception as e:
                fld_logger.warn(f"ModifierStackPanel: failed to register Selection observer: {e}")
        self.refresh()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._sel_observer is not None and getattr(FreeCADGui, "Selection", None):
            try:
                FreeCADGui.Selection.removeObserver(self._sel_observer)
                self._sel_observer = None
            except Exception as e:
                fld_logger.warn(f"ModifierStackPanel: failed to unregister Selection observer: {e}")

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.refresh()

    def _on_selection_event(self):
        if self._updating:
            return
        QtCore.QTimer.singleShot(10, self.refresh)

    def refresh(self):
        # Takes no target on purpose. It used to accept `target_obj=None`, which
        # nothing ever passed, and PySide fills an optional slot parameter from
        # `clicked(bool)` -- so the Refresh button handed it `False`, and `False`
        # is not None, so the `obj is None` early-out below was skipped and
        # `get_chain(False)` returned `base=False` (measured in a live session
        # 2026-08-26). The panel always refreshes from the selection; keeping the
        # signature argument-free is what makes the bool unrepresentable.
        if self._updating:
            return

        # Clear existing rows
        while self._list_layout.count() > 0:
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        sel = FreeCADGui.Selection.getSelection() if getattr(FreeCADGui, "Selection", None) else []
        obj = sel[0] if sel else None

        if obj is None:
            self._current_base = None
            self._current_chain = []
            lbl = QtWidgets.QLabel("<i>No object selected</i>")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            self._list_layout.addWidget(lbl)
            self._list_layout.addStretch()
            self._add_btn.setEnabled(False)
            return

        base, chain = get_chain(obj)
        if base is None:
            self._current_base = None
            self._current_chain = []
            lbl = QtWidgets.QLabel("<i>Select an SDF object</i>")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            self._list_layout.addWidget(lbl)
            self._list_layout.addStretch()
            self._add_btn.setEnabled(False)
            return

        self._current_base = base
        self._current_chain = chain
        self._add_btn.setEnabled(True)

        # 1. Base row (read-only label)
        base_frame = QtWidgets.QFrame()
        base_frame.setFrameShape(QtWidgets.QFrame.Box)
        base_layout = QtWidgets.QHBoxLayout(base_frame)
        base_layout.setContentsMargins(6, 4, 6, 4)

        base_name = getattr(base, "Label", getattr(base, "Name", "Base Object"))
        base_lbl = QtWidgets.QLabel(f"<b>Base:</b> {base_name}")
        base_layout.addWidget(base_lbl)
        base_layout.addStretch()
        self._list_layout.addWidget(base_frame)

        # 2. Modifier rows (base -> tail)
        n = len(chain)
        for i, mod in enumerate(chain):
            row_widget = self._create_modifier_row(mod, index=i, total=n)
            self._list_layout.addWidget(row_widget)

        self._list_layout.addStretch()

    def _create_modifier_row(self, mod, index, total):
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        row_layout = QtWidgets.QHBoxLayout(frame)
        row_layout.setContentsMargins(4, 2, 4, 2)
        row_layout.setSpacing(4)

        # Enabled Checkbox
        chk = QtWidgets.QCheckBox(frame)
        chk.setChecked(bool(getattr(mod, "Enabled", True)))
        chk.setToolTip("Enable/Disable (bypass) modifier")
        chk.toggled.connect(lambda checked, m=mod: self._on_toggle_enabled(m, checked))
        row_layout.addWidget(chk)

        # Label (Clickable / selectable)
        proxy_cls = type(getattr(mod, "Proxy", None)).__name__
        type_name = proxy_cls.replace("Fld", "").replace("Proxy", "")
        name = getattr(mod, "Label", getattr(mod, "Name", "Modifier"))

        lbl_btn = QtWidgets.QPushButton(f"{name} [{type_name}]", frame)
        lbl_btn.setFlat(True)
        lbl_btn.setStyleSheet("text-align: left; font-weight: 500;")
        lbl_btn.clicked.connect(lambda checked=False, m=mod: self._on_select_object(m))
        row_layout.addWidget(lbl_btn, 1)

        # Each op rewrites the Source of more rows than the two it visibly swaps, and
        # modifier_stack refuses the whole op if a deform cage sits on any of them.
        # Grey the button rather than letting the click be silently declined.
        cage_here = self._row_is_cage(index)
        cage_above = self._row_is_cage(index - 1)
        cage_below = self._row_is_cage(index + 1)
        cage_below2 = self._row_is_cage(index + 2)

        # Move Up button (▲) -- rewires the row above and the row below
        btn_up = QtWidgets.QPushButton("▲", frame)
        btn_up.setFixedWidth(24)
        btn_up.setToolTip("Move up (toward base)")
        btn_up.setEnabled(index > 0 and not (cage_here or cage_above or cage_below))
        btn_up.clicked.connect(lambda checked=False, m=mod: self._on_move_up(m))
        row_layout.addWidget(btn_up)

        # Move Down button (▼) -- rewires the next row and the one after it
        btn_down = QtWidgets.QPushButton("▼", frame)
        btn_down.setFixedWidth(24)
        btn_down.setToolTip("Move down (toward tail)")
        btn_down.setEnabled(index < total - 1 and not (cage_here or cage_below or cage_below2))
        btn_down.clicked.connect(lambda checked=False, m=mod: self._on_move_down(m))
        row_layout.addWidget(btn_down)

        # Delete button (✕) -- rewires the row below onto this row's source
        btn_del = QtWidgets.QPushButton("✕", frame)
        btn_del.setFixedWidth(24)
        btn_del.setToolTip("Remove modifier from stack")
        btn_del.setEnabled(not (cage_here or cage_below))
        btn_del.clicked.connect(lambda checked=False, m=mod: self._on_remove(m))
        row_layout.addWidget(btn_del)

        return frame

    def _row_is_cage(self, index):
        """True when chain row `index` is a deform cage. Out-of-range rows are not."""
        if index < 0 or index >= len(self._current_chain):
            return False
        return is_deform_cage(self._current_chain[index])

    def _on_select_object(self, obj):
        if not getattr(FreeCADGui, "Selection", None):
            return
        self._updating = True
        try:
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(obj)
        finally:
            self._updating = False

    def _on_toggle_enabled(self, mod, checked):
        doc = getattr(mod, "Document", None) or FreeCAD.activeDocument()
        def _apply():
            try:
                mod.Enabled = checked
                if doc and hasattr(doc, "recompute"):
                    doc.recompute()
            except Exception as e:
                fld_logger.error(f"ModifierStackPanel: failed to toggle Enabled on {mod.Name}: {e}")
        QtCore.QTimer.singleShot(0, _apply)

    def _on_move_up(self, mod):
        move_up(mod)
        QtCore.QTimer.singleShot(50, self.refresh)

    def _on_move_down(self, mod):
        move_down(mod)
        QtCore.QTimer.singleShot(50, self.refresh)

    def _on_remove(self, mod):
        remove(mod)
        QtCore.QTimer.singleShot(50, self.refresh)

    def _on_add_modifier(self, mod_type):
        if self._current_chain:
            tail = self._current_chain[-1]
        elif self._current_base:
            tail = self._current_base
        else:
            fld_logger.error("ModifierStack: Please select an SDF object first.")
            return

        doc = getattr(tail, "Document", None) or FreeCAD.activeDocument()
        if not doc:
            fld_logger.error("ModifierStack: No active document.")
            return

        # 1. Hide source object
        if hasattr(tail, "ViewObject") and tail.ViewObject:
            tail.ViewObject.Visibility = False

        # 2. Call factory
        name = f"{tail.Name}_{mod_type}"
        try:
            if mod_type == "Twist":
                from freecad.fields.core.objects.fld_deform_objects import create_twist_modifier
                create_twist_modifier(name, tail)
            elif mod_type == "Bend":
                from freecad.fields.core.objects.fld_deform_objects import create_bend_modifier
                create_bend_modifier(name, tail)
            elif mod_type == "Lattice":
                from freecad.fields.core.objects.fld_deform_objects import create_lattice_modifier
                create_lattice_modifier(name, tail)
            elif mod_type == "Array":
                from freecad.fields.core.objects.fld_deform_objects import create_array_modifier
                create_array_modifier(name, tail)
            elif mod_type == "Noise":
                from freecad.fields.core.objects.fld_noise_object import create_noise_modifier
                create_noise_modifier(name, tail)
            elif mod_type == "DeformCage":
                from freecad.fields.core.objects.fld_deform_objects import create_deform_cage_modifier
                create_deform_cage_modifier(doc, tail, name=name)

            if hasattr(doc, "recompute"):
                doc.recompute()
        except Exception as e:
            fld_logger.error(f"ModifierStack: failed to add {mod_type} modifier: {e}")

        QtCore.QTimer.singleShot(50, self.refresh)
