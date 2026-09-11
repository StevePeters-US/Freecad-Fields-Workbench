# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Snapping primitives for transform drags.

Split in two on purpose: the quantizers here are pure arithmetic and testable
headless; scene-dependent candidate collection lives in snap_world_point (TW-016).
Visual feedback is provided by FldSnapIndicator (TW-017).
"""
import math
from collections import namedtuple

import FreeCAD

# kind is one of: None, "grid", "angle", "vertex", "origin", "surface"
SnapResult = namedtuple("SnapResult", "point kind target")

def snap_active():
    """Whether snapping applies right now. The configured modifier inverts the default."""
    from PySide import QtWidgets, QtCore
    from freecad.fields.core.fld_settings import (
        get_snap_enabled, get_snap_invert_modifier)
    _MODS = {"ctrl": QtCore.Qt.ControlModifier,
             "shift": QtCore.Qt.ShiftModifier,
             "alt": QtCore.Qt.AltModifier}
    mods = QtWidgets.QApplication.keyboardModifiers()
    inverted = bool(mods & _MODS[get_snap_invert_modifier()])
    return get_snap_enabled() != inverted


def quantize(value, step):
    """Round `value` to the nearest multiple of `step`. step <= 0 is a pass-through."""
    if step is None or step <= 0.0:
        return value
    return round(value / step) * step


def snap_length(delta_len, step):
    """Quantize a signed distance along a drag axis."""
    return quantize(delta_len, step)


def snap_angle_deg(angle_deg, step_deg):
    """Quantize an angle in degrees, keeping it in (-180, 180]."""
    a = quantize(angle_deg, step_deg)
    a = math.fmod(a, 360.0)
    if a > 180.0:
        a -= 360.0
    elif a <= -180.0:
        a += 360.0
    return a


def snap_vector_to_grid(vec, step):
    """Quantize each component of a world-space vector independently."""
    if step is None or step <= 0.0:
        return FreeCAD.Vector(vec)
    return FreeCAD.Vector(quantize(vec.x, step), quantize(vec.y, step), quantize(vec.z, step))


def snap_world_point(view, pt, *, extra_points=(), working_plane=None, exclude=(), event_dict=None):
    """Find the nearest snap candidate (vertex, origin, surface) to pt within pixel radius.

    Returns:
        SnapResult(point, kind, target)
    """
    from freecad.fields.core.fld_settings import get_snap_vertex_enabled, get_snap_pixel_radius
    from freecad.fields.core.input.view_projector import ViewProjector

    if not get_snap_vertex_enabled() or view is None:
        return SnapResult(pt, None, None)

    projector = ViewProjector(view)
    try:
        px_scale = float(projector.px_per_world(pt))
    except Exception:
        px_scale = 10.0
    tol = get_snap_pixel_radius() / max(px_scale, 1e-6)

    candidates = []

    # 1. Extra explicit points (e.g. caller's control points)
    for p in extra_points:
        if p is not None:
            candidates.append((FreeCAD.Vector(p), "vertex", None))

    # 2. Origin of visible Fields objects in the active document
    doc = FreeCAD.ActiveDocument
    if doc and hasattr(doc, "Objects"):
        ex_set = set(exclude) if exclude else set()
        for obj in doc.Objects:
            if obj in ex_set:
                continue
            if getattr(obj, "ShapeType", None) == "sdf" and getattr(obj, "Visibility", True):
                if hasattr(obj, "Points") and obj.Points:
                    orig = obj.Placement.multVec(obj.Points[0])
                elif hasattr(obj, "Placement"):
                    orig = obj.Placement.Base
                else:
                    continue
                candidates.append((FreeCAD.Vector(orig), "origin", obj))

    # 3. Active working plane origin
    if working_plane is not None:
        wp_base = getattr(working_plane, "Base", None)
        if wp_base is not None:
            candidates.append((FreeCAD.Vector(wp_base), "origin", None))

    # 4. Find closest candidate within tolerance
    best_candidate = None
    best_dist = float("inf")
    best_kind = None
    best_target = None

    v_pt = FreeCAD.Vector(pt)
    for c_pt, kind, target in candidates:
        d = (c_pt - v_pt).Length
        if d <= tol and d < best_dist:
            best_dist = d
            best_candidate = c_pt
            best_kind = kind
            best_target = target

    if best_candidate is not None:
        return SnapResult(best_candidate, best_kind, best_target)

    # 5. SDF surface under cursor if event_dict is provided
    if event_dict:
        try:
            hit = projector.get_sdf_hit(event_dict, skip_objects=exclude)
            if hit is not None:
                d = (FreeCAD.Vector(hit) - v_pt).Length
                if d <= tol:
                    return SnapResult(FreeCAD.Vector(hit), "surface", None)
        except Exception:
            pass

    return SnapResult(pt, None, None)


MIN_DETENT_SPACING_PX = 7.0     # below this, dots read as a solid line
MAX_DETENTS = 201               # +-100 either side of the anchor, a hard cap


def world_per_px(view, ref_pt):
    """World units per screen pixel at ref_pt. Inverse of ViewProjector.px_per_world."""
    from freecad.fields.core.input.view_projector import ViewProjector
    try:
        s = float(ViewProjector(view).px_per_world(ref_pt))
    except Exception:
        return 0.1
    return 1.0 / max(s, 1e-6)


def detent_stride(step_mm, wpp, interval=None):
    """World distance between guide detents, or None to draw no dots.

    Detents are major ticks: `interval` grid steps apart (default from settings),
    NOT one per step -- 600 dots on screen is unreadable. The pixel floor below is
    only a backstop for extreme zoom-out, and widens by whole multiples of the
    interval so every dot stays on a snappable position.
    """
    if step_mm is None or step_mm <= 0.0 or wpp is None or wpp <= 0.0:
        return None
    if interval is None:
        from freecad.fields.core.fld_settings import get_snap_detent_interval
        interval = get_snap_detent_interval()
    interval = max(1, int(interval))

    stride = step_mm * interval
    px = stride / wpp
    if px >= MIN_DETENT_SPACING_PX:
        return stride
    # Too dense even at the interval: widen by whole multiples of it.
    k = int(MIN_DETENT_SPACING_PX / max(px, 1e-9)) + 1
    if k > 100:
        return None             # zoomed so far out no dot spacing is meaningful
    return stride * k


class FldSnapIndicator:
    """Coin3D visual indicator for active snaps (cross marker + status bar text)."""

    COLORS = {
        "grid": (0.7, 0.7, 0.7),      # grey
        "vertex": (0.0, 1.0, 1.0),    # cyan
        "origin": (0.0, 1.0, 1.0),    # cyan
        "surface": (1.0, 0.0, 1.0),   # magenta
    }

    def __init__(self):
        self._parent = None
        self._root = None
        self._coords = None
        self._lines = None
        self._material = None
        self._draw_style = None

    def draw(self, parent_sep):
        """Build the indicator node under parent_sep (typically an SoAnnotation)."""
        from pivy import coin
        if self._root:
            self.undraw()
        self._parent = parent_sep
        self._root = coin.SoSeparator()

        self._material = coin.SoMaterial()
        self._material.diffuseColor.setValue(0.0, 1.0, 1.0)
        self._material.emissiveColor.setValue(0.0, 1.0, 1.0)
        self._root.addChild(self._material)

        self._draw_style = coin.SoDrawStyle()
        self._draw_style.lineWidth.setValue(2.0)
        self._root.addChild(self._draw_style)

        self._coords = coin.SoCoordinate3()
        self._root.addChild(self._coords)

        self._lines = coin.SoLineSet()
        self._lines.numVertices.setValues(0, 3, [2, 2, 2])
        self._root.addChild(self._lines)

        self._parent.addChild(self._root)
        self.hide()

    def show(self, point, kind="vertex", size=None, view=None):
        """Show the cross at point with color corresponding to kind."""
        if not self._root or not self._coords or point is None:
            return
        if size is None:
            from freecad.fields.core.fld_settings import get_snap_marker_size_px
            size = get_snap_marker_size_px() * world_per_px(view, point)
        c = FreeCAD.Vector(point)
        # 3D cross aligned with world axes
        pts = [
            (c.x - size, c.y, c.z), (c.x + size, c.y, c.z),
            (c.x, c.y - size, c.z), (c.x, c.y + size, c.z),
            (c.x, c.y, c.z - size), (c.x, c.y, c.z + size),
        ]
        self._coords.point.setValues(0, len(pts), pts)

        color = self.COLORS.get(kind, (0.0, 1.0, 1.0))
        if self._material:
            self._material.diffuseColor.setValue(*color)
            self._material.emissiveColor.setValue(*color)

        if self._root and self._parent:
            if self._parent.findChild(self._root) < 0:
                self._parent.addChild(self._root)

        try:
            import FreeCADGui
            mw = FreeCADGui.getMainWindow()
            if mw:
                sb = mw.statusBar()
                if sb:
                    sb.showMessage(f"Snapped: {kind}", 2000)
        except Exception:
            pass

    def hide(self):
        """Hide indicator."""
        if self._root and self._parent:
            idx = self._parent.findChild(self._root)
            if idx >= 0:
                self._parent.removeChild(idx)

    def undraw(self):
        """Remove indicator from scene graph."""
        self.hide()
        self._parent = None
        self._root = None
        self._coords = None
        self._lines = None
        self._material = None
        self._draw_style = None


class FldSnapGuide:
    """Visual guide showing axis lines, detent landmarks, and plane grids during snapped drags."""

    def __init__(self):
        self._parent = None
        self._root = None
        self._line_sep = None
        self._line_coords = None
        self._line_mat = None
        self._line_style = None
        self._lines = None
        self._dot_sep = None
        self._dot_coords = None
        self._dot_mat = None
        self._dot_style = None
        self._pointset = None
        self._label_sep = None
        self._label_translation = None
        self._label_color = None
        self._label_font = None
        self._label_text = None

    def draw(self, parent_sep):
        """Build the guide node graph under parent_sep (typically an SoAnnotation)."""
        from pivy import coin
        if self._root:
            self.undraw()
        self._parent = parent_sep
        self._root = coin.SoSeparator()

        # Line branch (for 1-DOF axis line or 2-DOF grid)
        self._line_sep = coin.SoSeparator()
        self._line_mat = coin.SoMaterial()
        self._line_sep.addChild(self._line_mat)
        self._line_style = coin.SoDrawStyle()
        self._line_sep.addChild(self._line_style)
        self._line_coords = coin.SoCoordinate3()
        self._line_sep.addChild(self._line_coords)
        self._lines = coin.SoLineSet()
        self._line_sep.addChild(self._lines)
        self._root.addChild(self._line_sep)

        # Detent branch (for 1-DOF points)
        self._dot_sep = coin.SoSeparator()
        self._dot_mat = coin.SoMaterial()
        self._dot_sep.addChild(self._dot_mat)
        self._dot_style = coin.SoDrawStyle()
        self._dot_sep.addChild(self._dot_style)
        self._dot_coords = coin.SoCoordinate3()
        self._dot_sep.addChild(self._dot_coords)
        self._pointset = coin.SoPointSet()
        self._dot_sep.addChild(self._pointset)
        self._root.addChild(self._dot_sep)

        # Label branch: a screen-facing "World"/"Local" tag near the pivot,
        # so the active axis-lock space is visible in the viewport itself and
        # not just the status bar.
        self._label_sep = coin.SoSeparator()
        self._label_color = coin.SoBaseColor()
        self._label_sep.addChild(self._label_color)
        self._label_translation = coin.SoTranslation()
        self._label_sep.addChild(self._label_translation)
        self._label_font = coin.SoFont()
        self._label_font.size.setValue(14.0)
        self._label_sep.addChild(self._label_font)
        self._label_text = coin.SoText2()
        self._label_sep.addChild(self._label_text)
        self._root.addChild(self._label_sep)

        self._parent.addChild(self._root)
        self.hide()

    def show_axis(self, anchor, axis, view, step=None):
        """Draw a line through anchor along axis, with detents spaced at detent_stride."""
        from freecad.fields.core.fld_settings import (
            get_snap_guide_enabled,
            get_snap_grid_step,
            get_snap_guide_line_color,
            get_snap_guide_line_width,
            get_snap_detent_color,
            get_snap_detent_size,
            get_snap_guide_extent_px,
        )

        if not get_snap_guide_enabled() or not self._root or not self._line_coords:
            self.hide()
            return

        if step is None:
            step = get_snap_grid_step()

        ax = FreeCAD.Vector(axis)
        if ax.Length < 1e-6:
            self.hide()
            return
        ax = ax.normalize()

        anchor_pt = FreeCAD.Vector(anchor)
        wpp = world_per_px(view, anchor_pt)
        half = get_snap_guide_extent_px() * wpp

        # Axis line
        p1 = anchor_pt - ax * half
        p2 = anchor_pt + ax * half
        # setValues() overwrites and grows but never shrinks: without setNum() a grid
        # drawn by an earlier show_plane() on this same guide stays on screen, with only
        # its first segment replaced by the axis. Reachable by pressing X mid-drag.
        self._line_coords.point.setValues(0, 2, [(p1.x, p1.y, p1.z), (p2.x, p2.y, p2.z)])
        self._line_coords.point.setNum(2)
        self._lines.numVertices.setValues(0, 1, [2])
        self._lines.numVertices.setNum(1)
        line_color = get_snap_guide_line_color()
        if self._line_mat:
            self._line_mat.diffuseColor.setValue(*line_color)
            self._line_mat.emissiveColor.setValue(*line_color)
        if self._line_style:
            self._line_style.lineWidth.setValue(get_snap_guide_line_width())

        # Detents
        stride = detent_stride(step, wpp)
        detent_size = get_snap_detent_size()
        if stride is None or detent_size == 0 or not self._dot_coords:
            if self._dot_coords:
                self._dot_coords.point.setNum(0)
            if self._pointset:
                self._pointset.numPoints.setValue(0)
        else:
            n = min(int(half / stride), (MAX_DETENTS - 1) // 2)
            pts = []
            for i in range(-n, n + 1):
                p = anchor_pt + ax * (i * stride)
                pts.append((p.x, p.y, p.z))
            self._dot_coords.point.setValues(0, len(pts), pts)
            self._dot_coords.point.setNum(len(pts))
            if self._pointset:
                self._pointset.numPoints.setValue(len(pts))
            if self._dot_style:
                self._dot_style.pointSize.setValue(float(detent_size))
            det_color = get_snap_detent_color()
            if self._dot_mat:
                self._dot_mat.diffuseColor.setValue(*det_color)
                self._dot_mat.emissiveColor.setValue(*det_color)

        if self._root and self._parent:
            if self._parent.findChild(self._root) < 0:
                self._parent.addChild(self._root)

    def show_plane(self, anchor, normal, view, step=None, u_hint=None):
        """Draw a 2-DOF grid on the plane defined by anchor and normal."""
        from freecad.fields.core.fld_settings import (
            get_snap_guide_enabled,
            get_snap_grid_step,
            get_snap_grid_color,
            get_snap_grid_line_width,
            get_snap_guide_extent_px,
            get_snap_grid_extent_steps,
        )

        if not get_snap_guide_enabled() or not self._root or not self._line_coords:
            self.hide()
            return

        if step is None:
            step = get_snap_grid_step()

        norm = FreeCAD.Vector(normal)
        if norm.Length < 1e-6:
            self.hide()
            return
        norm = norm.normalize()

        anchor_pt = FreeCAD.Vector(anchor)

        # Determine in-plane basis (ref, tang)
        if u_hint is not None:
            u = FreeCAD.Vector(u_hint)
            proj = u - norm * u.dot(norm)
            if proj.Length > 1e-6:
                ref = proj.normalize()
                tang = norm.cross(ref).normalize()
            else:
                from freecad.fields.core.input.fld_gizmo import _perp_pair
                ref, tang = _perp_pair(norm)
        else:
            from freecad.fields.core.input.fld_gizmo import _perp_pair
            ref, tang = _perp_pair(norm)

        wpp = world_per_px(view, anchor_pt)
        stride = detent_stride(step, wpp)
        if stride is None:
            self.hide()
            return

        # Blank detents for plane display
        if self._dot_coords:
            self._dot_coords.point.setNum(0)
        if self._pointset:
            self._pointset.numPoints.setValue(0)

        extent_steps = get_snap_grid_extent_steps()
        n = min(extent_steps, max(1, int(get_snap_guide_extent_px() * wpp / stride)))
        half = n * stride

        pts = []
        for i in range(-n, n + 1):
            # Parallel to tang, offset along ref
            p1 = anchor_pt + ref * (i * stride) - tang * half
            p2 = anchor_pt + ref * (i * stride) + tang * half
            pts.append((p1.x, p1.y, p1.z))
            pts.append((p2.x, p2.y, p2.z))
            # Parallel to ref, offset along tang
            q1 = anchor_pt + tang * (i * stride) - ref * half
            q2 = anchor_pt + tang * (i * stride) + ref * half
            pts.append((q1.x, q1.y, q1.z))
            pts.append((q2.x, q2.y, q2.z))

        num_segments = (2 * n + 1) * 2
        num_verts = [2] * num_segments

        self._line_coords.point.setValues(0, len(pts), pts)
        self._line_coords.point.setNum(len(pts))
        self._lines.numVertices.setValues(0, len(num_verts), num_verts)
        self._lines.numVertices.setNum(len(num_verts))
        grid_color = get_snap_grid_color()
        if self._line_mat:
            self._line_mat.diffuseColor.setValue(*grid_color)
            self._line_mat.emissiveColor.setValue(*grid_color)
        if self._line_style:
            self._line_style.lineWidth.setValue(get_snap_grid_line_width())

        if self._root and self._parent:
            if self._parent.findChild(self._root) < 0:
                self._parent.addChild(self._root)

    def show_space_label(self, anchor, space, view):
        """Draw a small screen-facing 'World'/'Local' tag near anchor.

        Purely a readability affordance for the axis-lock space -- unlike
        show_axis/show_plane it has no snap semantics of its own, so it is
        not gated on get_snap_guide_enabled() the same way; callers decide
        when to show/hide it (tied to whether an axis lock is active).
        """
        if not self._root or not self._label_text or view is None:
            return

        anchor_pt = FreeCAD.Vector(anchor)
        wpp = world_per_px(view, anchor_pt)
        # Nudge above the anchor (world +Z) so the tag doesn't sit directly
        # on top of the guide's own anchor dot.
        pos = anchor_pt + FreeCAD.Vector(0, 0, 1) * (wpp * 22.0)
        self._label_translation.translation.setValue(pos.x, pos.y, pos.z)

        label = "Local" if str(space).lower() == 'local' else "World"
        self._label_text.string.setValue(label)
        if self._label_color:
            color = (1.0, 0.8, 0.2) if label == "Local" else (0.8, 0.8, 0.8)
            self._label_color.rgb.setValue(*color)

        if self._root and self._parent:
            if self._parent.findChild(self._root) < 0:
                self._parent.addChild(self._root)

    def hide_space_label(self):
        """Blank the label text without touching the axis/plane guide lines."""
        if self._label_text:
            self._label_text.string.setValue("")

    def hide(self):
        """Hide guide."""
        if self._root and self._parent:
            idx = self._parent.findChild(self._root)
            if idx >= 0:
                self._parent.removeChild(idx)

    def undraw(self):
        """Remove guide from scene graph."""
        self.hide()
        self._parent = None
        self._root = None
        self._line_sep = None
        self._line_coords = None
        self._line_mat = None
        self._line_style = None
        self._lines = None
        self._dot_sep = None
        self._dot_coords = None
        self._dot_mat = None
        self._dot_style = None
        self._pointset = None
        self._label_sep = None
        self._label_translation = None
        self._label_color = None
        self._label_font = None
        self._label_text = None
