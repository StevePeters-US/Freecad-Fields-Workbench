# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""The Fields Settings dialog.

Split out of commands/cmd_settings.py (CR-065) -- a single large, monolithic dialog
covering many unrelated settings groups (hatch/guide/detent/grid colors, warp
resolution, snap markers, etc.), all interleaved with color-picker logic and
ParamGet persistence in one class. Only file-level split done here; further
splitting the dialog's per-setting-group sections into their own builders is a
separate, lower-priority granularity the review flagged but did not ask for by
default.
"""
from PySide import QtWidgets, QtCore, QtGui
from freecad.fields.core import fld_logger
from freecad.fields.core.input.fld_gui_utils import CollapsibleSection


class _SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fields Settings")
        self.setMinimumWidth(380)
        self.resize(400, 550)

        from freecad.fields.core.objects.fld_object import (get_show_wireframe, get_line_width, get_point_size,
                                    get_interactive_throttle_interval, get_picking_radius, get_max_bounds,
                                    get_sdf_selection_outline_size, get_drag_tick_rate,
                                    get_render_quality, get_show_cage_curves,
                                    get_cage_patch_type, get_simplify_cage_drag,
                                    get_heightmap_resolution, get_gpu_field_eval,
                                    get_sdf_warp_resolution,
                                    get_voxel_grid_resolution, get_model_tolerance,
                                    get_hatch_size, get_hatch_color, get_hatch_strength)
        current_wire = get_show_wireframe()
        current_show_cage_curves = get_show_cage_curves()
        current_simplify_cage_drag = get_simplify_cage_drag()
        current_lw = get_line_width()
        current_ps = get_point_size()
        current_pr = get_picking_radius()
        current_mb = get_max_bounds()
        current_sos = get_sdf_selection_outline_size()
        current_hatch_size     = get_hatch_size()
        current_hatch_color    = get_hatch_color()
        current_hatch_strength = get_hatch_strength()
        from freecad.fields.core.objects.fld_object import get_max_sdf_render_size
        current_mss = get_max_sdf_render_size()
        current_dtr = get_drag_tick_rate()

        # Near Clip Distance spinbox
        from freecad.fields.core.objects.fld_object import get_near_clip_distance, get_ray_march_cell_size
        self._near_clip_spin = QtWidgets.QDoubleSpinBox()
        self._near_clip_spin.setRange(0.0, 10000.0)
        self._near_clip_spin.setSingleStep(1.0)
        self._near_clip_spin.setDecimals(1)
        self._near_clip_spin.setValue(get_near_clip_distance())
        self._near_clip_spin.setToolTip(
            "Override camera near clipping distance in mm.\n"
            "Set to 0 for automatic (FreeCAD default).\n"
            "Increase if SDF objects are clipped when zoomed in."
        )

        self._tol_spin = QtWidgets.QDoubleSpinBox()
        self._tol_spin.setRange(0.001, 10.0)
        self._tol_spin.setSingleStep(0.01)
        self._tol_spin.setDecimals(3)
        self._tol_spin.setValue(get_model_tolerance())
        self._tol_spin.setToolTip(
            "Project accuracy target in mm — the maximum surface deviation\n"
            "slicing and CAM toolpaths are allowed to leave behind.\n"
            "Seeds the Tolerance field of the SDF Slice and SDF CAM panels.\n"
            "Halving it multiplies control points and slice time. Default: 0.1 mm."
        )

        self._rm_res_spin = QtWidgets.QDoubleSpinBox()
        self._rm_res_spin.setRange(0.1, 20.0)
        self._rm_res_spin.setSingleStep(0.5)
        self._rm_res_spin.setDecimals(1)
        self._rm_res_spin.setValue(get_ray_march_cell_size())
        self._rm_res_spin.setToolTip(
            "SDF baking resolution for GPU ray march renderer in mm.\n"
            "Smaller = smoother surface, higher GPU memory. Default: 2.0 mm."
        )

        # Dialog main layout
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # Scroll Area for settings sections
        scroll_area = QtWidgets.QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll_area.setStyleSheet("QScrollArea { background: transparent; }")
        scroll_area.viewport().setStyleSheet("background: transparent;")
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        main_layout.addWidget(scroll_area)

        # Widget to hold sections in the Scroll Area
        scroll_widget = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(6)
        scroll_area.setWidget(scroll_widget)

        # Define Collapsible Sections
        section_display = CollapsibleSection("Display and Styling", self)
        section_interaction = CollapsibleSection("Interaction and Dragging", self)
        section_controls = CollapsibleSection("Controls and Keymap", self)
        section_snapping = CollapsibleSection("Snapping", self)
        section_rendering = CollapsibleSection("SDF Rendering and Meshing", self)
        section_system = CollapsibleSection("System and Diagnostics", self)

        scroll_layout.addWidget(section_display)
        scroll_layout.addWidget(section_interaction)
        scroll_layout.addWidget(section_controls)
        scroll_layout.addWidget(section_snapping)
        scroll_layout.addWidget(section_rendering)
        scroll_layout.addWidget(section_system)
        
        # Collapse advanced system section by default
        section_system.setExpanded(False)

        # Push elements to the top of the scroll widget
        scroll_layout.addStretch()

        # ── Group 1: Display and Styling ──
        # Show Wireframe checkbox
        self._wire_check = QtWidgets.QCheckBox()
        self._wire_check.setChecked(current_wire)
        self._wire_check.setToolTip("Show triangle wireframe on NURBS objects")
        section_display.addRow("Show Wireframe:", self._wire_check)

        # Show Cage Curves checkbox
        self._cage_curves_check = QtWidgets.QCheckBox()
        self._cage_curves_check.setChecked(current_show_cage_curves)
        self._cage_curves_check.setToolTip("Show patch boundary curves for cage objects")
        section_display.addRow("Show Cage Curves:", self._cage_curves_check)

        # Line Width spinbox
        self._lw_spin = QtWidgets.QDoubleSpinBox()
        self._lw_spin.setRange(0.5, 20.0)
        self._lw_spin.setValue(current_lw)
        section_display.addRow("Line Width:", self._lw_spin)

        # Point Size spinbox
        self._ps_spin = QtWidgets.QDoubleSpinBox()
        self._ps_spin.setRange(1.0, 50.0)
        self._ps_spin.setValue(current_ps)
        section_display.addRow("Point Size:", self._ps_spin)

        # Selection Outline Size spinbox
        self._sos_spin = QtWidgets.QSpinBox()
        self._sos_spin.setRange(0, 8)
        self._sos_spin.setValue(current_sos)
        self._sos_spin.setToolTip("Selection highlight outline width in pixels (0 to disable)")
        section_display.addRow("SDF Selection Outline (px):", self._sos_spin)

        # Crosshatch period spinbox
        self._hatch_size_spin = QtWidgets.QSpinBox()
        self._hatch_size_spin.setRange(0, 64)
        self._hatch_size_spin.setValue(current_hatch_size)
        self._hatch_size_spin.setSuffix(" px")
        self._hatch_size_spin.setToolTip(
            "Crosshatch period for subtractive SDFs, in screen pixels (0 to disable)")
        section_display.addRow("Subtractive Crosshatch:", self._hatch_size_spin)

        # Crosshatch colour button -- opens QColorDialog, paints its own swatch.
        self._hatch_color = tuple(current_hatch_color)
        self._hatch_color_btn = QtWidgets.QPushButton()
        self._hatch_color_btn.setToolTip("Crosshatch line colour")
        self._paint_hatch_swatch()
        self._hatch_color_btn.clicked.connect(self._on_pick_hatch_color)
        section_display.addRow("Crosshatch Colour:", self._hatch_color_btn)

        # Crosshatch strength
        self._hatch_strength_spin = QtWidgets.QDoubleSpinBox()
        self._hatch_strength_spin.setRange(0.0, 1.0)
        self._hatch_strength_spin.setSingleStep(0.05)
        self._hatch_strength_spin.setDecimals(2)
        self._hatch_strength_spin.setValue(current_hatch_strength)
        self._hatch_strength_spin.setToolTip(
            "How strongly the hatch colour replaces the body colour (1.0 = opaque lines)")
        section_display.addRow("Crosshatch Strength:", self._hatch_strength_spin)

        # ── Group 2: Interaction and Dragging ──
        # Simplify Cage Drag checkbox
        self._simplify_cage_drag_check = QtWidgets.QCheckBox()
        self._simplify_cage_drag_check.setChecked(current_simplify_cage_drag)
        self._simplify_cage_drag_check.setToolTip("Simplify cage model and reduce iterations during interactive dragging")
        section_interaction.addRow("Simplify Cage Drag:", self._simplify_cage_drag_check)

        # Picking Radius spinbox
        self._pr_spin = QtWidgets.QDoubleSpinBox()
        self._pr_spin.setRange(1.0, 50.0)
        self._pr_spin.setValue(current_pr)
        section_interaction.addRow("Picking Radius (mm):", self._pr_spin)

        # Interactive Throttle spinbox
        self._throttle_spin = QtWidgets.QDoubleSpinBox()
        self._throttle_spin.setRange(0.0, 1.0)
        self._throttle_spin.setSingleStep(0.005)
        self._throttle_spin.setDecimals(3)
        self._throttle_spin.setValue(get_interactive_throttle_interval())
        self._throttle_spin.setToolTip("Interactive update throttle interval in seconds (lower = more frequent updates but higher CPU)")
        section_interaction.addRow("Interactive Throttle (s):", self._throttle_spin)

        # Drag Tick Rate spinbox
        self._drag_tick_spin = QtWidgets.QSpinBox()
        self._drag_tick_spin.setRange(1, 120)
        self._drag_tick_spin.setValue(current_dtr)
        self._drag_tick_spin.setSuffix(" Hz")
        self._drag_tick_spin.setToolTip("Interactive dragging tick/update rate in Hz (default 30 Hz)")
        section_interaction.addRow("Drag Tick Rate:", self._drag_tick_spin)

        # ── Group: Controls and Keymap ──
        from freecad.fields.core.fld_settings import (
            CONTROL_SCHEMES, get_control_scheme, set_control_scheme,
            get_snap_during_direct_drag, set_snap_during_direct_drag,
            get_snap_invert_modifier, set_snap_invert_modifier,
            get_modal_requires_selection, set_modal_requires_selection,
        )
        from freecad.fields.core.input import fld_keymap

        self._control_scheme_combo = QtWidgets.QComboBox()
        for s in CONTROL_SCHEMES:
            self._control_scheme_combo.addItem(s.capitalize(), s)
        cur_scheme = get_control_scheme()
        idx = self._control_scheme_combo.findData(cur_scheme)
        if idx >= 0:
            self._control_scheme_combo.setCurrentIndex(idx)
        self._control_scheme_combo.setToolTip(
            "hybrid: direct drag and modal G/R/S both available\n"
            "modal: direct drag orbits the view; transforms require G/R/S\n"
            "direct: drag-first; modal transform keys inert"
        )
        section_controls.addRow("Control Scheme:", self._control_scheme_combo)

        self._snap_direct_drag_check = QtWidgets.QCheckBox()
        self._snap_direct_drag_check.setChecked(get_snap_during_direct_drag())
        self._snap_direct_drag_check.setToolTip("When off, only G/R/S transforms snap.")
        section_controls.addRow("Snap During Direct Drag:", self._snap_direct_drag_check)

        self._snap_invert_combo = QtWidgets.QComboBox()
        for mod in ("ctrl", "shift", "alt"):
            self._snap_invert_combo.addItem(mod.capitalize(), mod)
        cur_mod = get_snap_invert_modifier()
        idx = self._snap_invert_combo.findData(cur_mod)
        if idx >= 0:
            self._snap_invert_combo.setCurrentIndex(idx)
        self._snap_invert_combo.setToolTip("Modifier key that inverts snapping while held.")
        section_controls.addRow("Snap Invert Modifier:", self._snap_invert_combo)

        self._modal_req_sel_check = QtWidgets.QCheckBox()
        self._modal_req_sel_check.setChecked(get_modal_requires_selection())
        self._modal_req_sel_check.setToolTip(
            "When checked, Grab requires an active point selection to start."
        )
        section_controls.addRow("Modal Needs a Selection:", self._modal_req_sel_check)

        # Keymap table
        self._keymap_table = QtWidgets.QTableWidget(len(fld_keymap.ACTIONS), 4)
        self._keymap_table.setHorizontalHeaderLabels(["Context", "Action", "Binding", ""])
        header = self._keymap_table.horizontalHeader()
        if hasattr(header, "setSectionResizeMode"):
            try:
                header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
                header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
                header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
                header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
            except Exception:
                pass

        self._keymap_edits = {}  # action -> QKeySequenceEdit

        def _make_keymap_row(row, action, default_b, ctx, desc):
            # Context item
            ctx_item = QtWidgets.QTableWidgetItem(ctx.capitalize())
            ctx_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            self._keymap_table.setItem(row, 0, ctx_item)

            # Action description item
            act_item = QtWidgets.QTableWidgetItem(desc)
            act_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            act_item.setToolTip(action)
            self._keymap_table.setItem(row, 1, act_item)

            # Binding widget
            cur_binding = fld_keymap.binding_for(action)
            seq_edit = QtWidgets.QKeySequenceEdit(QtGui.QKeySequence(cur_binding))
            if hasattr(seq_edit, "setMaximumSequenceLength"):
                seq_edit.setMaximumSequenceLength(1)
            self._keymap_edits[action] = seq_edit

            is_locked = action in fld_keymap.LOCKED_ACTIONS
            if is_locked:
                seq_edit.setEnabled(False)
                seq_edit.setToolTip("This binding is structural and cannot be rebound.")
            else:
                def _on_seq_changed(*args, a=action, se=seq_edit):
                    self._validate_keymap_conflicts()
                if hasattr(seq_edit, "keySequenceChanged"):
                    seq_edit.keySequenceChanged.connect(_on_seq_changed)
                if hasattr(seq_edit, "editingFinished"):
                    seq_edit.editingFinished.connect(_on_seq_changed)

            self._keymap_table.setCellWidget(row, 2, seq_edit)

            # Reset button
            reset_btn = QtWidgets.QPushButton("Reset")
            reset_btn.setToolTip(f"Reset to default ({default_b})")
            if is_locked:
                reset_btn.setEnabled(False)
            else:
                def _do_row_reset(a=action, db=default_b, se=seq_edit):
                    se.setKeySequence(QtGui.QKeySequence(db))
                    self._validate_keymap_conflicts()
                reset_btn.clicked.connect(_do_row_reset)
            self._keymap_table.setCellWidget(row, 3, reset_btn)

        for row, (action, def_b, ctx, desc) in enumerate(fld_keymap.ACTIONS):
            _make_keymap_row(row, action, def_b, ctx, desc)

        self._keymap_table.setMinimumHeight(240)
        section_controls.addRow("Key Bindings:", self._keymap_table)

        # Reset All Bindings button
        reset_all_btn = QtWidgets.QPushButton("Reset All Bindings")
        reset_all_btn.setToolTip("Reset all key bindings to default workbench values.")
        def _do_reset_all():
            for action, def_b, ctx, desc in fld_keymap.ACTIONS:
                if action in self._keymap_edits:
                    self._keymap_edits[action].setKeySequence(QtGui.QKeySequence(def_b))
            self._validate_keymap_conflicts()
        reset_all_btn.clicked.connect(_do_reset_all)
        section_controls.addRow("", reset_all_btn)

        # ── Group: Snapping ──
        from freecad.fields.core.fld_settings import (
            get_snap_enabled, get_snap_grid_step, get_snap_angle_step,
            get_snap_vertex_enabled, get_snap_pixel_radius,
            get_snap_guide_enabled, get_snap_guide_line_color, get_snap_guide_line_width,
            get_snap_detent_color, get_snap_detent_size, get_snap_detent_interval,
            get_snap_grid_color, get_snap_grid_line_width,
            get_snap_guide_extent_px, get_snap_grid_extent_steps, get_snap_marker_size_px
        )
        self._snap_enabled_check = QtWidgets.QCheckBox()
        self._snap_enabled_check.setChecked(get_snap_enabled())
        self._snap_enabled_check.setToolTip("Enable snapping by default during transform drags (Ctrl inverts during drag).")
        section_snapping.addRow("Enable Snapping:", self._snap_enabled_check)

        self._snap_grid_spin = QtWidgets.QDoubleSpinBox()
        self._snap_grid_spin.setRange(0.0, 1000.0)
        self._snap_grid_spin.setSingleStep(0.5)
        self._snap_grid_spin.setDecimals(2)
        self._snap_grid_spin.setSuffix(" mm")
        self._snap_grid_spin.setValue(get_snap_grid_step())
        self._snap_grid_spin.setToolTip("Grid translation quantum in mm. Set to 0 to disable grid snap.")
        section_snapping.addRow("Grid Step:", self._snap_grid_spin)

        self._snap_angle_spin = QtWidgets.QDoubleSpinBox()
        self._snap_angle_spin.setRange(0.0, 180.0)
        self._snap_angle_spin.setSingleStep(5.0)
        self._snap_angle_spin.setDecimals(1)
        self._snap_angle_spin.setSuffix(" °")
        self._snap_angle_spin.setValue(get_snap_angle_step())
        self._snap_angle_spin.setToolTip("Rotation angle quantum in degrees. Set to 0 to disable angle snap.")
        section_snapping.addRow("Angle Step:", self._snap_angle_spin)

        self._snap_vertex_check = QtWidgets.QCheckBox()
        self._snap_vertex_check.setChecked(get_snap_vertex_enabled())
        self._snap_vertex_check.setToolTip("Snap to nearby control points and primitive origins.")
        section_snapping.addRow("Vertex Snapping:", self._snap_vertex_check)

        self._snap_pixel_spin = QtWidgets.QDoubleSpinBox()
        self._snap_pixel_spin.setRange(1.0, 100.0)
        self._snap_pixel_spin.setSingleStep(1.0)
        self._snap_pixel_spin.setDecimals(1)
        self._snap_pixel_spin.setSuffix(" px")
        self._snap_pixel_spin.setValue(get_snap_pixel_radius())
        self._snap_pixel_spin.setToolTip("Pixel radius on screen within which a candidate point is snapped.")
        section_snapping.addRow("Pixel Radius:", self._snap_pixel_spin)

        self._snap_guide_enabled_check = QtWidgets.QCheckBox()
        self._snap_guide_enabled_check.setChecked(get_snap_guide_enabled())
        section_snapping.addRow("Show Snap Guides:", self._snap_guide_enabled_check)

        self._guide_line_color = tuple(get_snap_guide_line_color())
        self._guide_line_color_btn = QtWidgets.QPushButton()
        self._guide_line_color_btn.setToolTip("Guide line colour")
        self._paint_guide_line_swatch()
        self._guide_line_color_btn.clicked.connect(self._on_pick_guide_line_color)
        section_snapping.addRow("Guide Line Colour:", self._guide_line_color_btn)

        self._guide_line_width_spin = QtWidgets.QDoubleSpinBox()
        self._guide_line_width_spin.setRange(0.5, 10.0)
        self._guide_line_width_spin.setSingleStep(0.5)
        self._guide_line_width_spin.setDecimals(1)
        self._guide_line_width_spin.setSuffix(" px")
        self._guide_line_width_spin.setValue(get_snap_guide_line_width())
        section_snapping.addRow("Guide Line Width:", self._guide_line_width_spin)

        self._detent_color = tuple(get_snap_detent_color())
        self._detent_color_btn = QtWidgets.QPushButton()
        self._detent_color_btn.setToolTip("Detent dot colour")
        self._paint_detent_swatch()
        self._detent_color_btn.clicked.connect(self._on_pick_detent_color)
        section_snapping.addRow("Detent Colour:", self._detent_color_btn)

        self._detent_size_spin = QtWidgets.QSpinBox()
        self._detent_size_spin.setRange(0, 20)
        self._detent_size_spin.setSuffix(" px")
        self._detent_size_spin.setValue(get_snap_detent_size())
        self._detent_size_spin.setToolTip("0 = hide dots")
        section_snapping.addRow("Detent Size:", self._detent_size_spin)

        self._detent_interval_spin = QtWidgets.QSpinBox()
        self._detent_interval_spin.setRange(1, 1000)
        self._detent_interval_spin.setSuffix(" steps")
        self._detent_interval_spin.setValue(get_snap_detent_interval())
        self._detent_interval_spin.setToolTip(
            "Draw a guide dot every N snap steps. Snapping still uses every step; "
            "this only controls how often a landmark is drawn."
        )
        section_snapping.addRow("Detent Every:", self._detent_interval_spin)

        self._grid_color = tuple(get_snap_grid_color())
        self._grid_color_btn = QtWidgets.QPushButton()
        self._grid_color_btn.setToolTip("Grid line colour")
        self._paint_grid_swatch()
        self._grid_color_btn.clicked.connect(self._on_pick_grid_color)
        section_snapping.addRow("Grid Colour:", self._grid_color_btn)

        self._grid_line_width_spin = QtWidgets.QDoubleSpinBox()
        self._grid_line_width_spin.setRange(0.5, 10.0)
        self._grid_line_width_spin.setSingleStep(0.5)
        self._grid_line_width_spin.setDecimals(1)
        self._grid_line_width_spin.setSuffix(" px")
        self._grid_line_width_spin.setValue(get_snap_grid_line_width())
        section_snapping.addRow("Grid Line Width:", self._grid_line_width_spin)

        self._guide_extent_spin = QtWidgets.QSpinBox()
        self._guide_extent_spin.setRange(50, 2000)
        self._guide_extent_spin.setSuffix(" px")
        self._guide_extent_spin.setValue(get_snap_guide_extent_px())
        section_snapping.addRow("Guide Extent:", self._guide_extent_spin)

        self._grid_extent_spin = QtWidgets.QSpinBox()
        self._grid_extent_spin.setRange(1, 200)
        self._grid_extent_spin.setSuffix(" steps")
        self._grid_extent_spin.setValue(get_snap_grid_extent_steps())
        section_snapping.addRow("Grid Extent:", self._grid_extent_spin)

        self._snap_marker_size_spin = QtWidgets.QSpinBox()
        self._snap_marker_size_spin.setRange(2, 40)
        self._snap_marker_size_spin.setSuffix(" px")
        self._snap_marker_size_spin.setValue(get_snap_marker_size_px())
        section_snapping.addRow("Snap Marker Size:", self._snap_marker_size_spin)

        # ── Group 3: SDF Rendering and Meshing ──
        # Overall Rendering Quality combo box
        self._quality_combo = QtWidgets.QComboBox()
        self._quality_combo.addItem("Draft", 0)
        self._quality_combo.addItem("Balanced", 1)
        self._quality_combo.addItem("High", 2)
        self._quality_combo.addItem("Ultra", 3)
        idx = self._quality_combo.findData(get_render_quality())
        if idx >= 0:
            self._quality_combo.setCurrentIndex(idx)
        self._quality_combo.setToolTip(
            "Overall SDF rendering quality preset.\n"
            "Controls viewport downscaling during navigation and ray march step counts.\n"
            "Draft = fastest, Ultra = full resolution everywhere."
        )
        section_rendering.addRow("Rendering Quality:", self._quality_combo)

        # Project accuracy target (slicing / CAM)
        section_rendering.addRow("Model Tolerance (mm):", self._tol_spin)

        # GPU evaluation checkbox
        self._gpu_eval_check = QtWidgets.QCheckBox()
        self._gpu_eval_check.setChecked(get_gpu_field_eval())
        self._gpu_eval_check.setToolTip("Enable OpenGL compute-shader acceleration for SDF field evaluation and volume baking (when supported).")
        section_rendering.addRow("Enable GPU Evaluation:", self._gpu_eval_check)

        # Voxel Grid Resolution combo box
        self._voxel_res_combo = QtWidgets.QComboBox()
        self._voxel_res_combo.addItem("128³ (8 MB)", 128)
        self._voxel_res_combo.addItem("256³ (67 MB)", 256)
        self._voxel_res_combo.addItem("384³ (227 MB)", 384)
        idx = self._voxel_res_combo.findData(get_voxel_grid_resolution())
        if idx >= 0:
            self._voxel_res_combo.setCurrentIndex(idx)
        self._voxel_res_combo.setToolTip("Scene volume resolution along its longest axis (default 256).")
        section_rendering.addRow("Voxel Grid Resolution:", self._voxel_res_combo)

        # Warp Resolution combo box
        self._warp_res_combo = QtWidgets.QComboBox()
        self._warp_res_combo.addItem("16 (fast)", 16)
        self._warp_res_combo.addItem("32 (balanced)", 32)
        self._warp_res_combo.addItem("64 (smooth)", 64)
        idx = self._warp_res_combo.findData(get_sdf_warp_resolution())
        if idx >= 0:
            self._warp_res_combo.setCurrentIndex(idx)
        self._warp_res_combo.setToolTip("Resolution of the 3D deformation warp texture (points along longest axis, default 32).")
        section_rendering.addRow("Warp Resolution:", self._warp_res_combo)

        # Ray March cell size / resolution
        section_rendering.addRow("Ray March Resolution (mm):", self._rm_res_spin)

        # Max SDF Render Size spinbox
        self._mss_spin = QtWidgets.QDoubleSpinBox()
        self._mss_spin.setRange(10.0, 100000.0) # 10mm to 100m
        self._mss_spin.setValue(current_mss)
        self._mss_spin.setToolTip("Maximum allowed dimension for an individual SDF field (mm). Larger fields will be clipped during preview rendering.")
        section_rendering.addRow("Max SDF Render Size (mm):", self._mss_spin)

        # Near Clip Distance
        section_rendering.addRow("Near Clip Distance (mm):", self._near_clip_spin)

        # Cage Patch Type combo box
        self._cage_patch_combo = QtWidgets.QComboBox()
        self._cage_patch_combo.addItem("Bilinear Coons", 0)
        self._cage_patch_combo.addItem("Bicubic Coons", 1)
        self._cage_patch_combo.addItem("Gregory-Coons", 2)
        idx = self._cage_patch_combo.findData(get_cage_patch_type())
        if idx >= 0:
            self._cage_patch_combo.setCurrentIndex(idx)
        self._cage_patch_combo.setToolTip("Interpolation algorithm for quad faces in cage objects")
        section_rendering.addRow("Cage Patch Type:", self._cage_patch_combo)

        # Heightmap Resolution combo box
        self._hmap_res_combo = QtWidgets.QComboBox()
        self._hmap_res_combo.addItem("128x128", 128)
        self._hmap_res_combo.addItem("256x256", 256)
        self._hmap_res_combo.addItem("512x512", 512)
        idx = self._hmap_res_combo.findData(get_heightmap_resolution())
        if idx >= 0:
            self._hmap_res_combo.setCurrentIndex(idx)
        self._hmap_res_combo.setToolTip("Resolution of the baked heightmap texture for curved surface extrusions")
        section_rendering.addRow("Heightmap Resolution:", self._hmap_res_combo)



        # ── Group 4: System and Diagnostics ──
        # Max Bounds spinbox
        self._mb_spin = QtWidgets.QDoubleSpinBox()
        self._mb_spin.setRange(100.0, 1000000.0) # 100mm to 1km
        self._mb_spin.setValue(current_mb)
        section_system.addRow("Max Bounds (mm):", self._mb_spin)

        # Crash Logging
        from freecad.fields.core.fld_logger import get_enable_crash_log
        self._log_check = QtWidgets.QCheckBox()
        self._log_check.setChecked(get_enable_crash_log())
        self._log_check.setToolTip("Enable persistent crash logging to Fields.log")
        section_system.addRow("Enable Crash Logs:", self._log_check)

        # Performance Profiler
        from freecad.fields.core.objects.fld_object import get_perf_profiler_enabled
        self._perf_check = QtWidgets.QCheckBox()
        self._perf_check.setChecked(get_perf_profiler_enabled())
        self._perf_check.setToolTip("Enable detailed performance logging (mesh generation, "
                                     "SDF baking, and ray march render pass timings)")
        section_system.addRow("Enable Performance Profiler:", self._perf_check)

        # Debug Logging — master switch + per-category toggles
        from freecad.fields.core.fld_logger import (get_enable_debug_log, get_debug_category,
                                     DEBUG_CATEGORIES)
        self._debug_check = QtWidgets.QCheckBox()
        self._debug_check.setChecked(get_enable_debug_log())
        self._debug_check.setToolTip("Master switch for all debug-level logging. "
                                     "When off, no debug() output is printed regardless of categories.")
        section_system.addRow("Enable Debug Logging:", self._debug_check)

        # Per-category checkboxes (Render is very verbose and off by default)
        self._debug_cat_checks = {}
        _cat_labels = {
            "general": "General", "render": "Render", "cage": "Cage",
            "sdf": "SDF", "freecad.fields.tools": "Tools", "input": "Input",
        }
        for cat in DEBUG_CATEGORIES:
            chk = QtWidgets.QCheckBox()
            chk.setChecked(get_debug_category(cat))
            if cat == "render":
                chk.setToolTip("Verbose GPU ray-march / renderer debug output. "
                               "Off by default; enable only when diagnosing render issues.")
            else:
                chk.setToolTip(f"Emit '{cat}' category debug messages (requires master Debug Logging on).")
            # Gray out category rows when the master switch is off
            chk.setEnabled(self._debug_check.isChecked())
            self._debug_cat_checks[cat] = chk
            section_system.addRow(f"  Debug – {_cat_labels.get(cat, cat.title())}:", chk)

        def _sync_debug_cats(state):
            on = self._debug_check.isChecked()
            for c in self._debug_cat_checks.values():
                c.setEnabled(on)
        self._debug_check.stateChanged.connect(_sync_debug_cats)

        # Dialog Button Box at the bottom
        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        main_layout.addWidget(btn_box)

    def _paint_hatch_swatch(self):
        """Show the current hatch colour as the button's background."""
        r, g, b = self._hatch_color
        self._hatch_color_btn.setStyleSheet(
            f"background-color: rgb({int(r * 255)}, {int(g * 255)}, {int(b * 255)});"
            " min-height: 18px;")

    def _on_pick_hatch_color(self):
        r, g, b = self._hatch_color
        initial = QtGui.QColor(int(r * 255), int(g * 255), int(b * 255))
        picked = QtWidgets.QColorDialog.getColor(initial, self, "Crosshatch Colour")
        if picked.isValid():
            self._hatch_color = (picked.redF(), picked.greenF(), picked.blueF())
            self._paint_hatch_swatch()

    def _paint_guide_line_swatch(self):
        r, g, b = self._guide_line_color
        self._guide_line_color_btn.setStyleSheet(
            f"background-color: rgb({int(r * 255)}, {int(g * 255)}, {int(b * 255)});"
            " min-height: 18px;")

    def _on_pick_guide_line_color(self):
        r, g, b = self._guide_line_color
        initial = QtGui.QColor(int(r * 255), int(g * 255), int(b * 255))
        picked = QtWidgets.QColorDialog.getColor(initial, self, "Guide Line Colour")
        if picked.isValid():
            self._guide_line_color = (picked.redF(), picked.greenF(), picked.blueF())
            self._paint_guide_line_swatch()

    def _paint_detent_swatch(self):
        r, g, b = self._detent_color
        self._detent_color_btn.setStyleSheet(
            f"background-color: rgb({int(r * 255)}, {int(g * 255)}, {int(b * 255)});"
            " min-height: 18px;")

    def _on_pick_detent_color(self):
        r, g, b = self._detent_color
        initial = QtGui.QColor(int(r * 255), int(g * 255), int(b * 255))
        picked = QtWidgets.QColorDialog.getColor(initial, self, "Detent Colour")
        if picked.isValid():
            self._detent_color = (picked.redF(), picked.greenF(), picked.blueF())
            self._paint_detent_swatch()

    def _paint_grid_swatch(self):
        r, g, b = self._grid_color
        self._grid_color_btn.setStyleSheet(
            f"background-color: rgb({int(r * 255)}, {int(g * 255)}, {int(b * 255)});"
            " min-height: 18px;")

    def _on_pick_grid_color(self):
        r, g, b = self._grid_color
        initial = QtGui.QColor(int(r * 255), int(g * 255), int(b * 255))
        picked = QtWidgets.QColorDialog.getColor(initial, self, "Grid Colour")
        if picked.isValid():
            self._grid_color = (picked.redF(), picked.greenF(), picked.blueF())
            self._paint_grid_swatch()

    def _validate_keymap_conflicts(self):
        """Check for keymap conflicts among pending table values and highlight conflicting cells."""
        from freecad.fields.core.input import fld_keymap
        # Build pending map by context: (ctx, seq_int) -> list of (action, edit_widget)
        by_ctx_seq = {}
        for action, edit in getattr(self, "_keymap_edits", {}).items():
            ctx = fld_keymap.context_of(action)
            seq = edit.keySequence()
            if seq.isEmpty():
                continue
            seq_int = fld_keymap._key_combo_int(seq[0])
            by_ctx_seq.setdefault((ctx, seq_int), []).append((action, edit))

        conflicts = []
        conflicting_edits = set()
        for (ctx, seq_int), entries in by_ctx_seq.items():
            if len(entries) > 1:
                actions = [a for a, e in entries]
                conflicts.append((entries[0][1].keySequence().toString(), actions))
                for a, e in entries:
                    conflicting_edits.add(e)

        # Update styling
        for action, edit in getattr(self, "_keymap_edits", {}).items():
            if edit in conflicting_edits:
                edit.setStyleSheet("border: 1px solid red; background: rgba(255, 0, 0, 40);")
            else:
                edit.setStyleSheet("")

        return conflicts

    def _on_accept(self):
        from freecad.fields.core.objects.fld_object import (set_show_wireframe, set_line_width, set_point_size,
                                    set_picking_radius, 
                                    set_max_bounds, set_perf_profiler_enabled,
                                    refresh_all_fld_objects,
                                     set_near_clip_distance, apply_near_clip_override,
                                     set_interactive_throttle_interval, set_ray_march_cell_size,
                                     set_max_sdf_render_size, set_sdf_selection_outline_size,
                                     set_hatch_size, set_hatch_color, set_hatch_strength,
                                     set_drag_tick_rate, set_render_quality,
                                     set_show_cage_curves, set_cage_patch_type, set_simplify_cage_drag,
                                     set_heightmap_resolution, set_gpu_field_eval,
                                     set_sdf_warp_resolution, set_voxel_grid_resolution,
                                     set_model_tolerance)
        from freecad.fields.core.fld_logger import set_enable_crash_log
        wire = self._wire_check.isChecked()
        show_cage_curves = self._cage_curves_check.isChecked()
        simplify_cage_drag = self._simplify_cage_drag_check.isChecked()
        lw = self._lw_spin.value()
        ps = self._ps_spin.value()
        pr = self._pr_spin.value()
        mb = self._mb_spin.value()
        sos = self._sos_spin.value()
        dtr = self._drag_tick_spin.value()

        set_show_wireframe(wire)
        set_show_cage_curves(show_cage_curves)
        set_simplify_cage_drag(simplify_cage_drag)
        set_line_width(lw)
        set_point_size(ps)
        set_picking_radius(pr)
        set_max_bounds(mb)
        set_max_sdf_render_size(self._mss_spin.value())
        set_perf_profiler_enabled(self._perf_check.isChecked())
        set_interactive_throttle_interval(self._throttle_spin.value())
        set_drag_tick_rate(dtr)

        
        set_enable_crash_log(self._log_check.isChecked())

        # Debug logging: master + per-category, then refresh the cached switches
        from freecad.fields.core.fld_logger import (set_enable_debug_log, set_debug_category,
                                     refresh_debug_settings)
        set_enable_debug_log(self._debug_check.isChecked())
        for cat, chk in self._debug_cat_checks.items():
            set_debug_category(cat, chk.isChecked())
        refresh_debug_settings()

        set_near_clip_distance(self._near_clip_spin.value())
        apply_near_clip_override()

        set_ray_march_cell_size(self._rm_res_spin.value())
        set_model_tolerance(self._tol_spin.value())
        set_sdf_selection_outline_size(sos)
        set_hatch_size(self._hatch_size_spin.value())
        set_hatch_color(self._hatch_color)
        set_hatch_strength(self._hatch_strength_spin.value())
        set_render_quality(self._quality_combo.itemData(self._quality_combo.currentIndex()))
        set_cage_patch_type(self._cage_patch_combo.itemData(self._cage_patch_combo.currentIndex()))
        set_heightmap_resolution(self._hmap_res_combo.itemData(self._hmap_res_combo.currentIndex()))

        set_gpu_field_eval(self._gpu_eval_check.isChecked())
        set_voxel_grid_resolution(self._voxel_res_combo.itemData(self._voxel_res_combo.currentIndex()))
        set_sdf_warp_resolution(self._warp_res_combo.itemData(self._warp_res_combo.currentIndex()))

        # Snapping preferences
        from freecad.fields.core.fld_settings import (
            set_snap_enabled, set_snap_grid_step, set_snap_angle_step,
            set_snap_vertex_enabled, set_snap_pixel_radius,
            set_snap_guide_enabled, set_snap_guide_line_color, set_snap_guide_line_width,
            set_snap_detent_color, set_snap_detent_size, set_snap_detent_interval,
            set_snap_grid_color, set_snap_grid_line_width,
            set_snap_guide_extent_px, set_snap_grid_extent_steps, set_snap_marker_size_px,
            set_control_scheme, set_snap_during_direct_drag, set_snap_invert_modifier,
            set_modal_requires_selection
        )
        from freecad.fields.core.input import fld_keymap

        # Check keymap conflicts before saving
        conflicts = self._validate_keymap_conflicts()
        if conflicts:
            msg = "Cannot save keymap because of conflicts in the same context:\n\n"
            for b, actions in conflicts:
                msg += f"• Binding '{b}' is assigned to: {', '.join(actions)}\n"
            msg += "\nPlease resolve these conflicts before saving."
            QtWidgets.QMessageBox.warning(self, "Keymap Conflicts", msg)
            return

        # Warn if snap invert modifier is shift or alt
        invert_mod = self._snap_invert_combo.itemData(self._snap_invert_combo.currentIndex())
        if invert_mod in ("shift", "alt"):
            QtWidgets.QMessageBox.information(
                self, "Snap Modifier Notice",
                f"Note: Setting the snap invert modifier to {invert_mod.capitalize()} means holding {invert_mod.capitalize()} "
                f"will also toggle plane locks / multi-handle rotations while toggling snap."
            )

        set_control_scheme(self._control_scheme_combo.itemData(self._control_scheme_combo.currentIndex()))
        set_snap_during_direct_drag(self._snap_direct_drag_check.isChecked())
        set_snap_invert_modifier(invert_mod)
        set_modal_requires_selection(self._modal_req_sel_check.isChecked())

        # Save keymap bindings
        for action, edit in getattr(self, "_keymap_edits", {}).items():
            seq_str = edit.keySequence().toString()
            fld_keymap.set_binding(action, seq_str)

        # Update Fields_Translate command accelerator if global.translate changed
        translate_seq = fld_keymap.binding_for("global.translate")
        import FreeCADGui
        cmd = FreeCADGui.getCommand("Fields_Translate")
        if cmd and hasattr(cmd, "accel"):
            cmd.accel = translate_seq

        set_snap_enabled(self._snap_enabled_check.isChecked())
        set_snap_grid_step(self._snap_grid_spin.value())
        set_snap_angle_step(self._snap_angle_spin.value())
        set_snap_vertex_enabled(self._snap_vertex_check.isChecked())
        set_snap_pixel_radius(self._snap_pixel_spin.value())

        set_snap_guide_enabled(self._snap_guide_enabled_check.isChecked())
        set_snap_guide_line_color(self._guide_line_color)
        set_snap_guide_line_width(self._guide_line_width_spin.value())
        set_snap_detent_color(self._detent_color)
        set_snap_detent_size(self._detent_size_spin.value())
        set_snap_detent_interval(self._detent_interval_spin.value())
        set_snap_grid_color(self._grid_color)
        set_snap_grid_line_width(self._grid_line_width_spin.value())
        set_snap_guide_extent_px(self._guide_extent_spin.value())
        set_snap_grid_extent_steps(self._grid_extent_spin.value())
        set_snap_marker_size_px(self._snap_marker_size_spin.value())


        # Apply to all existing objects
        refresh_all_fld_objects()

        fld_logger.info(f"Fields Settings: wire={wire}, lw={lw}, ps={ps}, pr={pr}, mb={mb}")
        self.accept()



