# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""The matcap picker: a thumbnail grid that applies live, and material variants.

Clicking a thumbnail sets `Matcap` on every selected Fields object at once, so the
viewport is the preview. OK keeps it; Cancel, Esc and the window close button all put
every object back to the value it had when the dialog opened.

A material variant is a base colour, metallic and roughness, made or edited in
`MaterialEditor` (matcap_material_editor.py).

**Making, editing and removing a variant is not undoable.** Variants live in `doc.Meta`
(render/matcap_variants.py), which is outside the undo stack, so Cancel cannot take a
variant back; the buttons' tooltips say so.
"""
import os

import FreeCAD
from PySide import QtCore, QtGui, QtWidgets

from freecad.fields.core import fld_logger
from freecad.fields.core.render import matcap_variants
from freecad.fields.core.gui.matcap_material_editor import MaterialEditor
from freecad.fields.core.render.matcap_bake import MATERIALS, base_color_srgb
from freecad.fields.core.render.matcap_texture_cache import (
    LIGHTING_NAMES, MATERIAL_NAMES, DATA_NAMES, MATCAP_SIZE, matcap_dir)
from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip

translate = QtWidgets.QApplication.translate

ICON = 96
NONE = "None"

#: (group key, built-in names); the "This document" group is built from the variants.
BUILTIN_GROUPS = (
    ("Lighting", (NONE, *LIGHTING_NAMES)),
    ("Materials", MATERIAL_NAMES),
    ("Data views", DATA_NAMES),
)


def can_make_variant(value, variant_names):
    """A new material can start from a built-in material or lighting preset, or from
    a variant: not from a data layer or None."""
    return value in MATERIALS or value in variant_names


def can_edit_variant(value, variant_names):
    return value in variant_names


def can_remove_variant(value, variant_names):
    return value in variant_names


class MatcapDialog(QtWidgets.QDialog):
    def __init__(self, doc, objects, parent=None):
        super().__init__(parent)
        self.setWindowTitle(translate("MatcapDialog", "Matcap"))
        self.setObjectName("matcapDialog")
        self.setMinimumWidth(460)
        self.resize(480, 560)

        self._doc = doc
        self._objects = list(objects)
        self._original = {o.Name: o.ViewObject.Matcap for o in self._objects}
        self._finished = False
        self._lists = []
        self._selected = None

        # One transaction across the whole interaction. Cancel aborts it and puts the
        # values back by hand as well, because view-property edits are not guaranteed
        # to be recorded.
        doc.openTransaction("Change matcap")

        outer = QtWidgets.QVBoxLayout(self)
        self._hint = QtWidgets.QLabel()
        self._hint.setWordWrap(True)
        outer.addWidget(self._hint)

        scroll = self._scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("matcapScroll")
        scroll.setWidgetResizable(True)
        body = QtWidgets.QWidget()
        self._body = QtWidgets.QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._doc_group_widgets = []
        self._build_groups()

        row = QtWidgets.QHBoxLayout()
        self._make_btn = QtWidgets.QPushButton(translate("MatcapDialog", "New material…"))
        self._make_btn.setObjectName("matcapNewMaterial")
        self._make_btn.setToolTip(rich_tooltip("MatcapDialog", QT_TRANSLATE_NOOP("MatcapDialog",
            "Makes a new material for this document from the selected one: base colour, metallic and roughness.\n"
            "Data views and None can't be the starting point.\n"
            "Not undoable, and Cancel does not remove it.")))
        self._make_btn.clicked.connect(self._new_material)
        self._edit_btn = QtWidgets.QPushButton(translate("MatcapDialog", "Edit…"))
        self._edit_btn.setObjectName("matcapEditVariant")
        self._edit_btn.setToolTip(rich_tooltip("MatcapDialog", QT_TRANSLATE_NOOP("MatcapDialog",
            "Changes the selected variant's colour, metallic and roughness.\n"
            "Objects using it update. Not undoable.")))
        self._edit_btn.clicked.connect(self._edit_variant)
        self._remove_btn = QtWidgets.QPushButton(translate("MatcapDialog", "Remove variant"))
        self._remove_btn.setObjectName("matcapRemoveVariant")
        self._remove_btn.setToolTip(rich_tooltip("MatcapDialog", QT_TRANSLATE_NOOP("MatcapDialog",
            "Deletes the selected variant from this document.\n"
            "Objects using it go back to its base material.\n"
            "Not undoable.")))
        self._remove_btn.clicked.connect(self._remove_variant)
        row.addWidget(self._make_btn)
        row.addWidget(self._edit_btn)
        row.addWidget(self._remove_btn)
        row.addStretch()
        outer.addLayout(row)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._select_current()
        self._update_state()

    # ---- building -----------------------------------------------------------

    def _variant_names(self):
        return [v["name"] for v in matcap_variants.variants(self._doc)]

    def _make_list(self, key):
        lst = QtWidgets.QListWidget()
        lst.setObjectName("matcapGroup_" + key)
        lst.setViewMode(QtWidgets.QListView.IconMode)
        lst.setIconSize(QtCore.QSize(ICON, ICON))
        lst.setResizeMode(QtWidgets.QListView.Adjust)
        lst.setMovement(QtWidgets.QListView.Static)
        lst.setWrapping(True)
        lst.setSpacing(4)
        lst.setUniformItemSizes(True)
        lst.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        lst.itemClicked.connect(self._on_clicked)
        self._lists.append(lst)
        return lst

    def _add_group(self, layout, key, title, entries, icon_for):
        header = QtWidgets.QLabel("<b>%s</b>" % title)
        lst = self._make_list(key)
        for value in entries:
            item = QtWidgets.QListWidgetItem(icon_for(value), value)
            item.setData(QtCore.Qt.UserRole, value)
            item.setToolTip(value)
            lst.addItem(item)
        layout.addWidget(header)
        layout.addWidget(lst)
        return header, lst

    def _build_groups(self):
        titles = {"Lighting": translate("MatcapDialog", "Lighting"),
                  "Materials": translate("MatcapDialog", "Materials"),
                  "Data views": translate("MatcapDialog", "Data views")}
        for key, names in BUILTIN_GROUPS:
            self._add_group(self._body, key, titles[key], names, self._builtin_icon)
        self._doc_box = QtWidgets.QVBoxLayout()
        self._body.addLayout(self._doc_box)
        self._body.addStretch()
        self._rebuild_document_group()

    def _rebuild_document_group(self):
        for w in self._doc_group_widgets:
            if w in self._lists:
                self._lists.remove(w)
            self._doc_box.removeWidget(w)
            w.setParent(None)
            w.deleteLater()
        self._doc_group_widgets = []
        names = self._variant_names()
        if not names:
            return
        header, lst = self._add_group(
            self._doc_box, "This document", translate("MatcapDialog", "This document"), names, self._variant_icon)
        self._doc_group_widgets = [header, lst]
        self._fit_lists()

    def _fit_lists(self):
        """Size every group to the rows Qt actually laid out, so none is clipped and
        none scrolls inside the outer scroll area."""
        for lst in self._lists:
            lst.doItemsLayout()
            n = lst.count()
            if not n:
                continue
            bottom = max(lst.visualItemRect(lst.item(i)).bottom() for i in range(n))
            lst.setFixedHeight(bottom + 14)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_lists()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_lists()
        QtCore.QTimer.singleShot(0, self._fit_lists)    # again once the layout has settled

    # ---- icons --------------------------------------------------------------

    def _builtin_icon(self, name):
        pm = QtGui.QPixmap(ICON, ICON)
        if name == NONE:
            pm.fill(QtCore.Qt.transparent)
            p = QtGui.QPainter(pm)
            p.setPen(QtGui.QPen(QtGui.QColor(120, 120, 120), 2))
            p.drawEllipse(6, 6, ICON - 12, ICON - 12)
            p.drawLine(18, ICON - 18, ICON - 18, 18)
            p.end()
            return QtGui.QIcon(pm)
        pm = QtGui.QPixmap(os.path.join(matcap_dir(), name + ".png"))
        return QtGui.QIcon(pm.scaled(ICON, ICON, QtCore.Qt.IgnoreAspectRatio,
                                     QtCore.Qt.SmoothTransformation))

    def _variant_icon(self, name):
        """From the cache's baked array, so the thumbnail is the texture drawn."""
        try:
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
            cache = FldSceneVoxelRenderer.get_instance()._matcap_cache
            for vname, arr in cache.variant_images(self._doc.Name):
                if vname == name:
                    img = QtGui.QImage(arr.tobytes(), MATCAP_SIZE, MATCAP_SIZE,
                                       MATCAP_SIZE * 4, QtGui.QImage.Format_RGBA8888).copy()
                    return QtGui.QIcon(QtGui.QPixmap.fromImage(img).scaled(
                        ICON, ICON, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation))
        except Exception as e:
            fld_logger.warn(f"MatcapDialog: no thumbnail for variant {name!r}: {e}")
        return QtGui.QIcon()

    # ---- selection and live preview -----------------------------------------

    def _on_clicked(self, item):
        value = item.data(QtCore.Qt.UserRole)
        for lst in self._lists:
            if lst is not item.listWidget():
                lst.clearSelection()
        self._selected = value
        self._apply(value)
        self._update_state()

    def _apply(self, value):
        for o in self._objects:
            o.ViewObject.Matcap = value

    def _select_current(self):
        if not self._objects:
            return
        current = self._objects[0].ViewObject.Matcap
        for lst in self._lists:
            for i in range(lst.count()):
                if lst.item(i).data(QtCore.Qt.UserRole) == current:
                    lst.setCurrentItem(lst.item(i))
                    self._selected = current
                    return

    def _update_state(self):
        names = self._variant_names()
        self._make_btn.setEnabled(bool(self._selected) and can_make_variant(self._selected, names))
        self._edit_btn.setEnabled(bool(self._selected) and can_edit_variant(self._selected, names))
        self._remove_btn.setEnabled(bool(self._selected) and can_remove_variant(self._selected, names))
        self._hint.setText(
            "" if self._objects else
            translate("MatcapDialog", "Select a Fields object to apply a matcap."))

    # ---- variants -----------------------------------------------------------

    def _recipe_of(self, name):
        return next((v for v in matcap_variants.variants(self._doc) if v["name"] == name), None)

    def _start_values(self, value):
        """(base, colour, metallic, roughness) a new material starts from."""
        recipe = self._recipe_of(value)
        if recipe is not None:
            return recipe["base"], tuple(recipe["color"]), recipe["metallic"], recipe["roughness"]
        m = MATERIALS[value]
        return value, base_color_srgb(m), m.metallic, m.roughness

    def _new_material(self):
        if not can_make_variant(self._selected, self._variant_names()):
            return
        base, colour, metallic, roughness = self._start_values(self._selected)
        editor = MaterialEditor(base, colour, metallic, roughness,
                                matcap_variants.default_name(base, colour),
                                name_editable=True, parent=self)
        if editor.exec_() != QtWidgets.QDialog.Accepted:
            return
        colour, metallic, roughness, name = editor.values()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            name = matcap_variants.add_variant(self._doc, base, colour, name or None,
                                               metallic=metallic, roughness=roughness)
            matcap_variants.ensure_loaded(self._doc)
            self._refresh_library()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._select_value(name)
        self._apply(name)
        self._update_state()

    def _edit_variant(self):
        name = self._selected
        recipe = self._recipe_of(name) if can_edit_variant(name, self._variant_names()) else None
        if recipe is None:
            return
        editor = MaterialEditor(recipe["base"], tuple(recipe["color"]), recipe["metallic"],
                                recipe["roughness"], name, name_editable=False, parent=self)
        if editor.exec_() != QtWidgets.QDialog.Accepted:
            return
        colour, metallic, roughness, _name = editor.values()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            matcap_variants.update_variant(self._doc, name, colour, metallic, roughness)
            matcap_variants.ensure_loaded(self._doc)
            self._refresh_library()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._select_value(name)
        self._update_state()

    def _remove_variant(self):
        name = self._selected
        if not can_remove_variant(name, self._variant_names()):
            return
        answer = QtWidgets.QMessageBox.question(
            self, translate("MatcapDialog", "Remove variant"),
            translate("MatcapDialog", "Remove this variant? Objects using it go back to its base material."),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        base = matcap_variants.remove_variant(self._doc, name)
        from freecad.fields.core.objects.fld_view_provider import fields_objects
        for o in fields_objects(self._doc):
            if o.ViewObject.Matcap == name:
                o.ViewObject.Matcap = base
        # A value remembered for Cancel that no longer exists would restore nothing.
        for k, v in self._original.items():
            if v == name:
                self._original[k] = base
        matcap_variants.ensure_loaded(self._doc)
        self._refresh_library()
        self._select_value(base)
        self._update_state()

    def _refresh_library(self):
        """After a variant is added or removed: every object's choice list, the group,
        and the renderer."""
        from freecad.fields.core.objects.fld_view_provider import refresh_document_matcap_choices
        refresh_document_matcap_choices(self._doc)
        self._rebuild_document_group()
        try:
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
            FldSceneVoxelRenderer.get_instance().refresh_appearance()
        except Exception as e:
            fld_logger.warn(f"MatcapDialog: renderer refresh failed: {e}")

    def _select_value(self, value):
        for lst in self._lists:
            lst.clearSelection()
            for i in range(lst.count()):
                if lst.item(i).data(QtCore.Qt.UserRole) == value:
                    lst.setCurrentItem(lst.item(i))
        self._selected = value

    # ---- finishing ----------------------------------------------------------

    def accept(self):
        self._finish(commit=True)
        super().accept()

    def reject(self):
        """Cancel, Esc and the close button all arrive here."""
        self._finish(commit=False)
        super().reject()

    def _finish(self, commit):
        """Close the transaction, then re-derive the choice lists from Meta.

        The lists are view properties, so the writes made while the dialog was open sit
        in its transaction and an abort rolls them back, while Meta (outside the undo
        stack) keeps the variant. Re-deriving AFTER the transaction closes puts the
        lists back in agreement with Meta; on Cancel it comes before the restore loop,
        so each restored value is checked against the re-derived list.
        """
        if self._finished:
            return
        self._finished = True
        from freecad.fields.core.objects.fld_view_provider import refresh_document_matcap_choices
        if commit:
            self._doc.commitTransaction()
            refresh_document_matcap_choices(self._doc)
        else:
            self._doc.abortTransaction()
            refresh_document_matcap_choices(self._doc)
            for o in self._objects:
                try:
                    o.ViewObject.Matcap = self._original[o.Name]
                except Exception as e:
                    fld_logger.warn(f"MatcapDialog: could not restore {o.Name}: {e}")
        try:
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
            FldSceneVoxelRenderer.get_instance().refresh_appearance()
        except Exception as e:
            fld_logger.warn(f"MatcapDialog: renderer refresh failed: {e}")
