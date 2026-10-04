# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.objects.fld_noise_shared

Property setup and custom-param resolution for `FldNoiseProxy` (fld_noise_object.py).
Keeps the property wall out of the proxy; `FldNoise2DProxy` was folded in on the merge.
"""
import FreeCAD


def setup_amplitude_frequency_properties(obj):
    """Add the full Noise property block: Amplitude/Frequency and their limits,
    plus NoiseType/Formula/Normalization. Orientation and center are no longer
    hand-rolled scalars here -- they are the object's own `Placement`, which
    `Part::FeaturePython` already provides.

    One noise proxy, one property set -- the `noise_field_cls`/`default_preset`
    parameters this took existed only to serve the deleted 2D proxy.
    """
    from freecad.fields.core.sdf.sdf.noise import SdfNoiseField
    if not hasattr(obj, "Amplitude"):
        obj.addProperty("App::PropertyFloat", "Amplitude", "Noise", "Noise amplitude").Amplitude = 5.0
    if not hasattr(obj, "AmplitudeMin"):
        obj.addProperty("App::PropertyFloat", "AmplitudeMin", "Noise", "Min amplitude limit").AmplitudeMin = 0.0
    if not hasattr(obj, "AmplitudeMax"):
        obj.addProperty("App::PropertyFloat", "AmplitudeMax", "Noise", "Max amplitude limit").AmplitudeMax = 10.0
    if not hasattr(obj, "Frequency"):
        obj.addProperty("App::PropertyFloat", "Frequency", "Noise", "Noise frequency").Frequency = 0.1
    if not hasattr(obj, "FrequencyMin"):
        obj.addProperty("App::PropertyFloat", "FrequencyMin", "Noise", "Min frequency limit").FrequencyMin = 0.001
    if not hasattr(obj, "FrequencyMax"):
        obj.addProperty("App::PropertyFloat", "FrequencyMax", "Noise", "Max frequency limit").FrequencyMax = 1.0
    if not hasattr(obj, "NoiseType"):
        obj.addProperty("App::PropertyEnumeration", "NoiseType", "Noise", "Noise formula preset")
        obj.NoiseType = SdfNoiseField.PRESET_NAMES
        obj.NoiseType = "Waves"
    if not hasattr(obj, "Formula"):
        obj.addProperty("App::PropertyString", "Formula", "Noise", "Custom GLSL expression (used when NoiseType is Custom)")
        obj.Formula = SdfNoiseField.DEFAULT_FORMULA
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


def resolve_custom_params(fp, custom_formula):
    """Parse `custom_formula`'s declared params, lazily add any missing `Custom_<name>`
    document properties (deferred via `QTimer` so this is safe to call mid-recompute),
    and return `{name: current_value}` for every declared param.
    """
    custom_params = {}
    from freecad.fields.core.sdf.sdf.formula_eval import parse_custom_params
    params = parse_custom_params(custom_formula) if custom_formula else []
    for param in params:
        pname = param['name']
        ptype = param['type']
        pdefault = param['default']
        prop_name = "Custom_" + pname
        if not hasattr(fp, prop_name):
            if ptype == 'bool':
                prop_type = "App::PropertyBool"
            elif ptype == 'int':
                prop_type = "App::PropertyInteger"
            elif ptype in ('vec2', 'vec3'):
                prop_type = "App::PropertyVector"
            else:
                prop_type = "App::PropertyFloat"

            if ptype in ('vec2', 'vec3'):
                import FreeCAD
                if isinstance(pdefault, (tuple, list)):
                    z_val = float(pdefault[2]) if len(pdefault) > 2 else 0.0
                    pd_vec = FreeCAD.Vector(float(pdefault[0]), float(pdefault[1]), z_val)
                else:
                    pd_vec = FreeCAD.Vector(0.0, 0.0, 0.0)
                pd = pd_vec
            else:
                pd = pdefault

            def _add_prop(obj=fp, pt=prop_type, pn=prop_name, val=pd):
                try:
                    if not hasattr(obj, pn):
                        obj.addProperty(pt, pn, "Custom Params", f"Custom parameter {pn}")
                        setattr(obj, pn, val)
                        obj.Document.recompute()
                except Exception as e:
                    from freecad.fields.core import fld_logger
                    fld_logger.warn(f"Failed to add dynamic property {pn}: {e}")
            from PySide.QtCore import QTimer
            QTimer.singleShot(0, _add_prop)
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
