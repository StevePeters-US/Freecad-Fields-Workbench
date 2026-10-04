# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.sdf.sdf.formula_eval

The GLSL-compatible Python evaluator. A user-typed formula is ONE piece of text
evaluated three ways -- scalar Python, NumPy over a grid, and GLSL on the GPU --
and this module owns the first two. Every name in EVAL_NS must mean exactly what
the GLSL builtin of the same name means, and must agree with its EVAL_NS_NP twin
on the same inputs INCLUDING NEGATIVE ONES: `mod` shipped with math.fmod's sign
convention and `min`/`max` with a reduce() that raised on (array, scalar), and
both survived a green suite because the tests only sampled positives
(`bug_noise_formula_namespace_drift`, `test_noise_scalar_np_parity.py`).

It lives here rather than inside noise.py because it is not about noise --
`array.py` evaluates domain-fold expressions through the same namespace, and
`SdfNoiseField` is only its first caller.
"""
import math
import operator
import re
import numpy as np
from freecad.fields.core import fld_logger


def parse_custom_params(formula_text):
    """
    Parses custom parameters from formula text comments.
    Format: // @param <type> <name> <default> [<min> <max> [<step>]]
    """
    params = []
    if not formula_text:
        return params
    # Match comment lines starting with // or # and @param
    pattern = r'^\s*(?://|#)\s*@param\s+(\w+)\s+(\w+)\s+([\w.,-]+)(?:\s+([\d.,-]+)\s+([\d.,-]+)(?:\s+([\d.,-]+))?)?'
    for line in formula_text.splitlines():
        match = re.match(pattern, line)
        if match:
            ptype = match.group(1).lower()
            pname = match.group(2)
            pdefault_str = match.group(3)
            pmin_str = match.group(4)
            pmax_str = match.group(5)
            pstep_str = match.group(6)

            if ptype not in ('float', 'int', 'slider', 'bool', 'vec2', 'vec3'):
                continue

            try:
                if ptype == 'int':
                    pdefault = int(pdefault_str)
                    pmin = int(pmin_str) if pmin_str is not None else None
                    pmax = int(pmax_str) if pmax_str is not None else None
                    pstep = int(pstep_str) if pstep_str is not None else None
                elif ptype == 'bool':
                    pdefault = True if pdefault_str.lower() in ('1', 'true', 'yes') else False
                    pmin = 0
                    pmax = 1
                    pstep = 1
                elif ptype in ('vec2', 'vec3'):
                    parts = [float(x.strip()) for x in pdefault_str.split(',') if x.strip()]
                    if ptype == 'vec2':
                        while len(parts) < 2:
                            parts.append(0.0)
                        pdefault = (parts[0], parts[1])
                    else:
                        while len(parts) < 3:
                            parts.append(0.0)
                        pdefault = (parts[0], parts[1], parts[2])
                    pmin = float(pmin_str) if pmin_str is not None else None
                    pmax = float(pmax_str) if pmax_str is not None else None
                    pstep = float(pstep_str) if pstep_str is not None else None
                else:
                    pdefault = float(pdefault_str)
                    pmin = float(pmin_str) if pmin_str is not None else None
                    pmax = float(pmax_str) if pmax_str is not None else None
                    pstep = float(pstep_str) if pstep_str is not None else None

                params.append({
                    'type': ptype,
                    'name': pname,
                    'default': pdefault,
                    'min': pmin,
                    'max': pmax,
                    'step': pstep
                })
            except ValueError as e:
                fld_logger.debug(f"parse_custom_params: skipping malformed param line ({e})")
                continue
    return params


def _format_vec_str(current_val, n):
    """Format a vec2/vec3-ish value as an n-component comma-separated string,
    matching whichever of Vector-like / tuple-like / bare-scalar shape it has."""
    names = "xyz"[:n]
    if hasattr(current_val, 'x') and all(hasattr(current_val, c) for c in names):
        return ','.join(f"{getattr(current_val, c):.4f}" for c in names)
    if isinstance(current_val, (tuple, list)):
        return ','.join(f"{float(current_val[i]):.4f}" for i in range(n))
    return ','.join([f"{current_val}"] + ['0.0'] * (n - 1))


def _append_range(new_line, min_val, max_val, step_val):
    """Append ` min max [step]` to a @param comment line, unformatted (matches
    the vec2/vec3 branches; the scalar float branch formats these itself)."""
    if min_val is not None and max_val is not None:
        new_line += f" {min_val} {max_val}"
        if step_val is not None:
            new_line += f" {step_val}"
    return new_line


def update_formula_param_comment(formula_text, pname, ptype, current_val, min_val=None, max_val=None, step_val=None):
    if not formula_text:
        return formula_text
    lines = formula_text.splitlines()
    for i, line in enumerate(lines):
        # Match this parameter's comment line
        match = re.match(rf'^(\s*(?://|#)\s*@param\s+{ptype}\s+{pname}\s+)([\d.,-]+)(?:\s+([\d.,-]+)\s+([\d.,-]+)(?:\s+([\d.,-]+))?)?', line)
        if match:
            prefix = match.group(1)
            # Construct new line
            if ptype == 'bool':
                new_line = f"{prefix.rstrip()} {1 if current_val else 0} 0 1 1"
            elif ptype == 'int':
                new_line = f"{prefix.rstrip()} {int(current_val)}"
                if min_val is not None and max_val is not None:
                    new_line += f" {int(min_val)} {int(max_val)}"
                    if step_val is not None:
                        new_line += f" {int(step_val)}"
            elif ptype in ('vec2', 'vec3'):
                n = 2 if ptype == 'vec2' else 3
                v_str = _format_vec_str(current_val, n)
                new_line = f"{prefix.rstrip()} {v_str}"
                new_line = _append_range(new_line, min_val, max_val, step_val)
            else:
                new_line = f"{prefix.rstrip()} {current_val:.3f}"
                if min_val is not None and max_val is not None:
                    new_line += f" {min_val:.3f} {max_val:.3f}"
                    if step_val is not None:
                        new_line += f" {step_val:.3f}"
            lines[i] = new_line
            break
    return "\n".join(lines)


def clean_formula_code(formula_text):
    if not formula_text:
        return ""
    lines = [line.strip() for line in formula_text.splitlines() if line.strip() and not line.strip().startswith("//") and not line.strip().startswith("#")]
    return "\n".join(lines)


# Above this many points the per-point eval() fallback is a multi-minute freeze;
# contribute nothing and tell the user instead of hanging the UI.
MAX_SCALAR_FALLBACK_POINTS = 50_000


# ── Python eval helpers (for Custom / simple formula presets) ─────────────────

class _GlslVecBase:
    """Shared componentwise operators for GlslVec2/GlslVec3, matching GLSL's
    vecN operators. Components hold either Python floats (scalar eval) or
    numpy arrays (grid eval) -- elementwise ops on them dispatch to the right
    arithmetic either way, so one implementation serves both EVAL_NS and
    EVAL_NS_NP (`cross`/`normalize` below reuse this too). Subclasses set
    __slots__/_FIELDS to their component names; this base is generic over them."""
    __slots__ = ()
    _FIELDS = ()

    def __init__(self, *values):
        for name, v in zip(self._FIELDS, values):
            setattr(self, name, v)

    def _map(self, o, op):
        cls = type(self)
        if isinstance(o, cls):
            return cls(*(op(getattr(self, f), getattr(o, f)) for f in self._FIELDS))
        return cls(*(op(getattr(self, f), o) for f in self._FIELDS))

    def __add__(self, o):
        return self._map(o, operator.add)
    __radd__ = __add__

    def __sub__(self, o):
        return self._map(o, operator.sub)

    def __rsub__(self, o):
        cls = type(self)
        return cls(*(o - getattr(self, f) for f in self._FIELDS))

    def __mul__(self, o):
        return self._map(o, operator.mul)
    __rmul__ = __mul__

    def __truediv__(self, o):
        return self._map(o, operator.truediv)

    def __neg__(self):
        cls = type(self)
        return cls(*(-getattr(self, f) for f in self._FIELDS))


class GlslVec2(_GlslVecBase):
    __slots__ = ('x', 'y')
    _FIELDS = ('x', 'y')


class GlslVec3(_GlslVecBase):
    __slots__ = ('x', 'y', 'z')
    _FIELDS = ('x', 'y', 'z')


def _glsl_dot(a, b):
    return a.x * b.x + a.y * b.y + (a.z * b.z if hasattr(a, 'z') and hasattr(b, 'z') else 0.0)


def _glsl_cross(a, b):
    # vec3-only: a graph wiring Orient/Align-to-Direction's Rodrigues rotation
    # needs both. Componentwise on x/y/z, so no scalar/array split is needed.
    return GlslVec3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x)


def _make_length(sqrt_fn):
    return lambda v: sqrt_fn(v.x ** 2 + v.y ** 2 + (v.z ** 2 if hasattr(v, 'z') else 0.0))


def _make_normalize(sqrt_fn):
    return lambda v: v * (1.0 / sqrt_fn(v.x ** 2 + v.y ** 2 + (v.z ** 2 if hasattr(v, 'z') else 0.0)))


# Entries whose lambda bodies are identical between the scalar (EVAL_NS) and
# numpy-grid (EVAL_NS_NP) namespaces -- they use no math./np.-specific call, so
# there is nothing to keep in sync separately. `length`/`normalize` are NOT
# here: their sqrt() call must be math.sqrt vs np.sqrt (see _make_length/
# _make_normalize below), so sharing the surrounding formula is as far as
# those two can go.
_SHARED_EVAL = {
    "dot": _glsl_dot,
    "cross": _glsl_cross,
    # ToWorldLocalNode (transform.py) emits `apply_rot_mat(to_world_rot, v)` /
    # `apply_inv_mat(to_local_inv, v)` -- the same call text GLSL sees, where
    # `to_world_rot`/`to_local_inv` are real mat4 uniforms. Here they are bound
    # (per-field, in noise.py) to plain closures over that field's own
    # placement, so `m` is just called rather than matrix-multiplied.
    "apply_rot_mat": lambda m, v: m(v),
    "apply_inv_mat": lambda m, v: m(v),
    "vec2": lambda x=0.0, y=0.0: GlslVec2(x, y),
    "vec3": lambda x=0.0, y=0.0, z=0.0: GlslVec3(x, y, z),
}


EVAL_NS = {
    "__builtins__": {},
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sqrt": math.sqrt, "abs": abs, "pow": pow,
    "exp": math.exp, "log": math.log,
    "tanh": math.tanh, "sinh": math.sinh, "cosh": math.cosh,
    "min": min, "max": max,
    "floor": math.floor, "ceil": math.ceil,
    "fract": lambda x: x - math.floor(x),
    "clamp": lambda x, lo, hi: max(lo, min(hi, x)),
    "mix": lambda a, b, t: a * (1.0 - t) + b * t,
    # GLSL mod() is x - y*floor(x/y), whose sign follows the DIVISOR. math.fmod's
    # sign follows the dividend, so it disagreed with both GLSL and the numpy
    # twin (np.mod) for every negative input -- mod(-3.7, 2.0) gave -1.7 here and
    # 0.3 on the grid path. Pinned by test_noise_scalar_np_parity.py.
    "mod": lambda a, b: a - b * math.floor(a / b) if b else 0.0,
    "sign": lambda x: (1.0 if x > 0 else (-1.0 if x < 0 else 0.0)),
    "step": lambda edge, x: 0.0 if x < edge else 1.0,
    "smoothstep": lambda e0, e1, x: (
        lambda t: t * t * (3 - 2 * t)
    )(max(0.0, min(1.0, (x - e0) / (e1 - e0) if e1 != e0 else 0.0))),
    "pi": math.pi,
    "radians": lambda deg: deg * math.pi / 180.0,
    "length": _make_length(math.sqrt),
    "normalize": _make_normalize(math.sqrt),
    **_SHARED_EVAL,
}


def _np_fold(op, args):
    """Left-fold a numpy binary ufunc over args, broadcasting mixed scalars and
    arrays. Used for min()/max(), which take a variable number of arguments."""
    if not args:
        raise TypeError("min()/max() need at least one argument")
    out = args[0]
    for a in args[1:]:
        out = op(out, a)
    return out


# Vectorized (numpy) counterpart of EVAL_NS — used to evaluate a Custom/simple
# formula string ONCE over a whole grid array instead of once per point. Most
# GLSL-style formulas (the only kind these presets are meant to hold) broadcast
# over numpy arrays with zero changes since they're built from +-*/ and the
# functions below. If a formula does something non-vectorizable (e.g. a Python
# `if/else` ternary on an array), eval() raises and the caller falls back to
# the guaranteed-correct per-point loop.
EVAL_NS_NP = {
    "__builtins__": {},
    "sin": np.sin, "cos": np.cos, "tan": np.tan,
    "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan,
    "sqrt": np.sqrt, "abs": np.abs, "pow": np.power,
    "exp": np.exp, "log": np.log,
    "tanh": np.tanh, "sinh": np.sinh, "cosh": np.cosh,
    # Fold pairwise rather than np.minimum.reduce(a): reduce() builds one array
    # out of its argument tuple first, so the ordinary `min(x, 0.5)` -- array
    # against scalar -- raised "inhomogeneous shape". That exception is caught by
    # the grid callers as "formula not vectorizable", and above
    # MAX_SCALAR_FALLBACK_POINTS they refuse the per-point fallback and return
    # zeros, so any formula using min()/max() with a constant silently lost its
    # noise on a large grid. Pairwise np.minimum broadcasts instead.
    "min": lambda *a: _np_fold(np.minimum, a),
    "max": lambda *a: _np_fold(np.maximum, a),
    "floor": np.floor, "ceil": np.ceil,
    "fract": lambda x: x - np.floor(x),
    "clamp": lambda x, lo, hi: np.clip(x, lo, hi),
    "mix": lambda a, b, t: a * (1.0 - t) + b * t,
    "mod": np.mod,
    "sign": np.sign,
    "step": lambda edge, x: np.where(x < edge, 0.0, 1.0),
    # e1==e0 would divide by zero here; caller wraps in errstate(ignore) +
    # nan_to_num, matching the scalar EVAL_NS version's try/except -> 0.0.
    "smoothstep": lambda e0, e1, x: (
        lambda t: t * t * (3.0 - 2.0 * t)
    )(np.clip((x - e0) / (e1 - e0), 0.0, 1.0)),
    "pi": math.pi,
    "radians": np.radians,
    "length": _make_length(np.sqrt),
    "normalize": _make_normalize(np.sqrt),
    **_SHARED_EVAL,
}


# Pattern nodes (CN-014) wrap an SdfNoiseNode kernel in the graph as
# `fld_<kernel>(Position, Amp, Freq)`. The kernel's own signature is
# `(x, y, z, amp, freq)` -- five scalars, not a vec3 -- so the formula-text
# binding has to unpack the GlslVec3 the graph always passes as `q`. Bound
# here, not in noise_nodes/, so a formula/graph typed by hand can call a
# kernel by name exactly like any builtin (`bug_noise_formula_namespace_drift`:
# a kernel missing from this namespace would raise and silently contribute 0).
from freecad.fields.core.sdf.sdf.noise_nodes import NOISE_NODES


def _bind_pattern_scalar(kernel):
    return lambda q, amp, freq: kernel.python_fn(q.x, q.y, q.z, amp, freq)


def _bind_pattern_numpy(kernel):
    return lambda q, amp, freq: kernel.numpy_fn(q.x, q.y, q.z, amp, freq)


for _kernel in NOISE_NODES.values():
    EVAL_NS[_kernel.main_fn_name] = _bind_pattern_scalar(_kernel)
    EVAL_NS_NP[_kernel.main_fn_name] = _bind_pattern_numpy(_kernel)


def _eval_formula_grid_np(formula, custom_params, extra_ns, shape):
    """Evaluate `formula` once over whole arrays via EVAL_NS_NP. `extra_ns`
    supplies the point/coordinate variables (e.g. {"p": GlslVec3(...)} or
    {"u": u_arr, "w": w_arr}). Raises on anything not vectorizable — callers
    must catch and fall back to the per-point loop."""
    ns = dict(EVAL_NS_NP)
    ns.update(extra_ns)
    for k, v in custom_params.items():
        if k == "radial" or k == "front":
            continue
        if isinstance(v, (tuple, list)):
            if len(v) == 2:
                ns[k] = GlslVec2(float(v[0]), float(v[1]))
            else:
                ns[k] = GlslVec3(float(v[0]), float(v[1]), float(v[2]))
        elif hasattr(v, 'x') and hasattr(v, 'y'):
            if hasattr(v, 'z'):
                ns[k] = GlslVec3(float(v.x), float(v.y), float(v.z))
            else:
                ns[k] = GlslVec2(float(v.x), float(v.y))
        else:
            ns[k] = v
    with np.errstate(all='ignore'):
        result = eval(formula, {}, ns)
        if isinstance(result, GlslVec3):
            rx = np.broadcast_to(np.asarray(result.x, dtype=np.float64), shape)
            ry = np.broadcast_to(np.asarray(result.y, dtype=np.float64), shape)
            rz = np.broadcast_to(np.asarray(result.z, dtype=np.float64), shape)
            return GlslVec3(
                np.nan_to_num(rx, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32),
                np.nan_to_num(ry, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32),
                np.nan_to_num(rz, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32),
            )
        if isinstance(result, GlslVec2):
            rx = np.broadcast_to(np.asarray(result.x, dtype=np.float64), shape)
            ry = np.broadcast_to(np.asarray(result.y, dtype=np.float64), shape)
            return GlslVec2(
                np.nan_to_num(rx, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32),
                np.nan_to_num(ry, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32),
            )
        result = np.broadcast_to(np.asarray(result, dtype=np.float64), shape)
        return np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
