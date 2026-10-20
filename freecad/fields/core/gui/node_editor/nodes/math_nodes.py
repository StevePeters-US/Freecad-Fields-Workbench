# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.math_nodes

Math and function node definitions: arithmetic, trigonometry, functions, and ranges.
"""
import ast
import math

from freecad.fields.core import fld_logger

from .base import BaseNode, SocketDef, SocketType
from .bounds import INF, Bound, badd, bmul


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

    def bound(self, ins, input_exprs, params, w):
        a, b = ins["A"], ins["B"]
        op = params.get("op", "Add (+)")
        Pa, La, Pb, Lb = a.peak, a.slope, b.peak, b.slope
        if op in ("Add (+)", "Subtract (-)"):
            r = (badd(Pa, Pb), badd(La, Lb))
        elif op == "Multiply (*)":
            r = (bmul(Pa, Pb), badd(bmul(La, Pb), bmul(Lb, Pa)))
        elif op == "Divide (/)" and b.const is not None:
            r = (f"bdiv({Pa}, abs({b.const}))", f"bdiv({La}, abs({b.const}))")
        elif op == "Abs (abs)":
            r = (Pa, La)
        elif op == "Exp (exp)":
            r = (f"exp({Pa})", bmul(La, f"exp({Pa})"))
        elif op == "Tanh (tanh)":
            r = (f"min(1.0, {Pa})", La)
        elif op == "Sqrt (sqrt)":
            r = (f"sqrt({Pa})", INF)                 # unbounded slope at 0
        elif op in ("Fract (fract)", "Sign (sign)"):
            r = ("1.0", INF)                         # discontinuous
        elif op in ("Floor (floor)", "Ceil (ceil)"):
            r = (badd(Pa, "1.0"), INF)
        elif op == "Mod (mod)" and b.const is not None:
            r = (f"abs({b.const})", INF)
        else:                                        # Divide/Power/Mod by a varying B, Log
            r = (INF, INF)
        return {"Out": r}


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

    def bound(self, ins, input_exprs, params, w):
        a = ins["In"]
        fn = params.get("fn", "sin")
        if fn in ("sin", "cos"):
            return {"Out": ("1.0", a.slope)}
        if fn == "atan":
            return {"Out": ("1.5707964", a.slope)}
        if fn == "radians":
            return {"Out": (f"({a.peak} * 0.017453293)", f"({a.slope} * 0.017453293)")}
        if fn in ("asin", "acos"):
            return {"Out": ("3.1415927", INF)}
        return {"Out": (INF, INF)}                   # tan


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
        a = params.get("a", input_exprs.get("A", "0.0"))
        b = params.get("b", input_exprs.get("B", "1.0"))
        t = params.get("t", input_exprs.get("T", "0.5"))
        return {"Out": f"mix({a}, {b}, {t})"}

    def bound(self, ins, input_exprs, params, w):
        # Mirrors generate_code: a param a/b/t overrides its socket.
        def pick(key, sock):
            if key in params:
                v = float(params[key])
                return Bound(repr(abs(v)), "0.0", repr(v))
            return ins[sock]
        a, b, t = pick("a", "A"), pick("b", "B"), pick("t", "T")
        if t.const is not None:                      # t fixed over space: exact weights
            wa, wb = f"abs(1.0 - ({t.const}))", f"abs({t.const})"
            return {"Out": (badd(bmul(a.peak, wa), bmul(b.peak, wb)),
                            badd(bmul(a.slope, wa), bmul(b.slope, wb)))}
        wa = badd("1.0", t.peak)
        return {"Out": (badd(bmul(a.peak, wa), bmul(b.peak, t.peak)),
                        badd(bmul(a.slope, wa), bmul(b.slope, t.peak),
                             bmul(badd(a.peak, b.peak), t.slope)))}


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

    def bound(self, ins, input_exprs, params, w):
        # |clamp(v, lo, hi)| <= max(min(Pv, M), m), M/m the larger/smaller of the
        # edge peaks: a clamp UP to lo > 0 exceeds |v| (NV-067).
        v, lo, hi = ins["In"], ins["Min"], ins["Max"]
        return {"Out": (f"max(min({v.peak}, max({lo.peak}, {hi.peak})), "
                        f"min({lo.peak}, {hi.peak}))",
                        badd(v.slope, lo.slope, hi.slope))}


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

    def bound(self, ins, input_exprs, params, w):
        a, b = ins["A"], ins["B"]
        return {"Out": (f"max({a.peak}, {b.peak})", f"max({a.slope}, {b.slope})")}


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
        e0 = params.get("edge0", input_exprs.get("Edge0", "0.0"))
        e1 = params.get("edge1", input_exprs.get("Edge1", "1.0"))
        x = input_exprs.get("X", "0.0")
        return {"Out": f"smoothstep({e0}, {e1}, {x})"}

    def bound(self, ins, input_exprs, params, w):
        e0 = repr(float(params["edge0"])) if "edge0" in params else ins["Edge0"].const
        e1 = repr(float(params["edge1"])) if "edge1" in params else ins["Edge1"].const
        x = ins["X"]
        if e0 is None or e1 is None:
            return {"Out": ("1.0", INF)}
        return {"Out": ("1.0", f"bdiv(bmul(1.5, {x.slope}), abs(({e1}) - ({e0})))")}


_EXPR_VARS = ("a", "b", "c", "d")
# name -> arity. Every name must mean the same thing in EVAL_NS, EVAL_NS_NP
# (formula_eval.py) and GLSL; test_expression_node pins the first two.
_EXPR_FUNCS = {
    "sin": 1, "cos": 1, "tan": 1, "asin": 1, "acos": 1, "atan": 1,
    "sqrt": 1, "abs": 1, "exp": 1, "log": 1, "tanh": 1,
    "floor": 1, "ceil": 1, "fract": 1, "sign": 1, "radians": 1,
    "pow": 2, "min": 2, "max": 2, "mod": 2, "step": 2,
    "clamp": 3, "mix": 3, "smoothstep": 3,
}
_EXPR_CONSTS = ("pi",)   # SdfNoiseField.to_glsl always declares it (noise.py:577)
_EXPR_BINOPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}


def _emit_expr(node, args):
    """Re-emit a parsed expression as GLSL that also evaluates under EVAL_NS.

    Raises ValueError on anything outside the whitelist. Every sub-expression is
    parenthesised and every number becomes a float literal, because `1/2` is 0.5
    in Python but integer 0 in GLSL.
    """
    if isinstance(node, ast.Expression):
        return _emit_expr(node.body, args)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        value = float(node.value)
        if not math.isfinite(value):
            raise ValueError(f"number {node.value!r} is out of range")
        return repr(value)
    if isinstance(node, ast.Name):
        if node.id in args:
            return f"({args[node.id]})"
        if node.id in _EXPR_CONSTS:
            return node.id
        raise ValueError(f"unknown name '{node.id}'; use a, b, c, d or pi")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        sign = "-" if isinstance(node.op, ast.USub) else "+"
        return f"({sign}{_emit_expr(node.operand, args)})"
    if isinstance(node, ast.BinOp) and type(node.op) in _EXPR_BINOPS:
        op = _EXPR_BINOPS[type(node.op)]
        return f"({_emit_expr(node.left, args)} {op} {_emit_expr(node.right, args)})"
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in _EXPR_FUNCS and not node.keywords):
        arity = _EXPR_FUNCS[node.func.id]
        if len(node.args) != arity:
            raise ValueError(f"{node.func.id}() takes {arity} argument(s), got {len(node.args)}")
        return f"{node.func.id}({', '.join(_emit_expr(a, args) for a in node.args)})"
    if isinstance(node, ast.BinOp):
        raise ValueError("only + - * / are allowed; use pow(a, b) or mod(a, b)")
    if isinstance(node, ast.Call):
        raise ValueError("unsupported function call; see the node's tooltip for the list")
    raise ValueError(f"'{type(node).__name__}' is not allowed")


class ExpressionNode(BaseNode):
    node_type = "expression"
    title = "Expression"
    category = "Math"
    doc = (
        "A formula in a, b, c and d, the node's four inputs.\n"
        "Operators: + - * / and parentheses. Numbers are floats, so 1/2 is 0.5.\n"
        "Use pow(a, 2) and mod(a, 2), not ** or %.\n"
        "Functions: sin cos tan asin acos atan sqrt abs exp log tanh floor ceil\n"
        "fract sign radians; pow min max mod step; clamp mix smoothstep. Constant: pi.\n"
        "Rotate (a, b) by c degrees: a*cos(radians(c)) - b*sin(radians(c)).\n"
        "An invalid formula is shown in the status bar and is not applied."
    )

    def __init__(self):
        super().__init__()
        self.inputs = [SocketDef(v.upper(), SocketType.FLOAT, 0.0) for v in _EXPR_VARS]
        self.outputs = [SocketDef("Out", SocketType.FLOAT)]
        self.default_params = {"expr": "a"}

    def generate_code(self, input_exprs, params):
        text = str(params.get("expr", "a")).strip()
        if not text:
            return {"Out": "0.0"}
        args = {v: input_exprs.get(v.upper(), "0.0") for v in _EXPR_VARS}
        try:
            return {"Out": _emit_expr(ast.parse(text, mode="eval"), args)}
        except (SyntaxError, ValueError, RecursionError) as exc:
            fld_logger.debug(f"ExpressionNode: rejected {text!r}: {exc}")
            return {"Out": "0.0", "__error__": f"Expression node: {exc}"}
