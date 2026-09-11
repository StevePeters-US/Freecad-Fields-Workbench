# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/gui/node_editor/node_definitions.py

Data models, node definitions, code generation, and templates for the
FreeCAD Fields visual noise formula node editor.
"""
from collections import deque
import json


class SocketType:
    FLOAT = "float"   # 1D Scalar
    VEC2 = "vec2"     # 2D Vector
    VEC3 = "vec3"     # 3D Vector
    BOOL = "bool"     # Boolean Toggle


SOCKET_TYPE_NAMES = {
    SocketType.FLOAT: "1D Float",
    SocketType.VEC2: "2D Vector",
    SocketType.VEC3: "3D Vector",
    SocketType.BOOL: "Boolean",
}

SOCKET_TYPE_SHORT_LABELS = {
    SocketType.FLOAT: "1D",
    SocketType.VEC2: "2D",
    SocketType.VEC3: "3D",
    SocketType.BOOL: "Bool",
}


class SocketDef:
    def __init__(self, name, socket_type=SocketType.FLOAT, default_value=0.0):
        self.name = name
        self.socket_type = socket_type
        self.default_value = default_value

    def to_dict(self):
        return {
            "name": self.name,
            "type": self.socket_type,
            "default": self.default_value,
        }


class BaseNode:
    """Base definition for a node type in the graph editor."""
    node_type = "base"
    title = "Base Node"
    category = "General"
    is_2d_only = False
    is_3d_only = False

    def __init__(self):
        self.inputs = []
        self.outputs = []
        self.default_params = {}

    def generate_code(self, input_exprs, params):
        """Returns dict of {output_socket_name: glsl_expression_str}."""
        return {}

    def get_param_comments(self, params):
        """Returns list of // @param comment strings."""
        return []


# ─── Inputs ───────────────────────────────────────────────────────────────────

class Coordinates3DNode(BaseNode):
    node_type = "coords_3d"
    title = "3D Coordinates"
    category = "Inputs"
    is_3d_only = True

    def __init__(self):
        super().__init__()
        self.inputs = []
        self.outputs = [
            SocketDef("p", SocketType.VEC3),
            SocketDef("p.xy", SocketType.VEC2),
            SocketDef("p.x", SocketType.FLOAT),
            SocketDef("p.y", SocketType.FLOAT),
            SocketDef("p.z", SocketType.FLOAT),
            SocketDef("len(p)", SocketType.FLOAT),
        ]

    def generate_code(self, input_exprs, params):
        return {
            "p": "p",
            "p.xy": "p.xy",
            "p.x": "p.x",
            "p.y": "p.y",
            "p.z": "p.z",
            "len(p)": "length(p)",
        }


class Coordinates2DNode(BaseNode):
    node_type = "coords_2d"
    title = "2D Coordinates"
    category = "Inputs"
    is_2d_only = True

    def __init__(self):
        super().__init__()
        self.inputs = []
        self.outputs = [
            SocketDef("xy", SocketType.VEC2),
            SocketDef("x", SocketType.FLOAT),
            SocketDef("y", SocketType.FLOAT),
            SocketDef("z", SocketType.FLOAT),
            SocketDef("r", SocketType.FLOAT),
        ]

    def generate_code(self, input_exprs, params):
        return {
            "xy": "vec2(x, y)",
            "x": "x",
            "y": "y",
            "z": "z",
            "r": "r",
        }


class NoiseInputsNode(BaseNode):
    node_type = "noise_inputs"
    title = "Noise Inputs"
    category = "Inputs"

    def __init__(self):
        super().__init__()
        self.inputs = []
        self.outputs = [
            SocketDef("amp", SocketType.FLOAT, 1.0),
            SocketDef("freq", SocketType.FLOAT, 0.1),
        ]

    def generate_code(self, input_exprs, params):
        return {
            "amp": "amp",
            "freq": "freq",
        }


class ConstantNode(BaseNode):
    node_type = "constant"
    title = "Constant"
    category = "Inputs"

    def __init__(self):
        super().__init__()
        self.inputs = []
        self.outputs = [SocketDef("Val", SocketType.FLOAT, 1.0)]
        self.default_params = {"val": 1.0}

    def generate_code(self, input_exprs, params):
        val = float(params.get("val", 1.0))
        return {"Val": f"{val:.4g}"}


class CustomParamNode(BaseNode):
    node_type = "custom_param"
    title = "Custom Parameter"
    category = "Inputs"

    def __init__(self):
        super().__init__()
        self.inputs = []
        self.outputs = [SocketDef("Val", SocketType.FLOAT, 1.0)]
        self.default_params = {
            "name": "param",
            "ptype": "float",
            "default": 1.0,
            "min": 0.0,
            "max": 5.0,
            "step": 0.1,
        }

    def generate_code(self, input_exprs, params):
        name = str(params.get("name", "param")).strip() or "param"
        return {"Val": name}

    def get_param_comments(self, params):
        name = str(params.get("name", "param")).strip() or "param"
        ptype = params.get("ptype", "float")

        def _fmt(val):
            try:
                v = float(val)
                if v.is_integer():
                    return str(int(v))
                return f"{v:.4f}".rstrip('0').rstrip('.')
            except Exception:
                return str(val)

        def_str = _fmt(params.get("default", 1.0))
        min_str = _fmt(params.get("min", 0.0))
        max_str = _fmt(params.get("max", 5.0))
        step_str = _fmt(params.get("step", 0.1))

        if ptype == "bool":
            def_v = 1 if params.get("default", 1) in (1, 1.0, True, "1", "true", "True") else 0
            return [f"// @param bool {name} {def_v}"]
        elif ptype == "int":
            return [f"// @param int {name} {def_str} {min_str} {max_str}"]
        return [f"// @param {ptype} {name} {def_str} {min_str} {max_str} {step_str}"]


# ─── Math & Functions ─────────────────────────────────────────────────────────

class MathNode(BaseNode):
    node_type = "math"
    title = "Math"
    category = "Math"

    OPS = {
        "Add (+)": lambda a, b: f"({a} + {b})",
        "Subtract (-)": lambda a, b: f"({a} - {b})",
        "Multiply (*)": lambda a, b: f"({a} * {b})",
        "Divide (/)": lambda a, b: f"({a} / {b})",
        "Power (pow)": lambda a, b: f"pow({a}, {b})",
        "Mod (mod)": lambda a, b: f"mod({a}, {b})",
    }

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("A", SocketType.FLOAT, 0.0),
            SocketDef("B", SocketType.FLOAT, 1.0),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]
        self.default_params = {"op": "Add (+)"}

    def generate_code(self, input_exprs, params):
        a = input_exprs.get("A", "0.0")
        b = input_exprs.get("B", "1.0")
        op = params.get("op", "Add (+)")
        fn = self.OPS.get(op, self.OPS["Add (+)"])
        return {"Out": fn(a, b)}


class TrigNode(BaseNode):
    node_type = "trig"
    title = "Trigonometry"
    category = "Trig"

    FNS = ["sin", "cos", "tan", "asin", "acos", "atan", "radians"]

    def __init__(self):
        super().__init__()
        self.inputs = [SocketDef("In", SocketType.FLOAT, 0.0)]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]
        self.default_params = {"fn": "sin"}

    def generate_code(self, input_exprs, params):
        val = input_exprs.get("In", "0.0")
        fn = params.get("fn", "sin")
        return {"Out": f"{fn}({val})"}


class FunctionNode(BaseNode):
    node_type = "function"
    title = "Function"
    category = "Functions"

    FNS = ["abs", "sqrt", "exp", "log", "floor", "ceil", "fract", "sign", "tanh"]

    def __init__(self):
        super().__init__()
        self.inputs = [SocketDef("In", SocketType.FLOAT, 0.0)]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]
        self.default_params = {"fn": "abs"}

    def generate_code(self, input_exprs, params):
        val = input_exprs.get("In", "0.0")
        fn = params.get("fn", "abs")
        return {"Out": f"{fn}({val})"}


class MixNode(BaseNode):
    node_type = "mix"
    title = "Mix (Lerp)"
    category = "Range"

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("A", SocketType.FLOAT, 0.0),
            SocketDef("B", SocketType.FLOAT, 1.0),
            SocketDef("T", SocketType.FLOAT, 0.5),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]

    def generate_code(self, input_exprs, params):
        a = input_exprs.get("A", "0.0")
        b = input_exprs.get("B", "1.0")
        t = input_exprs.get("T", "0.5")
        return {"Out": f"mix({a}, {b}, {t})"}


class ClampNode(BaseNode):
    node_type = "clamp"
    title = "Clamp"
    category = "Range"

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("In", SocketType.FLOAT, 0.0),
            SocketDef("Min", SocketType.FLOAT, 0.0),
            SocketDef("Max", SocketType.FLOAT, 1.0),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]

    def generate_code(self, input_exprs, params):
        val = input_exprs.get("In", "0.0")
        min_v = input_exprs.get("Min", "0.0")
        max_v = input_exprs.get("Max", "1.0")
        return {"Out": f"clamp({val}, {min_v}, {max_v})"}


class MinMaxNode(BaseNode):
    node_type = "min_max"
    title = "Min / Max"
    category = "Range"

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("A", SocketType.FLOAT, 0.0),
            SocketDef("B", SocketType.FLOAT, 0.0),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]
        self.default_params = {"fn": "min"}

    def generate_code(self, input_exprs, params):
        a = input_exprs.get("A", "0.0")
        b = input_exprs.get("B", "0.0")
        fn = params.get("fn", "min")
        return {"Out": f"{fn}({a}, {b})"}


class SmoothstepNode(BaseNode):
    node_type = "smoothstep"
    title = "Smoothstep"
    category = "Range"

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("Edge0", SocketType.FLOAT, 0.0),
            SocketDef("Edge1", SocketType.FLOAT, 1.0),
            SocketDef("X", SocketType.FLOAT, 0.0),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]

    def generate_code(self, input_exprs, params):
        e0 = input_exprs.get("Edge0", "0.0")
        e1 = input_exprs.get("Edge1", "1.0")
        x = input_exprs.get("X", "0.0")
        return {"Out": f"smoothstep({e0}, {e1}, {x})"}


# ─── Vector Nodes ─────────────────────────────────────────────────────────────

class Combine2DNode(BaseNode):
    node_type = "combine_2d"
    title = "Combine 2D"
    category = "Vector"

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("X", SocketType.FLOAT, 0.0),
            SocketDef("Y", SocketType.FLOAT, 0.0),
        ]
        self.outputs = [SocketDef("Out", SocketType.VEC2)]

    def generate_code(self, input_exprs, params):
        x = input_exprs.get("X", "0.0")
        y = input_exprs.get("Y", "0.0")
        return {"Out": f"vec2({x}, {y})"}


class Separate2DNode(BaseNode):
    node_type = "separate_2d"
    title = "Separate 2D"
    category = "Vector"

    def __init__(self):
        super().__init__()
        self.inputs = [SocketDef("In", SocketType.VEC2)]
        self.outputs = [
            SocketDef("X", SocketType.FLOAT),
            SocketDef("Y", SocketType.FLOAT),
        ]

    def generate_code(self, input_exprs, params):
        v = input_exprs.get("In", "vec2(0.0)")
        return {
            "X": f"({v}).x",
            "Y": f"({v}).y",
        }


class Combine3DNode(BaseNode):
    node_type = "combine_3d"
    title = "Combine 3D"
    category = "Vector"

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("X", SocketType.FLOAT, 0.0),
            SocketDef("Y", SocketType.FLOAT, 0.0),
            SocketDef("Z", SocketType.FLOAT, 0.0),
        ]
        self.outputs = [SocketDef("Out", SocketType.VEC3)]

    def generate_code(self, input_exprs, params):
        x = input_exprs.get("X", "0.0")
        y = input_exprs.get("Y", "0.0")
        z = input_exprs.get("Z", "0.0")
        return {"Out": f"vec3({x}, {y}, {z})"}


class Separate3DNode(BaseNode):
    node_type = "separate_3d"
    title = "Separate 3D"
    category = "Vector"

    def __init__(self):
        super().__init__()
        self.inputs = [SocketDef("In", SocketType.VEC3)]
        self.outputs = [
            SocketDef("X", SocketType.FLOAT),
            SocketDef("Y", SocketType.FLOAT),
            SocketDef("Z", SocketType.FLOAT),
        ]

    def generate_code(self, input_exprs, params):
        v = input_exprs.get("In", "vec3(0.0)")
        return {
            "X": f"({v}).x",
            "Y": f"({v}).y",
            "Z": f"({v}).z",
        }


# ─── Projection Nodes ─────────────────────────────────────────────────────────

def get_default_radial_projection_subgraph():
    """Default internal node graph for RadialProjectionNode."""
    return {
        "nodes": [
            {
                "id": "sg_in",
                "type": "subgraph_inputs",
                "pos": [40, 100],
                "params": {
                    "outputs": [
                        {"name": "X", "type": "float", "default": 0.0},
                        {"name": "Y", "type": "float", "default": 0.0},
                        {"name": "Z", "type": "float", "default": 0.0},
                        {"name": "Radial", "type": "bool", "default": 1.0},
                    ]
                },
            },
            {"id": "mul_x", "type": "math", "pos": [260, 40], "params": {"op": "Multiply (*)"}},
            {"id": "mul_y", "type": "math", "pos": [260, 160], "params": {"op": "Multiply (*)"}},
            {"id": "add_xy", "type": "math", "pos": [460, 100], "params": {"op": "Add (+)"}},
            {"id": "sqrt_r", "type": "function", "pos": [660, 100], "params": {"fn": "sqrt"}},
            {"id": "mix_u", "type": "mix", "pos": [860, 40], "params": {}},
            {"id": "mix_v", "type": "mix", "pos": [860, 180], "params": {}},
            {"id": "comb_uv", "type": "combine_2d", "pos": [1080, 240], "params": {}},
            {
                "id": "sg_out",
                "type": "subgraph_outputs",
                "pos": [1280, 100],
                "params": {
                    "inputs": [
                        {"name": "U", "type": "float", "default": 0.0},
                        {"name": "V", "type": "float", "default": 0.0},
                        {"name": "UV", "type": "vec2", "default": 0.0},
                    ]
                },
            },
        ],
        "wires": [
            {"from_node": "sg_in", "from_socket": "X", "to_node": "mul_x", "to_socket": "A"},
            {"from_node": "sg_in", "from_socket": "X", "to_node": "mul_x", "to_socket": "B"},
            {"from_node": "sg_in", "from_socket": "Y", "to_node": "mul_y", "to_socket": "A"},
            {"from_node": "sg_in", "from_socket": "Y", "to_node": "mul_y", "to_socket": "B"},
            {"from_node": "mul_x", "from_socket": "Out", "to_node": "add_xy", "to_socket": "A"},
            {"from_node": "mul_y", "from_socket": "Out", "to_node": "add_xy", "to_socket": "B"},
            {"from_node": "add_xy", "from_socket": "Out", "to_node": "sqrt_r", "to_socket": "In"},
            {"from_node": "sg_in", "from_socket": "X", "to_node": "mix_u", "to_socket": "A"},
            {"from_node": "sqrt_r", "from_socket": "Out", "to_node": "mix_u", "to_socket": "B"},
            {"from_node": "sg_in", "from_socket": "Radial", "to_node": "mix_u", "to_socket": "T"},
            {"from_node": "sg_in", "from_socket": "Y", "to_node": "mix_v", "to_socket": "A"},
            {"from_node": "sg_in", "from_socket": "Z", "to_node": "mix_v", "to_socket": "B"},
            {"from_node": "sg_in", "from_socket": "Radial", "to_node": "mix_v", "to_socket": "T"},
            {"from_node": "mix_u", "from_socket": "Out", "to_node": "comb_uv", "to_socket": "X"},
            {"from_node": "mix_v", "from_socket": "Out", "to_node": "comb_uv", "to_socket": "Y"},
            {"from_node": "mix_u", "from_socket": "Out", "to_node": "sg_out", "to_socket": "U"},
            {"from_node": "mix_v", "from_socket": "Out", "to_node": "sg_out", "to_socket": "V"},
            {"from_node": "comb_uv", "from_socket": "Out", "to_node": "sg_out", "to_socket": "UV"},
        ]
    }


class RadialProjectionNode(BaseNode):
    """Converts cartesian (X, Y, Z) to cylindrical (U = sqrt(x²+y²), V = z) toggled by Radial bool."""
    node_type = "radial_projection"
    title = "Radial Projection"
    category = "Subgraph"
    is_subgraph = True

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("X", SocketType.FLOAT, 0.0),
            SocketDef("Y", SocketType.FLOAT, 0.0),
            SocketDef("Z", SocketType.FLOAT, 0.0),
            SocketDef("Radial", SocketType.BOOL, 1.0),
        ]
        self.outputs = [
            SocketDef("U", SocketType.FLOAT),
            SocketDef("V", SocketType.FLOAT),
            SocketDef("UV", SocketType.VEC2),
        ]
        self.default_params = {
            "radial": True,
            "subgraph_data": get_default_radial_projection_subgraph()
        }

    def generate_code(self, input_exprs, params):
        sg_data = params.get("subgraph_data")
        if not sg_data or not sg_data.get("nodes"):
            sg_data = get_default_radial_projection_subgraph()

        inputs = dict(input_exprs)
        if "Radial" not in inputs:
            is_rad = params.get("radial", True)
            inputs["Radial"] = "1.0" if is_rad else "0.0"

        res, comments = compile_subgraph(sg_data, inputs)
        x = inputs.get("X", "0.0")
        y = inputs.get("Y", "0.0")
        z = inputs.get("Z", "0.0")
        rad = inputs.get("Radial", "1.0")
        if "U" not in res:
            res["U"] = f"mix({x}, sqrt(({x}) * ({x}) + ({y}) * ({y})), {rad})"
        if "V" not in res:
            res["V"] = f"mix({y}, {z}, {rad})"
        if "UV" not in res:
            res["UV"] = f"vec2({res['U']}, {res['V']})"
        return res

    def get_param_comments(self, params):
        sg_data = params.get("subgraph_data")
        if not sg_data or not sg_data.get("nodes"):
            sg_data = get_default_radial_projection_subgraph()
        _, comments = compile_subgraph(sg_data, {})
        return comments


# ─── Generators / Waveform Macros ──────────────────────────────────────────────

class WaveGeneratorNode(BaseNode):
    node_type = "wave_gen"
    title = "Wave Generator"
    category = "Generators"

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("Coord", SocketType.FLOAT, 0.0),
            SocketDef("Freq", SocketType.FLOAT, 1.0),
            SocketDef("Amp", SocketType.FLOAT, 1.0),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]

    def generate_code(self, input_exprs, params):
        c = input_exprs.get("Coord", "0.0")
        f = input_exprs.get("Freq", "1.0")
        a = input_exprs.get("Amp", "1.0")
        return {"Out": f"(sin({c} * {f}) * {a})"}


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


# ─── Subgraph / Group Nodes ───────────────────────────────────────────────────

class SubgraphInputsNode(BaseNode):
    node_type = "subgraph_inputs"
    title = "Group Inputs"
    category = "Subgraph"
    is_subgraph_only = True

    def __init__(self, output_defs=None):
        super().__init__()
        self.inputs = []
        if output_defs:
            self.outputs = list(output_defs)
        else:
            self.outputs = [
                SocketDef("X", SocketType.FLOAT, 0.0),
                SocketDef("Y", SocketType.FLOAT, 0.0),
                SocketDef("Z", SocketType.FLOAT, 0.0),
                SocketDef("Radial", SocketType.BOOL, 1.0),
                SocketDef("Freq", SocketType.FLOAT, 1.0),
                SocketDef("Amp", SocketType.FLOAT, 1.0),
            ]

    def generate_code(self, input_exprs, params):
        res = {}
        for s in self.outputs:
            res[s.name] = str(s.name)
        return res


class SubgraphOutputsNode(BaseNode):
    node_type = "subgraph_outputs"
    title = "Group Outputs"
    category = "Subgraph"
    is_subgraph_only = True

    def __init__(self, input_defs=None):
        super().__init__()
        if input_defs:
            self.inputs = list(input_defs)
        else:
            self.inputs = [SocketDef("Out", SocketType.FLOAT, 0.0)]
        self.outputs = []

    def generate_code(self, input_exprs, params):
        res = dict(input_exprs)
        for s in self.inputs:
            if s.name not in res:
                res[s.name] = str(s.default_value)
        return res


def get_default_rotated_wave_2d_subgraph():
    """Returns the default internal node graph for RotatedWave2DNode."""
    return {
        "nodes": [
            {"id": "sg_in", "type": "subgraph_inputs", "pos": [50, 140], "params": {}},
            {"id": "rad_param", "type": "custom_param", "pos": [50, 40],
             "params": {"name": "radial", "ptype": "bool", "default": 0}},
            {"id": "rad_proj", "type": "radial_projection", "pos": [280, 80], "params": {}},
            {"id": "ang", "type": "custom_param", "pos": [280, 280],
             "params": {"name": "angle", "ptype": "float", "default": 0.0, "min": 0.0, "max": 360.0, "step": 5.0}},
            {"id": "rad", "type": "trig", "pos": [500, 280], "params": {"fn": "radians"}},
            {"id": "cos", "type": "trig", "pos": [680, 240], "params": {"fn": "cos"}},
            {"id": "sin", "type": "trig", "pos": [680, 340], "params": {"fn": "sin"}},
            {"id": "u_cos", "type": "math", "pos": [860, 80], "params": {"op": "Multiply (*)"}},
            {"id": "v_sin", "type": "math", "pos": [860, 200], "params": {"op": "Multiply (*)"}},
            {"id": "add_rot", "type": "math", "pos": [1040, 140], "params": {"op": "Add (+)"}},
            {"id": "mul_freq", "type": "math", "pos": [1220, 140], "params": {"op": "Multiply (*)"}},
            {"id": "wave_sin", "type": "trig", "pos": [1400, 140], "params": {"fn": "sin"}},
            {"id": "mul_amp", "type": "math", "pos": [1580, 140], "params": {"op": "Multiply (*)"}},
            {"id": "sg_out", "type": "subgraph_outputs", "pos": [1760, 140], "params": {}},
        ],
        "wires": [
            {"from_node": "sg_in", "from_socket": "X", "to_node": "rad_proj", "to_socket": "X"},
            {"from_node": "sg_in", "from_socket": "Y", "to_node": "rad_proj", "to_socket": "Y"},
            {"from_node": "sg_in", "from_socket": "Z", "to_node": "rad_proj", "to_socket": "Z"},
            {"from_node": "rad_param", "from_socket": "Val", "to_node": "rad_proj", "to_socket": "Radial"},
            {"from_node": "rad_proj", "from_socket": "U", "to_node": "u_cos", "to_socket": "A"},
            {"from_node": "cos", "from_socket": "Out", "to_node": "u_cos", "to_socket": "B"},
            {"from_node": "rad_proj", "from_socket": "V", "to_node": "v_sin", "to_socket": "A"},
            {"from_node": "sin", "from_socket": "Out", "to_node": "v_sin", "to_socket": "B"},
            {"from_node": "ang", "from_socket": "Val", "to_node": "rad", "to_socket": "In"},
            {"from_node": "rad", "from_socket": "Out", "to_node": "cos", "to_socket": "In"},
            {"from_node": "rad", "from_socket": "Out", "to_node": "sin", "to_socket": "In"},
            {"from_node": "u_cos", "from_socket": "Out", "to_node": "add_rot", "to_socket": "A"},
            {"from_node": "v_sin", "from_socket": "Out", "to_node": "add_rot", "to_socket": "B"},
            {"from_node": "add_rot", "from_socket": "Out", "to_node": "mul_freq", "to_socket": "A"},
            {"from_node": "sg_in", "from_socket": "Freq", "to_node": "mul_freq", "to_socket": "B"},
            {"from_node": "mul_freq", "from_socket": "Out", "to_node": "wave_sin", "to_socket": "In"},
            {"from_node": "wave_sin", "from_socket": "Out", "to_node": "mul_amp", "to_socket": "A"},
            {"from_node": "sg_in", "from_socket": "Amp", "to_node": "mul_amp", "to_socket": "B"},
            {"from_node": "mul_amp", "from_socket": "Out", "to_node": "sg_out", "to_socket": "Out"},
        ]
    }


def _add_comment_dedup(clist, c):
    """Adds comment c to clist, deduplicating/updating by param name."""
    import re
    m = re.match(r'^\s*(?://|#)\s*@param\s+\w+\s+(\w+)', c)
    if m:
        pname = m.group(1)
        # Replace existing comment with same param name if already present
        for idx, existing in enumerate(clist):
            em = re.match(r'^\s*(?://|#)\s*@param\s+\w+\s+(\w+)', existing)
            if em and em.group(1) == pname:
                clist[idx] = c
                return
    if c not in clist:
        clist.append(c)


def compile_subgraph(subgraph_data, outer_input_exprs):
    """
    Compiles an inner subgraph using outer input expressions.
    Returns:
        (dict of {output_socket_name: expr_str}, list_of_param_comments)
    """
    nodes = {n["id"]: n for n in subgraph_data.get("nodes", [])}
    wires = subgraph_data.get("wires", [])

    output_node_id = None
    for nid, n in nodes.items():
        if n.get("type") in ("subgraph_outputs", "output"):
            output_node_id = nid
            break

    if not output_node_id:
        return {"Out": "0.0"}, []

    incoming = {nid: [] for nid in nodes}
    for w in wires:
        fn = w.get("from_node")
        tn = w.get("to_node")
        fs = w.get("from_socket")
        ts = w.get("to_socket")
        if fn in nodes and tn in nodes:
            incoming[tn].append((fn, fs, ts))

    visited = set()
    visiting = set()
    order = []
    has_cycle = False

    def dfs(nid):
        nonlocal has_cycle
        if nid in visiting:
            has_cycle = True
            return
        if nid in visited:
            return
        visiting.add(nid)
        for (fn, _, _) in incoming.get(nid, []):
            dfs(fn)
        visiting.remove(nid)
        visited.add(nid)
        order.append(nid)

    dfs(output_node_id)
    if has_cycle:
        return {"Out": "0.0"}, []

    eval_cache = {}
    param_comments = []

    for nid in order:
        node_data = nodes[nid]
        ntype = node_data.get("type")
        cls = NODE_REGISTRY.get(ntype)
        if not cls:
            continue
        instance = cls()
        params = node_data.get("params", {})

        if ntype == "subgraph_inputs":
            out_dict = {}
            if "outputs" in params:
                for s in params["outputs"]:
                    sname = s.get("name") if isinstance(s, dict) else getattr(s, "name", str(s))
                    sdef = s.get("default", 0.0) if isinstance(s, dict) else getattr(s, "default_value", 0.0)
                    out_dict[sname] = outer_input_exprs.get(sname, str(sdef))
            for s in instance.outputs:
                if s.name not in out_dict:
                    out_dict[s.name] = outer_input_exprs.get(s.name, str(s.default_value))
            for k, v in outer_input_exprs.items():
                if k not in out_dict:
                    out_dict[k] = v
            eval_cache[nid] = out_dict
            continue

        input_exprs = {}
        for s in instance.inputs:
            input_exprs[s.name] = str(s.default_value)

        for (fn, fs, ts) in incoming.get(nid, []):
            if fn in eval_cache and fs in eval_cache[fn]:
                input_exprs[ts] = eval_cache[fn][fs]

        outputs = instance.generate_code(input_exprs, params)
        for k, v in input_exprs.items():
            if k not in outputs:
                outputs[k] = v
        eval_cache[nid] = outputs

        comments = instance.get_param_comments(params)
        for c in comments:
            _add_comment_dedup(param_comments, c)

    for nid, node_data in nodes.items():
        if node_data.get("type") == "custom_param":
            cls = NODE_REGISTRY.get("custom_param")
            if cls:
                c_comments = cls().get_param_comments(node_data.get("params", {}))
                for c in c_comments:
                    _add_comment_dedup(param_comments, c)

    out_node_outputs = eval_cache.get(output_node_id, {})
    if out_node_outputs:
        return dict(out_node_outputs), param_comments
    return {"Out": "0.0"}, param_comments


class RotatedWave2DNode(BaseNode):
    node_type = "rotated_wave_2d"
    title = "Rotated Wave 2D"
    category = "Generators"
    is_2d_only = True
    is_subgraph = True

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("X", SocketType.FLOAT, 0.0),
            SocketDef("Y", SocketType.FLOAT, 0.0),
            SocketDef("Z", SocketType.FLOAT, 0.0),
            SocketDef("Freq", SocketType.FLOAT, 1.0),
            SocketDef("Amp", SocketType.FLOAT, 1.0),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]
        self.default_params = {
            "subgraph_data": get_default_rotated_wave_2d_subgraph()
        }

    def generate_code(self, input_exprs, params):
        sg_data = params.get("subgraph_data")
        if not sg_data or not sg_data.get("nodes"):
            sg_data = get_default_rotated_wave_2d_subgraph()
        res, comments = compile_subgraph(sg_data, input_exprs)
        return res

    def get_param_comments(self, params):
        sg_data = params.get("subgraph_data")
        if not sg_data or not sg_data.get("nodes"):
            sg_data = get_default_rotated_wave_2d_subgraph()
        _, comments = compile_subgraph(sg_data, {})
        return comments


# ─── Output ───────────────────────────────────────────────────────────────────

class OutputNode(BaseNode):
    node_type = "output"
    title = "Noise Output"
    category = "Output"

    def __init__(self):
        super().__init__()
        self.inputs = [SocketDef("Displacement", SocketType.FLOAT, 0.0)]
        self.outputs = []

    def generate_code(self, input_exprs, params):
        return {"result": input_exprs.get("Displacement", "0.0")}


# ─── Registry ─────────────────────────────────────────────────────────────────

NODE_CLASSES = [
    Coordinates3DNode,
    Coordinates2DNode,
    Combine2DNode,
    Separate2DNode,
    Combine3DNode,
    Separate3DNode,
    NoiseInputsNode,
    ConstantNode,
    CustomParamNode,
    MathNode,
    TrigNode,
    FunctionNode,
    MixNode,
    ClampNode,
    MinMaxNode,
    SmoothstepNode,
    RadialProjectionNode,
    WaveGeneratorNode,
    SquareWaveNode,
    RadialWaveNode,
    RotatedWave2DNode,
    SubgraphInputsNode,
    SubgraphOutputsNode,
    OutputNode,
]

NODE_REGISTRY = {cls.node_type: cls for cls in NODE_CLASSES}


def get_available_node_classes(is_2d=False, in_subgraph=False):
    """Returns list of node classes applicable to the given mode."""
    res = []
    for cls in NODE_CLASSES:
        if not in_subgraph and getattr(cls, "is_subgraph_only", False):
            continue
        if is_2d and cls.is_3d_only:
            continue
        if not is_2d and cls.is_2d_only:
            continue
        res.append(cls)
    return res


# ─── Graph Compilation ────────────────────────────────────────────────────────

def compile_graph(graph_data):
    """
    Compiles a node graph dictionary into a formula string and custom parameters.

    graph_data:
        {
            "nodes": [
                {"id": "n1", "type": "coords_3d", "params": {...}},
                ...
            ],
            "wires": [
                {"from_node": "n1", "from_socket": "p.x", "to_node": "n2", "to_socket": "A"},
                ...
            ]
        }

    Returns:
        (formula_string, list_of_param_comments, error_message)
    """
    nodes = {n["id"]: n for n in graph_data.get("nodes", [])}
    wires = graph_data.get("wires", [])

    # Find the output node
    output_node_id = None
    for nid, n in nodes.items():
        if n.get("type") == "output":
            output_node_id = nid
            break

    if not output_node_id:
        return "", [], "No Output node found in graph."

    # Build adjacency: to_node -> list of (from_node, from_socket, to_socket)
    incoming = {nid: [] for nid in nodes}
    for w in wires:
        fn = w.get("from_node")
        tn = w.get("to_node")
        fs = w.get("from_socket")
        ts = w.get("to_socket")
        if fn in nodes and tn in nodes:
            incoming[tn].append((fn, fs, ts))

    # Traverse backwards from Output to collect reachable nodes and check cycles
    visited = set()
    visiting = set()
    order = []
    has_cycle = False

    def dfs(nid):
        nonlocal has_cycle
        if nid in visiting:
            has_cycle = True
            return
        if nid in visited:
            return
        visiting.add(nid)
        for (fn, _, _) in incoming.get(nid, []):
            dfs(fn)
        visiting.remove(nid)
        visited.add(nid)
        order.append(nid)

    dfs(output_node_id)
    if has_cycle:
        return "", [], "Graph contains a cyclic dependency."

    # Node evaluation cache: nid -> {socket_name: expr_str}
    eval_cache = {}
    param_comments = []

    for nid in order:
        node_data = nodes[nid]
        ntype = node_data.get("type")
        cls = NODE_REGISTRY.get(ntype)
        if not cls:
            continue
        instance = cls()
        params = node_data.get("params", {})

        # Collect input expressions
        input_exprs = {}
        # Fill defaults first
        for s in instance.inputs:
            input_exprs[s.name] = str(s.default_value)

        # Wire inputs override defaults
        for (fn, fs, ts) in incoming.get(nid, []):
            if fn in eval_cache and fs in eval_cache[fn]:
                input_exprs[ts] = eval_cache[fn][fs]

        # Generate output expressions
        outputs = instance.generate_code(input_exprs, params)
        eval_cache[nid] = outputs

        # Collect any custom parameters
        comments = instance.get_param_comments(params)
        for c in comments:
            _add_comment_dedup(param_comments, c)

    # Also collect custom parameters from all custom_param nodes in graph (even if not yet wired to output)
    for nid, node_data in nodes.items():
        if node_data.get("type") == "custom_param":
            cls = NODE_REGISTRY.get("custom_param")
            if cls:
                c_comments = cls().get_param_comments(node_data.get("params", {}))
                for c in c_comments:
                    _add_comment_dedup(param_comments, c)

    final_expr = eval_cache.get(output_node_id, {}).get("result", "0.0")
    # Clean redundant outer parentheses if wrapped
    formula_body = final_expr.strip()

    full_formula = ""
    if param_comments:
        full_formula = "\n".join(param_comments) + "\n"
    full_formula += formula_body

    return full_formula, param_comments, None


# ─── Starter Templates ────────────────────────────────────────────────────────

def get_template_graph(name, is_2d=False):
    """Returns starter node graph dictionary for standard presets."""
    if not is_2d:
        if name in ("3D: Multi-Frequency Waves", "Multi-Frequency"):
            return {
                "nodes": [
                    {"id": "c3d", "type": "coords_3d", "pos": [50, 100], "params": {}},
                    {"id": "inputs", "type": "noise_inputs", "pos": [50, 320], "params": {}},
                    {"id": "m1", "type": "math", "pos": [280, 80], "params": {"op": "Multiply (*)"}},
                    {"id": "m2", "type": "math", "pos": [280, 200], "params": {"op": "Multiply (*)"}},
                    {"id": "m3", "type": "math", "pos": [280, 320], "params": {"op": "Multiply (*)"}},
                    {"id": "sin1", "type": "trig", "pos": [460, 80], "params": {"fn": "sin"}},
                    {"id": "sin2", "type": "trig", "pos": [460, 200], "params": {"fn": "sin"}},
                    {"id": "sin3", "type": "trig", "pos": [460, 320], "params": {"fn": "sin"}},
                    {"id": "mul12", "type": "math", "pos": [640, 120], "params": {"op": "Multiply (*)"}},
                    {"id": "mul123", "type": "math", "pos": [820, 160], "params": {"op": "Multiply (*)"}},
                    {"id": "mul_amp", "type": "math", "pos": [1000, 200], "params": {"op": "Multiply (*)"}},
                    {"id": "out", "type": "output", "pos": [1180, 200], "params": {}},
                ],
                "wires": [
                    {"from_node": "c3d", "from_socket": "p.x", "to_node": "m1", "to_socket": "A"},
                    {"from_node": "inputs", "from_socket": "freq", "to_node": "m1", "to_socket": "B"},
                    {"from_node": "c3d", "from_socket": "p.y", "to_node": "m2", "to_socket": "A"},
                    {"from_node": "inputs", "from_socket": "freq", "to_node": "m2", "to_socket": "B"},
                    {"from_node": "c3d", "from_socket": "p.z", "to_node": "m3", "to_socket": "A"},
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
                    {"from_node": "mul_amp", "from_socket": "Out", "to_node": "out", "to_socket": "Displacement"},
                ]
            }

        # Default 3D Simple Sine
        return {
            "nodes": [
                {"id": "c3d", "type": "coords_3d", "pos": [50, 100], "params": {}},
                {"id": "inputs", "type": "noise_inputs", "pos": [50, 260], "params": {}},
                {"id": "wave", "type": "wave_gen", "pos": [300, 120], "params": {}},
                {"id": "out", "type": "output", "pos": [520, 120], "params": {}},
            ],
            "wires": [
                {"from_node": "c3d", "from_socket": "p.x", "to_node": "wave", "to_socket": "Coord"},
                {"from_node": "inputs", "from_socket": "freq", "to_node": "wave", "to_socket": "Freq"},
                {"from_node": "inputs", "from_socket": "amp", "to_node": "wave", "to_socket": "Amp"},
                {"from_node": "wave", "from_socket": "Out", "to_node": "out", "to_socket": "Displacement"},
            ]
        }

    # 2D Templates
    if name in ("2D: Radial Ripple", "Radial Ripple"):
        return {
            "nodes": [
                {"id": "c2d", "type": "coords_2d", "pos": [50, 100], "params": {}},
                {"id": "inputs", "type": "noise_inputs", "pos": [50, 280], "params": {}},
                {"id": "decay_param", "type": "custom_param", "pos": [50, 420],
                 "params": {"name": "decay", "ptype": "float", "default": 0.05, "min": 0.0, "max": 1.0, "step": 0.01}},
                {"id": "rad_wave", "type": "radial_wave", "pos": [320, 140], "params": {}},
                {"id": "out", "type": "output", "pos": [560, 140], "params": {}},
            ],
            "wires": [
                {"from_node": "c2d", "from_socket": "r", "to_node": "rad_wave", "to_socket": "R"},
                {"from_node": "inputs", "from_socket": "freq", "to_node": "rad_wave", "to_socket": "Freq"},
                {"from_node": "inputs", "from_socket": "amp", "to_node": "rad_wave", "to_socket": "Amp"},
                {"from_node": "decay_param", "from_socket": "Val", "to_node": "rad_wave", "to_socket": "Decay"},
                {"from_node": "rad_wave", "from_socket": "Out", "to_node": "out", "to_socket": "Displacement"},
            ]
        }

    if name in ("2D: Square Wave", "Square Wave", "Square"):
        return {
            "nodes": [
                {"id": "c2d", "type": "coords_2d", "pos": [50, 100], "params": {}},
                {"id": "inputs", "type": "noise_inputs", "pos": [50, 260], "params": {}},
                {"id": "sharp_param", "type": "custom_param", "pos": [50, 400],
                 "params": {"name": "sharp", "ptype": "float", "default": 4.0, "min": 1.0, "max": 20.0, "step": 0.5}},
                {"id": "sq_wave", "type": "square_wave", "pos": [320, 120], "params": {}},
                {"id": "out", "type": "output", "pos": [560, 120], "params": {}},
            ],
            "wires": [
                {"from_node": "c2d", "from_socket": "x", "to_node": "sq_wave", "to_socket": "Coord"},
                {"from_node": "inputs", "from_socket": "freq", "to_node": "sq_wave", "to_socket": "Freq"},
                {"from_node": "inputs", "from_socket": "amp", "to_node": "sq_wave", "to_socket": "Amp"},
                {"from_node": "sharp_param", "from_socket": "Val", "to_node": "sq_wave", "to_socket": "Sharp"},
                {"from_node": "sq_wave", "from_socket": "Out", "to_node": "out", "to_socket": "Displacement"},
            ]
        }

    # Default 2D Waves with Angle (matches default Waves preset)
    return {
        "nodes": [
            {"id": "c2d", "type": "coords_2d", "pos": [50, 100], "params": {}},
            {"id": "inputs", "type": "noise_inputs", "pos": [50, 300], "params": {}},
            {"id": "wave1", "type": "rotated_wave_2d", "pos": [340, 100], "params": {}},
            {"id": "out", "type": "output", "pos": [600, 120], "params": {}},
        ],
        "wires": [
            {"from_node": "c2d", "from_socket": "x", "to_node": "wave1", "to_socket": "X"},
            {"from_node": "c2d", "from_socket": "y", "to_node": "wave1", "to_socket": "Y"},
            {"from_node": "c2d", "from_socket": "z", "to_node": "wave1", "to_socket": "Z"},
            {"from_node": "inputs", "from_socket": "freq", "to_node": "wave1", "to_socket": "Freq"},
            {"from_node": "inputs", "from_socket": "amp", "to_node": "wave1", "to_socket": "Amp"},
            {"from_node": "wave1", "from_socket": "Out", "to_node": "out", "to_socket": "Displacement"},
        ]
    }
