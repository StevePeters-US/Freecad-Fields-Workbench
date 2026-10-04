# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.templates

Starter templates for standard presets in the visual noise formula node editor.
"""


def _tmpl_waves():
    """Node-graph twin of SdfNoiseField.PRESETS["Waves"].

    Pinned against the preset string by test_template_graph_matches_preset (NE-006).
    A plain sine wave projected along local X onto the displacement axis.
    Direction and projection are controlled via ProjectionControlNode.
    """
    return {
        "nodes": [
            {"id": "c2d", "type": "position", "pos": [40, 100], "params": {}},
            {"id": "proj", "type": "projection_control", "pos": [240, 100],
             "params": {"direction": [0.0, 0.0, 1.0], "roll": 0.0}},
            {"id": "inputs", "type": "noise_inputs", "pos": [40, 320], "params": {}},
            {"id": "ibf_param", "type": "custom_param", "pos": [40, 480],
             "params": {"name": "ignore_back_face", "ptype": "bool", "default": False}},
            {"id": "wave", "type": "wave_gen", "pos": [480, 160], "params": {}},
            {"id": "ignore_back", "type": "ignore_back_face", "pos": [680, 160], "params": {}},
            {"id": "disp", "type": "displace", "pos": [880, 160], "params": {"dir": [0.0, 0.0, 1.0]}},
            {"id": "out", "type": "output", "pos": [1040, 160], "params": {}},
        ],
        "wires": [
            {"from_node": "c2d", "from_socket": "p", "to_node": "proj", "to_socket": "Position"},
            {"from_node": "proj", "from_socket": "U", "to_node": "wave", "to_socket": "Coord"},
            {"from_node": "inputs", "from_socket": "freq", "to_node": "wave", "to_socket": "Freq"},
            {"from_node": "inputs", "from_socket": "amp", "to_node": "wave", "to_socket": "Amp"},
            {"from_node": "wave", "from_socket": "Out", "to_node": "ignore_back", "to_socket": "Value"},
            {"from_node": "ibf_param", "from_socket": "Val", "to_node": "ignore_back", "to_socket": "Ignore Back Face"},
            {"from_node": "ignore_back", "from_socket": "Out", "to_node": "disp", "to_socket": "Amount"},
            {"from_node": "proj", "from_socket": "Direction", "to_node": "disp", "to_socket": "Direction"},
            {"from_node": "disp", "from_socket": "Out", "to_node": "out", "to_socket": "Displacement"},
        ]
    }


def _tmpl_lobed_wave(wave_type, id_prefix, sharp_node=None):
    """Shared builder for Square and Sawtooth: two lobes at coord = rotate(U, V, angle_n).U',
    feeding a wave node each, summed, masked with ignore_back_face, and displaced along Direction.
    """
    def _lobe(sfx, ang_id, freq_from, amp_from, y_off):
        nodes = [
            {"id": "rot" + sfx, "type": "rotate_2d", "pos": [460, 60 + y_off], "params": {}},
            {"id": id_prefix + sfx, "type": wave_type, "pos": [700, 60 + y_off], "params": {}},
        ]
        wires = [
            {"from_node": "proj", "from_socket": "U", "to_node": "rot" + sfx, "to_socket": "U"},
            {"from_node": "proj", "from_socket": "V", "to_node": "rot" + sfx, "to_socket": "V"},
            {"from_node": ang_id, "from_socket": "Val", "to_node": "rot" + sfx, "to_socket": "Angle"},
            {"from_node": "rot" + sfx, "from_socket": "U'", "to_node": id_prefix + sfx, "to_socket": "Coord"},
        ]
        if sharp_node is not None:
            wires.append({"from_node": "sharp", "from_socket": "Val",
                          "to_node": id_prefix + sfx, "to_socket": "Sharp"})
        wires.append(dict(freq_from, to_node=id_prefix + sfx, to_socket="Freq"))
        wires.append(dict(amp_from, to_node=id_prefix + sfx, to_socket="Amp"))
        return nodes, wires

    n1, w1 = _lobe("1", "ang",
                   {"from_node": "inputs", "from_socket": "freq"},
                   {"from_node": "inputs", "from_socket": "amp"}, 0)
    n2, w2 = _lobe("2", "ang2",
                   {"from_node": "freq2", "from_socket": "Val"},
                   {"from_node": "amp2", "from_socket": "Val"}, 300)

    y = 590
    param_nodes = []
    if sharp_node is not None:
        param_nodes.append(dict(sharp_node, pos=[40, y]))
        y += 140
    param_nodes.append({"id": "amp2", "type": "custom_param", "pos": [40, y],
                        "params": {"name": "amp2", "ptype": "float", "default": 0.0,
                                   "min": 0.0, "max": 1.0, "step": 0.05}})
    y += 140
    param_nodes.append({"id": "freq2", "type": "custom_param", "pos": [40, y],
                        "params": {"name": "freq2", "ptype": "float", "default": 1.0,
                                   "min": 0.1, "max": 10.0, "step": 0.1}})
    y += 140
    param_nodes.append({"id": "ang2", "type": "custom_param", "pos": [40, y],
                        "params": {"name": "angle2", "ptype": "float", "default": 90.0,
                                   "min": 0.0, "max": 360.0, "step": 5.0}})
    y += 140
    param_nodes.append({"id": "ibf_param", "type": "custom_param", "pos": [40, y],
                        "params": {"name": "ignore_back_face", "ptype": "bool", "default": False}})

    return {
        "nodes": [
            {"id": "c2d", "type": "position", "pos": [40, 100], "params": {}},
            {"id": "proj", "type": "projection_control", "pos": [220, 100],
             "params": {"direction": [0.0, 0.0, 1.0], "roll": 0.0}},
            {"id": "inputs", "type": "noise_inputs", "pos": [40, 330], "params": {}},
            {"id": "ang", "type": "custom_param", "pos": [40, 450],
             "params": {"name": "angle", "ptype": "float", "default": 0.0,
                        "min": 0.0, "max": 360.0, "step": 5.0}},
        ] + param_nodes + [
            {"id": "sum", "type": "math", "pos": [1160, 220], "params": {"op": "Add (+)"}},
            {"id": "ignore_back", "type": "ignore_back_face", "pos": [1300, 220], "params": {}},
            {"id": "disp", "type": "displace", "pos": [1460, 220], "params": {"dir": [0.0, 0.0, 1.0]}},
            {"id": "out", "type": "output", "pos": [1600, 220], "params": {}},
        ] + n1 + n2,
        "wires": w1 + w2 + [
            {"from_node": "c2d", "from_socket": "p", "to_node": "proj", "to_socket": "Position"},
            {"from_node": id_prefix + "1", "from_socket": "Out", "to_node": "sum", "to_socket": "A"},
            {"from_node": id_prefix + "2", "from_socket": "Out", "to_node": "sum", "to_socket": "B"},
            {"from_node": "sum", "from_socket": "Out", "to_node": "ignore_back", "to_socket": "Value"},
            {"from_node": "ibf_param", "from_socket": "Val", "to_node": "ignore_back", "to_socket": "Ignore Back Face"},
            {"from_node": "ignore_back", "from_socket": "Out", "to_node": "disp", "to_socket": "Amount"},
            {"from_node": "proj", "from_socket": "Direction", "to_node": "disp", "to_socket": "Direction"},
            {"from_node": "disp", "from_socket": "Out", "to_node": "out", "to_socket": "Displacement"},
        ]
    }


def _tmpl_square():
    """Node-graph twin of SdfNoiseField.PRESETS["Square"].

    Each lobe is coord = rotate(U, V, angle_n).U' -- same shape as the Sine Wave
    Generator, just with a Square Wave node on the end.
    """
    sharp_node = {"id": "sharp", "type": "custom_param",
                  "params": {"name": "sharp", "ptype": "float", "default": 4.0,
                             "min": 1.0, "max": 20.0, "step": 0.5}}
    return _tmpl_lobed_wave("square_wave", "sq", sharp_node=sharp_node)


def _tmpl_sawtooth():
    """Node-graph twin of SdfNoiseField.PRESETS["Sawtooth"]."""
    return _tmpl_lobed_wave("triangle_wave", "tri")


def _tmpl_radial_ripple():
    return {
        "nodes": [
            {"id": "c2d", "type": "position", "pos": [50, 100], "params": {}},
            {"id": "proj", "type": "projection_control", "pos": [230, 100],
             "params": {"direction": [0.0, 0.0, 1.0], "roll": 0.0}},
            {"id": "inputs", "type": "noise_inputs", "pos": [50, 300], "params": {}},
            {"id": "decay_param", "type": "custom_param", "pos": [50, 460],
             "params": {"name": "decay", "ptype": "float", "default": 0.05, "min": 0.0, "max": 1.0, "step": 0.01}},
            {"id": "ibf_param", "type": "custom_param", "pos": [50, 600],
             "params": {"name": "ignore_back_face", "ptype": "bool", "default": False}},
            {"id": "rad_wave", "type": "radial_wave", "pos": [490, 140], "params": {}},
            {"id": "ignore_back", "type": "ignore_back_face", "pos": [690, 140], "params": {}},
            {"id": "disp", "type": "displace", "pos": [870, 140], "params": {"dir": [0.0, 0.0, 1.0]}},
            {"id": "out", "type": "output", "pos": [1030, 140], "params": {}},
        ],
        "wires": [
            {"from_node": "c2d", "from_socket": "p", "to_node": "proj", "to_socket": "Position"},
            {"from_node": "proj", "from_socket": "R", "to_node": "rad_wave", "to_socket": "R"},
            {"from_node": "inputs", "from_socket": "freq", "to_node": "rad_wave", "to_socket": "Freq"},
            {"from_node": "inputs", "from_socket": "amp", "to_node": "rad_wave", "to_socket": "Amp"},
            {"from_node": "decay_param", "from_socket": "Val", "to_node": "rad_wave", "to_socket": "Decay"},
            {"from_node": "rad_wave", "from_socket": "Out", "to_node": "ignore_back", "to_socket": "Value"},
            {"from_node": "ibf_param", "from_socket": "Val", "to_node": "ignore_back", "to_socket": "Ignore Back Face"},
            {"from_node": "ignore_back", "from_socket": "Out", "to_node": "disp", "to_socket": "Amount"},
            {"from_node": "proj", "from_socket": "Direction", "to_node": "disp", "to_socket": "Direction"},
            {"from_node": "disp", "from_socket": "Out", "to_node": "out", "to_socket": "Displacement"},
        ]
    }


def _tmpl_simple_sine():
    return {
        "nodes": [
            {"id": "c3d", "type": "position", "pos": [50, 100], "params": {}},
            {"id": "proj", "type": "projection_control", "pos": [230, 100],
             "params": {"direction": [0.0, 0.0, 1.0], "roll": 0.0}},
            {"id": "inputs", "type": "noise_inputs", "pos": [50, 300], "params": {}},
            {"id": "ibf_param", "type": "custom_param", "pos": [50, 460],
             "params": {"name": "ignore_back_face", "ptype": "bool", "default": False}},
            {"id": "wave", "type": "wave_gen", "pos": [470, 120], "params": {}},
            {"id": "ignore_back", "type": "ignore_back_face", "pos": [670, 120], "params": {}},
            {"id": "disp", "type": "displace", "pos": [850, 120], "params": {"dir": [0.0, 0.0, 1.0]}},
            {"id": "out", "type": "output", "pos": [990, 120], "params": {}},
        ],
        "wires": [
            {"from_node": "c3d", "from_socket": "p", "to_node": "proj", "to_socket": "Position"},
            {"from_node": "proj", "from_socket": "U", "to_node": "wave", "to_socket": "Coord"},
            {"from_node": "inputs", "from_socket": "freq", "to_node": "wave", "to_socket": "Freq"},
            {"from_node": "inputs", "from_socket": "amp", "to_node": "wave", "to_socket": "Amp"},
            {"from_node": "wave", "from_socket": "Out", "to_node": "ignore_back", "to_socket": "Value"},
            {"from_node": "ibf_param", "from_socket": "Val", "to_node": "ignore_back", "to_socket": "Ignore Back Face"},
            {"from_node": "ignore_back", "from_socket": "Out", "to_node": "disp", "to_socket": "Amount"},
            {"from_node": "proj", "from_socket": "Direction", "to_node": "disp", "to_socket": "Direction"},
            {"from_node": "disp", "from_socket": "Out", "to_node": "out", "to_socket": "Displacement"},
        ]
    }


def _tmpl_multi_freq():
    return {
        "nodes": [
            {"id": "c3d", "type": "position", "pos": [50, 100], "params": {}},
            {"id": "proj", "type": "projection_control", "pos": [230, 80],
             "params": {"direction": [0.0, 0.0, 1.0], "roll": 0.0}},
            {"id": "inputs", "type": "noise_inputs", "pos": [50, 320], "params": {}},
            {"id": "ibf_param", "type": "custom_param", "pos": [50, 480],
             "params": {"name": "ignore_back_face", "ptype": "bool", "default": False}},
            {"id": "m1", "type": "math", "pos": [450, 80], "params": {"op": "Multiply (*)"}},
            {"id": "m2", "type": "math", "pos": [450, 200], "params": {"op": "Multiply (*)"}},
            {"id": "m3", "type": "math", "pos": [450, 320], "params": {"op": "Multiply (*)"}},
            {"id": "sin1", "type": "trig", "pos": [630, 80], "params": {"fn": "sin"}},
            {"id": "sin2", "type": "trig", "pos": [630, 200], "params": {"fn": "sin"}},
            {"id": "sin3", "type": "trig", "pos": [630, 320], "params": {"fn": "sin"}},
            {"id": "mul12", "type": "math", "pos": [810, 120], "params": {"op": "Multiply (*)"}},
            {"id": "mul123", "type": "math", "pos": [990, 160], "params": {"op": "Multiply (*)"}},
            {"id": "mul_amp", "type": "math", "pos": [1170, 200], "params": {"op": "Multiply (*)"}},
            {"id": "ignore_back", "type": "ignore_back_face", "pos": [1330, 200], "params": {}},
            {"id": "disp", "type": "displace", "pos": [1490, 200], "params": {"dir": [0.0, 0.0, 1.0]}},
            {"id": "out", "type": "output", "pos": [1610, 200], "params": {}},
        ],
        "wires": [
            {"from_node": "c3d", "from_socket": "p", "to_node": "proj", "to_socket": "Position"},
            {"from_node": "proj", "from_socket": "U", "to_node": "m1", "to_socket": "A"},
            {"from_node": "inputs", "from_socket": "freq", "to_node": "m1", "to_socket": "B"},
            {"from_node": "proj", "from_socket": "V", "to_node": "m2", "to_socket": "A"},
            {"from_node": "inputs", "from_socket": "freq", "to_node": "m2", "to_socket": "B"},
            {"from_node": "proj", "from_socket": "Depth", "to_node": "m3", "to_socket": "A"},
            {"from_node": "inputs", "from_socket": "freq", "to_node": "m3", "to_socket": "B"},
            {"from_node": "m1", "from_socket": "Out", "to_node": "sin1", "to_socket": "In"},
            {"from_node": "m2", "from_socket": "Out", "to_node": "sin2", "to_socket": "In"},
            {"from_node": "m3", "from_socket": "Out", "to_node": "sin3", "to_socket": "In"},
            {"from_node": "sin1", "from_socket": "Out", "to_node": "mul12", "to_socket": "A"},
            {"from_node": "sin2", "from_socket": "Out", "to_node": "mul12", "to_socket": "B"},
            {"from_node": "mul12", "from_socket": "Out", "to_node": "mul123", "to_socket": "A"},
            {"from_node": "sin3", "from_socket": "Out", "to_node": "mul123", "to_socket": "B"},
            {"from_node": "mul123", "from_socket": "Out", "to_node": "mul_amp", "to_socket": "A"},
            {"from_node": "inputs", "from_socket": "amp", "to_node": "mul_amp", "to_socket": "B"},
            {"from_node": "mul_amp", "from_socket": "Out", "to_node": "ignore_back", "to_socket": "Value"},
            {"from_node": "ibf_param", "from_socket": "Val", "to_node": "ignore_back", "to_socket": "Ignore Back Face"},
            {"from_node": "ignore_back", "from_socket": "Out", "to_node": "disp", "to_socket": "Amount"},
            {"from_node": "proj", "from_socket": "Direction", "to_node": "disp", "to_socket": "Direction"},
            {"from_node": "disp", "from_socket": "Out", "to_node": "out", "to_socket": "Displacement"},
        ]
    }


def _tmpl_wavy_panel():
    """Wavy panel template using DomainWarp2D and Distance2D with a 1D sine wave perturbation."""
    return {
        "nodes": [
            {"id": "c3d", "type": "position", "pos": [40, 100], "params": {}},
            {"id": "proj", "type": "projection_control", "pos": [220, 80],
             "params": {"direction": [0.0, 0.0, 1.0], "roll": 0.0}},
            {"id": "inputs", "type": "noise_inputs", "pos": [40, 300], "params": {}},
            {"id": "warp_freq", "type": "custom_param", "pos": [40, 440],
             "params": {"name": "warp_freq", "ptype": "float", "default": 0.05, "min": 0.001, "max": 1.0, "step": 0.01}},
            {"id": "warp_str", "type": "custom_param", "pos": [40, 580],
             "params": {"name": "warp_str", "ptype": "float", "default": 0.15, "min": 0.0, "max": 1.0, "step": 0.01}},
            {"id": "cx", "type": "custom_param", "pos": [40, 720],
             "params": {"name": "cx", "ptype": "float", "default": -44.2, "min": -1000.0, "max": 1000.0, "step": 1.0}},
            {"id": "cy", "type": "custom_param", "pos": [40, 860],
             "params": {"name": "cy", "ptype": "float", "default": -23.6, "min": -1000.0, "max": 1000.0, "step": 1.0}},
            {"id": "ibf_param", "type": "custom_param", "pos": [40, 1000],
             "params": {"name": "ignore_back_face", "ptype": "bool", "default": False}},
            {"id": "sine_warp", "type": "wave_gen", "pos": [440, 200], "params": {}},
            {"id": "warp", "type": "domain_warp_2d", "pos": [640, 100], "params": {}},
            {"id": "dist", "type": "distance_2d", "pos": [860, 120], "params": {}},
            {"id": "wave", "type": "wave_gen", "pos": [1060, 160], "params": {}},
            {"id": "ignore_back", "type": "ignore_back_face", "pos": [1240, 180], "params": {}},
            {"id": "disp", "type": "displace", "pos": [1400, 180], "params": {"dir": [0.0, 0.0, 1.0]}},
            {"id": "out", "type": "output", "pos": [1520, 180], "params": {}},
        ],
        "wires": [
            {"from_node": "c3d", "from_socket": "p", "to_node": "proj", "to_socket": "Position"},
            {"from_node": "proj", "from_socket": "U", "to_node": "sine_warp", "to_socket": "Coord"},
            {"from_node": "warp_freq", "from_socket": "Val", "to_node": "sine_warp", "to_socket": "Freq"},
            {"from_node": "proj", "from_socket": "U", "to_node": "warp", "to_socket": "X"},
            {"from_node": "proj", "from_socket": "V", "to_node": "warp", "to_socket": "Y"},
            {"from_node": "sine_warp", "from_socket": "Out", "to_node": "warp", "to_socket": "Noise"},
            {"from_node": "warp_str", "from_socket": "Val", "to_node": "warp", "to_socket": "Strength"},
            {"from_node": "warp", "from_socket": "X'", "to_node": "dist", "to_socket": "X"},
            {"from_node": "warp", "from_socket": "Y'", "to_node": "dist", "to_socket": "Y"},
            {"from_node": "cx", "from_socket": "Val", "to_node": "dist", "to_socket": "CenterX"},
            {"from_node": "cy", "from_socket": "Val", "to_node": "dist", "to_socket": "CenterY"},
            {"from_node": "dist", "from_socket": "Dist", "to_node": "wave", "to_socket": "Coord"},
            {"from_node": "inputs", "from_socket": "freq", "to_node": "wave", "to_socket": "Freq"},
            {"from_node": "inputs", "from_socket": "amp", "to_node": "wave", "to_socket": "Amp"},
            {"from_node": "wave", "from_socket": "Out", "to_node": "ignore_back", "to_socket": "Value"},
            {"from_node": "ibf_param", "from_socket": "Val", "to_node": "ignore_back", "to_socket": "Ignore Back Face"},
            {"from_node": "ignore_back", "from_socket": "Out", "to_node": "disp", "to_socket": "Amount"},
            {"from_node": "proj", "from_socket": "Direction", "to_node": "disp", "to_socket": "Direction"},
            {"from_node": "disp", "from_socket": "Out", "to_node": "out", "to_socket": "Displacement"},
        ]
    }


_TEMPLATES = {
    "Waves": _tmpl_waves,
    "Square": _tmpl_square,
    "Sawtooth": _tmpl_sawtooth,
    "Radial Ripple": _tmpl_radial_ripple,
    "Square Wave": _tmpl_square,
    "Simple Sine": _tmpl_simple_sine,
    "Multi-Frequency": _tmpl_multi_freq,
    "Wavy Panel": _tmpl_wavy_panel,
}


def get_template_graph(name):
    """Returns starter node graph dictionary for name, or None if there is no template."""
    builder = _TEMPLATES.get(name)
    return builder() if builder else None

