# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""UI utilities and custom widgets for the Fields workbench."""

from PySide import QtCore, QtGui, QtWidgets
from freecad.fields.core import fld_logger


class _DynamicLimitSliderBase(QtWidgets.QWidget):
    """Shared body of `DynamicLimitSlider` (float) and `DynamicLimitIntSlider`
    (int) -- displays a spinbox on the left representing the lower limit, a
    slider in the middle representing the current value, and a spinbox on the
    right representing the upper limit. Changing the limit spinboxes updates
    the slider's range in real time and clamps the value if it falls outside.

    Subclasses set `_is_int` and declare their own `valueChanged`/
    `limitsChanged` Qt Signal -- a Signal's element type is fixed at class
    definition and can't be parameterized here, so that part alone stays
    per-subclass (CR-035). Everything else -- layout, both spinboxes, the
    QSlider, and every handler -- lives in this base.

    `_is_int` branches in exactly two places that are a real algorithmic
    difference, not just formatting: the float slider maps a fixed
    `0.._STEPS` virtual range proportionally into `[min, max]` (so a float
    range's granularity doesn't depend on its span), while the int slider's
    QSlider range IS `[min, max]` directly (so a value step is one QSlider
    position) -- see `_update_slider_position`/`_on_slider_moved`.
    `_on_slider_pressed`/`_on_slider_released` were confirmed byte-identical
    and land here unconditionally, per the CR-035 task's own note.
    """

    _STEPS = 1000
    _is_int = False

    def __init__(self, value, min_val, max_val, step, decimals=3, parent=None):
        # QtWidgets.QWidget may resolve to `object` itself under a headless
        # test stub that mocks PySide out entirely; object.__init__() takes no
        # args, so passing `parent` there would raise.
        if QtWidgets.QWidget is not object:
            super().__init__(parent)
        else:
            super().__init__()
        self._value = int(value) if self._is_int else value
        self._updating = False

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        spin_cls = QtWidgets.QSpinBox if self._is_int else QtWidgets.QDoubleSpinBox

        self._min_spin = spin_cls()
        self._min_spin.setRange(-999999, 999999)
        self._min_spin.setValue(int(min_val) if self._is_int else min_val)
        self._min_spin.setSingleStep(int(step) if self._is_int else step)
        if not self._is_int:
            self._min_spin.setDecimals(decimals)
        self._min_spin.setFixedWidth(72)
        self._min_spin.setToolTip("Lower limit")

        self._slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        if not self._is_int:
            self._slider.setRange(0, self._STEPS)

        self._max_spin = spin_cls()
        self._max_spin.setRange(-999999, 999999)
        self._max_spin.setValue(int(max_val) if self._is_int else max_val)
        self._max_spin.setSingleStep(int(step) if self._is_int else step)
        if not self._is_int:
            self._max_spin.setDecimals(decimals)
        self._max_spin.setFixedWidth(72)
        self._max_spin.setToolTip("Upper limit")

        lay.addWidget(self._min_spin)
        lay.addWidget(self._slider, 1)
        lay.addWidget(self._max_spin)

        # Initialize value and slider position
        self._slider.mouseDoubleClickEvent = self._on_slider_double_click
        self._update_slider_position()

        self._slider.valueChanged.connect(self._on_slider_moved)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._min_spin.valueChanged.connect(self._on_min_changed)
        self._max_spin.valueChanged.connect(self._on_max_changed)

    def _format_tooltip(self):
        return f"Value: {self._value}" if self._is_int else f"Value: {self._value:.3f}"

    def _update_slider_position(self):
        """Updates the slider's handle position based on self._value and current limits."""
        min_v = self._min_spin.value()
        max_v = self._max_spin.value()
        self._slider.blockSignals(True)
        if self._is_int:
            self._slider.setRange(min_v, max_v)
            self._slider.setValue(self._value)
        else:
            if max_v <= min_v:
                pos = 0
            else:
                pos = int(round((self._value - min_v) / (max_v - min_v) * self._STEPS))
                pos = max(0, min(self._STEPS, pos))
            self._slider.setValue(pos)
        self._slider.blockSignals(False)
        self._slider.setToolTip(self._format_tooltip())

    def _on_slider_moved(self, pos):
        """Callback when the QSlider is dragged."""
        if self._updating:
            return
        self._updating = True
        try:
            if self._is_int:
                self._value = pos
            else:
                min_v = self._min_spin.value()
                max_v = self._max_spin.value()
                self._value = min_v + (max_v - min_v) * pos / self._STEPS
            self._slider.setToolTip(self._format_tooltip())
            self.valueChanged.emit(self._value)
        except Exception as e:
            fld_logger.error(f"Error in {type(self).__name__}._on_slider_moved: {e}")
        finally:
            self._updating = False

    def _on_slider_pressed(self):
        try:
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer, _RS_INTERACTIVE
            FldSceneVoxelRenderer.get_instance()._render_state = _RS_INTERACTIVE
        except Exception as e:
            fld_logger.debug(f"Failed to set interactive render state: {e}")

    def _on_slider_released(self):
        try:
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer, _RS_QUALITY
            FldSceneVoxelRenderer.get_instance()._render_state = _RS_QUALITY
            import FreeCADGui
            view = FreeCADGui.activeView()
            if view:
                view.redraw()
        except Exception as e:
            fld_logger.debug(f"Failed to restore quality render state: {e}")

    def _on_min_changed(self, new_min):
        """Callback when the minimum limit spinbox is modified."""
        if self._updating:
            return
        self._updating = True
        try:
            max_v = self._max_spin.value()
            # Enforce min <= max
            if max_v < new_min:
                self._max_spin.blockSignals(True)
                self._max_spin.setValue(new_min)
                self._max_spin.blockSignals(False)
                max_v = new_min

            self._update_slider_position()
            self.limitsChanged.emit(new_min, max_v)
        except Exception as e:
            fld_logger.error(f"Error in {type(self).__name__}._on_min_changed: {e}")
        finally:
            self._updating = False

    def _on_max_changed(self, new_max):
        """Callback when the maximum limit spinbox is modified."""
        if self._updating:
            return
        self._updating = True
        try:
            min_v = self._min_spin.value()
            # Enforce min <= max
            if min_v > new_max:
                self._min_spin.blockSignals(True)
                self._min_spin.setValue(new_max)
                self._min_spin.blockSignals(False)
                min_v = new_max

            self._update_slider_position()
            self.limitsChanged.emit(min_v, new_max)
        except Exception as e:
            fld_logger.error(f"Error in {type(self).__name__}._on_max_changed: {e}")
        finally:
            self._updating = False

    def _on_slider_double_click(self, event):
        """Allows double-clicking the slider to manually input any value."""
        try:
            try:
                from PySide.QtWidgets import QInputDialog
            except ImportError:
                from PySide.QtGui import QInputDialog

            if self._is_int:
                val, ok = QInputDialog.getInt(
                    self, "Set Value", "Enter value (ignores limits):",
                    self._value, -999999, 999999, 1)
            else:
                val, ok = QInputDialog.getDouble(
                    self, "Set Value", "Enter value (ignores limits):",
                    self._value, -999999.0, 999999.0, 4)
            if ok:
                self.setValue(val)
                self.valueChanged.emit(self._value)
        except Exception as e:
            fld_logger.error(f"Error in {type(self).__name__}._on_slider_double_click: {e}")

    def value(self):
        """Returns the current value of the slider."""
        return self._value

    def setValue(self, v):
        """Sets the current value of the slider.

        This sets the value regardless of the current limits.
        """
        try:
            self._value = int(v) if self._is_int else v
            self._update_slider_position()
        except Exception as e:
            fld_logger.error(f"Error in {type(self).__name__}.setValue: {e}")

    def setLimits(self, min_v, max_v):
        """Sets the min and max limits of the slider."""
        try:
            self._updating = True
            self._min_spin.blockSignals(True)
            self._min_spin.setValue(int(min_v) if self._is_int else min_v)
            self._min_spin.blockSignals(False)

            self._max_spin.blockSignals(True)
            self._max_spin.setValue(int(max_v) if self._is_int else max_v)
            self._max_spin.blockSignals(False)
            self._updating = False

            self._update_slider_position()
        except Exception as e:
            fld_logger.error(f"Error in {type(self).__name__}.setLimits: {e}")

    def blockSignals(self, b):
        """Blocks or unblocks signals for this widget and all child widgets."""
        super().blockSignals(b)
        self._min_spin.blockSignals(b)
        self._max_spin.blockSignals(b)
        self._slider.blockSignals(b)


class DynamicLimitSlider(_DynamicLimitSliderBase):
    """A custom float slider with adjustable endpoints.

    This widget displays a spinbox on the left representing the lower limit,
    a slider in the middle representing the current value, and a spinbox on the
    right representing the upper limit. Changing the limit spinboxes updates
    the slider's range in real time and clamps the value if it falls outside.
    """

    valueChanged = QtCore.Signal(float)
    limitsChanged = QtCore.Signal(float, float)
    _is_int = False

    def __init__(self, value, min_val, max_val, step=0.1, decimals=3, parent=None):
        """Initializes the DynamicLimitSlider.

        Args:
            value (float): The initial value of the slider.
            min_val (float): The initial lower limit (left spinbox).
            max_val (float): The initial upper limit (right spinbox).
            step (float): The step size for spinboxes.
            decimals (int): Number of decimals for spinbox display.
            parent (QWidget, optional): Parent widget.
        """
        super().__init__(value, min_val, max_val, step, decimals=decimals, parent=parent)


class DynamicLimitIntSlider(_DynamicLimitSliderBase):
    """A custom integer slider with adjustable endpoints.

    This widget displays a spinbox on the left representing the lower limit,
    a slider in the middle representing the current value, and a spinbox on the
    right representing the upper limit. Changing the limit spinboxes updates
    the slider's range in real time and clamps the value if it falls outside.
    """

    valueChanged = QtCore.Signal(int)
    limitsChanged = QtCore.Signal(int, int)
    _is_int = True

    def __init__(self, value, min_val, max_val, step=1, parent=None):
        super().__init__(value, min_val, max_val, step, parent=parent)


class NumericLineEdit(QtWidgets.QLineEdit):
    """A plain (unitless) line edit whose value can be nudged with the mouse wheel."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.step = 0.1
        self._text = str(args[0]) if args else ""

    def setText(self, val):
        self._text = str(val)
        try:
            super().setText(str(val))
        except Exception:
            pass

    def text(self):
        try:
            t = super().text()
            if isinstance(t, str):
                return t
        except Exception:
            pass
        return getattr(self, "_text", "")

    def wheelEvent(self, event):
        try:
            dy = event.angleDelta().y()
        except AttributeError:
            dy = event.delta()
        if dy == 0:
            event.ignore()
            return
        try:
            val = float(self.text())
            val += self.step if dy > 0 else -self.step
            self.setText(f"{val:.3f}")
            self.editingFinished.emit()
        except Exception as e:
            fld_logger.debug(f"NumericLineEdit.wheelEvent: Failed to apply wheel step: {e}")
        event.accept()



class CompactVector3Widget(QtWidgets.QWidget):
    """A compact horizontal layout widget for entering 3D coordinates (X, Y, Z)."""
    valuesChanged = QtCore.Signal(float, float, float)

    def __init__(self, step=0.1, decimals=3, parent=None):
        super().__init__(parent)
        self.step = step
        self.decimals = decimals
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self.x_input = NumericLineEdit()
        self.x_input.step = step
        self.y_input = NumericLineEdit()
        self.y_input.step = step
        self.z_input = NumericLineEdit()
        self.z_input.step = step

        for lbl_text, inp in [("X:", self.x_input), ("Y:", self.y_input), ("Z:", self.z_input)]:
            lbl = QtWidgets.QLabel(lbl_text)
            lbl.setStyleSheet("font-weight: bold; color: palette(text);")
            lay.addWidget(lbl)
            lay.addWidget(inp, 1)
            inp.editingFinished.connect(self._on_editing_finished)

    def _on_editing_finished(self):
        try:
            x, y, z = self.values()
            self.valuesChanged.emit(x, y, z)
        except Exception as e:
            fld_logger.debug(f"CompactVector3Widget._on_editing_finished: {e}")

    def values(self):
        try:
            x = float(self.x_input.text() or 0.0)
        except ValueError:
            x = 0.0
        try:
            y = float(self.y_input.text() or 0.0)
        except ValueError:
            y = 0.0
        try:
            z = float(self.z_input.text() or 0.0)
        except ValueError:
            z = 0.0
        return x, y, z

    def setValues(self, x, y, z):
        self.x_input.blockSignals(True)
        self.y_input.blockSignals(True)
        self.z_input.blockSignals(True)
        self.x_input.setText(f"{float(x):.{self.decimals}f}")
        self.y_input.setText(f"{float(y):.{self.decimals}f}")
        self.z_input.setText(f"{float(z):.{self.decimals}f}")
        self.x_input.blockSignals(False)
        self.y_input.blockSignals(False)
        self.z_input.blockSignals(False)

    def blockSignals(self, b):
        super().blockSignals(b)
        self.x_input.blockSignals(b)
        self.y_input.blockSignals(b)
        self.z_input.blockSignals(b)


class CollapsibleSection(QtWidgets.QWidget):
    """A custom widget representing a collapsible section with a form layout."""

    def __init__(self, title="", parent=None):
        super().__init__(parent)

        # Main vertical layout
        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 8)
        self.main_layout.setSpacing(0)

        # Toggle button
        self.toggle_button = QtWidgets.QToolButton()
        # In QToolButton text, '&' is interpreted as an accelerator mnemonic. Escape it to display as '&'.
        display_title = title.replace("&", "&&") if "&&" not in title else title
        self.toggle_button.setText(display_title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(True)
        self.toggle_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(QtCore.Qt.DownArrow)
        self.toggle_button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)

        self.toggle_button.setStyleSheet("""
            QToolButton {
                background-color: rgba(128, 128, 128, 40);
                border: 1px solid palette(mid);
                border-radius: 4px;
                font-weight: bold;
                padding: 6px;
                text-align: left;
            }
            QToolButton:hover {
                background-color: palette(highlight);
                color: palette(highlighted-text);
                border-color: palette(highlight);
            }
            QToolButton:checked {
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
                background-color: rgba(128, 128, 128, 60);
            }
        """)

        # Content widget
        self.content_area = QtWidgets.QWidget()
        self.content_area.setObjectName("content_area")
        self.content_area.setStyleSheet("""
            QWidget#content_area {
                background-color: transparent;
                border: 1px solid palette(mid);
                border-top: none;
                border-bottom-left-radius: 4px;
                border-bottom-right-radius: 4px;
            }
        """)

        self.content_layout = QtWidgets.QFormLayout(self.content_area)
        self.content_layout.setContentsMargins(12, 10, 12, 10)
        self.content_layout.setSpacing(8)

        self.main_layout.addWidget(self.toggle_button)
        self.main_layout.addWidget(self.content_area)

        self.toggle_button.clicked.connect(self.toggle)

    def toggle(self, checked=None):
        if checked is None:
            checked = self.toggle_button.isChecked()
        if checked:
            self.toggle_button.setArrowType(QtCore.Qt.DownArrow)
            self.content_area.setVisible(True)
        else:
            self.toggle_button.setArrowType(QtCore.Qt.RightArrow)
            self.content_area.setVisible(False)

    def setExpanded(self, expanded):
        self.toggle_button.setChecked(expanded)
        self.toggle(expanded)

    def addRow(self, label, widget):
        self.content_layout.addRow(label, widget)
