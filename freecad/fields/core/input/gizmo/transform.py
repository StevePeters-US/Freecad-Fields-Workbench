# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""What a `GizmoDelta` does to points: its total rotation, and the map it means on one point or
on an (N, 3) array. A translation moves by `translation`; a rotation turns about `pivot`; a scale
scales along the frozen `axes` about `pivot`, by `scale[k]` along axis k."""
import FreeCAD
import numpy as np

from freecad.fields.core.input.gizmo.models import AXES, Kind


def delta_rotation(delta):
    """The delta's total rotation (identity unless ROTATE)."""
    if delta.handle.kind is not Kind.ROTATE:
        return FreeCAD.Rotation()
    return FreeCAD.Rotation(delta.rotation_axis, delta.rotation_deg)


def _matrix(delta):
    """The linear part about the pivot, as a 3x3 array (None for a translation)."""
    kind = delta.handle.kind
    if kind in (Kind.TRANSLATE, Kind.PLANE):
        return None
    if kind is Kind.ROTATE:
        r = delta_rotation(delta)
        cols = [r.multVec(FreeCAD.Vector(*e)) for e in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]
        return np.array([[v.x for v in cols], [v.y for v in cols], [v.z for v in cols]],
                        dtype=np.float64)
    a = np.array([[delta.axes[k].x, delta.axes[k].y, delta.axes[k].z] for k in AXES],
                 dtype=np.float64)
    return a.T @ np.diag([delta.scale[k] for k in AXES]) @ a


def array_map(delta):
    """`delta` as a map on an (N, 3) float array of world points."""
    m = _matrix(delta)
    if m is None:
        t = delta.translation
        tr = np.array([t.x, t.y, t.z], dtype=np.float64)
        return lambda p: p + tr
    c = np.array([delta.pivot.x, delta.pivot.y, delta.pivot.z], dtype=np.float64)
    return lambda p: (p - c) @ m.T + c


def on_vector(f):
    """An (N, 3) array map `f` as a map on one `FreeCAD.Vector`."""
    return lambda v: FreeCAD.Vector(*f(np.array([[v.x, v.y, v.z]], dtype=np.float64))[0])


def point_map(delta):
    """`delta` as a map on one world `FreeCAD.Vector`."""
    return on_vector(array_map(delta))
