# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/primitive_panel.py

Task panel UI for SDF primitive creation/editing: QuantityLineEdit (a
mouse-wheel-adjustable unit-aware line edit) and PrimitiveTaskPanel (the
transform/parameters form shown while a PrimitiveCreatorBase tool is active).
"""
import FreeCAD
from PySide import QtWidgets
from freecad.fields.core import fld_logger


class QuantityLineEdit(QtWidgets.QLineEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.step = 1.0

    def wheelEvent(self, event):
        if self.isReadOnly():
            event.accept()
            return
        # angleDelta is the PySide6/Qt6 API; fallback to legacy delta() for Qt5
        try:
            dy = event.angleDelta().y()
        except AttributeError:
            dy = event.delta()
        if dy == 0:
            event.ignore()
            return
        try:
            q = FreeCAD.Units.Quantity(self.text())
            val = q.Value
            val += self.step if dy > 0 else -self.step
            unit_str = (q.getUserPreferred()[2] if hasattr(q, "getUserPreferred") else None) or "mm"
            self.setText(f"{val:.2f} {unit_str}")
            self.editingFinished.emit()
        except (ValueError, AttributeError) as e:
            fld_logger.warning(f"QuantityLineEdit.wheelEvent: Failed to parse/update quantity: {e}")
        # Always accept to prevent parent scroll area from stealing the event
        event.accept()


class PrimitiveTaskPanel:
    """Task panel for SDF primitives (both creation and edit modes)."""
    def __init__(self, creator):
        self.creator = creator
        from PySide import QtWidgets, QtCore
        self.form = QtWidgets.QWidget()
        self.layout = QtWidgets.QVBoxLayout(self.form)
        self.layout.setContentsMargins(10, 10, 10, 10)

        self._build_ui()
        self.update_ui()

    def _build_ui(self):
        from PySide import QtWidgets, QtCore
        # Transform Group
        self.transform_group = QtWidgets.QGroupBox("Transform")
        t_layout = QtWidgets.QFormLayout(self.transform_group)
        self.pos_x = QuantityLineEdit()
        self.pos_y = QuantityLineEdit()
        self.pos_z = QuantityLineEdit()

        self.pos_x.editingFinished.connect(self._on_pos_changed)
        self.pos_y.editingFinished.connect(self._on_pos_changed)
        self.pos_z.editingFinished.connect(self._on_pos_changed)

        t_layout.addRow("X:", self.pos_x)
        t_layout.addRow("Y:", self.pos_y)
        t_layout.addRow("Z:", self.pos_z)
        self.layout.addWidget(self.transform_group)

        # Params Group
        self.params_group = QtWidgets.QGroupBox("Parameters")
        self.p_layout = QtWidgets.QFormLayout(self.params_group)
        self.layout.addWidget(self.params_group)

        self.param_inputs = {}

        self.layout.addStretch()

    @staticmethod
    def _set_value(line_edit, value):
        """Write a mm value without emitting signals, leaving a field being typed in alone."""
        if line_edit is None or line_edit.hasFocus():
            return
        line_edit.blockSignals(True)
        line_edit.setText(f"{value:.2f} mm")
        line_edit.blockSignals(False)

    def update_ui(self):
        """Refresh every field from the tool's live state (called on each preview frame)."""
        origin = self._creator_origin()
        self._set_value(self.pos_x, origin.x)
        self._set_value(self.pos_y, origin.y)
        self._set_value(self.pos_z, origin.z)

        # Creators anchored to a linked curve/surface show their position read-only.
        editable = getattr(self.creator, "SUPPORTS_ORIGIN_EDIT", True)
        for le in (self.pos_x, self.pos_y, self.pos_z):
            if le.isReadOnly() != (not editable):
                le.setReadOnly(not editable)

        self._update_params_ui()

    def _creator_origin(self):
        """World-space origin of the primitive being created/edited."""
        creator = self.creator
        if hasattr(creator, "get_origin"):
            return creator.get_origin()
        wp = getattr(creator, "working_plane", None)
        return FreeCAD.Vector(wp.Base) if wp else FreeCAD.Vector()

    def _update_params_ui(self):
        from PySide import QtWidgets, QtCore
        creator = self.creator
        if not hasattr(creator, "get_parameters") or not hasattr(creator, "set_parameters"):
            self.params_group.hide()
            return

        params = creator.get_parameters()
        if not params:
            # No geometry yet (e.g. before the first creation click) — the fields
            # would be blank, so hide the group rather than show empty boxes.
            self.params_group.hide()
            return

        self.params_group.show()

        # Add new fields if needed
        for key in params.keys():
            if key not in self.param_inputs:
                le = QuantityLineEdit()
                le.editingFinished.connect(lambda k=key: self._on_param_changed(k))
                self.p_layout.addRow(f"{key}:", le)
                self.param_inputs[key] = le

        # Update values
        for key, val in params.items():
            self._set_value(self.param_inputs.get(key), val)

    def _on_pos_changed(self):
        import FreeCAD
        creator = self.creator
        try:
            x = FreeCAD.Units.Quantity(self.pos_x.text()).Value
            y = FreeCAD.Units.Quantity(self.pos_y.text()).Value
            z = FreeCAD.Units.Quantity(self.pos_z.text()).Value
        except Exception as e:
            fld_logger.debug(f"Error parsing position formula: {e}")
            return
        try:
            creator.set_origin(FreeCAD.Vector(x, y, z))
        except Exception as e:
            fld_logger.debug(f"PrimitiveTaskPanel._on_pos_changed: {e}")

    def _on_param_changed(self, key):
        import FreeCAD
        creator = self.creator
        if not hasattr(creator, "get_parameters") or not hasattr(creator, "set_parameters"):
            return

        le = self.param_inputs.get(key)
        if le is None:
            return
        try:
            val = FreeCAD.Units.Quantity(le.text()).Value
        except Exception as e:
            fld_logger.debug(f"Error parsing parameter formula: {e}")
            return
        params = creator.get_parameters()
        params[key] = val
        try:
            # `changed` pins only the value the user typed, so during creation the
            # mouse keeps driving the remaining dimensions.
            creator.set_parameters(params, changed=key)
        except Exception as e:
            fld_logger.debug(f"PrimitiveTaskPanel._on_param_changed({key}): {e}")
        self.update_ui()

    def accept(self):
        self.creator._dialog_open = False
        if hasattr(self.creator, 'finish'):
            self.creator.finish()
        else:
            self.creator.terminate()
        return True

    def reject(self):
        self.creator._dialog_open = False
        self.creator.cancel()
        return True

    def clear_focus(self):
        if hasattr(self, "pos_x") and hasattr(self, "pos_y") and hasattr(self, "pos_z"):
            self.pos_x.clearFocus()
            self.pos_y.clearFocus()
            self.pos_z.clearFocus()

    def focus_field(self, axis):
        le = None
        if axis == 'x' and hasattr(self, "pos_x"):
            le = self.pos_x
        elif axis == 'y' and hasattr(self, "pos_y"):
            le = self.pos_y
        elif axis == 'z' and hasattr(self, "pos_z"):
            le = self.pos_z
        if le:
            le.setFocus()
            le.selectAll()
