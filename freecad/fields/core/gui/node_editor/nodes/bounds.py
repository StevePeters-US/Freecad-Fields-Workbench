# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.gui.node_editor.nodes.bounds

The (peak, slope) bound the compiler carries through a node graph (NV-040).

Every socket gets a Bound: `peak` bounds |value| and `slope` bounds
|d value / d position|, each the NAME of a variable assigned in the formula's
`// @bound` lines (or a literal). `const` is the socket's code expression when it
does not vary over space, else None. SdfNoiseField evaluates the lines
(formula_eval.eval_bound) in EVAL_NS plus amp, freq, the custom params, inf, bmul,
bdiv, front_slope and surf_n_slope. Vectors are bounded in the Euclidean norm.
"""
import math
import re
from collections import namedtuple

Bound = namedtuple("Bound", "peak slope const")

INF = "inf"
ZERO = "0.0"

#: Names a constant expression may use besides the scalar custom params: the noise
#: inputs, pi, and the scalar functions formula_eval.EVAL_NS defines.
_CONST_NAMES = frozenset({
    "amp", "freq", "pi", "sin", "cos", "tan", "asin", "acos", "atan", "sqrt", "abs",
    "pow", "exp", "log", "tanh", "min", "max", "floor", "ceil", "fract", "clamp",
    "mix", "mod", "sign", "step", "smoothstep", "radians",
})
_IDENT = re.compile(r"[A-Za-z_]\w*")
_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_SCALAR_PTYPES = ("float", "int", "bool", "slider")


def const_param_names(graph):
    """Identifiers of every scalar custom param in `graph`, subgraphs included."""
    from .base import glsl_param_identifier
    names = set()
    for n in graph.get("nodes", []):
        params = n.get("params", {}) or {}
        if n.get("type") == "custom_param" and params.get("ptype", "float") in _SCALAR_PTYPES:
            ident = glsl_param_identifier(params.get("name", "param"))
            if ident:
                names.add(ident)
        sub = params.get("subgraph_data")
        if isinstance(sub, dict):
            names |= const_param_names(sub)
    return names


class BoundWriter:
    """The `// @bound` assignments of one compile, in order."""

    def __init__(self, param_names=()):
        self.lines = []
        self._n = 0
        self.param_names = frozenset(param_names)

    def var(self, expr):
        """Assign `expr` to a fresh `_bN` and return the name. Literals pass through.
        `_bN` can't collide with a custom param: glsl_param_identifier strips
        leading underscores."""
        expr = str(expr).strip()
        if expr == INF or _NUMBER.match(expr):
            return expr
        name = f"_b{self._n}"
        self._n += 1
        self.lines.append(f"// @bound {name} = {expr}")
        return name

    def is_const(self, code):
        """True when `code` (a socket's expression) does not vary over space."""
        if not isinstance(code, str):
            return False
        return set(_IDENT.findall(code)) <= (_CONST_NAMES | self.param_names)


def default_bound(value):
    """The bound of an unwired socket holding its default `value`."""
    try:
        if isinstance(value, (tuple, list)):
            peak = math.sqrt(sum(float(v) ** 2 for v in value))
        else:
            peak = abs(float(value))
    except (TypeError, ValueError):
        return Bound(INF, ZERO, None)
    return Bound(repr(peak), ZERO, None)


def node_bounds(instance, ins, input_exprs, params, outputs, w):
    """{socket: Bound} for one node: its rule, with every constant output replaced
    by (abs(code), 0). `outputs` is what generate_code returned."""
    raw = instance.bound(ins, input_exprs, params, w)
    out = {}
    for sock, b in raw.items():
        code = outputs.get(sock)
        if w.is_const(code):
            out[sock] = Bound(w.var(f"abs({code})"), ZERO, code)
        elif isinstance(b, Bound):
            out[sock] = b
        else:
            out[sock] = Bound(w.var(b[0]), w.var(b[1]), None)
    return out


def bmul(a, b):
    """`a * b` with 0 winning over inf, for bound expressions (text)."""
    return f"bmul({a}, {b})"


def badd(*terms):
    return "(" + " + ".join(str(t) for t in terms) + ")"
