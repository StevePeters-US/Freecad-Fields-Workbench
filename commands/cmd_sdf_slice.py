"""
commands/cmd_sdf_slice.py

Slice an F-Rep SDF on a plane and create DM curve objects.
"""
import FreeCAD
import FreeCADGui
from core import dm_logger


from PySide import QtCore, QtGui


class SDFSliceTaskPanel:
    """Task panel for adjusting the slicing plane."""

    def __init__(self, obj, field, base_origin, base_normal):
        self.obj = obj
        self.field = field
        self.base_origin = base_origin
        self.base_normal = base_normal
        
        from core.dm_object import get_ray_march_cell_size
        self.resolution = get_ray_march_cell_size()
        
        self.form = QtGui.QWidget()
        self.setup_ui()

    def setup_ui(self):
        layout = QtGui.QVBoxLayout(self.form)
        
        form_layout = QtGui.QFormLayout()
        
        # Offset slider/spinbox
        self.offset_spin = QtGui.QDoubleSpinBox()
        self.offset_spin.setRange(-1000, 1000)
        self.offset_spin.setValue(0.0)
        self.offset_spin.setSuffix(" mm")
        form_layout.addRow("Offset:", self.offset_spin)
        
        # Count
        self.count_spin = QtGui.QSpinBox()
        self.count_spin.setRange(1, 100)
        self.count_spin.setValue(1)
        form_layout.addRow("Count:", self.count_spin)
        
        # Spacing
        self.spacing_spin = QtGui.QDoubleSpinBox()
        self.spacing_spin.setRange(0.1, 500)
        self.spacing_spin.setValue(10.0)
        self.spacing_spin.setSuffix(" mm")
        form_layout.addRow("Spacing:", self.spacing_spin)
        
        layout.addLayout(form_layout)
        
        # Alignment buttons
        align_layout = QtGui.QHBoxLayout()
        self.btn_x = QtGui.QPushButton("X")
        self.btn_y = QtGui.QPushButton("Y")
        self.btn_z = QtGui.QPushButton("Z")
        self.btn_x.clicked.connect(lambda: self.set_axis(FreeCAD.Vector(1, 0, 0)))
        self.btn_y.clicked.connect(lambda: self.set_axis(FreeCAD.Vector(0, 1, 0)))
        self.btn_z.clicked.connect(lambda: self.set_axis(FreeCAD.Vector(0, 0, 1)))
        align_layout.addWidget(self.btn_x)
        align_layout.addWidget(self.btn_y)
        align_layout.addWidget(self.btn_z)
        layout.addLayout(align_layout)
        
        # Slice button
        self.btn_slice = QtGui.QPushButton("Slice Now")
        self.btn_slice.clicked.connect(self.do_slice)
        layout.addWidget(self.btn_slice)
        
        layout.addStretch()

    def set_axis(self, normal):
        self.base_normal = normal
        self.offset_spin.setValue(0.0)

    def getStandardButtons(self):
        return QtGui.QDialogButtonBox.Close

    def do_slice(self):
        try:
            from core.frep.sdf_slicer import slice_sdf, fit_dm_curve
            from core.dm_object import create_dm_object

            offset_val = self.offset_spin.value()
            count = self.count_spin.value()
            spacing = self.spacing_spin.value()
            
            origin = self.base_origin + self.base_normal * offset_val
            
            doc = FreeCAD.activeDocument()
            total_created = 0
            
            for c_idx in range(count):
                current_origin = origin + self.base_normal * (c_idx * spacing)
                contours = slice_sdf(self.field, current_origin, self.base_normal, resolution=self.resolution)
                
                for i, contour in enumerate(contours):
                    if len(contour) < 2:
                        continue
                    is_closed = (contour[0] - contour[-1]).Length < self.resolution * 2
                    params = fit_dm_curve(contour, closed=is_closed)
                    name = f"{self.obj.Label}_Slice_{c_idx}_{i}"
                    create_dm_object(name, "curve", params=params)
                    total_created += 1

            doc.recompute()
            dm_logger.info(f"SDFSlice: Created {total_created} slice curve(s)")
            
        except Exception as e:
            dm_logger.exception(f"SDFSlice: Error: {e}")


class SDFSliceCommand:
    """Slice an F-Rep object to create cross-section curves."""

    def GetResources(self):
        return {
            'Pixmap': 'SDFSlice',
            'MenuText': 'SDF Slice',
            'ToolTip': 'Slice an F-Rep SDF on a plane to create cross-section curves.\n'
                       'Select an F-Rep object first. Uses the active workplane or XY plane.',
        }

    def Activated(self):
        try:
            sel = FreeCADGui.Selection.getSelection()
            if not sel:
                dm_logger.warn("SDFSlice: No object selected")
                return
            obj = sel[0]
            proxy = getattr(obj, "Proxy", None)
            field = getattr(proxy, "SdfField", None) if proxy else None
            if field is None:
                dm_logger.warn("SDFSlice: Selected object has no SdfField")
                return

            # Get slice plane from active workplane or default to XY
            origin = FreeCAD.Vector(0, 0, 0)
            normal = FreeCAD.Vector(0, 0, 1)
            try:
                from core.dm_workplane import get_active_workplane
                wp = get_active_workplane()
                if wp:
                    placement = wp.Placement
                    origin = placement.Base
                    normal = placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
            except Exception:
                pass  # Use default XY plane

            panel = SDFSliceTaskPanel(obj, field, origin, normal)
            FreeCADGui.Control.showDialog(panel)

        except Exception as e:
            dm_logger.exception(f"SDFSlice: Error: {e}")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


FreeCADGui.addCommand('DM_SDFSlice', SDFSliceCommand())
