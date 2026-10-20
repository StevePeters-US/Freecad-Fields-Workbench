# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.templates

Starter templates for standard presets in the visual noise formula node editor.
"""


def _facing_mask_nodes_and_wires(ibf_param_id="ibf_param",
                                  wave_out_node="wave", wave_out_socket="Out",
                                  disp_node="disp", base_pos=(1280, 160)):
    """Standard facing mask subgraph (NV-037):
    - Front Face Front (0..1)
    - Mix(1.0, front, ibf_param)
    - Math Multiply(wave_output, mask) -> disp.Amount
    - ibf_param -> front_face.Enabled
    """
    bx, by = base_pos
    nodes = [
        {"id": "front_face", "type": "front_face", "pos": [bx, by + 160], "params": {}},
        {"id": "mix_facing", "type": "mix", "pos": [bx + 160, by + 160], "params": {"a": 1.0}},
        {"id": "mul_facing", "type": "math", "pos": [bx + 320, by], "params": {"op": "Multiply (*)"}},
    ]
    wires = [
        {"from_node": ibf_param_id, "from_socket": "Val", "to_node": "front_face", "to_socket": "Enabled"},
        {"from_node": "front_face", "from_socket": "Front", "to_node": "mix_facing", "to_socket": "B"},
        {"from_node": ibf_param_id, "from_socket": "Val", "to_node": "mix_facing", "to_socket": "T"},
        {"from_node": wave_out_node, "from_socket": wave_out_socket, "to_node": "mul_facing", "to_socket": "A"},
        {"from_node": "mix_facing", "from_socket": "Out", "to_node": "mul_facing", "to_socket": "B"},
        {"from_node": "mul_facing", "from_socket": "Out", "to_node": disp_node, "to_socket": "Amount"},
    ]
    return nodes, wires



def _param(pid, name, ptype, default, pos, lo=None, hi=None, step=None):
    p = {"name": name, "ptype": ptype, "default": default}
    if lo is not None:
        p.update({"min": lo, "max": hi, "step": step})
    return {"id": pid, "type": "custom_param", "pos": pos, "params": p}


def _tmpl_wave(profile_type, extra_nodes=(), extra_wires=()):
    """A 1D wave, propagated (Background item 10): coordinate (linear or radial,
    with an additive warp), frequency decay, profile, amplitude envelope, front mask, Displace.
    Every knob defaults to off; the decays and the warp are per wavelength."""
    mask_nodes, mask_wires = _facing_mask_nodes_and_wires(
        ibf_param_id="ibf_param", wave_out_node="env", wave_out_socket="Out",
        disp_node="disp", base_pos=(1700, 160))
    nodes = [
        {"id": "pos", "type": "part", "pos": [40, 100], "params": {}},
        {"id": "dt", "type": "direction_transform", "pos": [180, 100], "params": {}},
        {"id": "sep", "type": "separate_3d", "pos": [360, 60], "params": {}},
        {"id": "pol", "type": "polar", "pos": [360, 220], "params": {}},
        {"id": "mix_rad", "type": "mix", "pos": [560, 100], "params": {}},
        {"id": "inputs", "type": "noise_inputs", "pos": [40, 400], "params": {}},
        _param("rad_param", "radial", "bool", False, [40, 260]),
        _param("warp_str", "warp_str", "float", 0.0, [40, 540], 0.0, 0.5, 0.01),
        _param("warp_ratio", "warp_ratio", "float", 0.5, [40, 680], 0.05, 4.0, 0.05),
        _param("freq_decay", "freq_decay", "float", 0.0, [40, 820], 0.0, 2.0, 0.05),
        _param("amp_decay", "amp_decay", "float", 0.0, [40, 960], 0.0, 2.0, 0.05),
        _param("ibf_param", "ignore_back_face", "bool", True, [40, 1100]),
        {"id": "warp", "type": "warp", "pos": [780, 100], "params": {}},
        {"id": "k_f", "type": "math", "pos": [780, 300], "params": {"op": "Multiply (*)"}},
        {"id": "f_decay", "type": "freq_decay", "pos": [980, 100], "params": {}},
        {"id": "prof", "type": profile_type, "pos": [1180, 100], "params": {}},
        {"id": "k_a", "type": "math", "pos": [1180, 300], "params": {"op": "Multiply (*)"}},
        {"id": "env", "type": "envelope", "pos": [1400, 160], "params": {}},
        {"id": "disp", "type": "displace", "pos": [2200, 160], "params": {}},
        {"id": "out", "type": "output", "pos": [2360, 160], "params": {}},
    ] + list(extra_nodes) + mask_nodes
    wires = [
        ("pos", "p", "dt", "In"),
        ("dt", "Local", "sep", "In"), ("dt", "Local", "pol", "V"),
        ("sep", "X", "mix_rad", "A"), ("pol", "R", "mix_rad", "B"), ("rad_param", "Val", "mix_rad", "T"),
        ("mix_rad", "Out", "warp", "Coord"), ("sep", "Y", "warp", "Across"),
        ("inputs", "freq", "warp", "Freq"), ("warp_str", "Val", "warp", "Strength"),
        ("warp_ratio", "Val", "warp", "Ratio"),
        ("freq_decay", "Val", "k_f", "A"), ("inputs", "freq", "k_f", "B"),
        ("warp", "Out", "f_decay", "Coord"), ("k_f", "Out", "f_decay", "Rate"),
        ("f_decay", "Out", "prof", "Coord"), ("inputs", "freq", "prof", "Freq"),
        ("inputs", "amp", "prof", "Amp"),
        ("amp_decay", "Val", "k_a", "A"), ("inputs", "freq", "k_a", "B"),
        ("prof", "Out", "env", "In"), ("mix_rad", "Out", "env", "Coord"), ("k_a", "Out", "env", "Rate"),
        ("dt", "Direction", "disp", "Direction"), ("disp", "Out", "out", "Displacement"),
    ] + list(extra_wires)
    return {"nodes": nodes,
            "wires": [{"from_node": a, "from_socket": b, "to_node": c, "to_socket": d}
                      for a, b, c, d in wires] + mask_wires}


def _tmpl_waves():
    """Waves: a sine, propagated linearly or radially."""
    return _tmpl_wave("wave_gen")


def _tmpl_square():
    """Square: tanh(sharp * sine), band-limited so it stays continuous."""
    return _tmpl_wave("square_wave",
                      extra_nodes=[_param("sharp", "sharp", "float", 4.0, [40, 1240], 1.0, 20.0, 0.5)],
                      extra_wires=[("sharp", "Val", "prof", "Sharp")])


def _tmpl_sawtooth():
    """Sawtooth: the triangle profile (named for the look, not the waveform)."""
    return _tmpl_wave("triangle_wave")


def _tmpl_pattern(kernel_name):
    """A NOISE_NODES kernel as a graph: the kernel at the object-local point, masked.
    2D kernels displace along local +Z; 3D kernels wire into additive Offset (NV-038)."""
    from .patterns import PATTERN_NODES
    cls = next(c for c in PATTERN_NODES if c.kernel.name == kernel_name)
    is_planar = "2D" in kernel_name

    if is_planar:
        mask_nodes, mask_wires = _facing_mask_nodes_and_wires(
            ibf_param_id="ibf_param",
            wave_out_node="pat", wave_out_socket="Out",
            disp_node="disp", base_pos=(520, 160)
        )
        return {
            "nodes": [
                {"id": "pos", "type": "part", "pos": [40, 100], "params": {}},
                {"id": "dt", "type": "direction_transform", "pos": [140, 100], "params": {}},
                {"id": "inputs", "type": "noise_inputs", "pos": [40, 300], "params": {}},
                {"id": "ibf_param", "type": "custom_param", "pos": [40, 460],
                 "params": {"name": "ignore_back_face", "ptype": "bool", "default": True}},
                {"id": "pat", "type": cls.node_type, "pos": [320, 140], "params": {}},
                {"id": "disp", "type": "displace", "pos": [1340, 160], "params": {}},
                {"id": "out", "type": "output", "pos": [1500, 160], "params": {}},
            ] + mask_nodes,
            "wires": [
                {"from_node": "pos", "from_socket": "p", "to_node": "dt", "to_socket": "In"},
                {"from_node": "dt", "from_socket": "Local", "to_node": "pat", "to_socket": "Position"},
                {"from_node": "inputs", "from_socket": "amp", "to_node": "pat", "to_socket": "Amp"},
                {"from_node": "inputs", "from_socket": "freq", "to_node": "pat", "to_socket": "Freq"},
                {"from_node": "dt", "from_socket": "Direction", "to_node": "disp", "to_socket": "Direction"},
                {"from_node": "disp", "from_socket": "Out", "to_node": "out", "to_socket": "Displacement"},
            ] + mask_wires,
        }
    else:
        # 3D kernel: pattern multiplied by mix(1.0, front, ignore_back_face), wired to Offset
        bx, by = 520, 160
        mask_nodes = [
            {"id": "front_face", "type": "front_face", "pos": [bx, by + 160], "params": {}},
            {"id": "mix_facing", "type": "mix", "pos": [bx + 160, by + 160], "params": {"a": 1.0}},
            {"id": "mul_facing", "type": "math", "pos": [bx + 320, by], "params": {"op": "Multiply (*)"}},
        ]
        mask_wires = [
            {"from_node": "ibf_param", "from_socket": "Val", "to_node": "front_face", "to_socket": "Enabled"},
            {"from_node": "front_face", "from_socket": "Front", "to_node": "mix_facing", "to_socket": "B"},
            {"from_node": "ibf_param", "from_socket": "Val", "to_node": "mix_facing", "to_socket": "T"},
            {"from_node": "pat", "from_socket": "Out", "to_node": "mul_facing", "to_socket": "A"},
            {"from_node": "mix_facing", "from_socket": "Out", "to_node": "mul_facing", "to_socket": "B"},
            {"from_node": "mul_facing", "from_socket": "Out", "to_node": "out", "to_socket": "Offset"},
        ]
        return {
            "nodes": [
                {"id": "pos", "type": "part", "pos": [40, 100], "params": {}},
                {"id": "dt", "type": "direction_transform", "pos": [140, 100], "params": {}},
                {"id": "inputs", "type": "noise_inputs", "pos": [40, 300], "params": {}},
                {"id": "ibf_param", "type": "custom_param", "pos": [40, 460],
                 "params": {"name": "ignore_back_face", "ptype": "bool", "default": False}},
                {"id": "pat", "type": cls.node_type, "pos": [320, 140], "params": {}},
                {"id": "out", "type": "output", "pos": [1100, 160], "params": {}},
            ] + mask_nodes,
            "wires": [
                {"from_node": "pos", "from_socket": "p", "to_node": "dt", "to_socket": "In"},
                {"from_node": "dt", "from_socket": "Local", "to_node": "pat", "to_socket": "Position"},
                {"from_node": "inputs", "from_socket": "amp", "to_node": "pat", "to_socket": "Amp"},
                {"from_node": "inputs", "from_socket": "freq", "to_node": "pat", "to_socket": "Freq"},
            ] + mask_wires,
        }


from freecad.fields.core.sdf.sdf.noise_nodes import NOISE_NODES

_TEMPLATES = {
    "Waves": _tmpl_waves,
    "Square": _tmpl_square,
    "Sawtooth": _tmpl_sawtooth,
}

for _kname in NOISE_NODES:
    _TEMPLATES[_kname] = (lambda n=_kname: _tmpl_pattern(n))

#: The templates the task panel's Type combo offers, in combo order. Every other
#: _TEMPLATES entry is a node-editor starter only. "Custom" is a label, not a template.
NOISE_TYPE_TEMPLATES = ["Waves", "Square", "Sawtooth",
                        "Perlin 2D", "Perlin 3D", "Voronoi 2D", "Voronoi 3D"]
NOISE_TYPE_NAMES = NOISE_TYPE_TEMPLATES + ["Custom"]


def get_template_graph(name):
    """Returns starter node graph dictionary for name, or None if there is no template."""
    builder = _TEMPLATES.get(name)
    return builder() if builder else None


def template_names():
    """Every built-in template name: the Type-combo set and the editor starters."""
    return list(_TEMPLATES)

