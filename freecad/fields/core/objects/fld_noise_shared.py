# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.objects.fld_noise_shared

Property setup and custom-param resolution for `FldNoiseProxy` (fld_noise_object.py).
Keeps the property wall out of the proxy; `FldNoise2DProxy` was folded in on the merge.
"""
import FreeCAD


def setup_amplitude_frequency_properties(obj):
    """Add the full Noise property block: Amplitude/Wavelength and their limits,
    plus NoiseType/Formula/Normalization. Orientation and center are no longer
    hand-rolled scalars here -- they are the object's own `Placement`, which
    `Part::FeaturePython` already provides.

    One noise proxy, one property set -- the `noise_field_cls`/`default_preset`
    parameters this took existed only to serve the deleted 2D proxy.
    """
    from freecad.fields.core.objects.fld_noise_types import defaults_for, REFERENCE_T, REFERENCE_S
    d = defaults_for("Waves", REFERENCE_T, REFERENCE_S)
    if not hasattr(obj, "Amplitude"):
        obj.addProperty("App::PropertyFloat", "Amplitude", "Noise", "Noise amplitude").Amplitude = d["Amplitude"]
    if not hasattr(obj, "AmplitudeMin"):
        obj.addProperty("App::PropertyFloat", "AmplitudeMin", "Noise", "Min amplitude limit").AmplitudeMin = d["AmplitudeMin"]
    if not hasattr(obj, "AmplitudeMax"):
        obj.addProperty("App::PropertyFloat", "AmplitudeMax", "Noise", "Max amplitude limit").AmplitudeMax = d["AmplitudeMax"]
    if not hasattr(obj, "Wavelength"):
        obj.addProperty("App::PropertyFloat", "Wavelength", "Noise", "Noise wavelength (mm)").Wavelength = d["Wavelength"]
    if not hasattr(obj, "WavelengthMin"):
        obj.addProperty("App::PropertyFloat", "WavelengthMin", "Noise", "Min wavelength limit").WavelengthMin = d["WavelengthMin"]
    if not hasattr(obj, "WavelengthMax"):
        obj.addProperty("App::PropertyFloat", "WavelengthMax", "Noise", "Max wavelength limit").WavelengthMax = d["WavelengthMax"]
    from freecad.fields.core.gui.node_editor.nodes import NOISE_TYPE_NAMES
    if not hasattr(obj, "NoiseType"):
        obj.addProperty("App::PropertyEnumeration", "NoiseType", "Noise", "Noise formula preset")
        obj.NoiseType = NOISE_TYPE_NAMES
        obj.NoiseType = "Waves"
    if not hasattr(obj, "Formula"):
        obj.addProperty("App::PropertyString", "Formula", "Noise", "Custom GLSL expression (used when NoiseType is Custom)")
        obj.Formula = ""
        if hasattr(obj, "setEditorMode"):
            obj.setEditorMode("Formula", 1)
    if not hasattr(obj, "NodeGraphJson"):
        obj.addProperty("App::PropertyString", "NodeGraphJson", "Noise", "Serialized node graph")
        obj.NodeGraphJson = ""
        if hasattr(obj, "setEditorMode"):
            obj.setEditorMode("NodeGraphJson", 1)
    if not hasattr(obj, "Normalization"):
        obj.addProperty("App::PropertyFloat", "Normalization", "Noise",
                        "Lipschitz normalization (0 = auto)").Normalization = 0.0
    if obj.Placement.isIdentity():
        # Local +Z is the axis `SdfNoiseField.direction` reads back out; rotating
        # it to world -Z reproduces the historical default Direction (0, 0, -1)
        # ("noise pushes down into the stock") with no stored Direction property.
        obj.Placement = FreeCAD.Placement(
            FreeCAD.Vector(0, 0, 0),
            FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), FreeCAD.Vector(0, 0, -1)),
        )


_CUSTOM_PREFIX = "Custom_"
_PROP_TYPES = {
    "bool": "App::PropertyBool", "int": "App::PropertyInteger",
    "float": "App::PropertyFloat", "slider": "App::PropertyFloat",
    "vec2": "App::PropertyVector", "vec3": "App::PropertyVector",
}


def _to_prop_value(ptype, value):
    """A declared default or a caller's value, as the property type stores it."""
    if ptype == "bool":
        return bool(value)
    if ptype == "int":
        return int(value)
    if ptype in ("vec2", "vec3"):
        if hasattr(value, "x"):
            return FreeCAD.Vector(float(value.x), float(value.y), float(getattr(value, "z", 0.0)))
        v = list(value) if isinstance(value, (tuple, list)) else [0.0, 0.0]
        return FreeCAD.Vector(float(v[0]), float(v[1]), float(v[2]) if len(v) > 2 else 0.0)
    return float(value)


def apply_graph(obj, graph, label, values=None):
    """Make `graph` this noise object's state. The ONLY writer of NodeGraphJson,
    Formula, NoiseType and the Custom_* properties (NV-007).

    Compiles first and writes nothing if that fails, so a broken graph can never leave
    Formula and NodeGraphJson describing different things. Custom_* properties are made
    to match what the new formula declares: missing ones are added at their declared
    default, ones it no longer declares (or declares with another type) are removed,
    and same-name same-type ones keep their value -- the user's tuning survives an
    editor Apply. `values` ({name: value}) then overrides any of them.

    Returns None on success, or the compile error string.
    """
    import json
    from freecad.fields.core.gui.node_editor.nodes import compile_graph
    from freecad.fields.core.sdf.sdf.formula_eval import parse_custom_params
    res = compile_graph(graph)
    if res.err or not res.formula:
        return res.err or "graph compiled to an empty formula"
    declared = {p["name"]: p for p in parse_custom_params(res.formula)}
    props = getattr(obj, "PropertiesList", None)
    if props is None and hasattr(obj, "_props"):
        props = list(obj._props.keys())
    for prop in list(props or []):
        if not prop.startswith(_CUSTOM_PREFIX):
            continue
        p = declared.get(prop[len(_CUSTOM_PREFIX):])
        prop_type = obj.getTypeIdOfProperty(prop) if hasattr(obj, "getTypeIdOfProperty") else getattr(obj, "_props", {}).get(prop)
        if p is None or (prop_type and prop_type != _PROP_TYPES.get(p["type"], "App::PropertyFloat")):
            if hasattr(obj, "removeProperty"):
                obj.removeProperty(prop)
            elif hasattr(obj, "_props"):
                obj._props.pop(prop, None)
    values = values or {}
    for name, p in declared.items():
        prop = _CUSTOM_PREFIX + name
        ptype = p["type"]
        if not hasattr(obj, prop):
            obj.addProperty(_PROP_TYPES.get(ptype, "App::PropertyFloat"), prop,
                            "Custom Params", f"Custom parameter {name}")
            setattr(obj, prop, _to_prop_value(ptype, p["default"]))
        if name in values:
            setattr(obj, prop, _to_prop_value(ptype, values[name]))
    obj.NodeGraphJson = json.dumps(graph, default=str)
    obj.Formula = res.formula
    obj.NoiseType = label
    return None


def set_graph_param_limits(graph, name, min_val, max_val, step_val):
    """Write a slider's limits into the custom_param node that declares `name`,
    recursing into subgraphs. Returns True if one was found. The limits are the
    node's, so they are edited there and re-applied, never patched into Formula."""
    from freecad.fields.core.gui.node_editor.nodes import glsl_param_identifier
    found = False
    for node in graph.get("nodes", []):
        params = node.get("params", {})
        if (node.get("type") == "custom_param"
                and glsl_param_identifier(params.get("name", "param")) == name):
            params.update(min=min_val, max=max_val, step=step_val)
            found = True
        sub = params.get("subgraph_data")
        if sub and set_graph_param_limits(sub, name, min_val, max_val, step_val):
            found = True
    return found


def resolve_custom_params(fp, custom_formula):
    """Parse `custom_formula`'s declared params and return `{name: current_value}`
    for every declared param. Read-only; `apply_graph` creates and updates properties (NV-007).
    """
    custom_params = {}
    from freecad.fields.core.sdf.sdf.formula_eval import parse_custom_params
    from freecad.fields.core import fld_logger
    params = parse_custom_params(custom_formula) if custom_formula else []
    for param in params:
        pname = param['name']
        ptype = param['type']
        pdefault = param['default']
        prop_name = _CUSTOM_PREFIX + pname
        if not hasattr(fp, prop_name):
            fld_logger.debug_throttled(
                "noise_custom_param_missing",
                f"resolve_custom_params: property {prop_name!r} missing on object, using default {pdefault!r}"
            )
        val = getattr(fp, prop_name, pdefault)
        if ptype == 'vec2':
            if hasattr(val, 'x') and hasattr(val, 'y'):
                custom_params[pname] = (float(val.x), float(val.y))
            elif isinstance(val, (tuple, list)):
                custom_params[pname] = (float(val[0]), float(val[1]))
            else:
                custom_params[pname] = val
        elif ptype == 'vec3':
            if hasattr(val, 'x') and hasattr(val, 'y') and hasattr(val, 'z'):
                custom_params[pname] = (float(val.x), float(val.y), float(val.z))
            elif isinstance(val, (tuple, list)):
                custom_params[pname] = (float(val[0]), float(val[1]), float(val[2]))
            else:
                custom_params[pname] = val
        else:
            custom_params[pname] = val
    return custom_params
