# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.objects.fld_noise_user_presets

Noise graphs the user saved, kept in <UserAppData>/Fields/noise_presets.json (NV-016).

A preset is a template the user made: a node graph plus the NoiseTypeSpec that sizes it.
The file is per user, not per document, so a preset saved in one document is offered in
every other one.
"""
import json
import os

import FreeCAD

from freecad.fields.core import fld_logger
from freecad.fields.core.objects.fld_noise_types import NoiseTypeSpec


def presets_path():
    return os.path.join(FreeCAD.getUserAppDataDir(), "Fields", "noise_presets.json")


def _load():
    """{name: record}. A missing file is no presets; an unreadable one is logged and
    treated as none. It is never overwritten until the next save, which logs that it is
    replacing an unreadable file."""
    path = presets_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("presets", {})
    except Exception as e:
        fld_logger.warn(f"Noise presets: {path} is unreadable ({e}); no user presets loaded")
        return {}


def _store(presets):
    """Write via a temp file and os.replace, so a crash mid-write never truncates the
    store (and never reads and writes the same file in one expression)."""
    path = presets_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"presets": presets}, f, indent=1, default=str)
    os.replace(tmp, path)


def user_preset_names():
    return sorted(_load())


def get_user_preset(name):
    """(graph, NoiseTypeSpec), or None if there is no preset of that name."""
    rec = _load().get(name)
    if rec is None:
        return None
    spec = NoiseTypeSpec(rec["amp"], rec["wavelength"], bool(rec["mask"]),
                         params=dict(rec.get("params", {})))
    return rec["graph"], spec


def reserved_names():
    """Names a user preset may not take: the built-in types and editor templates."""
    from freecad.fields.core.gui.node_editor.nodes import NOISE_TYPE_NAMES, template_names
    return set(NOISE_TYPE_NAMES) | set(template_names())


def validate_name(name):
    """The stripped name, or raise ValueError with a message the dialog shows."""
    name = (name or "").strip()
    if not name:
        raise ValueError("A preset needs a name.")
    if name in reserved_names():
        raise ValueError(f"{name!r} is a built-in type; choose another name.")
    return name


def save_user_preset(name, graph, spec):
    name = validate_name(name)
    presets = _load()
    presets[name] = {"graph": graph, "amp": spec.amp, "wavelength": spec.wavelength,
                     "mask": spec.mask, "params": dict(spec.params)}
    _store(presets)
    return name


def delete_user_preset(name):
    presets = _load()
    if presets.pop(name, None) is not None:
        _store(presets)


def spec_from_object(obj):
    """The NoiseTypeSpec that reproduces `obj` on its own source: Amplitude and Wavelength
    as ratios of its measured (t, s), ignore_back_face as the mask, and every other
    Custom_* value verbatim."""
    from freecad.fields.core.objects.fld_noise_types import measure_source
    t, s = measure_source(obj)
    params = {}
    for prop in obj.PropertiesList:
        if prop.startswith("Custom_") and prop != "Custom_ignore_back_face":
            v = getattr(obj, prop)
            params[prop[len("Custom_"):]] = [v.x, v.y, v.z] if hasattr(v, "x") else v
    return NoiseTypeSpec(obj.Amplitude / t, obj.Wavelength / s,
                         bool(getattr(obj, "Custom_ignore_back_face", False)), params=params)
