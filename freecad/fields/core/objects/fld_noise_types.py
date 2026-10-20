# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.objects.fld_noise_types

Every noise type's defaults, as ratios of the part being textured (NV-013).

t = the source's thickness along the noise Direction, s = the smaller of its two
in-plane spans. A default is computed once -- at creation and on a type switch -- and
stored in mm; after that the user owns the number.
"""
import math
from dataclasses import dataclass, field

import FreeCAD

#: The size used when the source has no finite bounding box (an unbounded field).
REFERENCE_T, REFERENCE_S = 10.0, 100.0


@dataclass(frozen=True)
class NoiseTypeSpec:
    amp: float          # Amplitude = amp * t
    wavelength: float   # Wavelength = wavelength * s
    mask: bool          # Custom_ignore_back_face
    #: {param: value} set verbatim, and {param: k} set to k / s (a rate per mm).
    params: dict = field(default_factory=dict)
    per_s: dict = field(default_factory=dict)


NOISE_TYPES = {
    "Waves":         NoiseTypeSpec(0.10, 1 / 5, True),
    "Square":        NoiseTypeSpec(0.10, 1 / 5, True),
    "Sawtooth":      NoiseTypeSpec(0.10, 1 / 5, True),
    "Perlin 2D":     NoiseTypeSpec(0.10, 1 / 4, True),
    "Perlin 3D":     NoiseTypeSpec(0.10, 1 / 4, False),   # 3D noise wraps every face
    "Voronoi 2D":    NoiseTypeSpec(0.20, 1 / 6, True),    # peak_factor 0.5
    "Voronoi 3D":    NoiseTypeSpec(0.20, 1 / 6, False),
}


def _nice(v):
    """Two significant figures, so a default reads as a number someone typed."""
    return float(f"{v:.2g}") if v > 0 else 0.0


def measure_source(obj):
    """(t, s) of obj.Source in mm, in obj.Placement's frame: t along local Z (the
    Direction), s = min of the spans along local X and Y. The projection is
    SdfNoiseField._axis_extent's. Falls back to the reference size, with a warning,
    when the source has no finite bounding box."""
    from freecad.fields.core import fld_logger
    try:
        src = obj.Source
        bb_min, bb_max = src.Proxy.get_sdf_field(src).bounding_box()
        size = bb_max - bb_min
        rot = obj.Placement.Rotation

        def span(axis):
            a = rot.multVec(axis)
            return abs(size.x * a.x) + abs(size.y * a.y) + abs(size.z * a.z)

        t = span(FreeCAD.Vector(0, 0, 1))
        s = min(span(FreeCAD.Vector(1, 0, 0)), span(FreeCAD.Vector(0, 1, 0)))
        if all(math.isfinite(v) and v > 0 for v in (t, s)):
            return t, s
    except Exception as e:
        fld_logger.debug(f"measure_source: {e}")
    fld_logger.warn(f"{obj.Label}: source size unknown; noise defaults use "
                    f"t={REFERENCE_T} mm, s={REFERENCE_S} mm")
    return REFERENCE_T, REFERENCE_S


def defaults_for(name, t, s):
    """{property_or_param: value} for type `name` on a part of size (t, s)."""
    return spec_defaults(NOISE_TYPES[name], t, s)


def spec_defaults(spec, t, s):
    """defaults_for, from a NoiseTypeSpec directly. A user preset (NV-016) is a spec
    without a row in NOISE_TYPES, so it is sized by the same rule."""
    amp = _nice(spec.amp * t)
    wl = _nice(spec.wavelength * s)
    out = {
        "Amplitude": amp, "AmplitudeMin": 0.0, "AmplitudeMax": _nice(max(0.5 * t, 2.0 * amp)),
        "Wavelength": wl, "WavelengthMin": _nice(s / 50.0), "WavelengthMax": _nice(s),
        "params": {"ignore_back_face": spec.mask, **spec.params,
                   **{k: v / s for k, v in spec.per_s.items()}},
    }
    return out


def _write_sized(obj, d):
    for prop in ("AmplitudeMin", "AmplitudeMax", "Amplitude",
                 "WavelengthMin", "WavelengthMax", "Wavelength"):
        setattr(obj, prop, d[prop])


def apply_noise_type(obj, name):
    """Seed `obj` with type `name`: its template graph and its size-scaled defaults.
    Returns None, or apply_graph's error string (in which case nothing was written)."""
    from freecad.fields.core.gui.node_editor.nodes import get_template_graph
    from freecad.fields.core.objects.fld_noise_shared import apply_graph
    d = defaults_for(name, *measure_source(obj))
    err = apply_graph(obj, get_template_graph(name), name, values=d["params"])
    if err:
        return err
    _write_sized(obj, d)
    return None


def apply_user_preset(obj, name):
    """Seed `obj` with user preset `name` (NV-016), sized to its source like a
    built-in type. Labelled "Custom": NoiseType enumerates built-in names only.
    Returns None, or an error string (in which case nothing was written)."""
    from freecad.fields.core.objects.fld_noise_shared import apply_graph
    from freecad.fields.core.objects.fld_noise_user_presets import get_user_preset
    got = get_user_preset(name)
    if got is None:
        return f"no user preset named {name!r}"
    graph, spec = got
    d = spec_defaults(spec, *measure_source(obj))
    err = apply_graph(obj, graph, "Custom", values=d["params"])
    if err:
        return err
    _write_sized(obj, d)
    return None


def apply_picked(obj, name):
    """What picking `name` does, in the Type combo or the editor's Templates menu
    (NV-025): one rule, so the two pickers cannot drift. A built-in type is seeded
    sized to the part; a user preset likewise, with its stored custom values; any
    other editor starter is applied as a graph, labelled Custom, sizes untouched.
    Returns None, or an error string (in which case nothing was written)."""
    from freecad.fields.core.objects.fld_noise_user_presets import user_preset_names
    if name in NOISE_TYPES:
        return apply_noise_type(obj, name)
    if name in user_preset_names():
        return apply_user_preset(obj, name)
    from freecad.fields.core.gui.node_editor.nodes import get_template_graph
    from freecad.fields.core.objects.fld_noise_shared import apply_graph
    graph = get_template_graph(name)
    if not graph:
        return f"no template or preset named {name!r}"
    return apply_graph(obj, graph, "Custom")

