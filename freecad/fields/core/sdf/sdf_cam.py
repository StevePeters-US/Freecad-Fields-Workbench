# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/sdf/sdf_cam.py

Contains core CAM logic: SdfOffsetFieldLite, depth scheduler, contour extraction,
and Path.Command generation for Z-level profiling.
"""

import FreeCAD
import Part
from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf_constants import DEFAULT_MODEL_TOLERANCE_MM
from freecad.fields.core.sdf.sdf_slicer import trace_sdf
import math


class SdfOffsetFieldLite:
    """Wraps an SdfField, shifting the isosurface outward by `offset` mm.

    evaluate(p) returns field.evaluate(p) - offset, so zero-crossings of the
    wrapped field correspond to points where the original field equals `offset`.
    Used for cutter-radius compensation: offset = tool_radius gives the exact
    tool-center path for a ball-end mill.

    CR-053: NOT the same class as `sdf.offset.SdfOffsetField`, and deliberately
    so -- investigated before renaming rather than merging. This one forwards
    `gradient_grid` straight to the wrapped field's own (often analytic)
    `gradient_grid`, since offsetting moves the surface along its normal without
    rotating it. `SdfOffsetField` doesn't override `gradient_grid` at all, so it
    inherits `SdfField`'s numerical central-difference default -- 6 extra field
    evaluations per gradient call -- which the CPU-only contour-tracing path
    this feeds (`extract_contours_at_z` -> `sdf_contour.trace_sdf`) would pay on
    every one of the many gradient_grid calls a CAM toolpath makes. It also has
    no `to_glsl`/`surface_id`/GLSL-uniform overhead to skip in the first place,
    since this path never touches the GPU. A real, currently-load-bearing
    performance split, not an accidental duplicate -- renamed (not merged) so
    the name no longer looks like one.
    """

    def __init__(self, field, offset):
        self._field = field
        self._offset = float(offset)

    def evaluate(self, pt):
        return self._field.evaluate(pt) - self._offset

    def gradient(self, pt):
        return self._field.gradient(pt)

    # The slicer measures its error through `evaluate_grid`/`gradient_grid`, so a
    # wrapper that forwards only the scalar pair cannot be sliced at all. Forwarding
    # is also what makes the measurement affordable: batched, it costs 3.7 us/point
    # against 9.3 us for the scalar `evaluate`.
    def evaluate_grid(self, points):
        return self._field.evaluate_grid(points) - self._offset

    def gradient_grid(self, points, h=1e-4):
        return self._field.gradient_grid(points, h)

    def bounding_box(self):
        mn, mx = self._field.bounding_box()
        ov = FreeCAD.Vector(self._offset, self._offset, self._offset)
        return mn - ov, mx + ov


def compute_depth_levels(start_depth, final_depth, step_down, finish_step=0.0):
    """Return depths shallowest-to-deepest, inclusive of final_depth.

    Args:
        start_depth:  float — topmost cut Z (often 0.0 or stock top).
        final_depth:  float — deepest cut Z (negative, e.g. -10.0).
        step_down:    float — positive step between passes (e.g. 2.0).
        finish_step:  float — if > 0, the last pass is this close to final_depth
                               and a final clean-up pass follows.

    Returns:
        list[float] — Z values, e.g. [-2.0, -4.0, -6.0, -8.0, -9.5, -10.0].
    """
    if step_down <= 0.0:
        raise ValueError("step_down must be positive")

    if start_depth <= final_depth:
        return [float(final_depth)]

    levels = []
    target_limit = final_depth
    if finish_step > 0.0:
        target_limit = final_depth + finish_step

    current = start_depth
    while current > target_limit + 1e-9:
        current = current - step_down
        if current < target_limit - 1e-9:
            current = target_limit
        levels.append(float(current))

    if not levels or abs(levels[-1] - final_depth) > 1e-9:
        levels.append(float(final_depth))

    return levels


def extract_contours_at_z(field, z, tool_radius=0.0, tolerance=DEFAULT_MODEL_TOLERANCE_MM, max_control_points=50):
    """Extract SDF zero-crossing contours on the horizontal plane at height z.

    Applies SdfOffsetFieldLite(field, tool_radius) before tracing, so the returned
    curves are tool-center paths, not raw part-surface paths.

    Args:
        field:              SdfField — the part geometry.
        z:                  float — cut depth (Z coordinate of the slice plane).
        tool_radius:        float — cutter radius in mm (0 = profile the raw surface).
        tolerance:          float — curve accuracy tolerance in mm.
        max_control_points: int — cap on BSpline control points per contour.

    Returns:
        list[SlicingBSplineCurveWrapper] — one BSpline per closed contour loop.
    """
    offset_field = SdfOffsetFieldLite(field, tool_radius)
    origin = FreeCAD.Vector(0.0, 0.0, z)
    normal = FreeCAD.Vector(0.0, 0.0, 1.0)
    return trace_sdf(offset_field, origin, normal, tolerance=tolerance, max_control_points=max_control_points)


def bspline_to_commands(bspline, z, feed_rate, tolerance=DEFAULT_MODEL_TOLERANCE_MM):
    """Convert a closed BSpline or SlicingBSplineCurveWrapper to a flat list of Path.Command feed moves.

    Strategy:
      1. If bspline is a wrapper with creases (Vector handles), decompose span-by-span:
         - For straight spans (handles collinear and within chord span): emit exact G1 (Part.LineSegment).
         - For curved spans: convert span to BSpline and fit bi-arcs via toBiArcs(tolerance).
      2. For smooth closed curves (or unwrapped BSpline): whole-curve toBiArcs(tolerance)
         yields minimum bi-arcs.
      3. If toBiArcs fails or returns no edges: fall back to curve.discretize(Deflection=tolerance)
         and emit G1 moves per chord point.

    The returned commands start at the curve's first point (no rapid/plunge).
    The caller is responsible for rapids and depth moves.

    Args:
        bspline:    Part.BSplineCurve (or SlicingBSplineCurveWrapper).
        z:          float — cut depth Z coordinate for all commands.
        feed_rate:  float — feed rate in mm/min.
        tolerance:  float — arc approximation tolerance in mm.

    Returns:
        list[Path.Command] — G1/G2/G3 commands, starting from bspline first point.
    """
    try:
        import Path
    except ImportError as e:
        raise ImportError("FreeCAD Path module is not available. Please ensure the CAM workbench is installed.") from e

    curve = bspline._bspline if hasattr(bspline, "_bspline") else bspline

    commands = []
    edges = []

    # Check if bspline wrapper carries piecewise Bezier data
    has_wrapper_data = (hasattr(bspline, "_interpolation_points") and
                        hasattr(bspline, "_handle_in") and
                        hasattr(bspline, "_handle_out"))

    has_creases = False
    if has_wrapper_data:
        htypes = getattr(bspline, "_handle_types", None)
        if htypes is not None:
            has_creases = any(h == 1 for h in htypes)

    if has_wrapper_data and has_creases:
        pts = bspline._interpolation_points
        h_in = bspline._handle_in
        h_out = bspline._handle_out
        is_closed = getattr(bspline, "_is_closed", True)
        n = len(pts)
        n_spans = n if is_closed else n - 1

        # Has sharp corners / creases: decompose span-by-span to avoid OCC bi-arc singularities at C0 knots
        for i in range(n_spans):
            p0 = pts[i]
            p1 = pts[(i + 1) % n]
            h0 = h_out[i]
            h1 = h_in[(i + 1) % n]

            chord = p1 - p0
            chord_len = chord.Length
            is_straight = False
            if chord_len > 1e-6:
                u = chord * (1.0 / chord_len)
                proj0 = (h0 - p0).dot(u)
                proj1 = (h1 - p1).dot(-u)
                perp0 = ((h0 - p0) - u * proj0).Length
                perp1 = ((h1 - p1) - (-u) * proj1).Length
                if perp0 < 1e-5 and perp1 < 1e-5 and 0.0 <= proj0 <= chord_len and 0.0 <= proj1 <= chord_len:
                    is_straight = True

            if is_straight:
                edges.append(Part.LineSegment(p0, p1))
            else:
                try:
                    bs_span = Part.BSplineCurve()
                    bs_span.buildFromPolesMultsKnots([p0, h0, h1, p1], [4, 4], [0.0, 1.0], False, 3)
                    res_span = bs_span.toBiArcs(tolerance)
                    span_edges = res_span.Edges if hasattr(res_span, "Edges") else (res_span if isinstance(res_span, list) else [])
                    edges.extend(span_edges)
                except Exception as e:
                    fld_logger.warn(f"toBiArcs failed on span {i}: {e}. Emitting chord fallback.")
                    edges.append(Part.LineSegment(p0, p1))
    else:
        # Smooth closed curve (or bare BSpline): whole B-spline toBiArcs works directly and yields minimum bi-arcs
        try:
            wire = curve.toBiArcs(tolerance)
            edges = wire.Edges if hasattr(wire, "Edges") else (wire if isinstance(wire, list) else [])
        except Exception as e:
            fld_logger.debug(f"toBiArcs failed: {e}. Falling back to discretize.")

    if edges:
        for edge in edges:
            if hasattr(edge, "StartPoint") and hasattr(edge, "EndPoint"):
                start = edge.StartPoint
                end = edge.EndPoint
            elif hasattr(edge, "valueAt"):
                start = edge.valueAt(edge.FirstParameter)
                end = edge.valueAt(edge.LastParameter)
            elif hasattr(edge, "value"):
                start = edge.value(edge.FirstParameter)
                end = edge.value(edge.LastParameter)
            else:
                continue

            # Check if it is a circular arc
            center = None
            if hasattr(edge, "Center"):
                center = edge.Center
            elif hasattr(edge, "Curve") and hasattr(edge.Curve, "Center"):
                center = edge.Curve.Center
            elif hasattr(edge, "Circle") and hasattr(edge.Circle, "Center"):
                center = edge.Circle.Center

            arc_types = (getattr(Part, "ArcOfCircle", ()), getattr(Part, "Circle", ()))
            is_arc = (center is not None and (
                isinstance(edge, arc_types)
                or (hasattr(edge, "Curve") and isinstance(edge.Curve, arc_types))
                or hasattr(edge, "Radius")
            ))

            if is_arc:
                I = center.x - start.x
                J = center.y - start.y

                # Check orientation: cross = (start - center) x (end - center)
                v_start = start - center
                v_end = end - center
                cross_z = v_start.x * v_end.y - v_start.y * v_end.x

                cmd_name = "G3" if cross_z > 0.0 else "G2"

                commands.append(Path.Command(cmd_name, {
                    "X": float(end.x),
                    "Y": float(end.y),
                    "Z": float(z),
                    "I": float(I),
                    "J": float(J),
                    "F": float(feed_rate)
                }))
            else:
                commands.append(Path.Command("G1", {
                    "X": float(end.x),
                    "Y": float(end.y),
                    "Z": float(z),
                    "F": float(feed_rate)
                }))
    else:
        # Fallback to discretization
        pts = curve.discretize(Deflection=tolerance)
        if pts:
            # We skip the first point because bspline_to_commands starts from first point
            # and the caller generates the plunge to first point.
            for pt in pts[1:]:
                commands.append(Path.Command("G1", {
                    "X": float(pt.x),
                    "Y": float(pt.y),
                    "Z": float(z),
                    "F": float(feed_rate)
                }))

    return commands


def build_cam_path(field, params):
    """Build a FreeCAD Path.Path for Z-level profiling of the given SDF field.

    Args:
        field:  SdfField — the part geometry.
        params: dict with keys:
            tool_radius      float   mm, default 0.0
            start_depth      float   mm, default 0.0
            final_depth      float   mm (negative), e.g. -10.0
            step_down        float   mm, default 2.0
            finish_step      float   mm, default 0.0
            clearance_z      float   mm, height for rapids, default 5.0
            feed_rate        float   mm/min, default 600.0
            plunge_feed      float   mm/min, default 150.0
            tolerance        float   mm, default 0.1
            max_ctrl_pts     int     default 50
            adaptive         bool    use adaptive stepdown, default False
            scallop_height   float   target scallop height, default 0.1
            min_step_down    float   minimum stepdown in adaptive mode, default None

    Returns:
        Path.Path — ready to assign to a Path::Feature document object.
    """
    try:
        import Path
    except ImportError as e:
        raise ImportError("FreeCAD Path module is not available. Please ensure the CAM workbench is installed.") from e

    # Extract parameters with defaults
    tool_radius = float(params.get("tool_radius", 0.0))
    start_depth = float(params.get("start_depth", 0.0))
    final_depth = float(params.get("final_depth", -10.0))
    step_down = float(params.get("step_down", 2.0))
    finish_step = float(params.get("finish_step", 0.0))
    clearance_z = float(params.get("clearance_z", 5.0))
    feed_rate = float(params.get("feed_rate", 600.0))
    plunge_feed = float(params.get("plunge_feed", 150.0))
    tolerance = float(params.get("tolerance", 0.1))
    max_ctrl_pts = int(params.get("max_ctrl_pts", 50))
    adaptive = bool(params.get("adaptive", False))
    scallop_height = float(params.get("scallop_height", 0.1))
    min_step_down = params.get("min_step_down", None)
    if min_step_down is not None:
        min_step_down = float(min_step_down)

    if final_depth >= start_depth:
        raise ValueError("final_depth must be less than start_depth")
    if step_down <= 0.0:
        raise ValueError("step_down must be positive")

    if adaptive:
        depth_levels = compute_adaptive_depths(
            field, start_depth, final_depth, step_down, tool_radius,
            scallop_height=scallop_height, min_step_down=min_step_down
        )
    else:
        depth_levels = compute_depth_levels(start_depth, final_depth, step_down, finish_step)

    fld_logger.debug(f"build_cam_path: depth levels scheduled: {depth_levels}")

    path_cmds = []

    for z in depth_levels:
        contours = extract_contours_at_z(
            field, z, tool_radius=tool_radius, tolerance=tolerance, max_control_points=max_ctrl_pts
        )
        if not contours:
            fld_logger.debug(f"build_cam_path: No contours found at Z={z}")
            continue

        for bspline in contours:
            # Entry point is the start of the bspline curve
            curve = bspline._bspline if hasattr(bspline, "_bspline") else bspline
            entry = curve.value(curve.FirstParameter)

            # 1. Rapid to clearance
            path_cmds.append(Path.Command("G0", {"Z": clearance_z}))
            # 2. Rapid to loop entry (X, Y)
            path_cmds.append(Path.Command("G0", {"X": float(entry.x), "Y": float(entry.y)}))
            # 3. Plunge to Z depth at plunge feed
            path_cmds.append(Path.Command("G1", {"Z": float(z), "F": plunge_feed}))
            # 4. Commands for contour
            contour_cmds = bspline_to_commands(bspline, z, feed_rate, tolerance)
            path_cmds.extend(contour_cmds)
            # 5. Close loop (G1 to entry point)
            path_cmds.append(Path.Command("G1", {"X": float(entry.x), "Y": float(entry.y), "Z": float(z), "F": feed_rate}))

    # Final retract to clearance
    if path_cmds:
        path_cmds.append(Path.Command("G0", {"Z": clearance_z}))

    return Path.Path(path_cmds)


def detect_gouges(field, path, tool_radius, sample_spacing=1.0, tolerance=DEFAULT_MODEL_TOLERANCE_MM):
    """Verify that a path does not gouge the part field.

    Checks command positions and linear/circular segments between them.
    A point p is gouging if field.evaluate(p) < tool_radius - tolerance - 1e-4.

    Args:
        field:          SdfField — the part geometry.
        path:           Path.Path or list[Path.Command] — the toolpath.
        tool_radius:    float — cutter radius in mm.
        sample_spacing: float — spacing in mm between sample points.
        tolerance:      float — allowed chordal/bi-arc tolerance in mm (default 0.1).

    Returns:
        list[dict] — list of gouge dictionaries:
            {"index": int, "command": str, "point": Vector, "depth": float}
    """
    commands = path.Commands if hasattr(path, "Commands") else path
    gouges = []
    gouge_thresh = tool_radius - tolerance - 1e-4

    curr_x, curr_y, curr_z = 0.0, 0.0, 0.0
    is_first = True

    for idx, cmd in enumerate(commands):
        name = cmd.Name
        params = cmd.Parameters

        next_x = float(params.get("X", curr_x))
        next_y = float(params.get("Y", curr_y))
        next_z = float(params.get("Z", curr_z))

        if is_first:
            curr_x, curr_y, curr_z = next_x, next_y, next_z
            is_first = False
            # Check the first endpoint itself
            val = field.evaluate(FreeCAD.Vector(curr_x, curr_y, curr_z))
            if val < gouge_thresh:
                gouge_depth = tool_radius - val
                gouges.append({
                    "index": idx,
                    "command": f"{name} " + " ".join(f"{k}{v}" for k, v in params.items()),
                    "point": FreeCAD.Vector(curr_x, curr_y, curr_z),
                    "depth": gouge_depth
                })
            continue

        sampled_pts = []

        if name in ("G0", "G1"):
            start = FreeCAD.Vector(curr_x, curr_y, curr_z)
            end = FreeCAD.Vector(next_x, next_y, next_z)
            dist = (end - start).Length
            if dist > 1e-6:
                n_samples = max(2, int(math.ceil(dist / sample_spacing)) + 1)
                for i in range(n_samples):
                    t = i / (n_samples - 1)
                    sampled_pts.append(start + (end - start) * t)
            else:
                sampled_pts.append(end)

        elif name in ("G2", "G3"):
            I = float(params.get("I", 0.0))
            J = float(params.get("J", 0.0))

            cx = curr_x + I
            cy = curr_y + J

            r_start = math.hypot(curr_x - cx, curr_y - cy)
            r_end = math.hypot(next_x - cx, next_y - cy)
            r_avg = (r_start + r_end) * 0.5

            theta_start = math.atan2(curr_y - cy, curr_x - cx)
            theta_end = math.atan2(next_y - cy, next_x - cx)

            d_theta = theta_end - theta_start

            if name == "G3":  # CCW
                if d_theta <= 0.0:
                    d_theta += 2.0 * math.pi
            else:  # G2 (CW)
                if d_theta >= 0.0:
                    d_theta -= 2.0 * math.pi

            arc_length = r_avg * abs(d_theta)
            if arc_length > 1e-6:
                n_samples = max(2, int(math.ceil(arc_length / sample_spacing)) + 1)
                for i in range(n_samples):
                    t = i / (n_samples - 1)
                    angle = theta_start + d_theta * t
                    z_val = curr_z + (next_z - curr_z) * t
                    sampled_pts.append(FreeCAD.Vector(cx + r_avg * math.cos(angle), cy + r_avg * math.sin(angle), z_val))
            else:
                sampled_pts.append(FreeCAD.Vector(next_x, next_y, next_z))

        for pt in sampled_pts:
            val = field.evaluate(pt)
            if val < gouge_thresh:
                gouge_depth = tool_radius - val
                gouges.append({
                    "index": idx,
                    "command": f"{name} " + " ".join(f"{k}{v}" for k, v in params.items()),
                    "point": pt,
                    "depth": gouge_depth
                })
                break

        curr_x, curr_y, curr_z = next_x, next_y, next_z

    for g in gouges:
        pt = g["point"]
        fld_logger.warn(f"CAM Gouge Warning: Command {g['index']} ({g['command']}) gouges by {g['depth']:.4f} mm at Vector({pt.x:.2f}, {pt.y:.2f}, {pt.z:.2f})")

    return gouges


def compute_adaptive_depths(field, start_depth, final_depth, max_step_down, tool_radius, scallop_height=0.1, min_step_down=None):
    """Compute depth levels adaptively based on local slope to maintain a constant scallop height.

    Args:
        field:          SdfField — the part geometry.
        start_depth:    float — topmost Z level.
        final_depth:    float — deepest Z level (negative).
        max_step_down:  float — maximum Z step-down.
        tool_radius:    float — cutter radius.
        scallop_height: float — target scallop height in mm.
        min_step_down:  float — minimum Z step-down.

    Returns:
        list[float] — Z values from shallowest to deepest, including final_depth.
    """
    if max_step_down <= 0.0:
        raise ValueError("max_step_down must be positive")
    if start_depth <= final_depth:
        return [float(final_depth)]

    if min_step_down is None:
        min_step_down = 0.1 * max_step_down
    min_step_down = max(1e-4, min_step_down)

    levels = []
    z = start_depth

    use_scallop = tool_radius > 0.0 and scallop_height > 0.0
    factor = math.sqrt(8.0 * tool_radius * scallop_height) if use_scallop else 0.0

    while z > final_depth + 1e-9:
        dz = max_step_down
        if use_scallop:
            # Trace at current level to measure slope
            contours = extract_contours_at_z(field, z, tool_radius=tool_radius, tolerance=DEFAULT_MODEL_TOLERANCE_MM, max_control_points=16)
            if contours:
                min_sin_theta = 1.0
                sampled_any = False
                for bspline in contours:
                    curve = bspline._bspline if hasattr(bspline, "_bspline") else bspline
                    n_samples = 8
                    for i in range(n_samples + 1):
                        t = curve.FirstParameter + (curve.LastParameter - curve.FirstParameter) * i / n_samples
                        pt = curve.value(t)
                        grad = field.gradient(pt)
                        gl = grad.Length
                        if gl > 1e-6:
                            sin_theta = math.sqrt(grad.x**2 + grad.y**2) / gl
                            min_sin_theta = min(min_sin_theta, sin_theta)
                            sampled_any = True
                if sampled_any:
                    dz = min_sin_theta * factor
                    dz = max(min_step_down, min(max_step_down, dz))

        z_next = z - dz
        if z_next < final_depth + 1e-9:
            z_next = final_depth

        levels.append(float(z_next))
        z = z_next

        if abs(z - final_depth) < 1e-9:
            break

    return levels


def export_to_gcode(path, filepath):
    """Write a Path.Path or list of Path.Command to a G-code file.

    Args:
        path:     Path.Path or list[Path.Command]
        filepath: str — path to output file
    """
    commands = path.Commands if hasattr(path, "Commands") else path
    lines = []

    lines.append("; G-code generated by FreeCAD Fields Workbench")
    lines.append("G21 ; Units: mm")
    lines.append("G90 ; Absolute coordinates")

    for cmd in commands:
        parts = [cmd.Name]
        params = cmd.Parameters
        for key in ["X", "Y", "Z", "I", "J", "F"]:
            if key in params:
                val = params[key]
                if key == "F":
                    parts.append(f"F{val:.1f}")
                else:
                    parts.append(f"{key}{val:.4f}")
        lines.append(" ".join(parts))

    lines.append("M30 ; Program end")

    with open(filepath, "w") as f:
        f.write("\n".join(lines) + "\n")


def flowline_paths(cage_field, params):
    """Generate 3D surface finishing toolpaths following patch iso-parameter flowlines (CAM-013).

    Evaluates exact closed-form Coons patch points and normals with zero SDF discretization error.
    Offsets points along the surface normal by `tool_radius` for ball-end milling.

    Args:
        cage_field: SdfCageField (or object with _face_curve_handles and _face_verts).
        params: dict with keys:
            tool_radius:     float (default 2.0 mm)
            step_over:       float (default 1.0 mm)
            scallop_height:  float (default 0.05 mm)
            clearance_z:     float (default 5.0 mm)
            feed_rate:       float (default 600.0 mm/min)
            plunge_feed:     float (default 150.0 mm/min)
            n_samples_s:     int (default 30 samples per flowline)
            bidirectional:   bool (default True, zigzag passes)
            face_indices:    list[int] (optional face subset, default all quad faces)

    Returns:
        Path.Path ready to assign to a Path::Feature document object.
    """
    try:
        import Path
    except ImportError:
        raise ImportError("FreeCAD Path module is required for CAM toolpath generation")

    import numpy as np
    from freecad.fields.core.sdf.sdf.cage_patch_math import (
        _coons_grad, _coons_eval, _bez_tri3, _bez_tri3_homo_grads
    )

    patch_source = cage_field
    if hasattr(cage_field, "base_field") and hasattr(cage_field.base_field, "_face_curve_handles"):
        patch_source = cage_field.base_field

    if not hasattr(patch_source, "_face_verts"):
        raise ValueError("flowline_paths requires a patch cage field with _face_verts")

    tool_radius = float(params.get("tool_radius", 2.0))
    step_over = float(params.get("step_over", 1.0))
    scallop_height = float(params.get("scallop_height", 0.05))
    clearance_z = float(params.get("clearance_z", 5.0))
    feed_rate = float(params.get("feed_rate", 600.0))
    plunge_feed = float(params.get("plunge_feed", 150.0))
    n_samples_s = int(params.get("n_samples_s", 30))
    bidirectional = bool(params.get("bidirectional", True))
    face_indices = params.get("face_indices", None)

    if face_indices is None:
        face_indices = list(range(len(patch_source._face_verts)))

    all_commands = []

    for fi in face_indices:
        verts = patch_source._face_verts[fi]
        n_v = len(verts)
        if n_v not in (3, 4):
            continue

        if n_v == 4:
            C0, C1, D0, D1 = patch_source._face_curve_handles(fi)
            lengths = []
            for s_sample in (0.0, 0.5, 1.0):
                p0 = _coons_eval(C0, C1, D0, D1, s_sample, 0.0)
                p1 = _coons_eval(C0, C1, D0, D1, s_sample, 1.0)
                lengths.append(float(np.linalg.norm(p1 - p0)))
            lt_max = max(lengths) if lengths else 10.0
        else:  # n_v == 3
            tri_pts = patch_source._face_tri_ctrl_pts(fi)
            v0 = patch_source.vertices[verts[0]]
            v1 = patch_source.vertices[verts[1]]
            v2 = patch_source.vertices[verts[2]]
            lt_max = float(max(np.linalg.norm(v1 - v0), np.linalg.norm(v2 - v0), np.linalg.norm(v2 - v1)))

        if scallop_height > 0.0 and tool_radius > 0.0:
            w_scallop = 2.0 * math.sqrt(2.0 * scallop_height * tool_radius)
            effective_step = min(step_over, w_scallop)
        else:
            effective_step = step_over

        effective_step = max(1e-3, effective_step)
        n_t = max(2, int(math.ceil(lt_max / effective_step)) + 1)
        t_values = [k / (n_t - 1) for k in range(n_t)]

        face_commands = []
        for p_idx, t_val in enumerate(t_values):
            if bidirectional and (p_idx % 2 == 1):
                s_range = [1.0 - j / (n_samples_s - 1) for j in range(n_samples_s)]
            else:
                s_range = [j / (n_samples_s - 1) for j in range(n_samples_s)]

            pts_pass = []
            for s_val in s_range:
                if n_v == 4:
                    p, ps, pt = _coons_grad(C0, C1, D0, D1, s_val, t_val)
                else:
                    w = 1.0 - t_val
                    u = s_val * t_val
                    v = (1.0 - s_val) * t_val
                    p = _bez_tri3(*tri_pts, u, v, w)
                    pu, pv, pw = _bez_tri3_homo_grads(*tri_pts, u, v, w)
                    pt = s_val * pu + (1.0 - s_val) * pv - pw
                    ps = t_val * (pu - pv)

                n_vec = np.cross(ps, pt)
                nl = float(np.linalg.norm(n_vec))
                if nl > 1e-9:
                    n_vec = n_vec / nl
                else:
                    n_vec = np.array([0.0, 0.0, 1.0])
                pt_tool = p + tool_radius * n_vec
                pts_pass.append(FreeCAD.Vector(float(pt_tool[0]), float(pt_tool[1]), float(pt_tool[2])))

            if not pts_pass:
                continue

            start_pt = pts_pass[0]
            if p_idx == 0:
                face_commands.append(Path.Command("G0", {"Z": clearance_z}))
                face_commands.append(Path.Command("G0", {"X": start_pt.x, "Y": start_pt.y}))
                face_commands.append(Path.Command("G1", {"X": start_pt.x, "Y": start_pt.y, "Z": start_pt.z, "F": plunge_feed}))
            else:
                # Transition move to start of next pass
                face_commands.append(Path.Command("G1", {"X": start_pt.x, "Y": start_pt.y, "Z": start_pt.z, "F": feed_rate}))

            for pt in pts_pass[1:]:
                face_commands.append(Path.Command("G1", {"X": pt.x, "Y": pt.y, "Z": pt.z, "F": feed_rate}))

        if face_commands:
            face_commands.append(Path.Command("G0", {"Z": clearance_z}))
            all_commands.extend(face_commands)

    return Path.Path(all_commands)
