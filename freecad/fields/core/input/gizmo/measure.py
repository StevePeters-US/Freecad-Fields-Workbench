# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""The universal gizmo's maths: ray measures, edge-on test, angle unwrap, snapping a delta.

Qt-free and Coin-free. The formulas are the spec's (`universal_gizmo` skill,
reference/interaction.md) and match `tools/primitive_gizmo.py`, the reference.
"""
import dataclasses
import math

import FreeCAD

from freecad.fields.core.input import fld_snap
from freecad.fields.core.input.fld_gizmo import _perp_pair
from freecad.fields.core.input.gizmo.models import AXES, Kind

ROTATE_EDGE_ON = 0.17   # |ray_dir . axis| below this: the ring is within ~10 deg of edge-on
PLANE_EDGE_ON = 0.08    # |ray_dir . normal| below this: the plane is edge-on

_EPS = 1e-9


def line_param(ray_p, ray_d, base, axis):
    """Parameter `t` of the point on the line `base + axis*t` closest to the ray.

    With `w0 = base - ray_p`, `b = axis.ray_d`, `e = ray_d.w0` and `f = axis.w0`
    (both directions unit): `t = (b*e - f) / (1 - b^2)`.

    Returns:
        `t`, or None when the line and the ray are parallel (`|1 - b^2| < 1e-9`).
    """
    b = axis.dot(ray_d)
    denom = 1.0 - b * b
    if abs(denom) < _EPS:
        return None
    w0 = base - ray_p
    return (b * ray_d.dot(w0) - axis.dot(w0)) / denom


def ray_plane(ray_p, ray_d, point, normal):
    """Intersection of the ray with the plane `(point, normal)`: `p + d*s`,
    `s = (point - p).n / (d.n)`. None when `|d.n| < 1e-9` (parallel)."""
    dn = ray_d.dot(normal)
    if abs(dn) < _EPS:
        return None
    s = (point - ray_p).dot(normal) / dn
    return ray_p + ray_d * s


def ring_angle(p, pivot, axis):
    """Angle of `p` about `axis` through `pivot`, radians: `atan2(v.ref, v.tang)` with
    `v = p - pivot` and `(ref, tang) = _perp_pair(axis)`. The convention
    `primitive_gizmo.py` uses, so a ring drags the same way it does today."""
    ref, tang = _perp_pair(axis)
    v = p - pivot
    return math.atan2(v.dot(ref), v.dot(tang))


def unwrap(raw, prev):
    """`raw` moved by whole turns to sit nearest `prev`: `raw + round((prev - raw)/2pi)*2pi`."""
    return raw + round((prev - raw) / (2 * math.pi)) * (2 * math.pi)


def edge_on(ray_d, vec, limit):
    """True when the view ray is nearly parallel to the measuring plane: `|ray_d.vec| < limit`."""
    return abs(ray_d.dot(vec)) < limit


def snap_delta(delta, settings, origin, vertex=None):
    """A copy of `delta` with Ctrl snapping applied to its total.

    TRANSLATE: `t = translation.a` goes to the vertex's projection on the axis
    (`(vertex - origin).a`) when a vertex is given, else to a multiple of the grid
    step; the result stays parallel to `a`. PLANE: the same per in-plane component.
    ROTATE: `rotation_deg` to a multiple of the angle step (plain `quantize`, never
    `snap_angle_deg`, which wraps to +-180 and would undo the unwrap). SCALE: each
    factor of the handle's axes becomes `1 + quantize(f - 1, scale_step)`.

    `snapped` is True iff a value changed by more than 1e-9.
    """
    h = delta.handle
    out = dataclasses.replace(
        delta, translation=FreeCAD.Vector(delta.translation), scale=dict(delta.scale))
    changed = False
    if h.kind is Kind.TRANSLATE and len(h.axes) == 3:      # the free move (D-57)
        if vertex is not None:
            out.translation = vertex - origin
        else:
            out.translation = sum((delta.axes[a] * fld_snap.quantize(delta.translation.dot(delta.axes[a]), settings.grid_step)
                                   for a in AXES), FreeCAD.Vector())
        changed = (out.translation - delta.translation).Length > _EPS
    elif h.kind is Kind.TRANSLATE:
        a = delta.axes[h.axes[0]]
        t = delta.translation.dot(a)
        t2 = (vertex - origin).dot(a) if vertex is not None else fld_snap.quantize(t, settings.grid_step)
        out.translation = a * t2
        changed = abs(t2 - t) > _EPS
    elif h.kind is Kind.PLANE:
        u, v = (delta.axes[k] for k in h.axes)
        cu, cv = delta.translation.dot(u), delta.translation.dot(v)
        if vertex is not None:
            w = vertex - origin
            cu2, cv2 = w.dot(u), w.dot(v)
        else:
            cu2 = fld_snap.quantize(cu, settings.grid_step)
            cv2 = fld_snap.quantize(cv, settings.grid_step)
        out.translation = u * cu2 + v * cv2
        changed = abs(cu2 - cu) > _EPS or abs(cv2 - cv) > _EPS
    elif h.kind is Kind.ROTATE:
        deg = fld_snap.quantize(delta.rotation_deg, settings.angle_step)
        changed = abs(deg - delta.rotation_deg) > _EPS
        out.rotation_deg = deg
    elif h.kind is Kind.SCALE:
        for k in h.axes:
            f = 1.0 + fld_snap.quantize(delta.scale[k] - 1.0, settings.scale_step)
            changed = changed or abs(f - delta.scale[k]) > _EPS
            out.scale[k] = f
    out.snapped = changed
    return out
