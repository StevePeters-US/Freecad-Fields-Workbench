# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Edits one material variant: base colour, metallic, roughness, with a baked preview.

A modal dialog that writes nothing itself: the caller reads `values()` after `exec_()`
returns `Accepted`. The preview is the real bake (`matcap_bake.render_material`), cheap
because the irradiance is cached per environment.
"""
from PySide import QtCore, QtGui, QtWidgets

from freecad.fields.core import fld_logger
from freecad.fields.core.render import matcap_bake
from freecad.fields.core.render.matcap_variants import default_name   # noqa: F401  one format, shared
from freecad.fields.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip

translate = QtWidgets.QApplication.translate

PREVIEW = 192


def preview_material(base, color_srgb, metallic, roughness):
    """The Material the editor's current values describe."""
    return matcap_bake.variant_of(matcap_bake.MATERIALS[base], color_srgb, metallic, roughness)


def _unit_row(value, tooltip):
    """(layout, slider, spinbox): a 0..1 value as a slider beside a spinbox, kept in
    sync both ways. Callers connect to the spinbox's `valueChanged`."""
    slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    slider.setRange(0, 100)
    spin = QtWidgets.QDoubleSpinBox()
    spin.setRange(0.0, 1.0)
    spin.setSingleStep(0.01)
    spin.setDecimals(2)
    slider.setValue(round(value * 100))
    spin.setValue(value)
    for w in (slider, spin):
        w.setToolTip(tooltip)

    def from_slider(v):
        spin.setValue(v / 100.0)         # fires spin.valueChanged, which the caller watches

    def from_spin(v):
        slider.blockSignals(True)
        slider.setValue(round(v * 100))
        slider.blockSignals(False)

    slider.valueChanged.connect(from_slider)
    spin.valueChanged.connect(from_spin)
    row = QtWidgets.QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(slider, 1)
    row.addWidget(spin)
    return row, slider, spin


class MaterialEditor(QtWidgets.QDialog):
    def __init__(self, base, color_srgb, metallic, roughness, name, name_editable=True,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(translate("MatcapMaterialEditor", "Material"))
        self.setObjectName("matcapMaterialEditor")
        self._base = base
        self._color = tuple(float(c) for c in color_srgb)
        self._name_typed = False

        outer = QtWidgets.QVBoxLayout(self)
        self._preview = QtWidgets.QLabel()
        self._preview.setObjectName("matcapEditorPreview")
        self._preview.setFixedSize(PREVIEW, PREVIEW)
        self._preview.setAlignment(QtCore.Qt.AlignCenter)
        outer.addWidget(self._preview, 0, QtCore.Qt.AlignHCenter)

        form = QtWidgets.QFormLayout()
        self._name = QtWidgets.QLineEdit(name)
        self._name.setObjectName("matcapEditorName")
        self._name.setEnabled(name_editable)
        self._name.setToolTip(rich_tooltip("MatcapMaterialEditor", QT_TRANSLATE_NOOP(
            "MatcapMaterialEditor", "The name this material has in the matcap list.\n"
            "It can't be changed once the material is made.")))
        self._name.textEdited.connect(self._on_name_typed)
        form.addRow(translate("MatcapMaterialEditor", "Name:"), self._name)

        self._color_btn = QtWidgets.QPushButton()
        self._color_btn.setObjectName("matcapEditorColor")
        self._color_btn.setToolTip(rich_tooltip("MatcapMaterialEditor", QT_TRANSLATE_NOOP(
            "MatcapMaterialEditor", "The material's base colour.\n"
            "A plastic's diffuse colour, or the colour a metal reflects.")))
        self._color_btn.clicked.connect(self._pick_color)
        form.addRow(translate("MatcapMaterialEditor", "Base colour:"), self._color_btn)

        tip = rich_tooltip("MatcapMaterialEditor", QT_TRANSLATE_NOOP(
            "MatcapMaterialEditor",
            "0: plastic-like, white highlights over the colour.\n"
            "1: metal, the colour is in the reflections and there is no diffuse colour."))
        row, self._metal_slider, self._metal_spin = _unit_row(float(metallic), tip)
        form.addRow(translate("MatcapMaterialEditor", "Metallic:"), row)
        tip = rich_tooltip("MatcapMaterialEditor", QT_TRANSLATE_NOOP(
            "MatcapMaterialEditor",
            "0: sharp, mirror-like reflections.\n1: a broad, soft sheen."))
        row, self._rough_slider, self._rough_spin = _unit_row(float(roughness), tip)
        form.addRow(translate("MatcapMaterialEditor", "Roughness:"), row)
        outer.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        # One single-shot timer coalesces a burst of changes into one bake.
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._rebake)
        self._metal_spin.valueChanged.connect(self._schedule)
        self._rough_spin.valueChanged.connect(self._schedule)

        self._show_color()
        self._rebake()

    # ---- values -------------------------------------------------------------

    def values(self):
        """(color_srgb 3-tuple, metallic, roughness, name) as the user left them."""
        return (self._color, self._metal_spin.value(), self._rough_spin.value(),
                self._name.text())

    # ---- behaviour ----------------------------------------------------------

    def _on_name_typed(self, _text):
        self._name_typed = True

    def _pick_color(self):
        c = self._color
        chosen = QtWidgets.QColorDialog.getColor(
            QtGui.QColor.fromRgbF(*c), self, translate("MatcapMaterialEditor", "Base colour"))
        if not chosen.isValid():
            return
        self._set_color((chosen.redF(), chosen.greenF(), chosen.blueF()))

    def _set_color(self, color):
        self._color = tuple(float(c) for c in color)
        self._show_color()
        if not self._name_typed and self._name.isEnabled():
            self._name.setText(default_name(self._base, self._color))     # setText: no textEdited
        self._schedule()

    def _show_color(self):
        r, g, b = (round(c * 255) for c in self._color)
        self._color_btn.setStyleSheet(f"background-color: rgb({r},{g},{b}); min-height: 20px;")

    def _schedule(self, *_):
        self._timer.start()

    def _rebake(self):
        _color, metallic, roughness, _name = self.values()
        try:
            rgba = matcap_bake.render_material(
                preview_material(self._base, self._color, metallic, roughness))
            img = QtGui.QImage(rgba.tobytes(), rgba.shape[1], rgba.shape[0],
                               rgba.shape[1] * 4, QtGui.QImage.Format_RGBA8888).copy()
        except Exception as e:
            fld_logger.warn(f"MaterialEditor: preview bake failed: {e}")
            return
        self._preview.setPixmap(QtGui.QPixmap.fromImage(img).scaled(
            PREVIEW, PREVIEW, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation))
