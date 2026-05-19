import FreeCAD
import math as _math

_RING_SEGMENTS = 48   # polyline vertices per ring


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


class DMTransformGizmo:
    """3-axis translation gizmo: cylinder shaft + cone tip per axis.

    Axes default to global X/Y/Z. Pass an axes dict to use local space.
    """

    AXIS_COLORS = {
        'x': (1.0, 0.15, 0.15),
        'y': (0.15, 0.85, 0.15),
        'z': (0.25, 0.45, 1.0),
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

    def _shaft_h(self):
        return self._length * 0.80

    def _cone_h(self):
        return self._length * 0.20

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def draw(self, parent_sep, center, axes=None, length=None):
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

        shaft_r = self._length * 0.022
        cone_r  = self._length * 0.055
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

            # Shaft
            shaft_ctr = self._center + ax_vec * (shaft_h * 0.5)
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
            cone_ctr = self._center + ax_vec * (shaft_h + cone_h * 0.5)
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

    def update(self, center, axes=None):
        if not self._root:
            return
        if axes is not None:
            self._axes = axes
        self._center = FreeCAD.Vector(center)

        shaft_h = self._shaft_h()
        cone_h  = self._cone_h()

        for axis in ('x', 'y', 'z'):
            ax_vec  = self._axes[axis]
            sb_rot  = self._axis_sb_rot(ax_vec)
            xf_s    = self._shaft_xforms.get(axis)
            xf_c    = self._cone_xforms.get(axis)
            if xf_s is None or xf_c is None:
                continue
            shaft_ctr = center + ax_vec * (shaft_h * 0.5)
            xf_s.translation.setValue(shaft_ctr.x, shaft_ctr.y, shaft_ctr.z)
            xf_s.rotation.setValue(sb_rot)
            cone_ctr = center + ax_vec * (shaft_h + cone_h * 0.5)
            xf_c.translation.setValue(cone_ctr.x, cone_ctr.y, cone_ctr.z)
            xf_c.rotation.setValue(sb_rot)

    def undraw(self):
        if self._parent and self._root:
            try:
                self._parent.removeChild(self._root)
            except Exception:
                pass
        self._root = None
        self._shaft_xforms.clear()
        self._cone_xforms.clear()
        self._parent = None

    def hit_test(self, ray_p, ray_d, tolerance):
        """Returns 'x', 'y', 'z', or None — closest axis shaft within tolerance."""
        best_axis = None
        best_dist = tolerance
        for axis in ('x', 'y', 'z'):
            ax_vec  = self._axes[axis]
            seg_end = self._center + ax_vec * self._length
            dist = _ray_segment_dist(ray_p, ray_d, self._center, seg_end)
            if dist < best_dist:
                best_dist = dist
                best_axis = axis
        return best_axis


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
    s = max(0.0, min(seg_len, s))

    seg_pt = seg_a + seg_n * s
    t      = (seg_pt - ray_p).dot(ray_d)
    ray_pt = ray_p + ray_d * t
    return (ray_pt - seg_pt).Length
