# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.objects.fld_noise_shared

Property setup and custom-param resolution shared by `FldNoiseProxy` (3D,
`fld_noise_object.py`) and `FldNoise2DProxy` (2D, `fld_noise2d_object.py`) -- the two
proxies duplicated both near-verbatim before this was extracted (CR-028).
"""


def setup_amplitude_frequency_properties(obj, noise_field_cls, default_preset):
    """Add the Amplitude/AmplitudeMin/AmplitudeMax/Frequency/FrequencyMin/FrequencyMax/
    NoiseType/Formula/Normalization property block.

    `noise_field_cls` supplies `PRESET_NAMES` and `DEFAULT_FORMULA` -- `SdfNoiseField`
    for the 3D proxy, `SdfNoise2DField` for the 2D one. `default_preset` is that type's
    own starting `NoiseType` value ("Perlin" for 3D, "Waves" for 2D).
    """
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
        obj.NoiseType = noise_field_cls.PRESET_NAMES
        obj.NoiseType = default_preset
    if not hasattr(obj, "Formula"):
        obj.addProperty("App::PropertyString", "Formula", "Noise", "Custom GLSL expression (used when NoiseType is Custom)")
        obj.Formula = noise_field_cls.DEFAULT_FORMULA
    if not hasattr(obj, "Normalization"):
        obj.addProperty("App::PropertyFloat", "Normalization", "Noise",
                        "Lipschitz normalization (0 = auto)").Normalization = 0.0


def resolve_custom_params(fp, custom_formula):
    """Parse `custom_formula`'s declared params, lazily add any missing `Custom_<name>`
    document properties (deferred via `QTimer` so this is safe to call mid-recompute),
    and return `{name: current_value}` for every declared param.

    Returns `{}` if `custom_formula` is falsy (the complex-preset / no-formula case).
    """
    custom_params = {}
    if not custom_formula:
        return custom_params
    from freecad.fields.core.sdf.sdf.noise import parse_custom_params
    params = parse_custom_params(custom_formula)
    for param in params:
        pname = param['name']
        ptype = param['type']
        pdefault = param['default']
        prop_name = "Custom_" + pname
        if not hasattr(fp, prop_name):
            prop_type = "App::PropertyBool" if ptype == 'bool' else ("App::PropertyInteger" if ptype == 'int' else "App::PropertyFloat")
            def _add_prop(obj=fp, pt=prop_type, pn=prop_name, pd=pdefault):
                try:
                    if not hasattr(obj, pn):
                        obj.addProperty(pt, pn, "Custom Params", f"Custom parameter {pn}")
                        setattr(obj, pn, pd)
                        obj.Document.recompute()
                except Exception as e:
                    from freecad.fields.core import fld_logger
                    fld_logger.warn(f"Failed to add dynamic property {pn}: {e}")
            from PySide.QtCore import QTimer
            QTimer.singleShot(0, _add_prop)
        custom_params[pname] = getattr(fp, prop_name, pdefault)
    return custom_params
