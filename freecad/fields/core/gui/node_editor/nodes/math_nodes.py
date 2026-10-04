# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.math_nodes

Math and function node definitions: arithmetic, trigonometry, functions, and ranges.
"""
from .base import BaseNode, SocketDef, SocketType


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
        # Unary functions folded in from the retired Function node -- B is
        # simply ignored, same as any other op only some of its inputs.
        "Abs (abs)": lambda a, b: f"abs({a})",
        "Sqrt (sqrt)": lambda a, b: f"sqrt({a})",
        "Exp (exp)": lambda a, b: f"exp({a})",
        "Log (log)": lambda a, b: f"log({a})",
        "Floor (floor)": lambda a, b: f"floor({a})",
        "Ceil (ceil)": lambda a, b: f"ceil({a})",
        "Fract (fract)": lambda a, b: f"fract({a})",
        "Sign (sign)": lambda a, b: f"sign({a})",
        "Tanh (tanh)": lambda a, b: f"tanh({a})",
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


class IgnoreBackFaceNode(BaseNode):
    """Masks Value off the back face by multiplying it with `front` (see
    PositionNode) -- the node-graph equivalent of the retired
    `ignore_back_face` checkbox, but only present, and only doing anything,
    when a graph actually drops this node in (see
    [[design_front_face_node_graph]])."""
    node_type = "ignore_back_face"
    title = "Ignore Back Face"
    category = "Functions"
    doc = (
        "Multiplies Value by `front` -- 1 in front of the base field's "
        "surface along Direction, fading to 0 behind it -- masking Value off "
        "the back face. A wire into 'Ignore Back Face' overrides the node's "
        "own checkbox, same as Radial Projection's Radial Mode."
    )

    def __init__(self):
        super().__init__()
        self.inputs = [
            SocketDef("Value", SocketType.FLOAT, 0.0),
            SocketDef("Ignore Back Face", SocketType.BOOL, 1.0,
                      doc="Multiply Value by `front`. Unwired, the node's "
                          "own checkbox decides."),
        ]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]
        self.default_params = {"ignore_back_face": True}

    def generate_code(self, input_exprs, params):
        value = input_exprs.get("Value", "0.0")
        wired = input_exprs.get("__wired__", set()) or set()
        if "Ignore Back Face" in wired:
            ignore_expr = input_exprs.get("Ignore Back Face", "0.0")
        else:
            ignore_expr = "1.0" if params.get("ignore_back_face", True) else "0.0"
        # Skip referencing `front` at all when the mask is off and unwired --
        # naming it is what turns on the extra `base_field.evaluate` weight
        # computation (`_formula_uses_front`), so an unchecked node should cost
        # nothing, not just multiply by a weight of 1.
        if ignore_expr == "0.0":
            return {"Out": f"({value})"}
        return {"Out": f"(({value}) * mix(1.0, front, {ignore_expr}))"}


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
