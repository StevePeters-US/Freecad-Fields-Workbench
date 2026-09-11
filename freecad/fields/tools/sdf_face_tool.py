# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from PySide import QtWidgets, QtCore
from freecad.fields.tools.fld_base import FldBase
from freecad.fields.core import fld_logger
from freecad.fields.core.input.fld_gui_utils import DynamicLimitIntSlider, DynamicLimitSlider

class SdfFaceTaskPanel:
    """Task panel for modifying the SDF face parameters."""
    def __init__(self, tool):
        self.tool = tool
        self.form = QtWidgets.QWidget()
        self.layout = QtWidgets.QVBoxLayout(self.form)
        self.layout.setContentsMargins(10, 10, 10, 10)

        # The panel deliberately shows only what the height-field fill actually reads.
        # Fill Type, Laplacian Iterations and Center Offset parameterised the NURBS and
        # relaxation fills that the boundary height-field solve replaced. Fill Type is
        # now deleted outright, from saved files too; Iterations and Center Offset are
        # still created and still read by nothing. Do not re-add a widget here without a
        # code path that reads the property -- Tension is back only because
        # solve_boundary_height_field takes it, and even Tension greys out on the flat
        # curves whose fill never reaches that solve.
        group = QtWidgets.QGroupBox("SDF Face Options")
        form_layout = QtWidgets.QFormLayout(group)
        self.layout.addWidget(group)

        # U is angular and V radial in the disk mesh that fills the curve.
        self.u_spin = DynamicLimitIntSlider(8, 4, 100, 1)
        self.u_spin.valueChanged.connect(self._on_params_changed)
        form_layout.addRow("U Resolution:", self.u_spin)

        self.v_spin = DynamicLimitIntSlider(8, 4, 100, 1)
        self.v_spin.valueChanged.connect(self._on_params_changed)
        form_layout.addRow("V Resolution:", self.v_spin)

        # 1.0 = membrane (taut, creases into the boundary), 0.0 = thin plate (matches
        # the boundary slope and bulges). The plate takes many more relaxation sweeps,
        # so this re-solves rather than re-samples; see solve_boundary_height_field.
        self.tension_spin = DynamicLimitSlider(1.0, 0.0, 1.0, 0.05, 2)
        self.tension_spin.valueChanged.connect(self._on_params_changed)
        # Kept as a handle so the label greys out with the slider; a lone greyed
        # control next to a black label reads as a glitch rather than as a state.
        self.tension_label = QtWidgets.QLabel("Tension:")
        form_layout.addRow(self.tension_label, self.tension_spin)

        self.ext_checkbox = QtWidgets.QCheckBox("Show Infinite Extension")
        self.ext_checkbox.stateChanged.connect(self._on_params_changed)
        form_layout.addRow("", self.ext_checkbox)

        self.layout.addStretch()
        self.update_ui()

    def update_ui(self):
        obj = self.tool._target_obj
        if not obj:
            return

        for w in (self.u_spin, self.v_spin, self.tension_spin, self.ext_checkbox):
            w.blockSignals(True)

        self.u_spin.setValue(getattr(obj, "UCount", 8))
        self.v_spin.setValue(getattr(obj, "VCount", 8))
        self.tension_spin.setValue(getattr(obj, "Tension", 1.0))
        self.ext_checkbox.setChecked(getattr(obj, "ShowExtension", False))

        for w in (self.u_spin, self.v_spin, self.tension_spin, self.ext_checkbox):
            w.blockSignals(False)

        self._sync_tension_enabled(obj)

    def _sync_tension_enabled(self, obj):
        """Tension only means something where a height field is actually solved.

        A flat curve is filled with an exact plane -- no relaxation runs, so the
        slider would move and the face would not. Grey it there rather than leave
        a control that silently does nothing, which is how Fill Type, Iterations
        and Center Offset came to be dropped from this panel.
        """
        from freecad.fields.core.objects.fld_curve_fill_geometry import curve_fill_is_planar
        curve = getattr(obj, "SourceCurve", None) or obj
        try:
            live = not curve_fill_is_planar(curve)
        except Exception as e:
            fld_logger.debug(f"SdfFaceTaskPanel: planarity unknown ({e}); leaving Tension live")
            live = True

        for w in (self.tension_label, self.tension_spin):
            w.setEnabled(live)
        self.tension_spin.setToolTip(
            "1.0 = membrane (taut, creases into the boundary); 0.0 = thin plate "
            "(follows the boundary slope and bulges)." if live else
            "This curve is flat, so its fill is an exact plane -- there is no height "
            "field to tension. Lift the curve out of plane to use this.")
        self.tension_label.setToolTip(self.tension_spin.toolTip())

    def _on_params_changed(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.UCount = self.u_spin.value()
        obj.VCount = self.v_spin.value()
        obj.Tension = float(self.tension_spin.value())
        obj.ShowExtension = self.ext_checkbox.isChecked()
        self.tool._commit_changes()

    def accept(self):
        self.tool._dialog_open = False
        self.tool.finish()
        return True

    def reject(self):
        self.tool._dialog_open = False
        self.tool.cancel()
        return True

class SdfFaceEditTool(FldBase):
    """Interactive tool for editing parameters of an SDF face."""
    def get_command_id(self):
        return "Fields_EditObject"

    def get_handled_types(self):
        return ["FldSurface"]

    def __init__(self):
        super().__init__()
        self._target_obj = None
        self._is_editing = False

    def edit_object(self, obj):
        super().edit_object(obj)
        self._target_obj = obj
        self._is_editing = True
        
        # Ensure properties are present (in case of legacy/partially created object)
        try:
            import FreeCAD
            if "ControlGrid" not in obj.PropertiesList:
                obj.addProperty("App::PropertyVectorList", "ControlGrid", "SDF Face", "Control point grid")
                obj.addProperty("App::PropertyInteger", "UCount", "SDF Face", "Width of grid")
                obj.addProperty("App::PropertyInteger", "VCount", "SDF Face", "Height of grid")
                obj.addProperty("App::PropertyLink", "SourceCurve", "SDF Face", "The curve this surface depends on")
            if "FillType" in obj.PropertiesList:
                obj.removeProperty("FillType")  # see onDocumentRestored -- nothing reads it
            if "Iterations" not in obj.PropertiesList:
                obj.addProperty("App::PropertyInteger", "Iterations", "SDF Face", "Number of Laplacian iterations")
                obj.Iterations = 200
            if "CenterOffset" not in obj.PropertiesList:
                obj.addProperty("App::PropertyVector", "CenterOffset", "SDF Face", "Offset for the center point/centroid")
                obj.CenterOffset = FreeCAD.Vector(0.0, 0.0, 0.0)
            if "Tension" not in obj.PropertiesList:
                obj.addProperty("App::PropertyFloat", "Tension", "SDF Face", "Tension of the surface (0.0 to 1.0)")
                obj.Tension = 1.0
            if "ShowExtension" not in obj.PropertiesList:
                obj.addProperty("App::PropertyBool", "ShowExtension", "SDF Face", "Whether to show the infinite extension of the SDF face")
                obj.ShowExtension = False
        except Exception as e:
            fld_logger.error(f"SdfFaceEditTool: Failed to ensure surface properties: {e}")

        self._original_props = {
            "Iterations": getattr(obj, "Iterations", 200),
            "CenterOffset": FreeCAD.Vector(getattr(obj, "CenterOffset", FreeCAD.Vector(0.0, 0.0, 0.0))),
            "UCount": getattr(obj, "UCount", 8),
            "VCount": getattr(obj, "VCount", 8),
            "Tension": getattr(obj, "Tension", 1.0),
            "ShowExtension": getattr(obj, "ShowExtension", False),
        }
        self.panel = SdfFaceTaskPanel(self)
        FreeCADGui.Control.showDialog(self.panel)
        self._dialog_open = True
        fld_logger.info(f"SdfFaceEditTool: editing surface '{obj.Label}'")

    def _commit_changes(self):
        obj = self._target_obj
        if obj and obj.Document:
            obj.touch()
            obj.Document.recompute()
            if self.view:
                self.view.redraw()

    def restore_original(self):
        obj = self._target_obj
        if not obj or not hasattr(self, "_original_props"):
            return
        for k, v in self._original_props.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        self._commit_changes()

    def cancel(self):
        self._is_editing = False
        super().cancel()

    def finish(self):
        self._is_editing = False
        self.terminate()

    def update_preview(self):
        pass

    def handle_click(self, event_dict):
        return True
