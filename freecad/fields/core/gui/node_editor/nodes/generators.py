# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.generators

Generators and waveform macro node definitions: sine, square and triangle profiles, and the Envelope, Frequency Decay and Warp 1D wave nodes.
"""
from .base import BaseNode, SocketDef, SocketType
from .bounds import Bound, badd, bmul


def get_default_wave_gen_subgraph():
    """Default internal graph for WaveGeneratorNode: sin(Coord * Freq) * Amp."""
    return {
        "nodes": [
            {"id": "sg_in", "type": "subgraph_inputs", "pos": [40, 120],
             "params": {"outputs": [
                 {"name": "Coord", "type": "float", "default": 0.0},
                 {"name": "Freq", "type": "float", "default": 1.0},
                 {"name": "Amp", "type": "float", "default": 1.0},
             ]}},
            {"id": "mul_f", "type": "math", "pos": [320, 120], "params": {"op": "Multiply (*)"}},
            {"id": "wave", "type": "trig", "pos": [560, 120], "params": {"fn": "sin"}},
            {"id": "mul_a", "type": "math", "pos": [780, 160], "params": {"op": "Multiply (*)"}},
            {"id": "sg_out", "type": "subgraph_outputs", "pos": [1020, 160],
             "params": {"inputs": [{"name": "Out", "type": "float", "default": 0.0}]}},
        ],
        "wires": [
            {"from_node": "sg_in", "from_socket": "Coord", "to_node": "mul_f", "to_socket": "A"},
            {"from_node": "sg_in", "from_socket": "Freq", "to_node": "mul_f", "to_socket": "B"},
            {"from_node": "mul_f", "from_socket": "Out", "to_node": "wave", "to_socket": "In"},
            {"from_node": "wave", "from_socket": "Out", "to_node": "mul_a", "to_socket": "A"},
            {"from_node": "sg_in", "from_socket": "Amp", "to_node": "mul_a", "to_socket": "B"},
            {"from_node": "mul_a", "from_socket": "Out", "to_node": "sg_out", "to_socket": "Out"},
        ]
    }


def _phase_slope(c, f):
    """|d (f * c)| for bounds text."""
    return badd(bmul(c.slope, f.peak), bmul(f.slope, c.peak))


class WaveGeneratorNode(BaseNode):
    node_type = "wave_gen"
    title = "Sine Wave Generator"
    category = "Generators"
    is_subgraph = True
    doc = "Generates a sine wave: sin(6.2831853 * Coord * Freq) * Amp."

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("Coord", SocketType.FLOAT, 0.0, doc="Distance along the wave's travel direction"),
            SocketDef("Freq", SocketType.FLOAT, 1.0, doc="Cycles per mm"),
            SocketDef("Amp", SocketType.FLOAT, 1.0, doc="Peak displacement"),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT, doc="sin(6.2831853 * Coord * Freq) * Amp")]
        self.default_params = {"subgraph_data": get_default_wave_gen_subgraph()}

    def generate_code(self, input_exprs, params):
        from .subgraph import compile_subgraph
        sg = params.get("subgraph_data")
        if not sg or not sg.get("nodes"):
            sg = get_default_wave_gen_subgraph()
        # Freq on this node is cycles per mm (NV-011); the subgraph computes
        # sin(Coord * Freq), so it is handed the phase rate 2*pi*Freq.
        input_exprs = dict(input_exprs)
        input_exprs["Freq"] = f"(6.2831853 * {input_exprs.get('Freq', '1.0')})"
        res, _comments = compile_subgraph(sg, input_exprs)
        c = input_exprs.get("Coord", "0.0")
        f = input_exprs.get("Freq")
        a = input_exprs.get("Amp", "1.0")
        res.setdefault("Out", f"(sin({c} * {f}) * {a})")
        return res

    def bound(self, ins, input_exprs, params, w):
        from .subgraph import compile_subgraph
        sg = params.get("subgraph_data")
        if not sg or not sg.get("nodes"):
            sg = get_default_wave_gen_subgraph()
        f = ins["Freq"]
        fexpr = f"(6.2831853 * {input_exprs.get('Freq', '1.0')})"
        ins2 = dict(ins)
        ins2["Freq"] = Bound(w.var(f"({f.peak} * 6.2831853)"), w.var(f"({f.slope} * 6.2831853)"),
                             fexpr if f.const is not None else None)
        exprs2 = dict(input_exprs)
        exprs2["Freq"] = fexpr
        res, _ = compile_subgraph(sg, exprs2, ins2, w)
        b = res.get("__bounds__", {}).get("Out")
        if b is None:     # the setdefault fallback: sin(c * f) * a
            c, a = ins["Coord"], ins["Amp"]
            return {"Out": (a.peak, badd(bmul(a.peak, _phase_slope(c, ins2["Freq"])), a.slope))}
        return {"Out": b}

    def get_param_comments(self, params):
        from .subgraph import compile_subgraph
        sg = params.get("subgraph_data") or get_default_wave_gen_subgraph()
        _res, comments = compile_subgraph(sg, {})
        return comments


class SquareWaveNode(BaseNode):
    node_type = "square_wave"
    title = "Square Wave"
    category = "Generators"

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("Coord", SocketType.FLOAT, 0.0),
            SocketDef("Freq", SocketType.FLOAT, 1.0, doc="Cycles per mm"),
            SocketDef("Amp", SocketType.FLOAT, 1.0),
            SocketDef("Sharp", SocketType.FLOAT, 4.0),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]

    def generate_code(self, input_exprs, params):
        c = input_exprs.get("Coord", "0.0")
        f = input_exprs.get("Freq", "1.0")
        a = input_exprs.get("Amp", "1.0")
        s = input_exprs.get("Sharp", "4.0")
        return {"Out": f"(tanh({s} * sin(6.2831853 * {c} * {f})) * {a})"}

    def bound(self, ins, input_exprs, params, w):
        # Steepest at the zero crossings: sharp * 2*pi * the phase slope.
        c, f, a, s = ins["Coord"], ins["Freq"], ins["Amp"], ins["Sharp"]
        lphi = f"(6.2831853 * {_phase_slope(c, f)})"
        return {"Out": (a.peak, badd(bmul(a.peak, bmul(s.peak, lphi)), a.slope,
                                     bmul(a.peak, s.slope)))}


class TriangleWaveNode(BaseNode):
    node_type = "triangle_wave"
    title = "Triangle Wave"
    category = "Generators"
    doc = ("Unit triangle wave: rises and falls linearly, peak +Amp, trough -Amp, "
           "period 1/Freq. This is what the 'Sawtooth' preset uses -- that preset is "
           "named for the look, not the waveform.")

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("Coord", SocketType.FLOAT, 0.0,
                      doc="Distance along the wave's travel direction"),
            SocketDef("Freq", SocketType.FLOAT, 1.0, doc="Cycles per unit of Coord"),
            SocketDef("Amp", SocketType.FLOAT, 1.0, doc="Peak displacement"),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]

    def generate_code(self, input_exprs, params):
        c = input_exprs.get("Coord", "0.0")
        f = input_exprs.get("Freq", "1.0")
        a = input_exprs.get("Amp", "1.0")
        return {"Out": f"((4.0 * abs(fract({c} * {f} - 0.25) - 0.5) - 1.0) * {a})"}

    def bound(self, ins, input_exprs, params, w):
        c, f, a = ins["Coord"], ins["Freq"], ins["Amp"]
        return {"Out": (a.peak, badd(bmul(a.peak, f"(4.0 * {_phase_slope(c, f)})"), a.slope))}


class EnvelopeNode(BaseNode):
    node_type = "envelope"
    title = "Envelope"
    category = "Generators"
    doc = ("Fades a wave along its propagation distance: In * exp(-|Rate| * |Coord|).\n"
           "Rate is per mm; the presets wire amp_decay * freq, so 1 loses e per wavelength.")

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("In", SocketType.FLOAT, 0.0, doc="The wave"),
            SocketDef("Coord", SocketType.FLOAT, 0.0, doc="Propagation distance"),
            SocketDef("Rate", SocketType.FLOAT, 0.0, doc="Decay per mm (sign ignored)"),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]

    def generate_code(self, input_exprs, params):
        v = input_exprs.get("In", "0.0")
        c = input_exprs.get("Coord", "0.0")
        k = input_exprs.get("Rate", "0.0")
        return {"Out": f"(({v}) * exp(-abs({k}) * abs({c})))"}

    def bound(self, ins, input_exprs, params, w):
        v, c, k = ins["In"], ins["Coord"], ins["Rate"]
        return {"Out": (v.peak, badd(v.slope, bmul(v.peak, badd(bmul(k.peak, c.slope),
                                                                bmul(k.slope, c.peak)))))}


class FrequencyDecayNode(BaseNode):
    node_type = "freq_decay"
    title = "Frequency Decay"
    category = "Generators"
    doc = ("Stretches the wavelength with distance: Out = sign(c)(1 - exp(-|Rate||c|))/|Rate|.\n"
           "The local frequency falls as exp(-|Rate||c|); it never rises. Rate is per mm;\n"
           "the presets wire freq_decay * freq.")

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("Coord", SocketType.FLOAT, 0.0, doc="Propagation distance"),
            SocketDef("Rate", SocketType.FLOAT, 0.0, doc="Stretch per mm (sign ignored)"),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT, doc="The stretched coordinate")]

    def generate_code(self, input_exprs, params):
        c = input_exprs.get("Coord", "0.0")
        k = input_exprs.get("Rate", "0.0")
        # Out = c * g(x), x = |k||c|; g = (1 - e^-x)/x, with a Taylor series below
        # x = 0.01 where the exact form loses float32 precision.
        x = f"(abs({k}) * abs({c}))"
        g = (f"mix(1.0 - {x} * 0.5 + {x} * {x} / 6.0, "
             f"(1.0 - exp(-{x})) / max({x}, 1e-12), step(0.01, {x}))")
        return {"Out": f"(({c}) * {g})"}

    def bound(self, ins, input_exprs, params, w):
        c, k = ins["Coord"], ins["Rate"]
        # d/dc = exp(-k|c|) <= 1; d/dk <= c^2 / 2 (unbounded unless k is constant)
        return {"Out": (c.peak, badd(c.slope, bmul(k.slope, bmul("0.5", bmul(c.peak, c.peak)))))}


class WarpNode(BaseNode):
    node_type = "warp"
    title = "Warp"
    category = "Generators"
    doc = ("Wobbles a coordinate: Out = Coord + (Strength / Freq) * sin(2*pi * Ratio * Freq * Across).\n"
           "Strength is in wavelengths, Ratio is the wobble's frequency over the wave's.\n"
           "Additive, so it looks the same everywhere on the part.")

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("Coord", SocketType.FLOAT, 0.0, doc="The coordinate to wobble"),
            SocketDef("Across", SocketType.FLOAT, 0.0, doc="Where along the wavefront"),
            SocketDef("Freq", SocketType.FLOAT, 0.1, doc="The wave's cycles per mm"),
            SocketDef("Strength", SocketType.FLOAT, 0.0, doc="Wobble, in wavelengths"),
            SocketDef("Ratio", SocketType.FLOAT, 0.5, doc="Wobble frequency / wave frequency"),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]

    def generate_code(self, input_exprs, params):
        c = input_exprs.get("Coord", "0.0")
        a = input_exprs.get("Across", "0.0")
        f = input_exprs.get("Freq", "0.1")
        s = input_exprs.get("Strength", "0.0")
        r = input_exprs.get("Ratio", "0.5")
        return {"Out": f"(({c}) + ({s}) / max(abs({f}), 1e-9) * sin(6.2831853 * ({r}) * ({f}) * ({a})))"}

    def bound(self, ins, input_exprs, params, w):
        c, a, f, s, r = (ins[k] for k in ("Coord", "Across", "Freq", "Strength", "Ratio"))
        if f.const is None:
            return {"Out": ("inf", "inf")}
        peak = badd(c.peak, f"bdiv({s.peak}, abs({f.const}))")
        slope = badd(c.slope, bmul("6.2831853", bmul(bmul(s.peak, r.peak), a.slope)),
                     bmul(s.slope, "inf"), bmul(r.slope, "inf"))
        return {"Out": (peak, slope)}
