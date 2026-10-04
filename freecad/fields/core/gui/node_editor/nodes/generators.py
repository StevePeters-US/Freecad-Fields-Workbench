# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.generators

Generators and waveform macro node definitions: wave generator, square wave, and radial wave.
"""
from .base import BaseNode, SocketDef, SocketType


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


class WaveGeneratorNode(BaseNode):
    node_type = "wave_gen"
    title = "Sine Wave Generator"
    category = "Generators"
    is_subgraph = True
    doc = "Generates a sine wave: sin(Coord * Freq) * Amp."

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("Coord", SocketType.FLOAT, 0.0, doc="Distance along the wave's travel direction"),
            SocketDef("Freq", SocketType.FLOAT, 1.0, doc="Cycles per unit of Coord"),
            SocketDef("Amp", SocketType.FLOAT, 1.0, doc="Peak displacement"),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT, doc="sin(Coord * Freq) * Amp")]
        self.default_params = {"subgraph_data": get_default_wave_gen_subgraph()}

    def generate_code(self, input_exprs, params):
        from .subgraph import compile_subgraph
        sg = params.get("subgraph_data")
        if not sg or not sg.get("nodes"):
            sg = get_default_wave_gen_subgraph()
        res, _comments = compile_subgraph(sg, input_exprs)
        c = input_exprs.get("Coord", "0.0")
        f = input_exprs.get("Freq", "1.0")
        a = input_exprs.get("Amp", "1.0")
        res.setdefault("Out", f"(sin({c} * {f}) * {a})")
        return res

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
            SocketDef("Freq", SocketType.FLOAT, 1.0),
            SocketDef("Amp", SocketType.FLOAT, 1.0),
            SocketDef("Sharp", SocketType.FLOAT, 4.0),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]

    def generate_code(self, input_exprs, params):
        c = input_exprs.get("Coord", "0.0")
        f = input_exprs.get("Freq", "1.0")
        a = input_exprs.get("Amp", "1.0")
        s = input_exprs.get("Sharp", "4.0")
        return {"Out": f"(tanh({s} * sin({c} * {f})) * {a})"}


class RadialWaveNode(BaseNode):
    node_type = "radial_wave"
    title = "Radial Wave"
    category = "Generators"

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("R", SocketType.FLOAT, 0.0),
            SocketDef("Freq", SocketType.FLOAT, 1.0),
            SocketDef("Amp", SocketType.FLOAT, 1.0),
            SocketDef("Decay", SocketType.FLOAT, 0.0),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]

    def generate_code(self, input_exprs, params):
        r = input_exprs.get("R", "r")
        f = input_exprs.get("Freq", "1.0")
        a = input_exprs.get("Amp", "1.0")
        d = input_exprs.get("Decay", "0.0")
        return {"Out": f"(sin({r} * {f}) * {a} * exp(-({r}) * ({d})))"}


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
