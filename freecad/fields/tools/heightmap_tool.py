# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from PySide import QtGui, QtWidgets, QtCore
from freecad.fields.tools.fld_base import DragTimerMixin
from freecad.fields.tools.fld_sdf_tool_base import FldSdfModifierToolBase, DirectionGizmoMixin
import os
from freecad.fields.core import fld_logger
from freecad.fields.core.input.fld_gui_utils import DynamicLimitSlider
from pivy import coin


class PlainNumberLineEdit(QtWidgets.QLineEdit):
    """A bare-float line edit with mouse-wheel nudging -- no FreeCAD units.

    Was `_QuantityLineEdit` (CR-051): despite the old name, this never parsed
    `FreeCAD.Units.Quantity` the way `primitive_panel.QuantityLineEdit` does --
    it always used a plain `float(self.text())`. Its 3 call sites (`dir_x`/
    `dir_y`/`dir_z`) hold direction-vector components, which are genuinely
    unitless, so the rename fixes the misleading name rather than changing
    behavior to match it.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.step = 0.1

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
            fld_logger.debug(f"PlainNumberLineEdit.wheelEvent: Failed to apply wheel step: {e}")
        event.accept()


class HeightmapTaskPanel:
    def __init__(self, tool):
        self.tool = tool
        self.form = QtWidgets.QWidget()
        self.layout = QtWidgets.QVBoxLayout(self.form)
        self.layout.setContentsMargins(10, 10, 10, 10)

        # ── Image ──────────────────────────────────────────────────────────────
        img_group = QtWidgets.QGroupBox("Heightmap Image")
        img_layout = QtWidgets.QVBoxLayout(img_group)
        self.layout.addWidget(img_group)

        self._img_name_label = QtWidgets.QLabel("No image loaded")
        self._img_name_label.setWordWrap(True)
        img_layout.addWidget(self._img_name_label)

        self._img_preview = QtWidgets.QLabel()
        self._img_preview.setFixedHeight(80)
        self._img_preview.setAlignment(QtCore.Qt.AlignCenter)
        self._img_preview.setStyleSheet("background: #222; border: 1px solid #444;")
        img_layout.addWidget(self._img_preview)

        browse_btn = QtWidgets.QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse)
        img_layout.addWidget(browse_btn)

        # ── Displacement ────────────────────────────────────────────────────────
        params_group = QtWidgets.QGroupBox("Displacement")
        p_layout = QtWidgets.QFormLayout(params_group)
        self.layout.addWidget(params_group)

        self.amp_slider = DynamicLimitSlider(value=0.5, min_val=0.0, max_val=1.0, step=0.01, decimals=3)
        self.amp_slider.valueChanged.connect(self._on_params_changed)
        self.amp_slider.limitsChanged.connect(self._on_limits_changed)
        p_layout.addRow("Amplitude:", self.amp_slider)

        self.sizex_slider = DynamicLimitSlider(value=100.0, min_val=1.0, max_val=500.0, step=1.0, decimals=1)
        self.sizex_slider.valueChanged.connect(self._on_params_changed)
        self.sizex_slider.limitsChanged.connect(self._on_limits_changed)
        p_layout.addRow("Size X:", self.sizex_slider)

        self.sizey_slider = DynamicLimitSlider(value=100.0, min_val=1.0, max_val=500.0, step=1.0, decimals=1)
        self.sizey_slider.valueChanged.connect(self._on_params_changed)
        self.sizey_slider.limitsChanged.connect(self._on_limits_changed)
        p_layout.addRow("Size Y:", self.sizey_slider)

        self.cutoff_slider = DynamicLimitSlider(value=0.0, min_val=-1.0, max_val=1.0, step=0.05, decimals=2)
        self.cutoff_slider.setToolTip(
            "Facing cutoff: -1 = displace all surfaces, 0 = front hemisphere only,\n"
            "0.5 = only surfaces whose normal is within ~60° of the direction"
        )
        self.cutoff_slider.valueChanged.connect(self._on_params_changed)
        p_layout.addRow("Facing Cutoff:", self.cutoff_slider)

        self.tile_check = QtWidgets.QCheckBox("Tile texture")
        self.tile_check.setChecked(True)
        self.tile_check.setToolTip("Repeat (tile) the heightmap image. Uncheck to clamp to edges.")
        self.tile_check.stateChanged.connect(self._on_params_changed)
        p_layout.addRow("", self.tile_check)

        self.group_btn = QtWidgets.QPushButton("Additive")
        self.group_btn.clicked.connect(self._on_group_toggled)
        self.group_btn.setToolTip("Toggle rendering group (Q)")
        p_layout.addRow("Group:", self.group_btn)

        # ── Direction ──────────────────────────────────────────────────────────
        dir_group = QtWidgets.QGroupBox("Displacement Direction")
        dir_layout = QtWidgets.QFormLayout(dir_group)
        self.layout.addWidget(dir_group)

        self.dir_x = PlainNumberLineEdit()
        self.dir_x.step = 0.1
        self.dir_x.editingFinished.connect(self._on_dir_changed)
        dir_layout.addRow("X:", self.dir_x)

        self.dir_y = PlainNumberLineEdit()
        self.dir_y.step = 0.1
        self.dir_y.editingFinished.connect(self._on_dir_changed)
        dir_layout.addRow("Y:", self.dir_y)

        self.dir_z = PlainNumberLineEdit()
        self.dir_z.step = 0.1
        self.dir_z.editingFinished.connect(self._on_dir_changed)
        dir_layout.addRow("Z:", self.dir_z)

        self.layout.addStretch()
        self.update_ui()

    # ── UI sync ───────────────────────────────────────────────────────────────

    def update_ui(self):
        obj = self.tool._target_obj
        if not obj:
            return

        self.amp_slider.blockSignals(True)
        self.sizex_slider.blockSignals(True)
        self.sizey_slider.blockSignals(True)
        self.cutoff_slider.blockSignals(True)
        self.tile_check.blockSignals(True)
        for w in (self.dir_x, self.dir_y, self.dir_z):
            w.blockSignals(True)

        # Limits are already initialized by FldHeightmapProxy.__init__

        self.amp_slider.setLimits(obj.AmplitudeMin, obj.AmplitudeMax)
        self.amp_slider.setValue(getattr(obj, "Amplitude", 5.0))
        self.sizex_slider.setLimits(obj.SizeMin, obj.SizeMax)
        self.sizex_slider.setValue(getattr(obj, "SizeX", 100.0))
        self.sizey_slider.setLimits(obj.SizeMin, obj.SizeMax)
        self.sizey_slider.setValue(getattr(obj, "SizeY", 100.0))
        self.cutoff_slider.setValue(getattr(obj, "FacingCutoff", 0.0))
        self.tile_check.setChecked(getattr(obj, "Tile", True))
        self.dir_x.setText(f"{getattr(obj, 'DirectionX', 0.0):.3f}")
        self.dir_y.setText(f"{getattr(obj, 'DirectionY', 0.0):.3f}")
        self.dir_z.setText(f"{getattr(obj, 'DirectionZ', 1.0):.3f}")

        self.amp_slider.blockSignals(False)
        self.sizex_slider.blockSignals(False)
        self.sizey_slider.blockSignals(False)
        self.cutoff_slider.blockSignals(False)
        self.tile_check.blockSignals(False)
        for w in (self.dir_x, self.dir_y, self.dir_z):
            w.blockSignals(False)

        self.group_btn.setText(getattr(obj, "Group", "Additive"))
        self._update_image_preview(getattr(obj, "ImagePath", "") or "")

    def _update_image_preview(self, path):
        if path and os.path.isfile(path):
            self._img_name_label.setText(os.path.basename(path))
            pix = QtGui.QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(240, 78, QtCore.Qt.KeepAspectRatio,
                                    QtCore.Qt.SmoothTransformation)
                self._img_preview.setPixmap(scaled)
                return
        self._img_name_label.setText("No image loaded")
        self._img_preview.setPixmap(QtGui.QPixmap())

    # ── Signal handlers ───────────────────────────────────────────────────────

    @staticmethod
    def _browse_start_dir(current_path=""):
        """Where the image browser opens: the folder of the image already chosen,
        else the addon's bundled textures. Passing "" drops the dialog in whatever
        the process's cwd happens to be, which for FreeCAD is rarely useful."""
        import os
        from freecad.fields import RESOURCES_DIR
        if current_path:
            folder = os.path.dirname(current_path)
            if os.path.isdir(folder):
                return folder
        textures = os.path.join(RESOURCES_DIR, "Textures")
        return textures if os.path.isdir(textures) else ""

    def _on_browse(self):
        obj = self.tool._target_obj
        if not obj:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.form, "Open Heightmap Image", self._browse_start_dir(getattr(obj, "ImagePath", "") or ""),
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;All Files (*)"
        )
        if not path:
            return
        obj.ImagePath = path
        self._update_image_preview(path)
        self.tool._commit_changes()

    def _on_params_changed(self):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.Amplitude = self.amp_slider.value()
        obj.SizeX = self.sizex_slider.value()
        obj.SizeY = self.sizey_slider.value()
        obj.FacingCutoff = self.cutoff_slider.value()
        obj.Tile = self.tile_check.isChecked()
        self.tool._commit_changes()

    def _on_limits_changed(self, _min, _max):
        obj = self.tool._target_obj
        if not obj:
            return
        obj.AmplitudeMin = self.amp_slider._min_spin.value()
        obj.AmplitudeMax = self.amp_slider._max_spin.value()
        obj.SizeMin = self.sizex_slider._min_spin.value()
        obj.SizeMax = self.sizex_slider._max_spin.value()
        self.tool._commit_changes()

    def _on_group_toggled(self):
        obj = self.tool._target_obj
        if not obj or not hasattr(obj, "Group"):
            return
        obj.Group = "Subtractive" if getattr(obj, "Group", "Additive") == "Additive" else "Additive"
        self.group_btn.setText(obj.Group)
        self.tool._commit_changes()

    def _on_dir_changed(self):
        import math
        try:
            obj = self.tool._target_obj
            if not obj:
                return
            x = float(self.dir_x.text())
            y = float(self.dir_y.text())
            z = float(self.dir_z.text())
            length = math.sqrt(x * x + y * y + z * z)
            if length < 1e-8:
                return
            obj.DirectionX = x / length
            obj.DirectionY = y / length
            obj.DirectionZ = z / length
            for w in (self.dir_x, self.dir_y, self.dir_z):
                w.blockSignals(True)
            self.dir_x.setText(f"{obj.DirectionX:.3f}")
            self.dir_y.setText(f"{obj.DirectionY:.3f}")
            self.dir_z.setText(f"{obj.DirectionZ:.3f}")
            for w in (self.dir_x, self.dir_y, self.dir_z):
                w.blockSignals(False)
            self.tool._commit_changes()
        except Exception as e:
            fld_logger.debug(f"HeightmapTaskPanel._on_dir_changed: Failed to commit direction change: {e}")

    def set_amplitude(self, val):
        self.amp_slider.blockSignals(True)
        self.amp_slider.setValue(val)
        self.amp_slider.blockSignals(False)

    def accept(self):
        self.tool._dialog_open = False
        self.tool.finish()
        return True

    def reject(self):
        self.tool._dialog_open = False
        self.tool.cancel()
        return True


# ── Tool ──────────────────────────────────────────────────────────────────────

class HeightmapTool(FldSdfModifierToolBase, DirectionGizmoMixin, DragTimerMixin):
    SNAPSHOT_PROPERTIES = [
        ("ImagePath", ""),
        ("Amplitude", 5.0),
        ("SizeX", 100.0),
        ("SizeY", 100.0),
        ("FacingCutoff", 0.0),
        ("Tile", True),
        ("Group", "Additive"),
        ("DirectionX", 0.0),
        ("DirectionY", 0.0),
        ("DirectionZ", 1.0),
        ("OriginX", 0.0),
        ("OriginY", 0.0),
        ("OriginZ", 0.0),
    ]

    def get_command_id(self):
        return "Fields_EditObject"

    def get_handled_types(self):
        return ["FldHeightmapProxy"]

    def _gizmo_pivot(self):
        obj = self._target_obj
        if obj and hasattr(obj, "OriginX"):
            return FreeCAD.Vector(obj.OriginX, obj.OriginY, obj.OriginZ)
        return super()._gizmo_pivot()

    def _gizmo_origin_prop_names(self):
        return ("OriginX", "OriginY", "OriginZ")

    def __init__(self):
        super().__init__()
        self._gizmo = None
        self._arrow_root = None
        self._amp_sphere_root = None

        self.points_root = coin.SoAnnotation()
        if self.view and self.view.getSceneGraph():
            self.view.getSceneGraph().addChild(self.points_root)

    def _make_panel(self):
        return HeightmapTaskPanel(self)

    def _after_group_toggle(self, obj):
        super()._after_group_toggle(obj)
        self._draw_visuals()

    def edit_object(self, obj):
        super().edit_object(obj)
        self._init_gizmo()
        self._draw_visuals()

    # ── Visuals: direction arrow (from mixin) + amplitude sphere ─────────────

    def _on_direction_changed(self):
        self._draw_visuals()

    def _draw_visuals(self):
        self._draw_direction_arrow()
        self._draw_amp_sphere()

    def _draw_amp_sphere(self):
        """Draw a draggable sphere at origin + direction * amplitude."""
        if self._amp_sphere_root:
            try:
                self.points_root.removeChild(self._amp_sphere_root)
            except Exception as e:
                fld_logger.debug(f"HeightmapTool._draw_amp_sphere: Failed to remove old amp sphere: {e}")
        self._amp_sphere_root = coin.SoSeparator()
        self.points_root.addChild(self._amp_sphere_root)

        obj = self._target_obj
        if not obj:
            return

        source = getattr(obj, "Source", None)
        origin = source.Placement.Base if source else FreeCAD.Vector(0, 0, 0)
        dir_vec = FreeCAD.Vector(obj.DirectionX, obj.DirectionY, obj.DirectionZ)
        if dir_vec.Length < 1e-8:
            dir_vec = FreeCAD.Vector(0, 0, 1)
        else:
            dir_vec.normalize()
        sphere_pos = origin + dir_vec * getattr(obj, "Amplitude", 5.0)

        grp = getattr(obj, "Group", "Additive")
        color = (1.0, 0.9, 0.2) if grp != "Subtractive" else (0.2, 1.0, 0.6)

        mat = coin.SoMaterial()
        mat.diffuseColor.setValue(*color)
        mat.specularColor.setValue(0.8, 0.8, 0.8)
        mat.shininess.setValue(0.8)
        self._amp_sphere_root.addChild(mat)

        radius = self._compute_handle_radius()
        xf = coin.SoTransform()
        xf.translation.setValue(sphere_pos.x, sphere_pos.y, sphere_pos.z)
        sphere = coin.SoSphere()
        sphere.radius = radius * 1.8
        self._amp_sphere_root.addChild(xf)
        self._amp_sphere_root.addChild(sphere)

    def _get_amp_sphere_pos(self):
        obj = self._target_obj
        if not obj:
            return None
        source = getattr(obj, "Source", None)
        origin = source.Placement.Base if source else FreeCAD.Vector(0, 0, 0)
        dir_vec = FreeCAD.Vector(obj.DirectionX, obj.DirectionY, obj.DirectionZ)
        if dir_vec.Length < 1e-8:
            dir_vec = FreeCAD.Vector(0, 0, 1)
        else:
            dir_vec.normalize()
        return origin + dir_vec * getattr(obj, "Amplitude", 5.0)

    def _get_origin_and_dir(self):
        obj = self._target_obj
        if not obj:
            return FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1)
        source = getattr(obj, "Source", None)
        origin = source.Placement.Base if source else FreeCAD.Vector(0, 0, 0)
        dir_vec = FreeCAD.Vector(obj.DirectionX, obj.DirectionY, obj.DirectionZ)
        if dir_vec.Length < 1e-8:
            dir_vec = FreeCAD.Vector(0, 0, 1)
        else:
            dir_vec.normalize()
        return origin, dir_vec

    # ── Input handling ────────────────────────────────────────────────────────

    def handle_move(self, event_dict):
        if not self._is_editing or getattr(self, "_is_dragging", False):
            return
        from freecad.fields.core.input.input_manager import FldInputManager
        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            self._restore_cursor()
            return
        tol = self._compute_handle_radius(self._gizmo_pivot()) * 2.5
        sph = self._get_amp_sphere_pos()
        if sph:
            perp = (sph - ray_p).cross(ray_d).Length / max(ray_d.Length, 1e-10)
            if perp < tol:
                from PySide.QtCore import Qt
                self._set_cursor(Qt.SizeVerCursor)
                return
        if self._gizmo_handle_move(ray_p, ray_d, tol):
            return
        self._restore_cursor()

    def on_button1_down(self, event_dict):
        if not self._is_editing:
            return False
        if event_dict.get("Button") != QtCore.Qt.LeftButton:
            return False
        from freecad.fields.core.input.input_manager import FldInputManager
        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return False
        tol = self._compute_handle_radius(self._gizmo_pivot()) * 2.5
        sph = self._get_amp_sphere_pos()
        if sph:
            perp = (sph - ray_p).cross(ray_d).Length / max(ray_d.Length, 1e-10)
            if perp < tol:
                self._dragging_idx = "amp_sphere"
                origin, dir_vec = self._get_origin_and_dir()
                self._drag_origin = origin
                self._drag_dir = dir_vec
                ref = FreeCAD.Vector(1, 0, 0) if abs(dir_vec.x) < 0.9 else FreeCAD.Vector(0, 1, 0)
                u = dir_vec.cross(ref)
                if u.Length > 1e-8:
                    u.normalize()
                self._drag_plane_normal = u
                self._start_drag_timer()
                return True
        return self._gizmo_try_start_drag(ray_p, ray_d, event_dict, tol)

    def _drag_update(self):
        if self._drag_check_lmb_released():
            return
        dragging = getattr(self, "_dragging_idx", None)
        if not dragging:
            return
        if dragging == "amp_sphere":
            from freecad.fields.core.input.input_manager import FldInputManager
            self._update_amp_drag(FldInputManager.get_instance()._last_qt_pos)
            return
        self._gizmo_drag_tick()

    def _update_amp_drag(self, mouse_pos):
        origin = getattr(self, "_drag_origin", None)
        dir_vec = getattr(self, "_drag_dir", None)
        plane_normal = getattr(self, "_drag_plane_normal", None)
        if not origin or not dir_vec or not plane_normal:
            return
        pt = self.projector.get_mouse_world_pos(
            {"Position": mouse_pos}, plane_normal, origin, place_on_geometry=False)
        if not pt:
            return
        t = (pt - origin).dot(dir_vec)
        from freecad.fields.core.input import fld_snap
        from freecad.fields.core.fld_settings import get_snap_grid_step, get_snap_during_direct_drag
        step = get_snap_grid_step() if (get_snap_during_direct_drag() and fld_snap.snap_active()) else 0.0
        t = fld_snap.snap_length(t, step)
        obj = self._target_obj
        if not obj:
            return
        obj.Amplitude = max(0.0, t)
        if hasattr(self, "panel") and self.panel:
            self.panel.set_amplitude(obj.Amplitude)
        self._draw_amp_sphere()
        self._commit_changes()

    # ── Commit / restore ──────────────────────────────────────────────────────

    def _update_render_field(self, renderer, label, sdf_field):
        renderer.register_heightmap_texture(label, getattr(self._target_obj, "ImagePath", "") or "")
        super()._update_render_field(renderer, label, sdf_field)

    def _do_terminate(self):
        try:
            try:
                self._gizmo_teardown()  # cleans _gizmo and _arrow_root
            except Exception as e:
                fld_logger.debug(f"HeightmapTool._do_terminate: Failed to teardown gizmo: {e}")
            root = getattr(self, "_amp_sphere_root", None)
            if root:
                try:
                    self.points_root.removeChild(root)
                except Exception as e:
                    fld_logger.debug(f"HeightmapTool._do_terminate: Failed to remove amp sphere: {e}")
                self._amp_sphere_root = None
            if self.points_root and self.view and self.view.getSceneGraph():
                self.view.getSceneGraph().removeChild(self.points_root)
        except Exception as e:
            fld_logger.debug(f"HeightmapTool._do_terminate exception: {e}")
        super()._do_terminate()
