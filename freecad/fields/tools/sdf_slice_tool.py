# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/sdf_slice_tool.py

Interactive tool for slicing an SDF object on a plane to produce
cross-section Fields curve objects (Catmull-Rom curve fitting, live preview,
and the task panel UI). See commands/cmd_sdf_slice.py for the thin
command-registration wrapper (CommandFldSdfSlice).
"""
import FreeCAD
import FreeCADGui
from PySide import QtCore, QtWidgets
from freecad.fields.core import fld_logger
from pivy import coin
from freecad.fields.tools.fld_sdf_tool_base import FldSdfToolBase
from freecad.fields.tools.fld_base import DragTimerMixin
import math

from freecad.fields.core.sdf.sdf_slicer import _compute_bezier_handles as _compute_catmull_rom_handles

# Ceiling on one array. Slicing a 50 mm part at a 0.05 mm machining pitch is a
# thousand planes and that is a legitimate ask, so the limit is high; it exists
# because "spacing over length" turns a mistyped spacing into an unbounded number
# of document objects, and the panel says when it has capped rather than
# silently producing something other than what was asked for.
_MAX_ARRAY_SLICES = 2000


def resolve_array(length, by_count, count, spacing, limit=_MAX_ARRAY_SLICES):
    """(count, spacing, note) for one array of slice planes.

    Free of Qt on purpose. The panel that reads these numbers off spin boxes
    cannot be constructed without a GUI, and arithmetic that can only be
    exercised by hand in a running FreeCAD is arithmetic nobody checks.

    Both modes describe the same array from opposite ends. `by_count` fixes how
    many planes there are and divides the length between them, so the first sits
    on the slice plane and the last on the far end -- N planes make N-1 gaps, and
    using N there is the classic way to come up one gap short. Otherwise the
    spacing is fixed and the count is however many whole steps fit, plus the
    plane at zero.

    Args:
        length:   how far the array runs from the slice plane, in mm.
        by_count: True for count-over-length, False for spacing-over-length.
        count:    requested plane count, used only when `by_count`.
        spacing:  requested distance between planes, used only when not.
        limit:    ceiling on the returned count.

    Returns:
        (count, spacing, note) -- `note` is empty unless the count was capped.
    """
    length = max(float(length), 0.0)
    if by_count:
        count = max(2, int(count))
        spacing = (length / float(count - 1)) if length > 0.0 else 0.0
    else:
        spacing = max(float(spacing), 1e-6)
        count = max(2, int(math.floor(length / spacing + 1e-9)) + 1)

    note = ""
    if count > limit:
        note = (f"{count} slices exceeds the {limit}-slice limit, capping there. "
                f"Raise the spacing or shorten the length.")
        count = limit
    return count, spacing, note



def curve_params_from_slice(bspline):
    """The Fields curve object parameters for one contour returned by `trace_sdf`.

    do_slice does not store the returned BSpline -- it stores these parameters and
    the renderer redraws the Bezier chain from them, so this reconstruction *is* the
    curve the user sees. Kept as a free function so a test can measure it without a
    document or a task panel.

    `Closed` comes from the wrapper's `_is_closed`, never from the curve. The BSpline
    `_bezier_chain_to_bspline` builds is clamped, so OCC answers `isPeriodic()` False
    for every contour the slicer traces; reading it reopened every closed loop.

    Handles come from the wrapper's `_handle_in` / `_handle_out` for the same class of
    reason. Their lengths are a least-squares fit to the dense polyline
    (`sdf_slicer._solve_bezier_handles`), and recomputing them here from points and
    types alone would put chord/3 back -- the local rule the fit exists to replace --
    so the curve the user edits would not be the curve the slicer converged on. The
    chord/3 reconstruction stays as the fallback for a curve that carries no handles.
    """
    if hasattr(bspline, "_interpolation_points"):
        fit_pts = [FreeCAD.Vector(p) for p in bspline._interpolation_points]
    else:
        fit_pts = [FreeCAD.Vector(p) for p in bspline.getPoles()]
    is_closed = bool(getattr(bspline, "_is_closed", False))
    handle_types = list(getattr(bspline, "_handle_types", []) or [])

    solved_in = list(getattr(bspline, "_handle_in", []) or [])
    solved_out = list(getattr(bspline, "_handle_out", []) or [])
    if len(solved_in) == len(fit_pts) and len(solved_out) == len(fit_pts):
        h_in = [FreeCAD.Vector(p) for p in solved_in]
        h_out = [FreeCAD.Vector(p) for p in solved_out]
    else:
        h_in, h_out = _compute_catmull_rom_handles(fit_pts, is_closed, handle_types)

    # Expand per-point handle types to the flat 2*n HandleTypes list, and mark
    # Vector-handle points as Split (PointType=1) so the curve editor knows those
    # handles are intentionally non-collinear.
    ht_flat = []
    pt_types = []
    for j in range(len(fit_pts)):
        ht = handle_types[j] if j < len(handle_types) else 0
        ht_flat.extend([ht, ht])       # same type for in/out handle
        pt_types.append(1 if ht == 1 else 0)  # Split=1 at corners

    return {
        "Points": fit_pts,
        "HandleIn": h_in,
        "HandleOut": h_out,
        "Closed": is_closed,
        "HandleTypes": ht_flat,
        "PointTypes": pt_types,
    }


def build_slice_group_members(
    field, origin, normal, count=1, spacing=0.0,
    tolerance=0.1, max_control_points=200, angle_tolerance_deg=5.0
):
    """(c_idx, list[dict]) for one array of slice planes on `field`.

    Free of Qt and document dependencies so it can be verified in headless tests.

    Args:
        field: SdfField object to slice.
        origin: Base plane origin (FreeCAD.Vector).
        normal: Slice plane normal vector (FreeCAD.Vector).
        count: Number of slice planes.
        spacing: Distance between slice planes along normal, in mm.
        tolerance: Contour tolerance in mm.
        max_control_points: Maximum control points per fitted curve.
        angle_tolerance_deg: Corner detection angle threshold in degrees.

    Returns:
        list of (c_idx, [curve_dict, ...]) tuples, where each curve_dict
        contains the curve parameters and metadata (_achieved_deviation,
        _tolerance_met, etc.).
    """
    from freecad.fields.core.sdf.sdf_slicer import trace_sdf

    members = []
    for c_idx in range(count):
        current_origin = origin + normal * (c_idx * spacing)
        curves = trace_sdf(
            field, current_origin, normal,
            tolerance=tolerance,
            max_control_points=max_control_points,
            angle_tolerance_deg=angle_tolerance_deg
        )
        plane_curves = []
        for i, bspline in enumerate(curves):
            params = curve_params_from_slice(bspline)
            params["_achieved_deviation"] = getattr(bspline, "_achieved_deviation", None)
            params["_tolerance_met"] = getattr(bspline, "_tolerance_met", None)
            plane_curves.append(params)
        members.append((c_idx, plane_curves))
    return members


def _create_debug_slab_bool(doc, group, src_obj, field, origin, normal, extent, c_idx):
    """Create a 1mm SDF slab intersection for visual debug comparison.

    The slab is a pure in-memory SdfBoxField — no document object — so it never
    appears as a stray visible shape in the renderer.  Only the intersection result
    is added to the document.
    """
    from freecad.fields.core.sdf.sdf.box import SdfBoxField
    from freecad.fields.core.sdf.sdf_composer import IntersectionField
    from freecad.fields.core.objects.fld_object import create_fld_object

    # Build a placement that orients local Z to the slice normal
    z_axis = FreeCAD.Vector(0, 0, 1)
    if (normal + z_axis).Length < 1e-6:
        rot = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 180)
    else:
        rot = FreeCAD.Rotation(z_axis, normal)
    slab_placement = FreeCAD.Placement(origin, rot)

    # 0.1mm thick in local Z, large enough to cover the whole shape in XY
    slab_size = FreeCAD.Vector(extent * 2, extent * 2, 0.1)
    slab_field = SdfBoxField(FreeCAD.Vector(0, 0, 0), slab_size, placement=slab_placement)

    # Intersection field built entirely in memory — no slab document object needed
    src_proxy = getattr(src_obj, "Proxy", None)
    src_field = ((src_proxy.get_sdf_field(src_obj) if hasattr(src_proxy, "get_sdf_field")
                  else getattr(src_proxy, "SdfField", None)) if src_proxy else None) or field
    intersect_field = IntersectionField(src_field, slab_field)

    # Only the result object goes in the document / group
    result_label = f"{src_obj.Label}_DebugBool_{c_idx}"
    result_obj = create_fld_object(result_label, "sdf")
    result_obj.Proxy.SdfField = intersect_field
    result_obj.Group = "Additive"
    if not hasattr(result_obj, "SdfType"):
        result_obj.addProperty("App::PropertyString", "SdfType", "Sdf", "SDF primitive type")
    result_obj.SdfType = "boolean"
    if not hasattr(result_obj, "DebugAlpha"):
        result_obj.addProperty("App::PropertyFloat", "DebugAlpha", "Debug", "Visual transparency (0=invisible, 1=opaque)")
    result_obj.DebugAlpha = 0.4

    group.addObject(result_obj)


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
        fld_logger.debug("SDFSlice: Grid geometry initialized.")

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
            fld_logger.debug("SDFSlice: Preview cleaned up.")


class SdfSliceTaskPanel:
    """Task panel for adjusting the slicing plane."""

    def __init__(self, tool):
        self.tool = tool
        self.obj = tool.obj
        self.field = tool.field
        self.resolution = tool.resolution

        self.form = QtWidgets.QWidget()
        self.setup_ui()
        # Removed premature preview update to avoid None panel reference
        # self.update_preview()

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self.form)
        form_layout = QtWidgets.QFormLayout()

        # Offset slider/spinbox combined with axis alignment buttons
        offset_layout = QtWidgets.QHBoxLayout()
        self.offset_spin = QtWidgets.QDoubleSpinBox()
        self.offset_spin.setRange(-1000, 1000)
        self.offset_spin.setValue(0.0)
        self.offset_spin.setSuffix(" mm")
        offset_layout.addWidget(self.offset_spin)

        self.btn_x = QtWidgets.QPushButton("X")
        self.btn_y = QtWidgets.QPushButton("Y")
        self.btn_z = QtWidgets.QPushButton("Z")
        self.btn_x.setFixedWidth(28)
        self.btn_y.setFixedWidth(28)
        self.btn_z.setFixedWidth(28)
        self.btn_x.setToolTip("Align slice normal to X axis")
        self.btn_y.setToolTip("Align slice normal to Y axis")
        self.btn_z.setToolTip("Align slice normal to Z axis")
        self.btn_x.clicked.connect(lambda: self.set_axis(FreeCAD.Vector(1, 0, 0)))
        self.btn_y.clicked.connect(lambda: self.set_axis(FreeCAD.Vector(0, 1, 0)))
        self.btn_z.clicked.connect(lambda: self.set_axis(FreeCAD.Vector(0, 0, 1)))
        offset_layout.addWidget(self.btn_x)
        offset_layout.addWidget(self.btn_y)
        offset_layout.addWidget(self.btn_z)
        form_layout.addRow("Offset:", offset_layout)

        # Base tolerance: absolute accuracy target, scaled internally by shape size.
        # Seeded from the global Model Tolerance (Fields Settings), overridable per run.
        from freecad.fields.core.objects.fld_object import get_model_tolerance
        self.tolerance_spin = QtWidgets.QDoubleSpinBox()
        self.tolerance_spin.setRange(0.001, 10.0)
        self.tolerance_spin.setValue(get_model_tolerance())
        self.tolerance_spin.setDecimals(3)
        self.tolerance_spin.setSuffix(" mm")
        form_layout.addRow("Tolerance:", self.tolerance_spin)

        self.max_pts_spin = QtWidgets.QSpinBox()
        self.max_pts_spin.setRange(4, 1000)
        self.max_pts_spin.setValue(200)
        form_layout.addRow("Max Points:", self.max_pts_spin)

        self.angle_tol_spin = QtWidgets.QDoubleSpinBox()
        self.angle_tol_spin.setRange(0.0, 45.0)
        self.angle_tol_spin.setValue(5.0)
        self.angle_tol_spin.setDecimals(1)
        self.angle_tol_spin.setSuffix("°")
        form_layout.addRow("Angle Tol:", self.angle_tol_spin)

        self.offset_spin.valueChanged.connect(self.update_preview)

        layout.addLayout(form_layout)

        # Array group (collapsible via checkbox)
        self.array_group = QtWidgets.QGroupBox("Array")
        self.array_group.setCheckable(True)
        self.array_group.setChecked(False)
        array_layout = QtWidgets.QFormLayout()

        # Two ways to say the same thing, and which one is natural depends
        # entirely on the job: laying out inspection sections is "give me eight
        # of them across this much", and generating machining layers is "every
        # 0.05 mm, all the way through". Whichever is chosen, the other value is
        # derived and shown rather than hidden, so the two never disagree.
        self.array_mode = QtWidgets.QComboBox()
        self.array_mode.addItems(["Count over length", "Spacing over length"])
        array_layout.addRow("Space by:", self.array_mode)

        self.count_spin = QtWidgets.QSpinBox()
        self.count_spin.setRange(2, _MAX_ARRAY_SLICES)
        self.count_spin.setValue(5)
        array_layout.addRow("Count:", self.count_spin)

        self.spacing_spin = QtWidgets.QDoubleSpinBox()
        self.spacing_spin.setRange(0.001, 500)
        self.spacing_spin.setDecimals(3)
        self.spacing_spin.setValue(1.0)
        self.spacing_spin.setSuffix(" mm")
        array_layout.addRow("Spacing:", self.spacing_spin)

        self.length_spin = QtWidgets.QDoubleSpinBox()
        self.length_spin.setRange(0.001, 10000.0)
        self.length_spin.setDecimals(3)
        self.length_spin.setValue(50.0)
        self.length_spin.setSuffix(" mm")
        self.length_spin.setToolTip(
            "How far the array runs from the slice plane, along the normal.")
        array_layout.addRow("Length:", self.length_spin)

        self.span_part_chk = QtWidgets.QCheckBox("Whole part")
        self.span_part_chk.setChecked(True)
        self.span_part_chk.setToolTip(
            "Take the length from the plane to the far side of the shape's "
            "bounding box, so the array covers everything ahead of it.")
        array_layout.addRow("", self.span_part_chk)

        self.array_summary = QtWidgets.QLabel("")
        self.array_summary.setWordWrap(True)
        array_layout.addRow("", self.array_summary)

        self.array_group.setLayout(array_layout)
        layout.addWidget(self.array_group)

        self.array_group.toggled.connect(self.update_preview)
        self.array_mode.currentIndexChanged.connect(self.update_preview)
        self.count_spin.valueChanged.connect(self.update_preview)
        self.spacing_spin.valueChanged.connect(self.update_preview)
        self.length_spin.valueChanged.connect(self.update_preview)
        self.span_part_chk.toggled.connect(self.update_preview)
        self._sync_array_fields()

        # Debug group (collapsible via checkbox, collapsed by default)
        self.debug_group = QtWidgets.QGroupBox("Debug")
        self.debug_group.setCheckable(True)
        self.debug_group.setChecked(False)
        debug_layout = QtWidgets.QFormLayout()

        self.debug_bool_chk = QtWidgets.QCheckBox("Debug SDF Bool")
        self.debug_bool_chk.setChecked(False)
        debug_layout.addRow(self.debug_bool_chk)

        # A B-Rep body straight off the field's zero set, with no curve fitting
        # in the path -- so a wrong slice can be blamed on the extraction or on
        # the fit, instead of on both at once.
        self.debug_body_chk = QtWidgets.QCheckBox("Debug Slice Body")
        self.debug_body_chk.setChecked(False)
        self.debug_body_chk.setToolTip(
            "Add a Part solid built directly from the SDF on this plane "
            "(marching squares, snapped, no curve fit).\n"
            "Compare it against the fitted curves to see which one is wrong.")
        debug_layout.addRow(self.debug_body_chk)

        self.body_thick_spin = QtWidgets.QDoubleSpinBox()
        self.body_thick_spin.setRange(0.0, 10.0)
        self.body_thick_spin.setValue(0.1)
        self.body_thick_spin.setDecimals(3)
        self.body_thick_spin.setSuffix(" mm")
        self.body_thick_spin.setToolTip(
            "Slab thickness for the debug body. 0 gives flat faces; above 0 the "
            "outline is the silhouette of the whole layer, so a feature thinner "
            "than the layer cannot fall between sample planes and vanish.")
        debug_layout.addRow("Body Thickness:", self.body_thick_spin)

        self.debug_group.setLayout(debug_layout)
        layout.addWidget(self.debug_group)

        self.debug_body_chk.toggled.connect(self._sync_debug_fields)
        self.debug_group.toggled.connect(lambda on: self._sync_debug_fields() if on else None)
        self._sync_debug_fields()

        # Slice button
        self.btn_slice = QtWidgets.QPushButton("Slice Now")
        self.btn_slice.clicked.connect(self.do_slice)
        layout.addWidget(self.btn_slice)

        layout.addStretch()
        fld_logger.debug("SDFSlice: UI setup complete.")

    def _sync_debug_fields(self):
        """Enable body_thick_spin only when debug_body_chk is checked."""
        self.body_thick_spin.setEnabled(self.debug_body_chk.isChecked())

    def _sync_array_fields(self):
        """Grey out whichever of count/spacing/length this mode derives."""
        by_count = (self.array_mode.currentIndex() == 0)
        self.count_spin.setEnabled(by_count)
        self.spacing_spin.setEnabled(not by_count)
        self.length_spin.setEnabled(not self.span_part_chk.isChecked())

    def _span_to_far_side(self, origin):
        """Distance from `origin` to the far face of the shape's bounding box.

        Along the slice normal, so an array of this length starting at the plane
        reaches everything in front of it and stops. Falls back to the whole
        bounding-box diagonal when the plane is already past the shape or the
        field cannot say how big it is -- a length that is too long only costs
        empty planes, and those are skipped.
        """
        try:
            bb_min, bb_max = self.field.bounding_box()
        except Exception as e:
            fld_logger.debug(f"SDFSlice: no bounding box for array span: {e}")
            return None
        n = self.tool.base_normal
        corners = [FreeCAD.Vector(x, y, z)
                   for x in (bb_min.x, bb_max.x)
                   for y in (bb_min.y, bb_max.y)
                   for z in (bb_min.z, bb_max.z)]
        base = origin.dot(n)
        reach = max(c.dot(n) for c in corners) - base
        if reach > 1e-6:
            return reach
        return (bb_max - bb_min).Length or None

    def array_layout(self, origin):
        """(count, spacing, note) for the current settings.

        The single place either half of this panel is allowed to work it out.
        `update_preview` and `do_slice` used to read the widgets separately, which
        is one copy of the arithmetic too many the moment there is arithmetic.
        """
        if not self.array_group.isChecked():
            return 1, 0.0, ""

        length = self.length_spin.value()
        note_len = ""
        if self.span_part_chk.isChecked():
            reach = self._span_to_far_side(origin)
            if reach:
                length = reach
                note_len = " (whole part)"

        count, spacing, note = resolve_array(
            length, self.array_mode.currentIndex() == 0,
            self.count_spin.value(), self.spacing_spin.value())

        summary = (f"{count} slices, {spacing:.3f} mm apart, over "
                   f"{spacing * (count - 1):.3f} mm{note_len}.")
        self.array_summary.setText(summary + (("  " + note) if note else ""))
        return count, spacing, note

    def set_axis(self, normal):
        self.tool.base_normal = normal
        self.offset_spin.blockSignals(True)
        self.offset_spin.setValue(0.0)
        self.offset_spin.blockSignals(False)
        self.update_preview()

    def update_preview(self):
        try:
            self._sync_array_fields()
            self.tool.update_preview()
        except Exception as e:
            fld_logger.debug(f"SDFSlice: Preview update failed: {e}")

    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.Close

    def reject(self):
        self.tool.cancel()
        return True

    def accept(self):
        self.tool._dialog_open = False
        self.tool.finish()
        return True

    def do_slice(self):
        try:
            fld_logger.debug("SDFSlice: Starting slice operation.")
            from freecad.fields.core.objects.fld_object import (
                create_fld_object, apply_display_tolerance)

            offset_val = self.offset_spin.value()
            tolerance = self.tolerance_spin.value()
            origin = self.tool.base_origin + self.tool.base_normal * offset_val
            count, spacing, array_note = self.array_layout(origin)
            if array_note:
                fld_logger.warn(f"SDFSlice: {array_note}")

            doc = FreeCAD.activeDocument()
            total_created = 0
            debug_bool = self.debug_group.isChecked() and self.debug_bool_chk.isChecked()
            debug_body = self.debug_group.isChecked() and self.debug_body_chk.isChecked()
            body_thickness = self.body_thick_spin.value()
            curve_summaries = []
            pending_group_members = []

            try:
                bb_min, bb_max = self.field.bounding_box()
                slab_extent = (bb_max - bb_min).Length
            except Exception:
                slab_extent = 200.0

            angle_tol = self.angle_tol_spin.value()
            max_pts = self.max_pts_spin.value()

            slice_members = build_slice_group_members(
                self.field, origin, self.tool.base_normal,
                count=count, spacing=spacing, tolerance=tolerance,
                max_control_points=max_pts, angle_tolerance_deg=angle_tol
            )

            has_curves = any(curves for _, curves in slice_members)
            if has_curves or debug_body:
                group_name = f"{self.obj.Label}_Slice"
                group = doc.addObject("App::DocumentObjectGroup", group_name)
                group.Label = group_name

                for c_idx, plane_curves in slice_members:
                    current_origin = origin + self.tool.base_normal * (c_idx * spacing)

                    # A plane the tracer found nothing on is exactly the plane worth
                    # building a debug body for, so this is not gated on `plane_curves`.
                    if not plane_curves and not debug_body:
                        continue

                    if debug_body:
                        try:
                            from freecad.fields.core.sdf.sdf_slice_body import (
                                create_slice_body)
                            body = create_slice_body(
                                doc, f"{self.obj.Label}_RawSlice_{c_idx}", self.field,
                                current_origin, self.tool.base_normal,
                                tolerance=tolerance, thickness=body_thickness)
                            if body is not None:
                                pending_group_members.append((group, body))
                        except Exception as exc:
                            fld_logger.exception(f"SDFSlice: debug body failed: {exc}")

                    for i, params in enumerate(plane_curves):
                        name = f"{self.obj.Label}_Slice_{c_idx}_{i}"
                        curve_obj = create_fld_object(name, "curve", params=params)
                        pending_group_members.append((group, curve_obj))
                        total_created += 1

                        n_vector = sum(1 for ht in params["PointTypes"] if ht == 1)
                        achieved_dev = params.get("_achieved_deviation")
                        tol_met = params.get("_tolerance_met")
                        curve_summaries.append((name, len(params["Points"]), n_vector, achieved_dev, tol_met))

                    if debug_bool:
                        QtCore.QTimer.singleShot(0, lambda d=doc, g=group, o=self.obj, f=self.field,
                                                  org=current_origin, nrm=self.tool.base_normal,
                                                  ext=slab_extent, idx=c_idx:
                                                  _create_debug_slab_bool(d, g, o, f, org, nrm, ext, idx))

                def _deferred_finalize(d=doc, members=pending_group_members, tol=tolerance):
                    for grp, curve_obj in members:
                        grp.addObject(curve_obj)
                    d.recompute()
                    # After the recompute, not before: the bounding box the display
                    # tolerance is relative to does not exist until the Shape does.
                    for _grp, curve_obj in members:
                        apply_display_tolerance(curve_obj, tol)
                QtCore.QTimer.singleShot(0, _deferred_finalize)
            else:
                fld_logger.warn("SDFSlice: No contours found.")

            sdf_cell = getattr(self.tool, 'resolution', None)
            tol_str = f"{tolerance:.3f}mm"
            cell_str = f"{sdf_cell:.3f}mm" if sdf_cell is not None else "unknown"
            fld_logger.info(f"SDFSlice: {total_created} curve(s) | tolerance {tol_str} | SDF cell {cell_str}")
            for cname, n_pts, n_vec, ach_dev, tol_met in curve_summaries:
                if ach_dev is not None:
                    if tol_met is False:
                        fld_logger.warn(f"  {cname}: {n_pts} pts, {ach_dev:.3f} mm — tolerance {tol_str} NOT met (raise Max Points)")
                    else:
                        fld_logger.info(f"  {cname}: {n_pts} pts, {ach_dev:.3f} mm — tolerance {tol_str} met  corners={n_vec}  smooth={n_pts - n_vec}")
                else:
                    fld_logger.info(f"  {cname}: {n_pts} pts  corners={n_vec}  smooth={n_pts - n_vec}")
            if sdf_cell is not None and sdf_cell > tolerance:
                fld_logger.warn(f"SDFSlice: SDF cell ({cell_str}) > tolerance ({tol_str}) — gradient resolution may limit curve accuracy")

        except Exception as e:
            fld_logger.exception(f"SDFSlice: Error: {e}")


class SdfSliceTool(FldSdfToolBase, DragTimerMixin):
    """Interactive tool for slicing an SDF field with a transform gizmo."""

    def get_command_id(self):
        return "Fields_SDFSlice"

    def __init__(self):
        super().__init__()
        self.obj = None
        self.field = None
        self.base_origin = None
        self.base_normal = None
        self.resolution = 0.1
        self._gizmo = None

        self.points_root = coin.SoSeparator()
        if self.view and self.view.getSceneGraph():
            self.view.getSceneGraph().addChild(self.points_root)

    def _get_sdf_obj(self):
        return self.obj

    def start_slicing(self, obj, field, origin, normal):
        self.obj = obj
        self.field = field
        self.base_origin = origin
        self.base_normal = normal
        self._is_editing = True

        # Setup initial coordinate axes
        z_axis = FreeCAD.Vector(0, 0, 1)
        if (self.base_normal + z_axis).Length < 1e-6:
            rot = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 180)
        else:
            rot = FreeCAD.Rotation(z_axis, self.base_normal)
        self.local_x = rot.multVec(FreeCAD.Vector(1, 0, 0))
        self.local_y = rot.multVec(FreeCAD.Vector(0, 1, 0))

        from freecad.fields.core.objects.fld_object import get_ray_march_cell_size
        self.resolution = get_ray_march_cell_size()

        self.preview = SdfSlicePreview()

        self.panel = SdfSliceTaskPanel(self)
        FreeCADGui.Control.showDialog(self.panel)
        self._dialog_open = True

        self._init_gizmo()
        self.update_preview()

    def _init_gizmo(self):
        if self._gizmo:
            self._gizmo.undraw()
            self._gizmo = None

        from freecad.fields.core.input.fld_gizmo import FldTransformGizmo
        self._gizmo = FldTransformGizmo()

        offset_val = self.panel.offset_spin.value()
        origin = self.base_origin + self.base_normal * offset_val

        # Setup coordinate axes for the local gizmo based on stored local frame
        axes = {
            'x': self.local_x,
            'y': self.local_y,
            'z': self.base_normal,
        }

        length = 50.0
        if self.field:
            try:
                bb_min, bb_max = self.field.bounding_box()
                diag = (bb_max - bb_min).Length
                if diag > 1.0:
                    length = diag * 0.4
            except Exception as e:
                fld_logger.debug(f"_gizmo length calc from bounding_box failed: {e}")

        self._gizmo.draw(self.points_root, origin, axes=axes, length=length, draw_translation=True)

    def update_preview(self):
        if getattr(self, "_terminated", False):
            return
        # Guard against missing panel (e.g., during early UI init)
        if not hasattr(self, "panel") or self.panel is None:
            return
        try:
            offset_val = self.panel.offset_spin.value()
            origin = self.base_origin + self.base_normal * offset_val
            count, spacing, _note = self.panel.array_layout(origin)

            self.preview.update(origin, self.base_normal, count, spacing)

            if self._gizmo:
                axes = {
                    'x': self.local_x,
                    'y': self.local_y,
                    'z': self.base_normal,
                }
                self._gizmo.update(origin, axes=axes)
        except Exception as e:
            fld_logger.debug(f"SDFSlice: Preview/Gizmo update failed: {e}")

    def handle_move(self, event_dict):
        if self._is_editing and hasattr(self, "_gizmo") and self._gizmo:
            if getattr(self, "_is_dragging", False):
                return
            from freecad.fields.core.input.input_manager import FldInputManager
            ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
            if ray_p and ray_d:
                gizmo_center = self.base_origin + self.base_normal * self.panel.offset_spin.value()
                tol = self._compute_handle_radius(gizmo_center) * 2.5
                axis = self._gizmo.hit_test(ray_p, ray_d, tol)
                if axis:
                    from PySide.QtCore import Qt
                    self._set_cursor(Qt.PointingHandCursor)
                    return
            self._restore_cursor()

    def on_button1_down(self, event_dict):
        if not self._is_editing or not hasattr(self, "_gizmo") or not self._gizmo:
            return False

        btn = event_dict.get("Button")
        if btn != QtCore.Qt.LeftButton:
            return False

        from freecad.fields.core.input.input_manager import FldInputManager
        ray_p, ray_d = FldInputManager.get_instance().get_ray(self.view, event_dict)
        if not ray_p or not ray_d:
            return False

        gizmo_center = self.base_origin + self.base_normal * self.panel.offset_spin.value()
        tol = self._compute_handle_radius(gizmo_center) * 2.5
        axis = self._gizmo.hit_test(ray_p, ray_d, tol)
        if axis:
            self._dragging_idx = f'gizmo_{axis}'
            self._start_base_origin = FreeCAD.Vector(self.base_origin)
            self._start_base_normal = FreeCAD.Vector(self.base_normal)
            self._start_offset = self.panel.offset_spin.value()
            self._edit_pivot = self.base_origin + self.base_normal * self._start_offset

            if axis.startswith('rot_'):
                ax_key = axis[4:]
                ax_vec = self._gizmo._axes[ax_key]
                from freecad.fields.core.input.fld_gizmo import _perp_pair
                ref, tang = _perp_pair(ax_vec)
                self._rot_ring_ref = ref
                self._rot_ring_tang = tang

                pt = self.projector.get_mouse_world_pos(
                    event_dict, ax_vec, self._edit_pivot, place_on_geometry=False)
                if pt:
                    v = pt - self._edit_pivot
                    self._edit_last_angle = math.atan2(v.dot(ref), v.dot(tang))
                else:
                    self._edit_last_angle = 0.0
            elif axis.startswith('plane_'):
                plane_key = axis[6:]
                norm_axis = {'xy': 'z', 'xz': 'y', 'yz': 'x'}[plane_key]
                plane_norm = self._gizmo._axes[norm_axis]
                pt = self.projector.get_mouse_world_pos(
                    event_dict, plane_norm, self._edit_pivot, place_on_geometry=False)
                self._drag_constraint_base = pt if pt else self._edit_pivot
            else:
                ax_vec = self._gizmo._axes[axis]
                click_pt = FldInputManager.get_instance().get_axis_point(
                    self.view, self._edit_pivot, ax_vec, event_dict)
                self._drag_constraint_base = click_pt if click_pt else self._edit_pivot

            self._start_drag_timer()
            return True
        return False

    def _drag_update(self):
        if self._drag_check_lmb_released():
            self.update_preview()
            return

        if not getattr(self, "_dragging_idx", None) or not self._dragging_idx.startswith('gizmo_'):
            return

        from freecad.fields.core.input.input_manager import FldInputManager
        mouse_pos = FldInputManager.get_instance()._last_qt_pos
        event_dict = {"Position": mouse_pos}

        axis = self._dragging_idx[len('gizmo_'):]

        if axis.startswith('rot_'):
            ax_key = axis[4:]
            ax_vec = self._gizmo._axes[ax_key]
            pivot = self._edit_pivot
            ref = self._rot_ring_ref
            tang = self._rot_ring_tang

            if pivot is None or ref is None or tang is None:
                return

            pt = self.projector.get_mouse_world_pos(
                event_dict, ax_vec, pivot, place_on_geometry=False)
            if not pt:
                return

            v = pt - pivot
            angle = math.atan2(v.dot(ref), v.dot(tang))
            da = angle - self._edit_last_angle
            if abs(da) < 1e-8:
                return

            rot = FreeCAD.Rotation(ax_vec, math.degrees(da))
            new_normal = rot.multVec(self.base_normal)
            new_normal.normalize()
            self.base_normal = new_normal

            self.local_x = rot.multVec(self.local_x)
            self.local_x.normalize()
            self.local_y = rot.multVec(self.local_y)
            self.local_y.normalize()

            # Recalculate base_origin so the plane rotates around the pivot point
            self.base_origin = pivot - self.base_normal * self.panel.offset_spin.value()

            self._edit_last_angle = angle
            self.update_preview()

        elif axis.startswith('plane_'):
            plane_key = axis[6:]
            norm_axis = {'xy': 'z', 'xz': 'y', 'yz': 'x'}[plane_key]
            plane_norm = self._gizmo._axes[norm_axis]
            current_pt = self.projector.get_mouse_world_pos(
                event_dict, plane_norm, self._drag_constraint_base, place_on_geometry=False)
            if not current_pt:
                return
            delta = current_pt - self._drag_constraint_base
            delta = delta - plane_norm * delta.dot(plane_norm)
            if delta.Length < 1e-8:
                return
            dz = delta.dot(self.base_normal)
            new_offset = self._start_offset + dz
            self.panel.offset_spin.blockSignals(True)
            self.panel.offset_spin.setValue(new_offset)
            self.panel.offset_spin.blockSignals(False)
            d_planar = delta - self.base_normal * dz
            self.base_origin = self._start_base_origin + d_planar
            self.update_preview()

        else:
            if axis not in self._gizmo._axes:
                return
            ax_vec = self._gizmo._axes[axis]

            current_pt = FldInputManager.get_instance().get_axis_point(
                self.view, self._drag_constraint_base, ax_vec, event_dict)
            if not current_pt:
                return

            delta = current_pt - self._drag_constraint_base
            if delta.Length < 1e-8:
                return

            if axis == 'z':
                dz = delta.dot(self.base_normal)
                new_offset = self._start_offset + dz
                self.panel.offset_spin.blockSignals(True)
                self.panel.offset_spin.setValue(new_offset)
                self.panel.offset_spin.blockSignals(False)
            else:
                self.base_origin = self._start_base_origin + delta

            self.update_preview()

    def finish(self):
        self._is_editing = False
        self.terminate()

    def _do_terminate(self):
        try:
            if self._gizmo:
                try:
                    self._gizmo.undraw()
                except Exception as e:
                    fld_logger.debug(f"_do_terminate: gizmo.undraw failed: {e}")
                self._gizmo = None
            if self.points_root and self.view and self.view.getSceneGraph():
                self.view.getSceneGraph().removeChild(self.points_root)
            if hasattr(self, "preview") and self.preview:
                try:
                    self.preview.cleanup()
                except Exception as e:
                    fld_logger.debug(f"_do_terminate: preview.cleanup failed: {e}")
                self.preview = None
        except Exception as e:
            fld_logger.debug(f"SdfSliceTool._do_terminate exception: {e}")
        super()._do_terminate()

