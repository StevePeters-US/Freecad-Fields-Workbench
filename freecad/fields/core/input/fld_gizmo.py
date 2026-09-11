# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import math as _math

_RING_SEGMENTS = 48   # polyline vertices per ring
# Two rings whose normalized pick scores differ by less than this are treated as a
# tie and resolved by depth instead. Fraction of the ring pick tolerance.
_RING_TIE_MARGIN = 0.25
_PLANE_TIE_MARGIN = 0.25


def _perp_pair(ax_vec):
    """Return two unit vectors perpendicular to ax_vec and to each other."""
    up = FreeCAD.Vector(0, 0, 1)
    if abs(ax_vec.dot(up)) > 0.98:
        up = FreeCAD.Vector(1, 0, 0)
    ref  = ax_vec.cross(up)
    ref.normalize()
    tang = ref.cross(ax_vec)
    tang.normalize()
    return ref, tang


class FldTransformGizmo:
    """3-axis translation gizmo: cylinder shaft + cone tip per axis, planar handles, and rotation rings.

    Axes default to global X/Y/Z. Pass an axes dict to use local space.
    """

    AXIS_COLORS = {
        'x': (1.0, 0.15, 0.15),
        'y': (0.15, 0.85, 0.15),
        'z': (0.25, 0.45, 1.0),
    }

    PLANE_NORMALS = {
        'xy': 'z',
        'xz': 'y',
        'yz': 'x',
    }

    PLANE_AXES = {
        'xy': ('x', 'y'),
        'xz': ('x', 'z'),
        'yz': ('y', 'z'),
    }

    PLANE_COLORS = {
        'xy': AXIS_COLORS['z'],  # Blue (normal to Z)
        'xz': AXIS_COLORS['y'],  # Green (normal to Y)
        'yz': AXIS_COLORS['x'],  # Red (normal to X)
    }

    def __init__(self):
        self._center = FreeCAD.Vector()
        self._axes = {
            'x': FreeCAD.Vector(1, 0, 0),
            'y': FreeCAD.Vector(0, 1, 0),
            'z': FreeCAD.Vector(0, 0, 1),
        }
        self._length = 50.0
        self._parent = None
        self._root = None
        self._shaft_xforms = {}
        self._cone_xforms = {}
        self._ring_seps   = {}   # axis → SoSeparator
        self._ring_coords = {}   # axis → SoCoordinate3
        self._axis_mats   = {}   # axis → SoMaterial
        self._ring_mats   = {}   # axis → SoMaterial
        self._plane_seps   = {}  # plane → SoSeparator
        self._plane_coords = {}  # plane → SoCoordinate3 (face)
        self._plane_line_coords = {}  # plane → SoCoordinate3 (border)
        self._plane_mats   = {}  # plane → SoMaterial (face)
        self._plane_line_mats = {}  # plane → SoMaterial (border)
        self._highlight   = None

    # ------------------------------------------------------------------
    # Coin3D helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _axis_sb_rot(axis_vec):
        """Return SbRotation that aligns Coin3D +Y axis with axis_vec (for SoCylinder/SoCone)."""
        from pivy import coin
        y = FreeCAD.Vector(0, 1, 0)
        target = FreeCAD.Vector(axis_vec).normalize()
        dot = y.dot(target)
        if abs(dot - 1.0) < 1e-6:
            return coin.SbRotation(coin.SbVec3f(0, 1, 0), 0.0)
        if abs(dot + 1.0) < 1e-6:
            return coin.SbRotation(coin.SbVec3f(1, 0, 0), 3.14159265358979)
        rot_fc = FreeCAD.Rotation(y, target)
        q = rot_fc.Q
        return coin.SbRotation(q[0], q[1], q[2], q[3])

    def _shaft_offset(self):
        return self._length * 0.18

    def _shaft_h(self):
        return self._length * 0.62

    def _cone_h(self):
        return self._length * 0.20

    def _ring_radius(self):
        return self._length * 1.15

    def _plane_offset(self):
        return self._length * 0.20

    def _plane_size(self):
        return self._length * 0.22

    def _draw_ring(self, axis, center):
        from pivy import coin
        ax_vec       = self._axes[axis]
        ref, tang    = _perp_pair(ax_vec)
        R            = self._ring_radius()
        color        = self.AXIS_COLORS[axis]
        N            = _RING_SEGMENTS

        ring_pts = []
        for i in range(N + 1):
            theta = 2.0 * _math.pi * i / N
            pt = center + ref * (_math.cos(theta) * R) + tang * (_math.sin(theta) * R)
            ring_pts.append((pt.x, pt.y, pt.z))

        coords = coin.SoCoordinate3()
        coords.point.setValues(0, len(ring_pts), ring_pts)

        ls = coin.SoLineSet()
        ls.numVertices.setValue(len(ring_pts))

        ds = coin.SoDrawStyle()
        ds.lineWidth.setValue(2.5)

        mat = coin.SoMaterial()
        mat.diffuseColor.setValue(*color)
        mat.emissiveColor.setValue(*color)

        ring_sep = coin.SoSeparator()
        ring_sep.addChild(mat)
        ring_sep.addChild(ds)
        ring_sep.addChild(coords)
        ring_sep.addChild(ls)

        self._root.addChild(ring_sep)
        self._ring_seps[axis]   = ring_sep
        self._ring_coords[axis] = coords
        self._ring_mats[axis]   = mat

    def _draw_plane(self, plane, center):
        from pivy import coin
        color = self.PLANE_COLORS[plane]
        ax1_name, ax2_name = self.PLANE_AXES[plane]
        u_vec = self._axes[ax1_name]
        v_vec = self._axes[ax2_name]
        offset = self._plane_offset()
        size = self._plane_size()

        c0 = center + u_vec * offset + v_vec * offset
        c1 = center + u_vec * (offset + size) + v_vec * offset
        c2 = center + u_vec * (offset + size) + v_vec * (offset + size)
        c3 = center + u_vec * offset + v_vec * (offset + size)

        quad_pts = [
            (c0.x, c0.y, c0.z),
            (c1.x, c1.y, c1.z),
            (c2.x, c2.y, c2.z),
            (c3.x, c3.y, c3.z),
        ]
        line_pts = [
            (c0.x, c0.y, c0.z),
            (c1.x, c1.y, c1.z),
            (c2.x, c2.y, c2.z),
            (c3.x, c3.y, c3.z),
            (c0.x, c0.y, c0.z),
        ]

        plane_sep = coin.SoSeparator()

        # Face: semi-transparent quad
        face_sep = coin.SoSeparator()
        mat_face = coin.SoMaterial()
        mat_face.diffuseColor.setValue(*color)
        mat_face.transparency.setValue(0.5)

        hints = coin.SoShapeHints()
        hints.vertexOrdering.setValue(coin.SoShapeHints.UNKNOWN_ORDERING)
        hints.shapeType.setValue(coin.SoShapeHints.UNKNOWN_SHAPE_TYPE)

        coords_face = coin.SoCoordinate3()
        coords_face.point.setValues(0, len(quad_pts), quad_pts)
        fs = coin.SoFaceSet()
        fs.numVertices.setValue(4)

        face_sep.addChild(hints)
        face_sep.addChild(mat_face)
        face_sep.addChild(coords_face)
        face_sep.addChild(fs)
        plane_sep.addChild(face_sep)

        # Border outline: opaque line
        line_sep = coin.SoSeparator()
        mat_line = coin.SoMaterial()
        mat_line.diffuseColor.setValue(*color)
        mat_line.emissiveColor.setValue(*color)
        ds = coin.SoDrawStyle()
        ds.lineWidth.setValue(1.5)
        coords_line = coin.SoCoordinate3()
        coords_line.point.setValues(0, len(line_pts), line_pts)
        ls = coin.SoLineSet()
        ls.numVertices.setValue(5)

        line_sep.addChild(mat_line)
        line_sep.addChild(ds)
        line_sep.addChild(coords_line)
        line_sep.addChild(ls)
        plane_sep.addChild(line_sep)

        self._root.addChild(plane_sep)
        self._plane_seps[plane] = plane_sep
        self._plane_coords[plane] = coords_face
        self._plane_line_coords[plane] = coords_line
        self._plane_mats[plane] = mat_face
        self._plane_line_mats[plane] = mat_line

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def draw(self, parent_sep, center, axes=None, length=None, draw_translation=True, rot_center=None, draw_planes=True):
        from pivy import coin
        if self._root:
            self.undraw()
        self._parent = parent_sep
        self._root = coin.SoSeparator()
        parent_sep.addChild(self._root)

        if axes is not None:
            self._axes = axes
        if length is not None:
            self._length = length
        self._center = FreeCAD.Vector(center)
        self._rot_center = FreeCAD.Vector(rot_center) if rot_center is not None else FreeCAD.Vector(center)

        if draw_translation:
            shaft_r = self._length * 0.022
            cone_r  = self._length * 0.055
            shaft_offset = self._shaft_offset()
            shaft_h = self._shaft_h()
            cone_h  = self._cone_h()

            for axis in ('x', 'y', 'z'):
                color  = self.AXIS_COLORS[axis]
                ax_vec = self._axes[axis]
                sb_rot = self._axis_sb_rot(ax_vec)

                ax_sep = coin.SoSeparator()
                mat = coin.SoMaterial()
                mat.diffuseColor.setValue(*color)
                mat.specularColor.setValue(0.5, 0.5, 0.5)
                mat.shininess.setValue(0.5)
                ax_sep.addChild(mat)
                self._axis_mats[axis] = mat

                # Shaft
                shaft_ctr = self._center + ax_vec * (shaft_offset + shaft_h * 0.5)
                xf_shaft = coin.SoTransform()
                xf_shaft.translation.setValue(shaft_ctr.x, shaft_ctr.y, shaft_ctr.z)
                xf_shaft.rotation.setValue(sb_rot)
                cyl = coin.SoCylinder()
                cyl.radius = shaft_r
                cyl.height = shaft_h
                shaft_sep = coin.SoSeparator()
                shaft_sep.addChild(xf_shaft)
                shaft_sep.addChild(cyl)
                ax_sep.addChild(shaft_sep)

                # Cone tip
                cone_ctr = self._center + ax_vec * (shaft_offset + shaft_h + cone_h * 0.5)
                xf_cone = coin.SoTransform()
                xf_cone.translation.setValue(cone_ctr.x, cone_ctr.y, cone_ctr.z)
                xf_cone.rotation.setValue(sb_rot)
                cone = coin.SoCone()
                cone.bottomRadius = cone_r
                cone.height = cone_h
                cone_sep = coin.SoSeparator()
                cone_sep.addChild(xf_cone)
                cone_sep.addChild(cone)
                ax_sep.addChild(cone_sep)

                self._root.addChild(ax_sep)
                self._shaft_xforms[axis] = xf_shaft
                self._cone_xforms[axis]  = xf_cone

            if draw_planes:
                for plane in ('xy', 'xz', 'yz'):
                    self._draw_plane(plane, self._center)

        for axis in ('x', 'y', 'z'):
            self._draw_ring(axis, self._rot_center)

    def update(self, center, axes=None, length=None, rot_center=None):
        if not self._root:
            return
        if axes is not None:
            self._axes = axes
        if length is not None:
            self._length = length
        self._center = FreeCAD.Vector(center)
        if rot_center is not None:
            self._rot_center = FreeCAD.Vector(rot_center)
        elif not hasattr(self, '_rot_center') or self._rot_center is None:
            self._rot_center = FreeCAD.Vector(center)

        shaft_offset = self._shaft_offset()
        shaft_h = self._shaft_h()
        cone_h  = self._cone_h()

        for axis in ('x', 'y', 'z'):
            ax_vec  = self._axes[axis]
            sb_rot  = self._axis_sb_rot(ax_vec)
            xf_s    = self._shaft_xforms.get(axis)
            xf_c    = self._cone_xforms.get(axis)
            if xf_s is None or xf_c is None:
                continue
            shaft_ctr = center + ax_vec * (shaft_offset + shaft_h * 0.5)
            xf_s.translation.setValue(shaft_ctr.x, shaft_ctr.y, shaft_ctr.z)
            xf_s.rotation.setValue(sb_rot)
            cone_ctr = center + ax_vec * (shaft_offset + shaft_h + cone_h * 0.5)
            xf_c.translation.setValue(cone_ctr.x, cone_ctr.y, cone_ctr.z)
            xf_c.rotation.setValue(sb_rot)

        offset = self._plane_offset()
        size   = self._plane_size()
        for plane in ('xy', 'xz', 'yz'):
            coords_f = self._plane_coords.get(plane)
            coords_l = self._plane_line_coords.get(plane)
            if coords_f is None or coords_l is None:
                continue
            ax1_name, ax2_name = self.PLANE_AXES[plane]
            u_vec = self._axes[ax1_name]
            v_vec = self._axes[ax2_name]
            c0 = center + u_vec * offset + v_vec * offset
            c1 = center + u_vec * (offset + size) + v_vec * offset
            c2 = center + u_vec * (offset + size) + v_vec * (offset + size)
            c3 = center + u_vec * offset + v_vec * (offset + size)
            quad_pts = [
                (c0.x, c0.y, c0.z),
                (c1.x, c1.y, c1.z),
                (c2.x, c2.y, c2.z),
                (c3.x, c3.y, c3.z),
            ]
            line_pts = [
                (c0.x, c0.y, c0.z),
                (c1.x, c1.y, c1.z),
                (c2.x, c2.y, c2.z),
                (c3.x, c3.y, c3.z),
                (c0.x, c0.y, c0.z),
            ]
            coords_f.point.setValues(0, len(quad_pts), quad_pts)
            coords_l.point.setValues(0, len(line_pts), line_pts)

        for axis in ('x', 'y', 'z'):
            coords = self._ring_coords.get(axis)
            if coords is None:
                continue
            ax_vec = self._axes[axis]
            ref, tang = _perp_pair(ax_vec)
            R = self._ring_radius()
            N = _RING_SEGMENTS
            ring_pts = []
            for i in range(N + 1):
                theta = 2.0 * _math.pi * i / N
                pt = self._rot_center + ref * (_math.cos(theta) * R) + tang * (_math.sin(theta) * R)
                ring_pts.append((pt.x, pt.y, pt.z))
            coords.point.setValues(0, len(ring_pts), ring_pts)

    def undraw(self):
        if self._parent and self._root:
            try:
                self._parent.removeChild(self._root)
            except Exception as e:
                from freecad.fields.core import fld_logger
                fld_logger.debug(f"FldTransformGizmo.undraw: removeChild failed: {e}")
        self._root = None
        self._shaft_xforms.clear()
        self._cone_xforms.clear()
        self._ring_seps.clear()
        self._ring_coords.clear()
        self._axis_mats.clear()
        self._ring_mats.clear()
        self._plane_seps.clear()
        self._plane_coords.clear()
        self._plane_line_coords.clear()
        self._plane_mats.clear()
        self._plane_line_mats.clear()
        self._highlight = None
        self._parent = None

    HIGHLIGHT_COLOR = (1.0, 0.85, 0.1)   # amber

    def set_highlight(self, handle):
        """Tint one handle ('x'/'y'/'z'/'plane_xy'/…/'rot_x'/…) and restore the others.

        Pass None to clear. Cheap enough to call on every mouse-move: it writes at
        most twelve SoMaterial fields and Coin3D no-ops an unchanged value.
        Returns True if the highlight changed, False if unchanged.
        """
        if self._highlight == handle:
            return False
        self._highlight = handle
        for axis in ('x', 'y', 'z'):
            base = self.AXIS_COLORS[axis]
            shaft_c = self.HIGHLIGHT_COLOR if handle == axis else base
            ring_c  = self.HIGHLIGHT_COLOR if handle == f'rot_{axis}' else base
            m = self._axis_mats.get(axis)
            if m is not None:
                m.diffuseColor.setValue(*shaft_c)
            m = self._ring_mats.get(axis)
            if m is not None:
                m.diffuseColor.setValue(*ring_c)
                m.emissiveColor.setValue(*ring_c)
        for plane in ('xy', 'xz', 'yz'):
            base = self.PLANE_COLORS[plane]
            is_hl = (handle == f'plane_{plane}')
            c = self.HIGHLIGHT_COLOR if is_hl else base
            m_face = self._plane_mats.get(plane)
            if m_face is not None:
                m_face.diffuseColor.setValue(*c)
                m_face.transparency.setValue(0.25 if is_hl else 0.5)
            m_line = self._plane_line_mats.get(plane)
            if m_line is not None:
                m_line.diffuseColor.setValue(*c)
                m_line.emissiveColor.setValue(*c)
        return True

    def hit_test(self, ray_p, ray_d, tolerance):
        """Returns handle name or None — selects shaft, plane, or rotation ring with best normalized proximity.

        Shafts, planes, and rings are measured in true 3D distance, then normalized by their
        respective tolerances so they compete on equal footing regardless of geometry scale.
        Rings get 1.5x the shaft tolerance because they are thinner targets visually.

        Handles that are near-equally close are resolved by depth: the one in
        front is the one under the cursor.
        """
        ring_r = self._ring_radius()
        ring_tolerance = tolerance * 1.5

        # (normalized_score, depth_along_ray, handle_name) — lower score wins, depth breaks ties
        candidates = []

        if self._shaft_xforms:
            shaft_offset = self._shaft_offset()
            for axis in ('x', 'y', 'z'):
                seg_start = self._center + self._axes[axis] * shaft_offset
                seg_end   = self._center + self._axes[axis] * self._length
                dist = _ray_segment_dist(ray_p, ray_d, seg_start, seg_end)
                if dist < tolerance:
                    proj = ((seg_start + seg_end) * 0.5 - ray_p).dot(ray_d)
                    candidates.append((dist / tolerance, proj, axis))

        plane_hits = []
        if self._plane_seps:
            offset = self._plane_offset()
            size   = self._plane_size()
            for plane in ('xy', 'xz', 'yz'):
                if plane not in self._plane_seps:
                    continue
                ax1_name, ax2_name = self.PLANE_AXES[plane]
                u_vec = self._axes[ax1_name]
                v_vec = self._axes[ax2_name]
                dist, depth = _ray_quad_dist(
                    ray_p, ray_d, self._center, u_vec, v_vec,
                    offset, offset + size, offset, offset + size
                )
                if dist < tolerance:
                    plane_hits.append((depth, dist / tolerance, f'plane_{plane}'))
            if plane_hits:
                best_score = min(c[1] for c in plane_hits)
                close = [c for c in plane_hits if c[1] <= best_score + _PLANE_TIE_MARGIN]
                close.sort(key=lambda c: (c[0], c[1]))
                candidates.append((close[0][1], close[0][0], close[0][2]))

        # (depth_along_ray, normalized_score, axis_name) — nearest to the eye wins
        ring_hits = []
        rot_ctr = getattr(self, "_rot_center", self._center)
        for axis in ('x', 'y', 'z'):
            dist, depth = _ray_ring_dist(ray_p, ray_d, rot_ctr, self._axes[axis], ring_r)
            if dist < ring_tolerance:
                ring_hits.append((depth, dist / ring_tolerance, f'rot_{axis}'))
        if ring_hits:
            best_score = min(c[1] for c in ring_hits)
            close = [c for c in ring_hits if c[1] <= best_score + _RING_TIE_MARGIN]
            close.sort(key=lambda c: (c[0], c[1]))
            candidates.append((close[0][1], close[0][0], close[0][2]))

        if not candidates:
            return None
        candidates.sort(key=lambda c: (c[0], c[1]))
        return candidates[0][2]


# ------------------------------------------------------------------
# Module-level geometry helper
# ------------------------------------------------------------------

def _ray_segment_dist(ray_p, ray_d, seg_a, seg_b):
    """Minimum distance between an infinite ray and a finite line segment."""
    seg_v   = seg_b - seg_a
    seg_len = seg_v.Length
    if seg_len < 1e-8:
        v    = seg_a - ray_p
        proj = v.dot(ray_d)
        return (ray_p + ray_d * proj - seg_a).Length

    seg_n = seg_v * (1.0 / seg_len)
    w0    = ray_p - seg_a
    b     = ray_d.dot(seg_n)
    d     = ray_d.dot(w0)
    e     = seg_n.dot(w0)
    denom = 1.0 - b * b

    s = 0.0 if abs(denom) < 1e-8 else (e - b * d) / denom
    if s < 0.0:
        # Ray's closest approach along the axis line is before seg_a (in the inner deadzone around center)
        return float('inf')
    s = min(seg_len, s)

    seg_pt = seg_a + seg_n * s
    t      = (seg_pt - ray_p).dot(ray_d)
    ray_pt = ray_p + ray_d * t
    return (ray_pt - seg_pt).Length


def _ray_ring_dist(ray_p, ray_d, center, ax_vec, ring_r):
    """Minimum 3D distance from an infinite ray to a circle, and its depth along the ray.

    Samples _RING_SEGMENTS points on the circle and returns the smallest
    perpendicular distance from any sample point to the ray.  This is correct
    at all viewing angles (including edge-on rings where the old plane-
    intersection approach returned inf or a misleading planar residual).

    The coarse sample spacing (2*pi*R/48, i.e. ~0.13*R) is itself of the same order
    as the pick tolerance, so the best sample is refined by a ternary search over
    its neighbouring interval — otherwise a click landing between two samples reads
    as several millimetres of error and can miss the ring entirely.

    Returns (distance, depth); (inf, inf) when the whole circle is behind the ray.
    """
    ref, tang = _perp_pair(ax_vec)

    def _at(theta):
        pt = center + ref * (_math.cos(theta) * ring_r) + tang * (_math.sin(theta) * ring_r)
        v = pt - ray_p
        proj = v.dot(ray_d)
        # No `proj <= 0` rejection: in an orthographic view get_ray() returns the
        # focal-plane origin, not the eye, so points in front of the user can have
        # negative projection. See fld_base._hit_test_perp for the same caveat.
        return (v - ray_d * proj).Length, proj

    step = 2.0 * _math.pi / _RING_SEGMENTS
    best_theta = None
    best_dist = float('inf')
    best_depth = float('inf')
    for i in range(_RING_SEGMENTS):
        theta = step * i
        dist, depth = _at(theta)
        if dist < best_dist:
            best_dist, best_depth, best_theta = dist, depth, theta

    # Nowhere near the ring: the coarse pass already bounds it well outside any
    # sane pick tolerance, so skip the refinement on every non-hovering move.
    if best_theta is None or best_dist > 0.2 * ring_r:
        return best_dist, best_depth

    lo, hi = best_theta - step, best_theta + step
    for _ in range(12):
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        if _at(m1)[0] < _at(m2)[0]:
            hi = m2
        else:
            lo = m1
    dist, depth = _at(0.5 * (lo + hi))
    if dist < best_dist:
        best_dist, best_depth = dist, depth
    return best_dist, best_depth


def _ray_quad_dist(ray_p, ray_d, center, u_vec, v_vec, u_min, u_max, v_min, v_max):
    """Minimum 3D distance and depth from infinite ray to a planar rectangle.

    Returns (distance, depth).
    """
    n_vec = u_vec.cross(v_vec)
    n_len = n_vec.Length
    if n_len < 1e-6:
        return float('inf'), float('inf')
    n_vec = n_vec * (1.0 / n_len)

    denom = ray_d.dot(n_vec)
    if abs(denom) > 1e-4:
        # Intersect ray with infinite plane
        t = (center - ray_p).dot(n_vec) / denom
        p_int = ray_p + ray_d * t
        w = p_int - center
        u_val = w.dot(u_vec)
        v_val = w.dot(v_vec)

        # Clamped closest point on rectangle
        q_u = min(max(u_val, u_min), u_max)
        q_v = min(max(v_val, v_min), v_max)
        closest_pt = center + u_vec * q_u + v_vec * q_v

        v = closest_pt - ray_p
        proj = v.dot(ray_d)
        dist = (v - ray_d * proj).Length
        return dist, proj
    else:
        # Edge-on ray: ray is nearly parallel to plane.
        # Test distance to the 4 bounding line segments.
        c0 = center + u_vec * u_min + v_vec * v_min
        c1 = center + u_vec * u_max + v_vec * v_min
        c2 = center + u_vec * u_max + v_vec * v_max
        c3 = center + u_vec * u_min + v_vec * v_max
        segs = [(c0, c1), (c1, c2), (c2, c3), (c3, c0)]
        best_dist = float('inf')
        best_depth = float('inf')
        for s0, s1 in segs:
            dist = _ray_segment_dist(ray_p, ray_d, s0, s1)
            if dist < best_dist:
                seg_v = s1 - s0
                seg_len = seg_v.Length
                if seg_len > 1e-8:
                    seg_n = seg_v * (1.0 / seg_len)
                    b = ray_d.dot(seg_n)
                    d = ray_d.dot(ray_p - s0)
                    e = seg_n.dot(ray_p - s0)
                    denom_s = 1.0 - b * b
                    s = 0.0 if abs(denom_s) < 1e-8 else (e - b * d) / denom_s
                    s = min(seg_len, max(0.0, s))
                    pt_on_seg = s0 + seg_n * s
                    proj = (pt_on_seg - ray_p).dot(ray_d)
                else:
                    proj = (s0 - ray_p).dot(ray_d)
                best_dist = dist
                best_depth = proj
        return best_dist, best_depth

