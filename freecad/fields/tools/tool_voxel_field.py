# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/tool_voxel_field.py

Editor tool and task panel for the discrete 3D voxel SDF field: dimensions,
grid resolution, sampling mode, and in-place carve/deposit sculpting.
"""
import FreeCAD
from PySide import QtWidgets, QtCore
from freecad.fields.tools.fld_sdf_tool_base import FldSdfModifierToolBase
from freecad.fields.core.input.fld_gui_utils import DynamicLimitSlider, DynamicLimitIntSlider
from freecad.fields.core.objects.fld_voxel_field import write_back_voxel_data


class VoxelFieldTaskPanel:
    def __init__(self, tool):
        self.tool = tool
        self.form = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.form)
        layout.setContentsMargins(10, 10, 10, 10)

        # ── Size Group ──────────────────────────────────────────────────────────
        size_grp = QtWidgets.QGroupBox("Dimensions (mm)")
        sl = QtWidgets.QFormLayout(size_grp)
        layout.addWidget(size_grp)

        self.sizex_slider = DynamicLimitSlider(value=100.0, min_val=1.0, max_val=500.0, step=1.0, decimals=1)
        self.sizex_slider.valueChanged.connect(self._on_changed)
        sl.addRow("Size X:", self.sizex_slider)

        self.sizey_slider = DynamicLimitSlider(value=100.0, min_val=1.0, max_val=500.0, step=1.0, decimals=1)
        self.sizey_slider.valueChanged.connect(self._on_changed)
        sl.addRow("Size Y:", self.sizey_slider)

        self.sizez_slider = DynamicLimitSlider(value=100.0, min_val=1.0, max_val=500.0, step=1.0, decimals=1)
        self.sizez_slider.valueChanged.connect(self._on_changed)
        sl.addRow("Size Z:", self.sizez_slider)

        # ── Resolution Group ────────────────────────────────────────────────────
        res_grp = QtWidgets.QGroupBox("Grid Resolution (voxels)")
        rl = QtWidgets.QFormLayout(res_grp)
        layout.addWidget(res_grp)

        self.resx_slider = DynamicLimitIntSlider(value=32, min_val=4, max_val=128, step=2)
        self.resx_slider.valueChanged.connect(self._on_changed)
        rl.addRow("Res X:", self.resx_slider)

        self.resy_slider = DynamicLimitIntSlider(value=32, min_val=4, max_val=128, step=2)
        self.resy_slider.valueChanged.connect(self._on_changed)
        rl.addRow("Res Y:", self.resy_slider)

        self.resz_slider = DynamicLimitIntSlider(value=32, min_val=4, max_val=128, step=2)
        self.resz_slider.valueChanged.connect(self._on_changed)
        rl.addRow("Res Z:", self.resz_slider)

        # ── Mode & Appearance Group ─────────────────────────────────────────────
        opt_grp = QtWidgets.QGroupBox("Sampling & Mode")
        ol = QtWidgets.QFormLayout(opt_grp)
        layout.addWidget(opt_grp)

        self.interp_combo = QtWidgets.QComboBox()
        self.interp_combo.addItems(["Trilinear", "Nearest"])
        self.interp_combo.currentIndexChanged.connect(self._on_changed)
        ol.addRow("Interpolation:", self.interp_combo)

        self.group_btn = QtWidgets.QPushButton("Additive")
        self.group_btn.clicked.connect(self._on_group_toggled)
        self.group_btn.setToolTip("Toggle rendering group (Q)")
        ol.addRow("Group:", self.group_btn)

        # ── Sculpting / Carving Group ───────────────────────────────────────────
        sculpt_grp = QtWidgets.QGroupBox("Sculpting & Carving")
        scl = QtWidgets.QFormLayout(sculpt_grp)
        layout.addWidget(sculpt_grp)

        self.brush_mode_combo = QtWidgets.QComboBox()
        self.brush_mode_combo.addItems(["Carve (Subtract)", "Deposit (Union)"])
        scl.addRow("Brush Mode:", self.brush_mode_combo)

        self.radius_slider = DynamicLimitSlider(value=15.0, min_val=1.0, max_val=100.0, step=1.0, decimals=1)
        scl.addRow("Brush Radius:", self.radius_slider)

        self.smooth_k_slider = DynamicLimitSlider(value=0.0, min_val=0.0, max_val=20.0, step=0.5, decimals=1)
        scl.addRow("Smooth K:", self.smooth_k_slider)

        self.stamp_btn = QtWidgets.QPushButton("Apply Stamp at Center")
        self.stamp_btn.clicked.connect(self._on_stamp_center)
        scl.addRow("", self.stamp_btn)

        layout.addStretch()
        self.update_ui()

    def update_ui(self):
        obj = self.tool._target_obj
        if not obj:
            return
        self.sizex_slider.blockSignals(True)
        self.sizey_slider.blockSignals(True)
        self.sizez_slider.blockSignals(True)
        self.resx_slider.blockSignals(True)
        self.resy_slider.blockSignals(True)
        self.resz_slider.blockSignals(True)
        self.interp_combo.blockSignals(True)

        self.sizex_slider.setValue(getattr(obj, "SizeX", 100.0))
        self.sizey_slider.setValue(getattr(obj, "SizeY", 100.0))
        self.sizez_slider.setValue(getattr(obj, "SizeZ", 100.0))
        self.resx_slider.setValue(getattr(obj, "ResolutionX", 32))
        self.resy_slider.setValue(getattr(obj, "ResolutionY", 32))
        self.resz_slider.setValue(getattr(obj, "ResolutionZ", 32))

        interp = str(getattr(obj, "Interpolation", "Trilinear"))
        idx = self.interp_combo.findText(interp)
        if idx >= 0:
            self.interp_combo.setCurrentIndex(idx)

        self.group_btn.setText(getattr(obj, "Group", "Additive"))

        self.sizex_slider.blockSignals(False)
        self.sizey_slider.blockSignals(False)
        self.sizez_slider.blockSignals(False)
        self.resx_slider.blockSignals(False)
        self.resy_slider.blockSignals(False)
        self.resz_slider.blockSignals(False)
        self.interp_combo.blockSignals(False)

    def _on_changed(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.SizeX = self.sizex_slider.value()
        obj.SizeY = self.sizey_slider.value()
        obj.SizeZ = self.sizez_slider.value()
        obj.ResolutionX = self.resx_slider.value()
        obj.ResolutionY = self.resy_slider.value()
        obj.ResolutionZ = self.resz_slider.value()
        obj.Interpolation = self.interp_combo.currentText()
        self.tool._commit_changes()

    def _on_group_toggled(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.Group = "Subtractive" if getattr(obj, "Group", "Additive") == "Additive" else "Additive"
        self.group_btn.setText(obj.Group)
        self.tool._commit_changes()

    def _on_stamp_center(self):
        mode = "carve" if self.brush_mode_combo.currentIndex() == 0 else "deposit"
        radius = self.radius_slider.value()
        smooth_k = self.smooth_k_slider.value()
        self.tool.apply_sculpt_at(center=None, radius=radius, mode=mode, smooth_k=smooth_k)

    def accept(self):
        self.tool._dialog_open = False
        self.tool.finish()
        return True

    def reject(self):
        self.tool.cancel()
        return True


class VoxelFieldTool(FldSdfModifierToolBase):
    SNAPSHOT_PROPERTIES = [
        ("SizeX", 100.0),
        ("SizeY", 100.0),
        ("SizeZ", 100.0),
        ("ResolutionX", 32),
        ("ResolutionY", 32),
        ("ResolutionZ", 32),
        ("Interpolation", "Trilinear"),
        ("Group", "Additive"),
        ("Sculpted", False),
        ("VoxelData", ""),
        ("DataShape", [32, 32, 32]),
    ]

    def get_command_id(self):
        return "Fields_EditObject"

    def get_handled_types(self):
        return ["FldVoxelFieldProxy"]

    def _make_panel(self):
        return VoxelFieldTaskPanel(self)

    def apply_sculpt_at(self, center=None, radius=None, mode="carve", smooth_k=0.0):
        """Applies an in-place carve or deposit operation to the voxel grid."""
        obj = self._target_obj
        if not obj or not hasattr(obj, "Proxy") or not obj.Proxy:
            return
        fld = obj.Proxy.get_sdf_field(obj)
        if fld is None:
            return

        if center is None:
            center = FreeCAD.Vector(obj.Placement.Base) if hasattr(obj, "Placement") else FreeCAD.Vector(0, 0, 0)

        if radius is None:
            radius = getattr(self.panel, "radius_slider", None).value() if hasattr(self, "panel") else 15.0

        from freecad.fields.core.sdf.sdf.sphere import SdfSphereField
        sphere = SdfSphereField(center, radius)
        res = fld.stamp_local(sphere, operation="cut" if mode == "carve" else "add", smooth_k=smooth_k)
        if res is None:
            return
        fld.geometry_version += 1

        write_back_voxel_data(obj, fld)
        self._commit_changes(force=True)

    def _get_mouse_hit(self, event_dict):
        """Where on the surface the cursor is, or None when it is over nothing."""
        if not self.projector:
            return None
        return self.projector.get_surface_hit(event_dict)

    def on_button1_down(self, event_dict):
        # Allow viewport clicks to project onto the SDF surface and sculpt directly
        pos = self._get_mouse_hit(event_dict)
        if pos is not None:
            panel = getattr(self, "panel", None)
            # Index, not truthiness: `panel and ...` collapses "no panel" and
            # "Deposit selected" into one falsy branch, so whichever mode that
            # branch names, one of the two cases gets the wrong one. Default to
            # the combo's own default (0 = Carve) when there is no panel.
            mode_index = panel.brush_mode_combo.currentIndex() if panel else 0
            mode = "carve" if mode_index == 0 else "deposit"
            radius = panel.radius_slider.value() if panel else 15.0
            smooth_k = panel.smooth_k_slider.value() if panel else 0.0
            self.apply_sculpt_at(center=pos, radius=radius, mode=mode, smooth_k=smooth_k)
            return True
        return super().on_button1_down(event_dict)

