# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Typed entry for the universal gizmo (`universal_gizmo` skill, reference/interaction.md,
"Typed values"). Pure: buffer editing and the delta a set of buffers means."""
import FreeCAD

from freecad.fields.core.input.gizmo.models import AXES, GizmoDelta, Kind

TYPED_CHARS = frozenset("0123456789.-")

# The readout's rich line in TYPED (D-52) and in a creation stage (INP-060): typed values
# bold, the mouse's (or the identity) grey. `white-space:pre` keeps the double spaces; a
# QLabel's default AutoText format renders it.
TYPED_HTML = "<b>{}</b>"
LIVE_HTML = '<span style="color:#9a9a9a">{}</span>'
ROW_HTML = '<span style="white-space:pre">{}</span>'


def fmt(value):
    """`{:g}` of a value rounded to 3 decimals; never "-0"."""
    return "{:g}".format(round(value, 3) + 0.0)


def identity(handle):
    """What an untyped component means: a factor of 1 for SCALE, else 0."""
    return 1.0 if handle.kind is Kind.SCALE else 0.0


def components(handle):
    """How many values typed entry takes: two for a PLANE, three for xyz TRANSLATE, else one."""
    return len(handle.axes) if handle.kind in (Kind.TRANSLATE, Kind.PLANE) else 1


def edit_buffer(buf, key):
    """`buf` after `key`: a character of TYPED_CHARS, or "backspace".

    Grammar: an optional leading "-", digits, at most one ".". "-" toggles the sign
    wherever it is typed. Backspace on an empty buffer does nothing. Anything else
    leaves the buffer as it is.
    """
    if key == "backspace":
        return buf[:-1]
    if key == "-":
        return buf[1:] if buf.startswith("-") else "-" + buf
    if key == ".":
        return buf if "." in buf else buf + "."
    if len(key) == 1 and key in "0123456789":
        return buf + key
    return buf


def buffer_value(buf, ident):
    """The number `buf` means; "", "-", "." and "-." mean `ident`."""
    if buf in ("", "-", ".", "-."):
        return ident
    return float(buf)


def values_of(delta):
    """`delta`'s value for each typed component, in its own axes: what the mouse means for
    each one (D-52)."""
    h = delta.handle
    if h.kind in (Kind.TRANSLATE, Kind.PLANE):
        return [delta.translation.dot(delta.axes[a]) for a in h.axes]
    if h.kind is Kind.ROTATE:
        return [delta.rotation_deg]
    if h.uniform:
        return [delta.scale["x"]]
    return [delta.scale[h.axes[0]]]


def typed_delta(handle, frame, buffers, mouse=None):
    """The GizmoDelta that `buffers` mean for `handle` in the frozen `frame` (whose
    local_axes are already resolved for the space). A typed component (a non-empty buffer)
    is exact and never snapped. An untyped one takes `mouse`'s value when the mouse drives
    (D-52: typing began mid-drag), else the identity. `snapped` is set only when an
    untyped component took a snapped mouse value."""
    ident = identity(handle)
    m = values_of(mouse) if mouse is not None else [ident] * len(buffers)
    vals = [buffer_value(b, ident) if b else v for b, v in zip(buffers, m)]
    delta = GizmoDelta(handle, FreeCAD.Vector(frame.pivot), frame.local_axes, typed=True)
    if handle.kind in (Kind.TRANSLATE, Kind.PLANE):
        delta.translation = sum((frame.local_axes[a] * v for a, v in zip(handle.axes, vals)), FreeCAD.Vector())
    elif handle.kind is Kind.ROTATE:
        delta.rotation_deg = vals[0]
        delta.rotation_axis = FreeCAD.Vector(frame.local_axes[handle.axes[0]])
    elif handle.uniform:
        delta.scale = {k: vals[0] for k in AXES}
    else:
        delta.scale[handle.axes[0]] = vals[0]
    delta.snapped = bool(mouse is not None and mouse.snapped and not all(buffers))
    return delta
