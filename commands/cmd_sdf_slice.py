"""
commands/cmd_sdf_slice.py

Slice an SDF SDF on a plane and create DM curve objects.
"""
import FreeCAD
import FreeCADGui
from core import dm_logger


from PySide import QtCore, QtGui
from pivy import coin


class SdfSlicePreview:
    """Manages Coin3D visual preview for the slicing planes."""

    def __init__(self):
        self.view = FreeCADGui.activeView()
        self.root = None
        if not self.view:
            return

        self.root = coin.SoSeparator()
        self.root.setName("SDFSlice_Preview")

        # Shared geometry nodes
        self.coords = coin.SoCoordinate3()
        self.lines = coin.SoLineSet()
        self.face_coords = coin.SoCoordinate3()
        self.face_set = coin.SoFaceSet()

        self._setup_grid_geom(200, 200, 20.0)

        self.view.getSceneGraph().addChild(self.root)

    def _setup_grid_geom(self, length, width, spacing):
        points = []
        half_l = length / 2.0
        half_w = width / 2.0

        if spacing <= 0:
            spacing = 10.0

        num_line_l = int(length / spacing)
        num_line_w = int(width / spacing)

        for i in range(-num_line_w // 2, num_line_w // 2 + 1):
            y = i * spacing
            points.append((-half_l, y, 0))
            points.append((half_l, y, 0))

        for i in range(-num_line_l // 2, num_line_l // 2 + 1):
            x = i * spacing
            points.append((x, -half_w, 0))
            points.append((x, half_w, 0))

        self.coords.point.setValues(0, len(points), points)
        self.lines.numVertices.setValues(0, len(points) // 2, [2] * (len(points) // 2))

        f_points = [
            (-half_l, -half_w, 0),
            (half_l, -half_w, 0),
            (half_l, half_w, 0),
            (-half_l, half_w, 0)
        ]
        self.face_coords.point.setValues(0, 4, f_points)
        self.face_set.numVertices.setValue(4)

    def update(self, origin, normal, count=1, spacing=10.0):
        if not self.root:
            return

        self.root.removeAllChildren()

        # Calculate rotation from Z-up to normal
        z_axis = FreeCAD.Vector(0, 0, 1)
        if (normal + z_axis).Length < 1e-6:
            rot = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 180)
        else:
            rot = FreeCAD.Rotation(z_axis, normal)
        q = rot.Q

        # Limit count for preview performance
        display_count = min(count, 20)

        for i in range(display_count):
            current_origin = origin + normal * (i * spacing)

            p_sep = coin.SoSeparator()
            trans = coin.SoTransform()
            trans.translation.setValue(current_origin.x, current_origin.y, current_origin.z)
            trans.rotation.setValue(q[0], q[1], q[2], q[3])
            p_sep.addChild(trans)

            mat = coin.SoMaterial()
            mat.diffuseColor.setValue(1.0, 0.0, 0.0)  # Red
            # Fade out subsequent slices
            alpha = 0.4 * (1.0 - (i / display_count) * 0.6) if display_count > 1 else 0.4
            mat.transparency.setValue(1.0 - alpha)
            p_sep.addChild(mat)

            # Add shared geometry
            p_sep.addChild(self.coords)
            p_sep.addChild(self.lines)
            p_sep.addChild(self.face_coords)
            p_sep.addChild(self.face_set)

            self.root.addChild(p_sep)

        if self.view:
            self.view.redraw()

    def cleanup(self):
        if self.view and self.root:
            self.view.getSceneGraph().removeChild(self.root)
            self.root = None
            self.view.redraw()


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
        
        self.preview = SdfSlicePreview()
        self.update_preview()

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
        self.spacing_spin.valueChanged.connect(self.update_preview)
        form_layout.addRow("Spacing:", self.spacing_spin)
        
        self.offset_spin.valueChanged.connect(self.update_preview)
        self.count_spin.valueChanged.connect(self.update_preview)
        
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
        self.update_preview()

    def update_preview(self):
        try:
            offset_val = self.offset_spin.value()
            count = self.count_spin.value()
            spacing = self.spacing_spin.value()
            origin = self.base_origin + self.base_normal * offset_val
            self.preview.update(origin, self.base_normal, count, spacing)
        except Exception as e:
            dm_logger.debug(f"SDFSlice: Preview update failed: {e}")

    def getStandardButtons(self):
        return QtGui.QDialogButtonBox.Close

    def reject(self):
        self.preview.cleanup()
        FreeCADGui.Control.closeDialog()
        return True

    def accept(self):
        self.preview.cleanup()
        FreeCADGui.Control.closeDialog()
        return True

    def do_slice(self):
        try:
            from core.sdf.sdf_slicer import slice_sdf, fit_dm_curve
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
    """Slice an SDF object to create cross-section curves."""

    def GetResources(self):
        return {
            'Pixmap': 'SDFSlice',
            'MenuText': 'SDF Slice',
            'ToolTip': 'Slice an SDF SDF on a plane to create cross-section curves.\n'
                       'Select an SDF object first. Uses the active workplane or XY plane.',
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
