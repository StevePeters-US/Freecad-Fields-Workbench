# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.sdf.sdf_field import SdfField
import math
from collections import namedtuple
import numpy as np
import re


def parse_custom_params(formula_text):
    """
    Parses custom parameters from formula text comments.
    Format: // @param <type> <name> <default> [<min> <max> [<step>]]
    """
    params = []
    if not formula_text:
        return params
    # Match comment lines starting with // or # and @param
    pattern = r'^\s*(?://|#)\s*@param\s+(\w+)\s+(\w+)\s+([\d.-]+)(?:\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?)?'
    for line in formula_text.splitlines():
        match = re.match(pattern, line)
        if match:
            ptype = match.group(1).lower()
            pname = match.group(2)
            pdefault_str = match.group(3)
            pmin_str = match.group(4)
            pmax_str = match.group(5)
            pstep_str = match.group(6)
            
            if ptype not in ('float', 'int', 'slider', 'bool'):
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
                from freecad.fields.core import fld_logger
                fld_logger.debug(f"parse_custom_params: skipping malformed param line ({e})")
                continue
    return params


def update_formula_param_comment(formula_text, pname, ptype, current_val, min_val=None, max_val=None, step_val=None):
    if not formula_text:
        return formula_text
    lines = formula_text.splitlines()
    for i, line in enumerate(lines):
        # Match this parameter's comment line
        match = re.match(rf'^(\s*(?://|#)\s*@param\s+{ptype}\s+{pname}\s+)([\d.-]+)(?:\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?)?', line)
        if match:
            prefix = match.group(1)
            # Construct new line
            if ptype == 'bool':
                new_line = f"{prefix.rstrip()} {1 if current_val else 0}"
            elif ptype == 'int':
                new_line = f"{prefix.rstrip()} {int(current_val)}"
                if min_val is not None and max_val is not None:
                    new_line += f" {int(min_val)} {int(max_val)}"
                    if step_val is not None:
                        new_line += f" {int(step_val)}"
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


_ComplexPreset = namedtuple(
    '_ComplexPreset',
    ['dep_helpers', 'main_fn_name', 'main_fn_code', 'python_fn', 'numpy_fn', 'display',
     'lip_factor', 'peak_factor']
)


# ── Python noise implementations (CPU fallback) ───────────────────────────────

def _fade(t): return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
def _lerp(a, b, t): return a + t * (b - a)


# ── Lattice hash: integer, so the CPU and the GPU get the SAME noise ──────────
#
# This used to be `fract(sin(dot(p, k)) * 43758.5453123)`, the ubiquitous
# shadertoy hash. It is not one function -- it is a different function on each
# side of the CPU/GPU line. `sin` runs in float64 here and in float32 there (and
# GLSL only requires `sin` to be accurate to a few ULP, so the driver's is worse
# still), the lattice coordinate is scaled by ~300 before the sine, and the
# result is then multiplied by 43758: a 1e-5 disagreement in the argument moves
# the product by a whole integer and `fract` lands somewhere unrelated.
#
# Measured, same algorithm, only the precision changed -- 4000 points on
# sphere(r=30) + Perlin(amp 3, freq 0.1): the float32 and float64 fields
# correlate at +0.18 and differ by up to 2.45 mm; at amp 8 by up to 7.49 mm.
# They are different lumps of the same statistical character. The slicer traces
# the CPU field and the viewport draws the GPU one, so a contour that is
# genuinely 0.09 mm from the surface it was fitted to sits millimetres off the
# surface on screen -- which is exactly what it looked like.
#
# pcg3d / pcg2d (Jarzynski & Olano, "Hash Functions for GPU Rendering", JCGT
# 9(3), 2020) are integer mixers. uint32 arithmetic wraps identically in GLSL,
# in numpy and in Python-with-a-mask, so all three get the same bits out; the
# top 24 of those bits convert to float exactly in float32 as well as float64,
# so the gradients are bit-identical rather than merely close. Every hash below
# feeds on `floor()`ed lattice coordinates, so the integer domain costs nothing.
_U32 = 0xFFFFFFFF
_HASH_SCALE = 1.0 / 16777216.0   # 2^-24; (h >> 8) < 2^24 is exact as a float32


def _pcg3d_py(x, y, z):
    x = (x * 1664525 + 1013904223) & _U32
    y = (y * 1664525 + 1013904223) & _U32
    z = (z * 1664525 + 1013904223) & _U32
    x = (x + y * z) & _U32; y = (y + z * x) & _U32; z = (z + x * y) & _U32
    x ^= x >> 16; y ^= y >> 16; z ^= z >> 16
    x = (x + y * z) & _U32; y = (y + z * x) & _U32; z = (z + x * y) & _U32
    return x, y, z


def _pcg2d_py(x, y):
    x = (x * 1664525 + 1013904223) & _U32
    y = (y * 1664525 + 1013904223) & _U32
    x = (x + y * 1664525) & _U32
    y = (y + x * 1664525) & _U32
    x ^= x >> 16; y ^= y >> 16
    x = (x + y * 1664525) & _U32
    y = (y + x * 1664525) & _U32
    x ^= x >> 16; y ^= y >> 16
    return x, y


def _ghash3_py(px, py, pz):
    hx, hy, hz = _pcg3d_py(int(px) & _U32, int(py) & _U32, int(pz) & _U32)
    return (
        -1.0 + 2.0 * ((hx >> 8) * _HASH_SCALE),
        -1.0 + 2.0 * ((hy >> 8) * _HASH_SCALE),
        -1.0 + 2.0 * ((hz >> 8) * _HASH_SCALE),
    )

def _perlin3d_py(px, py, pz, amp, freq):
    px *= freq; py *= freq; pz *= freq
    ix = math.floor(px); iy = math.floor(py); iz = math.floor(pz)
    fx = px - ix; fy = py - iy; fz = pz - iz
    ux = _fade(fx); uy = _fade(fy); uz = _fade(fz)
    def n(dx, dy, dz):
        gx, gy, gz = _ghash3_py(ix+dx, iy+dy, iz+dz)
        return gx*(fx-dx) + gy*(fy-dy) + gz*(fz-dz)
    v = _lerp(
        _lerp(_lerp(n(0,0,0), n(1,0,0), ux), _lerp(n(0,1,0), n(1,1,0), ux), uy),
        _lerp(_lerp(n(0,0,1), n(1,0,1), ux), _lerp(n(0,1,1), n(1,1,1), ux), uy), uz)
    return amp * v


def _vhash33_py(px, py, pz):
    hx, hy, hz = _pcg3d_py(int(px) & _U32, int(py) & _U32, int(pz) & _U32)
    return ((hx >> 8) * _HASH_SCALE,
            (hy >> 8) * _HASH_SCALE,
            (hz >> 8) * _HASH_SCALE)

def _voronoi3d_py(px, py, pz, amp, freq):
    px *= freq; py *= freq; pz *= freq
    ix = math.floor(px); iy = math.floor(py); iz = math.floor(pz)
    fx = px - ix; fy = py - iy; fz = pz - iz
    md = 1e9
    for xi in range(-1, 2):
        for yi in range(-1, 2):
            for zi in range(-1, 2):
                hx, hy, hz = _vhash33_py(ix+xi, iy+yi, iz+zi)
                rx = xi - fx + hx; ry = yi - fy + hy; rz = zi - fz + hz
                d = rx*rx + ry*ry + rz*rz
                if d < md: md = d
    return amp * math.sqrt(md) / 0.866025 - amp * 0.5


def _ghash2_py(u, w):
    hx, hy = _pcg2d_py(int(u) & _U32, int(w) & _U32)
    return (-1.0 + 2.0 * ((hx >> 8) * _HASH_SCALE),
            -1.0 + 2.0 * ((hy >> 8) * _HASH_SCALE))

def _perlin2d_py(x, y, amp, freq):
    x *= freq; y *= freq
    ix = math.floor(x); iy = math.floor(y)
    fx = x - ix; fy = y - iy
    ux = _fade(fx); uy = _fade(fy)
    def n(dx, dy):
        gx, gy = _ghash2_py(ix+dx, iy+dy)
        return gx*(fx-dx) + gy*(fy-dy)
    return amp * _lerp(_lerp(n(0,0), n(1,0), ux), _lerp(n(0,1), n(1,1), ux), uy)


def _vhash22_py(x, y):
    hx, hy = _pcg2d_py(int(x) & _U32, int(y) & _U32)
    return ((hx >> 8) * _HASH_SCALE, (hy >> 8) * _HASH_SCALE)

def _voronoi2d_py(x, y, amp, freq):
    x *= freq; y *= freq
    ix = math.floor(x); iy = math.floor(y)
    fx = x - ix; fy = y - iy
    md = 1e9
    for xi in range(-1, 2):
        for yi in range(-1, 2):
            hx, hy = _vhash22_py(ix+xi, iy+yi)
            rx = xi - fx + hx; ry = yi - fy + hy
            d = rx*rx + ry*ry
            if d < md: md = d
    return amp * math.sqrt(md) / 0.707107 - amp * 0.5


# ── Vectorized numpy noise implementations (CPU dense volume bake) ─────────────
# Bit-exact (max abs error 0.0 over 200k random points) vs the _py scalar
# versions above — verified numerically since these are hand-transcribed, not
# derived. Used by evaluate_grid() so a dense volume bake over ~2M points
# doesn't degrade into a ~10us/point Python interpreter loop (was ~22s for a
# single bake; this path is ~1-2s and is real numpy vectorization, not a
# rewrite of the algorithm).

# Above this many points the per-point eval() fallback is a multi-minute freeze;
# contribute nothing and tell the user instead of hanging the UI.
_MAX_SCALAR_FALLBACK_POINTS = 50_000


def _fade_np(t): return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
def _lerp_np(a, b, t): return a + t * (b - a)


_U32_C = np.uint32(1664525)
_U32_A = np.uint32(1013904223)
_U32_S = np.uint32(16)


def _to_u32(a):
    """Integral float lattice coordinates as wrapped uint32, GLSL's uvec(ivec()).

    The intermediate int64 is not optional: casting a negative float straight to
    uint32 is undefined in numpy, while int64 -> uint32 wraps two's-complement,
    which is what `uvec3(ivec3(p))` does in the shader.
    """
    return np.asarray(a, dtype=np.float64).astype(np.int64).astype(np.uint32)


def _pcg3d_np(x, y, z):
    x = _to_u32(x) * _U32_C + _U32_A
    y = _to_u32(y) * _U32_C + _U32_A
    z = _to_u32(z) * _U32_C + _U32_A
    x = x + y * z; y = y + z * x; z = z + x * y
    x = x ^ (x >> _U32_S); y = y ^ (y >> _U32_S); z = z ^ (z >> _U32_S)
    x = x + y * z; y = y + z * x; z = z + x * y
    return x, y, z


def _pcg2d_np(x, y):
    x = _to_u32(x) * _U32_C + _U32_A
    y = _to_u32(y) * _U32_C + _U32_A
    x = x + y * _U32_C
    y = y + x * _U32_C
    x = x ^ (x >> _U32_S); y = y ^ (y >> _U32_S)
    x = x + y * _U32_C
    y = y + x * _U32_C
    x = x ^ (x >> _U32_S); y = y ^ (y >> _U32_S)
    return x, y


def _u2f(h):
    return (h >> np.uint32(8)).astype(np.float64) * _HASH_SCALE


def _ghash3_np(px, py, pz):
    hx, hy, hz = _pcg3d_np(px, py, pz)
    return (-1.0 + 2.0 * _u2f(hx),
            -1.0 + 2.0 * _u2f(hy),
            -1.0 + 2.0 * _u2f(hz))

def _perlin3d_np(px, py, pz, amp, freq):
    px = px * freq; py = py * freq; pz = pz * freq
    ix = np.floor(px); iy = np.floor(py); iz = np.floor(pz)
    fx = px - ix; fy = py - iy; fz = pz - iz
    ux = _fade_np(fx); uy = _fade_np(fy); uz = _fade_np(fz)
    def n(dx, dy, dz):
        gx, gy, gz = _ghash3_np(ix+dx, iy+dy, iz+dz)
        return gx*(fx-dx) + gy*(fy-dy) + gz*(fz-dz)
    v = _lerp_np(
        _lerp_np(_lerp_np(n(0,0,0), n(1,0,0), ux), _lerp_np(n(0,1,0), n(1,1,0), ux), uy),
        _lerp_np(_lerp_np(n(0,0,1), n(1,0,1), ux), _lerp_np(n(0,1,1), n(1,1,1), ux), uy), uz)
    return amp * v


def _vhash33_np(px, py, pz):
    hx, hy, hz = _pcg3d_np(px, py, pz)
    return _u2f(hx), _u2f(hy), _u2f(hz)

def _voronoi3d_np(px, py, pz, amp, freq):
    px = px * freq; py = py * freq; pz = pz * freq
    ix = np.floor(px); iy = np.floor(py); iz = np.floor(pz)
    fx = px - ix; fy = py - iy; fz = pz - iz
    md = np.full(px.shape, 1e9, dtype=np.float64)
    for xi in range(-1, 2):
        for yi in range(-1, 2):
            for zi in range(-1, 2):
                hx, hy, hz = _vhash33_np(ix+xi, iy+yi, iz+zi)
                rx = xi - fx + hx; ry = yi - fy + hy; rz = zi - fz + hz
                d = rx*rx + ry*ry + rz*rz
                md = np.minimum(md, d)
    return amp * np.sqrt(md) / 0.866025 - amp * 0.5


def _ghash2_np(x, y):
    hx, hy = _pcg2d_np(x, y)
    return -1.0 + 2.0 * _u2f(hx), -1.0 + 2.0 * _u2f(hy)

def _perlin2d_np(x, y, amp, freq):
    x = x * freq; y = y * freq
    ix = np.floor(x); iy = np.floor(y)
    fx = x - ix; fy = y - iy
    ux = _fade_np(fx); uy = _fade_np(fy)
    def n(dx, dy):
        gx, gy = _ghash2_np(ix+dx, iy+dy)
        return gx*(fx-dx) + gy*(fy-dy)
    return amp * _lerp_np(_lerp_np(n(0,0), n(1,0), ux), _lerp_np(n(0,1), n(1,1), ux), uy)


def _vhash22_np(x, y):
    hx, hy = _pcg2d_np(x, y)
    return _u2f(hx), _u2f(hy)

def _voronoi2d_np(x, y, amp, freq):
    x = x * freq; y = y * freq
    ix = np.floor(x); iy = np.floor(y)
    fx = x - ix; fy = y - iy
    md = np.full(x.shape, 1e9, dtype=np.float64)
    for xi in range(-1, 2):
        for yi in range(-1, 2):
            hx, hy = _vhash22_np(ix+xi, iy+yi)
            rx = xi - fx + hx; ry = yi - fy + hy
            d = rx*rx + ry*ry
            md = np.minimum(md, d)
    return amp * np.sqrt(md) / 0.707107 - amp * 0.5


# ── GLSL helper strings ───────────────────────────────────────────────────────

# The GPU half of the integer lattice hash -- see `_pcg3d_py` for why `sin` had
# to go. These must stay a line-by-line match for the numpy versions; the whole
# point is that both sides compute the same bits.
_PCG3D_GLSL = """uvec3 fld_pcg3d(uvec3 v) {
    v = v * 1664525u + 1013904223u;
    v.x += v.y * v.z; v.y += v.z * v.x; v.z += v.x * v.y;
    v ^= v >> 16u;
    v.x += v.y * v.z; v.y += v.z * v.x; v.z += v.x * v.y;
    return v;
}"""

_PCG2D_GLSL = """uvec2 fld_pcg2d(uvec2 v) {
    v = v * 1664525u + 1013904223u;
    v.x += v.y * 1664525u;
    v.y += v.x * 1664525u;
    v ^= v >> 16u;
    v.x += v.y * 1664525u;
    v.y += v.x * 1664525u;
    v ^= v >> 16u;
    return v;
}"""

_GHASH3_GLSL = """vec3 fld_ghash3(vec3 p) {
    uvec3 h = fld_pcg3d(uvec3(ivec3(p))) >> 8u;
    return -1.0 + 2.0 * (vec3(h) * (1.0 / 16777216.0));
}"""

_PERLIN3D_GLSL = """float fld_perlin3d(vec3 p, float amp, float freq) {
    p = p * freq;
    vec3 i = floor(p);
    vec3 f = fract(p);
    vec3 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
    float n000 = dot(fld_ghash3(i),               f);
    float n100 = dot(fld_ghash3(i+vec3(1.,0.,0.)), f-vec3(1.,0.,0.));
    float n010 = dot(fld_ghash3(i+vec3(0.,1.,0.)), f-vec3(0.,1.,0.));
    float n110 = dot(fld_ghash3(i+vec3(1.,1.,0.)), f-vec3(1.,1.,0.));
    float n001 = dot(fld_ghash3(i+vec3(0.,0.,1.)), f-vec3(0.,0.,1.));
    float n101 = dot(fld_ghash3(i+vec3(1.,0.,1.)), f-vec3(1.,0.,1.));
    float n011 = dot(fld_ghash3(i+vec3(0.,1.,1.)), f-vec3(0.,1.,1.));
    float n111 = dot(fld_ghash3(i+vec3(1.,1.,1.)), f-vec3(1.,1.,1.));
    return amp * mix(
        mix(mix(n000, n100, u.x), mix(n010, n110, u.x), u.y),
        mix(mix(n001, n101, u.x), mix(n011, n111, u.x), u.y), u.z);
}"""

_VHASH33_GLSL = """vec3 fld_vhash33(vec3 p) {
    uvec3 h = fld_pcg3d(uvec3(ivec3(p))) >> 8u;
    return vec3(h) * (1.0 / 16777216.0);
}"""

_VORONOI3D_GLSL = """float fld_voronoi3d(vec3 p, float amp, float freq) {
    p = p * freq;
    vec3 pi = floor(p);
    vec3 pf = fract(p);
    float md = 1e9;
    for(int x=-1; x<=1; x++) for(int y=-1; y<=1; y++) for(int z=-1; z<=1; z++) {
        vec3 b = vec3(float(x), float(y), float(z));
        vec3 r = b - pf + fld_vhash33(pi + b);
        md = min(md, dot(r, r));
    }
    return amp * sqrt(md) / 0.866025 - amp * 0.5;
}"""

_GHASH2_GLSL = """vec2 fld_ghash2(vec2 p) {
    uvec2 h = fld_pcg2d(uvec2(ivec2(p))) >> 8u;
    return -1.0 + 2.0 * (vec2(h) * (1.0 / 16777216.0));
}"""

_PERLIN2D_GLSL = """float fld_perlin2d(vec3 p, float amp, float freq, vec3 u_ax, vec3 w_ax) {
    vec2 q = vec2(dot(p, u_ax), dot(p, w_ax)) * freq;
    vec2 i = floor(q);
    vec2 f = fract(q);
    vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
    float n00 = dot(fld_ghash2(i),              f);
    float n10 = dot(fld_ghash2(i+vec2(1.,0.)), f-vec2(1.,0.));
    float n01 = dot(fld_ghash2(i+vec2(0.,1.)), f-vec2(0.,1.));
    float n11 = dot(fld_ghash2(i+vec2(1.,1.)), f-vec2(1.,1.));
    return amp * mix(mix(n00, n10, u.x), mix(n01, n11, u.x), u.y);
}"""

_VHASH22_GLSL = """vec2 fld_vhash22(vec2 p) {
    uvec2 h = fld_pcg2d(uvec2(ivec2(p))) >> 8u;
    return vec2(h) * (1.0 / 16777216.0);
}"""

_VORONOI2D_GLSL = """float fld_voronoi2d(vec3 p, float amp, float freq, vec3 u_ax, vec3 w_ax) {
    vec2 q = vec2(dot(p, u_ax), dot(p, w_ax)) * freq;
    vec2 pi = floor(q);
    vec2 pf = fract(q);
    float md = 1e9;
    for(int x=-1; x<=1; x++) for(int y=-1; y<=1; y++) {
        vec2 b = vec2(float(x), float(y));
        vec2 r = b - pf + fld_vhash22(pi + b);
        md = min(md, dot(r, r));
    }
    return amp * sqrt(md) / 0.707107 - amp * 0.5;
}"""


# ── Complex preset registries ─────────────────────────────────────────────────

# Value noise was removed from both registries (2D first, 3D on 2026-08-03) as
# not useful in practice. Its scalar, numpy and GLSL implementations and the
# fld_vhash2/fld_vhash3 helpers that served only it went with it -- do not
# resurrect one half. Perlin and Voronoi are the only genuinely 3D noise
# functions here.
_COMPLEX_PRESETS_3D = {
    "Perlin": _ComplexPreset(
        dep_helpers=[("fld_pcg3d", _PCG3D_GLSL), ("fld_ghash3", _GHASH3_GLSL)],
        main_fn_name="fld_perlin3d",
        main_fn_code=_PERLIN3D_GLSL,
        python_fn=_perlin3d_py,
        numpy_fn=_perlin3d_np,
        display="# Classic Perlin gradient noise\n# Output: approx [-amp, +amp]",
        lip_factor=2.0,
        peak_factor=1.0,
    ),
    "Voronoi": _ComplexPreset(
        dep_helpers=[("fld_pcg3d", _PCG3D_GLSL), ("fld_vhash33", _VHASH33_GLSL)],
        main_fn_name="fld_voronoi3d",
        main_fn_code=_VORONOI3D_GLSL,
        python_fn=_voronoi3d_py,
        numpy_fn=_voronoi3d_np,
        display="# Voronoi cellular noise (centered)\n# Output: [-amp/2, +amp/2]",
        lip_factor=1.5,
        peak_factor=0.5,
    ),
}

_COMPLEX_PRESETS_2D = {
    "Perlin": _ComplexPreset(
        dep_helpers=[("fld_pcg2d", _PCG2D_GLSL), ("fld_ghash2", _GHASH2_GLSL)],
        main_fn_name="fld_perlin2d",
        main_fn_code=_PERLIN2D_GLSL,
        python_fn=_perlin2d_py,
        numpy_fn=_perlin2d_np,
        display="# Classic Perlin gradient noise projected onto plane\n# Output: approx [-amp, +amp]",
        lip_factor=2.0,
        peak_factor=1.0,
    ),
    "Voronoi": _ComplexPreset(
        dep_helpers=[("fld_pcg2d", _PCG2D_GLSL), ("fld_vhash22", _VHASH22_GLSL)],
        main_fn_name="fld_voronoi2d",
        main_fn_code=_VORONOI2D_GLSL,
        python_fn=_voronoi2d_py,
        numpy_fn=_voronoi2d_np,
        display="# Voronoi cellular noise projected onto plane (centered)\n# Output: [-amp/2, +amp/2]",
        lip_factor=1.5,
        peak_factor=0.5,
    ),
}


# ── Python eval helpers (for Custom / simple formula presets) ─────────────────

class _GlslVec3:
    __slots__ = ('x', 'y', 'z')
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


_EVAL_NS = {
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
    "length": lambda v: math.sqrt(v.x ** 2 + v.y ** 2 + v.z ** 2),
    "dot": lambda a, b: a.x * b.x + a.y * b.y + a.z * b.z,
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


# Vectorized (numpy) counterpart of _EVAL_NS — used to evaluate a Custom/simple
# formula string ONCE over a whole grid array instead of once per point. Most
# GLSL-style formulas (the only kind these presets are meant to hold) broadcast
# over numpy arrays with zero changes since they're built from +-*/ and the
# functions below. If a formula does something non-vectorizable (e.g. a Python
# `if/else` ternary on an array), eval() raises and the caller falls back to
# the guaranteed-correct per-point loop.
_EVAL_NS_NP = {
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
    # _MAX_SCALAR_FALLBACK_POINTS they refuse the per-point fallback and return
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
    # nan_to_num, matching the scalar _EVAL_NS version's try/except -> 0.0.
    "smoothstep": lambda e0, e1, x: (
        lambda t: t * t * (3.0 - 2.0 * t)
    )(np.clip((x - e0) / (e1 - e0), 0.0, 1.0)),
    "pi": math.pi,
    "radians": np.radians,
    "length": lambda v: np.sqrt(v.x ** 2 + v.y ** 2 + v.z ** 2),
    "dot": lambda a, b: a.x * b.x + a.y * b.y + a.z * b.z,
}


def _eval_formula_grid_np(formula, custom_params, extra_ns, shape):
    """Evaluate `formula` once over whole arrays via _EVAL_NS_NP. `extra_ns`
    supplies the point/coordinate variables (e.g. {"p": _GlslVec3(...)} or
    {"u": u_arr, "w": w_arr}). Raises on anything not vectorizable — callers
    must catch and fall back to the per-point loop."""
    ns = dict(_EVAL_NS_NP)
    ns.update(extra_ns)
    for k, v in custom_params.items():
        if k == "radial":
            continue
        ns[k] = v
    with np.errstate(all='ignore'):
        result = eval(formula, {}, ns)
        result = np.broadcast_to(np.asarray(result, dtype=np.float64), shape)
        return np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


# ── SdfNoise2DField ───────────────────────────────────────────────────────────

class SdfNoise2DField(SdfField):
    """
    Applies 2D projected noise to a base SDF field.
    Formula variables (Custom): u (float), w (float), amp (float), freq (float).
    """
    # Where the original surface sits on the waveform. The surface is displaced
    # to `s + noise * direction` and `direction` points INTO the material (the
    # arrow aims at the face it perturbs), so a positive noise value carves and a
    # negative one grows. "Middle" leaves the wave centred on the surface -- half
    # of it therefore sticks out past the stock it started from. "Top" adds one
    # peak so the wave hangs entirely inside (removal only, stock never grows);
    # "Bottom" subtracts one so it sits entirely outside (material only added).
    # The wave keeps its peak-to-peak size in every mode -- this is a shift, not
    # a rescale, exactly like Blender's Displace "Midlevel".
    #
    # The offset is a DC term, so it only means anything measured against a
    # surface: applied to the whole field it slides the entire solid along the
    # direction instead of sitting the wave on the face. It therefore always
    # rides the front-face weight -- see _apply_front_weight.
    BIAS_MODES = ["Bottom", "Middle", "Top"]
    DEFAULT_BIAS = "Middle"

    PRESETS = {
        "Waves": (
            "// @param bool radial 0\n"
            "// @param float angle 0.0 0.0 360.0 5.0\n"
            "sin((mix(x, sqrt(x * x + y * y), radial) * cos(radians(angle)) + mix(y, z, radial) * sin(radians(angle))) * freq) * amp"
        ),
        # `sharp` band-limits what used to be sign(sin(...)). A sign() step is a
        # jump discontinuity, so no finite Lipschitz constant exists for it and
        # _lip() below could only ever lie — which pits the octree (it culls with
        # |d| <= half_diag * L) and lets the marcher overstep the wall.
        # tanh(k*sin(f*x)) is the same shape with a slope of exactly k*f.
        "Square": (
            "// @param float angle 0.0 0.0 360.0 5.0\n"
            "// @param float sharp 4.0 1.0 20.0 0.5\n"
            "// @param float amp2 0.0 0.0 1.0 0.05\n"
            "// @param float freq2 1.0 0.1 10.0 0.1\n"
            "// @param float angle2 90.0 0.0 360.0 5.0\n"
            "tanh(sharp * sin(mix(x * cos(radians(angle)) + y * sin(radians(angle)), r, radial) * freq)) * amp"
            " + tanh(sharp * sin(mix(x * cos(radians(angle2)) + y * sin(radians(angle2)), r, radial) * freq2)) * amp2"
        ),
        "Sawtooth": (
            "// @param float angle 0.0 0.0 360.0 5.0\n"
            "// @param float amp2 0.0 0.0 1.0 0.05\n"
            "// @param float freq2 1.0 0.1 10.0 0.1\n"
            "// @param float angle2 90.0 0.0 360.0 5.0\n"
            "(4.0 * abs(fract(mix(x * cos(radians(angle)) + y * sin(radians(angle)), r, radial) * freq - 0.25) - 0.5) - 1.0) * amp"
            " + (4.0 * abs(fract(mix(x * cos(radians(angle2)) + y * sin(radians(angle2)), r, radial) * freq2 - 0.25) - 0.5) - 1.0) * amp2"
        ),
    }
    COMPLEX_PRESETS = _COMPLEX_PRESETS_2D
    PRESET_NAMES = list(PRESETS.keys()) + list(COMPLEX_PRESETS.keys()) + ["Custom"]
    DEFAULT_FORMULA = PRESETS["Waves"]

    def __init__(self, base_field: SdfField, amplitude: float = 1.0, frequency: float = 1.0,
                 direction: FreeCAD.Vector = None, formula: str = None, normalization: float = 0.0,
                 preset_name: str = None, custom_params: dict = None, ignore_back_face: bool = False,
                 center: FreeCAD.Vector = None, radial: bool = False, roll: float = 0.0,
                 bias: str = None):
        self.base_field = base_field
        self.amplitude = amplitude
        self.frequency = frequency
        d = FreeCAD.Vector(direction if direction is not None else FreeCAD.Vector(0, 0, -1))
        length = d.Length
        self.direction = d / length if length > 1e-8 else FreeCAD.Vector(0, 0, -1)
        self.roll = float(roll)
        self._u_axis, self._w_axis = self._make_basis(self.direction, self.roll)
        self.center = FreeCAD.Vector(center) if center is not None else FreeCAD.Vector(0, 0, 0)
        self._normalization = normalization
        self.custom_params = custom_params or {}
        self._ignore_back_face = ignore_back_face
        self.radial = radial
        self.bias = bias if bias in self.BIAS_MODES else self.DEFAULT_BIAS
        self._axis_extent_cache = None

        self._preset_name = preset_name or "Waves"
        if self._preset_name in self.COMPLEX_PRESETS:
            self.formula = None
            self._clean_formula = None
        else:
            self.formula = formula if formula is not None else self.PRESETS.get(self._preset_name, self.DEFAULT_FORMULA)
            if self.formula:
                for p in parse_custom_params(self.formula):
                    if p['name'] not in self.custom_params and p['default'] is not None:
                        self.custom_params[p['name']] = p['default']
            self._clean_formula = clean_formula_code(self.formula)

    @staticmethod
    def _make_basis(d, roll: float = 0.0):
        """Orthonormal (u, w) spanning the plane the pattern is drawn in.

        `roll` (degrees) spins that basis about `d` itself. Direction alone cannot
        express it — rotating d about d is the identity — so without a stored roll
        the in-plane orientation of the pattern is not editable at all.
        """
        ref = FreeCAD.Vector(1, 0, 0) if abs(d.x) < 0.9 else FreeCAD.Vector(0, 1, 0)
        u = d.cross(ref)
        u = u / u.Length if u.Length > 1e-8 else FreeCAD.Vector(0, 1, 0)
        w = d.cross(u)
        w = w / w.Length if w.Length > 1e-8 else FreeCAD.Vector(1, 0, 0)
        if abs(roll) > 1e-9:
            rot = FreeCAD.Rotation(d, roll)
            u = rot.multVec(u)
            w = rot.multVec(w)
        return u, w

    @staticmethod
    def roll_from_basis(d, u_vec):
        """Inverse of _make_basis' roll: the spin of `u_vec` about `d`, in degrees.

        Lets a caller rotate the whole frame (direction + u axis) with one rotation
        and hand the leftover spin back as a Roll value.
        """
        u0, w0 = SdfNoise2DField._make_basis(d)
        return math.degrees(math.atan2(u_vec.dot(w0), u_vec.dot(u0)))

    def _lip(self):
        if self._normalization > 0.0:
            return self._normalization
        if self._preset_name in self.COMPLEX_PRESETS:
            factor = self.COMPLEX_PRESETS[self._preset_name].lip_factor
            return max(1.0 + abs(self.amplitude) * self.frequency * factor
                       + self._front_weight_slope(), 1.0)
        factor = 1.414
        amp2 = abs(self.custom_params.get("amp2", 0.0))
        freq2 = abs(self.custom_params.get("freq2", 0.0))
        slope = abs(self.amplitude) * self.frequency + amp2 * freq2
        # `Square` is a tanh-band-limited square wave: d/dx tanh(k*sin(f*x))
        # peaks at k*f (sech^2 == 1 at the zero crossing), so its slope is k
        # times a plain sinusoid's. Band-limiting is what makes this number
        # meaningful at all — a raw sign() step has no finite bound.
        if self._preset_name == "Square":
            slope *= max(abs(self.custom_params.get("sharp", 4.0)), 1.0)
        return max(1.0 + slope * factor + self._front_weight_slope(), 1.0)

    def lipschitz(self) -> float:
        """Upper bound on |grad| of the value this field returns.

        The value itself is NOT divided by `_lip()`. Dividing it keeps the ray
        march's step safe and wrecks everything the march compares against a
        constant in millimetres: it declares a hit at `d < 0.5*vmax`, so on a
        field scaled down by L that fires `0.5*vmax*L` mm from the surface — 28 mm
        at amp 100 on a 3.3 mm voxel, in every direction at once, which is the
        "2D noise deforms in x and y as well as z" report. The bound belongs here,
        where the octree, the bake's block scan and the march's STEP divisor read
        it. Same resolution UX-009 took for `SdfCageDeformField`.
        """
        return self.base_field.lipschitz() * self._lip()

    def _wave_peak(self):
        """Upper bound on |noise| — how far the wave can push the surface either way.

        Bias shifts the whole wave by exactly this, so it has to be an
        OVER-estimate: too small and "Top" would still let the stock grow. The
        presets stay bounded by amp + amp2 (`Waves` also multiplies by
        exp(-r*decay) <= 1, `Square` by tanh() < 1, `Sawtooth` by a triangle in
        [-1, 1]); a Custom formula can of course exceed it, which costs accuracy
        of the anchor, not validity of the field.
        """
        if self._preset_name in self.COMPLEX_PRESETS:
            return abs(self.amplitude) * self.COMPLEX_PRESETS[self._preset_name].peak_factor
        return abs(self.amplitude) + abs(self.custom_params.get("amp2", 0.0))

    def _bias_offset(self):
        """Constant added to the noise before it displaces the point.

        It is a *constant*: it moves the wave, it never tilts it, so `_lip()` is
        unaffected and needs no bias term.
        """
        if self.bias == "Top":
            return self._wave_peak()
        if self.bias == "Bottom":
            return -self._wave_peak()
        return 0.0

    def _eval_formula(self, x, y, z=0.0):
        if self._preset_name in self.COMPLEX_PRESETS:
            return self.COMPLEX_PRESETS[self._preset_name].python_fn(x, y, self.amplitude, self.frequency)
        ns = dict(_EVAL_NS)
        ns["x"] = x; ns["y"] = y; ns["z"] = z
        ns["u"] = x; ns["w"] = y  # backward compatibility aliases
        ns["r"] = math.sqrt(x * x + y * y)
        is_radial = bool(self.radial or self.custom_params.get("radial", False))
        ns["d"] = ns["r"] if is_radial else x
        ns["radial"] = 1.0 if is_radial else 0.0
        ns["amp"] = self.amplitude; ns["freq"] = self.frequency
        for k, v in self.custom_params.items():
            if k == "radial":
                continue
            ns[k] = v
        try:
            return float(eval(self._clean_formula, {}, ns))
        except Exception as e:
            from freecad.fields.core import fld_logger
            fld_logger.debug_throttled(
                "noise2d_formula_eval_fail",
                f"SdfNoise2DField: formula eval failed ({e}); contributing 0.0"
            )
            return 0.0

    def _eval_noise_grid(self, x_vals, y_vals, z_vals=None):
        """Vectorized counterpart of _eval_formula for a whole grid at once."""
        if z_vals is None:
            z_vals = np.zeros_like(x_vals)
        if self._preset_name in self.COMPLEX_PRESETS:
            preset = self.COMPLEX_PRESETS[self._preset_name]
            return preset.numpy_fn(x_vals, y_vals, self.amplitude, self.frequency).astype(np.float32)
        try:
            is_radial = bool(self.radial or self.custom_params.get("radial", False))
            r_vals = np.sqrt(x_vals**2 + y_vals**2)
            d_vals = r_vals if is_radial else x_vals
            radial_val = np.float32(1.0 if is_radial else 0.0)
            return _eval_formula_grid_np(
                self._clean_formula, self.custom_params,
                {"x": x_vals, "y": y_vals, "z": z_vals,
                 "u": x_vals, "w": y_vals,
                 "r": r_vals, "d": d_vals, "radial": radial_val,
                 "amp": self.amplitude, "freq": self.frequency},
                x_vals.shape,
            )
        except Exception as e:
            from freecad.fields.core import fld_logger
            n = len(x_vals)
            if n > _MAX_SCALAR_FALLBACK_POINTS:
                fld_logger.error(
                    f"SdfNoise2DField: formula not vectorizable ({e}) and grid is "
                    f"{n} points — refusing the per-point fallback (would freeze). "
                    "Noise contributes 0; rewrite the formula using numpy-safe ops."
                )
                return np.zeros(x_vals.shape, dtype=np.float32)
            fld_logger.debug_throttled(
                "noise2d_vectorize_fallback",
                f"SdfNoise2DField: formula not vectorizable ({e}), falling back to per-point eval"
            )
            return np.fromiter(
                (self._eval_formula(float(x_vals[i]), float(y_vals[i]), float(z_vals[i])) for i in range(len(x_vals))),
                dtype=np.float32, count=len(x_vals),
            )

    def _reach(self):
        """The furthest the surface can travel, in mm. Bias makes it two peaks."""
        return self._wave_peak() + abs(self._bias_offset())

    def _axis_extent(self):
        """How thick the stock is along `direction`, from its own bounding box.

        Cached: the base field is fixed at construction, but bounding_box()
        recurses the whole tree and _front_face_band runs once per sample.
        """
        if self._axis_extent_cache is None:
            bb_min, bb_max = self.base_field.bounding_box()
            size = bb_max - bb_min
            d = self.direction
            self._axis_extent_cache = (abs(size.x * d.x) + abs(size.y * d.y)
                                       + abs(size.z * d.z))
        return self._axis_extent_cache

    def _front_face_band(self):
        """(lift, ramp) for the front-face weight, `clamp(base(p - dir*lift) / ramp)`.

        `lift` is how far a sample is carried back along the direction to ask "is
        there still material above me?", and it is squeezed from both sides.

        Too long and it clears the whole part: the back face reads as
        front-facing and travels with the wave -- 5.6 mm of growth on a 20 mm
        plate, the exact thing "Top never grows the stock" rules out. The stock's
        own extent is what caps it, halved, since a bounding box over-states the
        local thickness of anything that is not a slab.

        Too short and it clears the part the other way. A point sitting `s` below
        the back face lifts to `s - lift`, and once that is out the far side the
        mask switches back ON down there; the wave then drags a phantom skin off
        the bottom of the solid (measured: a second surface 20 mm adrift). Only
        points within `_reach()` of the solid can be pulled into it at all, so a
        lift of `_reach()` covers every one that matters -- and that floor wins,
        because a back face creeping is a smaller lie than a detached one.

        `ramp` is the width of the 0..1 fade. On a planar front face the weight
        works out to `(lift - depth)/ramp`, so it holds at a flat 1 down to
        `lift - ramp` and only then fades: that plateau has to cover the whole
        displacement or the wave fights its own mask, losing weight with every mm
        it sinks and settling short (at ramp = lift there is no plateau at all
        and a full trough lands 20% shallow). Half the lift is the plateau, which
        covers the displacement whenever the stock is thick enough to hold it.
        """
        reach = max(self._reach(), 1e-6)
        lift = min(max(self._axis_extent() * 0.5, reach), reach * 2.0)
        ramp = max(lift * 0.5, reach)
        lift = max(lift, ramp)
        return lift, ramp

    def _uses_front_weight(self):
        # A bias is a DC term. Left unanchored it does not sit the wave on the
        # surface at all -- it translates the entire solid along the direction,
        # back face and all -- so it needs the front-face weight even when the
        # user has left the back face free to move.
        return self._ignore_back_face or self._bias_offset() != 0.0

    def _front_weight_slope(self):
        """The mask's own contribution to the Lipschitz bound.

        The weight is a clamped distance ramp, so it tilts by up to 1/ramp, and
        it multiplies a displacement of up to `_reach()` mm. Ignoring that term
        under-reports the bound by ~1 for a full mask, which is the number the
        octree culls with and the marcher steps by.
        """
        if not self._uses_front_weight():
            return 0.0
        moved = self._reach() if self._ignore_back_face else abs(self._bias_offset())
        return moved / self._front_face_band()[1]

    def _apply_front_weight(self, noise, weight):
        """Fold the bias and the mask into the noise -- the one rule, three impls.

        With the back face ignored the whole displacement is masked, so the far
        side of the stock does not move at all. Without it only the BIAS is
        masked: the wave still runs through the solid symmetrically the way it
        always has, while the constant that anchors it stays on the front face
        instead of dragging the back one along.
        """
        bias = self._bias_offset()
        if self._ignore_back_face:
            return (noise + bias) * weight
        if bias != 0.0:
            return noise + bias * weight
        return noise

    def evaluate(self, point: FreeCAD.Vector) -> float:
        pc = point - self.center
        x = pc.dot(self._u_axis)
        y = pc.dot(self._w_axis)
        z = pc.dot(self.direction)
        noise_val = self._eval_formula(x, y, z)
        if self._uses_front_weight():
            lift, ramp = self._front_face_band()
            behind = FreeCAD.Vector(
                point.x - self.direction.x * lift,
                point.y - self.direction.y * lift,
                point.z - self.direction.z * lift,
            )
            d_behind = self.base_field.evaluate(behind)
            weight = max(0.0, min(1.0, d_behind / ramp))
            noise_val = self._apply_front_weight(noise_val, weight)
        displaced = FreeCAD.Vector(
            point.x - noise_val * self.direction.x,
            point.y - noise_val * self.direction.y,
            point.z - noise_val * self.direction.z,
        )
        return self.base_field.evaluate(displaced)

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        import time
        from freecad.fields.core import fld_logger
        t0 = time.perf_counter()
        ua = np.array([self._u_axis.x, self._u_axis.y, self._u_axis.z], dtype=np.float32)
        wa = np.array([self._w_axis.x, self._w_axis.y, self._w_axis.z], dtype=np.float32)
        da = np.array([self.direction.x, self.direction.y, self.direction.z], dtype=np.float32)
        center_arr = np.array([self.center.x, self.center.y, self.center.z], dtype=np.float32)
        points_c = points - center_arr
        x_vals = points_c @ ua
        y_vals = points_c @ wa
        z_vals = points_c @ da
        t_base = time.perf_counter()
        noise = self._eval_noise_grid(x_vals, y_vals, z_vals)
        t_noise = time.perf_counter()
        if self._uses_front_weight():
            lift, ramp = self._front_face_band()
            dir_arr = np.array([self.direction.x, self.direction.y, self.direction.z], dtype=np.float32)
            behind_points = points - dir_arr * lift
            d_behind = self.base_field.evaluate_grid(behind_points)
            weight = np.clip(d_behind / ramp, 0.0, 1.0)
            noise = self._apply_front_weight(noise, weight)
        else:
            noise = noise + np.float32(self._bias_offset())
        dir_arr = np.array([self.direction.x, self.direction.y, self.direction.z], dtype=np.float32)
        displaced = points - noise[:, np.newaxis] * dir_arr
        d_eval = self.base_field.evaluate_grid(displaced)
        res = d_eval.astype(np.float32)
        t_total = time.perf_counter() - t0
        fld_logger.debug(
            f"SdfNoise2DField.evaluate_grid: points={len(points)}, "
            f"noise_eval_time={t_noise - t_base:.4f}s, "
            f"total_time={t_total:.4f}s"
        )
        return res

    def bounding_box(self):
        bb_min, bb_max = self.base_field.bounding_box()
        # A biased wave reaches two peaks to one side instead of one peak to
        # each, so the box has to grow with the bias or "Bottom" would push
        # geometry straight through the wall of its own bounds.
        reach = self._reach()
        offset = FreeCAD.Vector(reach, reach, reach)
        return (bb_min - offset, bb_max + offset)

    def to_patch_cage(self):
        # Same rationale as SdfNoiseField.to_patch_cage: 2D noise perturbs the
        # SDF value along the projection direction, not the coordinate space,
        # so the base field's cage net is still valid to forward as-is.
        return self.base_field.to_patch_cage()

    def to_glsl(self, ctx, point_var="p"):
        amp_u = ctx.uniform("float", self.amplitude)
        freq_u = ctx.uniform("float", self.frequency)
        u_ax_u = ctx.uniform("vec3", (self._u_axis.x, self._u_axis.y, self._u_axis.z))
        w_ax_u = ctx.uniform("vec3", (self._w_axis.x, self._w_axis.y, self._w_axis.z))
        dir_u = ctx.uniform("vec3", (self.direction.x, self.direction.y, self.direction.z))
        center_u = ctx.uniform("vec3", (self.center.x, self.center.y, self.center.z))
        pc_expr = f"({point_var} - {center_u})"
        ctx.add_custom_helper("fld_pi_const", "const float pi = 3.14159265358979323846;")

        weight_expr = None
        bias_u = None
        if self._uses_front_weight():
            lift, ramp = self._front_face_band()
            lift_u = ctx.uniform("float", lift)
            ramp_u = ctx.uniform("float", ramp)
            offset_pt = f"({point_var} - {dir_u} * {lift_u})"
            behind_glsl = self.base_field.to_glsl(ctx, offset_pt)
            weight_expr = f"clamp({behind_glsl} / {ramp_u}, 0.0, 1.0)"
            # Emitted even at 0.0: once the weight exists the bias is a uniform
            # VALUE inside otherwise identical source, so moving between the
            # three modes pushes a number and never forces a recompile.
            bias_u = ctx.uniform("float", self._bias_offset())

        def displacement(noise_expr):
            """GLSL twin of _apply_front_weight -- keep the three impls in step."""
            if weight_expr is None:
                return f"({noise_expr})"
            if self._ignore_back_face:
                return f"(({noise_expr} + {bias_u}) * {weight_expr})"
            return f"({noise_expr} + {bias_u} * {weight_expr})"

        if self._preset_name in self.COMPLEX_PRESETS:
            preset = self.COMPLEX_PRESETS[self._preset_name]
            for dep_name, dep_code in preset.dep_helpers:
                ctx.add_custom_helper(dep_name, dep_code)
            ctx.add_custom_helper(preset.main_fn_name, preset.main_fn_code)
            h_scaled = displacement(
                f"{preset.main_fn_name}({pc_expr}, {amp_u}, {freq_u}, {u_ax_u}, {w_ax_u})")
            displaced_pt = f"({point_var} - {h_scaled} * {dir_u})"
            base_at_displaced = self.base_field.to_glsl(ctx, displaced_pt)
            return f"({base_at_displaced})"

        # Register custom parameters as uniforms
        param_args_decl = []
        param_args_call = []
        is_radial = bool(self.radial or self.custom_params.get("radial", False))
        for name, val in self.custom_params.items():
            if name == "radial":
                ptype = "float"
                u_val = ctx.uniform(ptype, 1.0 if is_radial else 0.0)
            elif isinstance(val, bool):
                ptype = "float"
                u_val = ctx.uniform(ptype, 1.0 if val else 0.0)
            elif isinstance(val, int):
                ptype = "int"
                u_val = ctx.uniform(ptype, val)
            else:
                ptype = "float"
                u_val = ctx.uniform(ptype, float(val))
            param_args_decl.append(f"{ptype} {name}")
            param_args_call.append(u_val)

        decl_str = ", ".join([f"vec3 p", "float amp", "float freq", "vec3 u_ax", "vec3 w_ax", "vec3 dir"] + param_args_decl)
        call_str = ", ".join([pc_expr, amp_u, freq_u, u_ax_u, w_ax_u, dir_u] + param_args_call)

        fn = f"noise2d_{abs(hash((self._clean_formula, is_radial))) & 0xFFFFFF:06x}"
        d_line = "r" if is_radial else "u"
        radial_line = "1.0" if is_radial else "0.0"

        body_lines = [
            "    float x = dot(p, u_ax);",
            "    float y = dot(p, w_ax);",
            "    float z = dot(p, dir);",
        ]
        if "u" not in self.custom_params:
            body_lines.append("    float u = x;")
        if "w" not in self.custom_params:
            body_lines.append("    float w = y;")
        if "r" not in self.custom_params:
            body_lines.append("    float r = sqrt(x * x + y * y);")
        if "d" not in self.custom_params:
            body_lines.append(f"    float d = {d_line};")
        if "radial" not in self.custom_params:
            body_lines.append(f"    float radial = {radial_line};")
        body_lines.append(f"    return {self._clean_formula};")

        body_str = "\n".join(body_lines)
        ctx.add_custom_helper(fn,
            f"float {fn}({decl_str}) {{\n"
            f"{body_str}\n"
            f"}}"
        )
        h_scaled = displacement(f"{fn}({call_str})")
        displaced_pt = f"({point_var} - {h_scaled} * {dir_u})"
        base_at_displaced = self.base_field.to_glsl(ctx, displaced_pt)
        return f"({base_at_displaced})"


# ── SdfNoiseField ─────────────────────────────────────────────────────────────

class SdfNoiseField(SdfField):
    """
    Applies procedural noise to a base SDF field.
    Formula variables (Custom): p (vec3), amp (float), freq (float).
    """
    # No named formula presets. Sine, Ripple, Diagonal Waves and Turbulence were
    # removed 2026-08-03: none of them is a 3D noise function. Sine and
    # Turbulence are separable products/sums of 1D sines, Diagonal Waves is a
    # plane wave (its isosurfaces are parallel planes, so it varies along one
    # direction only), and Ripple is a 1D sine of the radius. Perlin and Voronoi
    # are the only two entries here that are genuinely 3D, and they live in
    # COMPLEX_PRESETS. Anything else belongs in Custom, where the user can see
    # it is a formula rather than a noise field.
    #
    # Documents saved with any of those four names still open: the object keeps
    # its `Formula` string, and FldNoiseProxy._build_field passes it through for
    # every non-complex preset name, so the shape is unchanged. Do not add a
    # legacy-name table here -- the formula is already persisted per object.
    PRESETS = {}
    COMPLEX_PRESETS = _COMPLEX_PRESETS_3D
    PRESET_NAMES = list(PRESETS.keys()) + list(COMPLEX_PRESETS.keys()) + ["Custom"]
    # Seed text for Custom, not a preset -- nothing selects it by name.
    DEFAULT_FORMULA = "sin(p.x * freq) * sin(p.y * freq) * sin(p.z * freq) * amp"

    def __init__(self, base_field: SdfField, amplitude: float = 1.0, frequency: float = 1.0,
                 formula: str = None, normalization: float = 0.0, preset_name: str = None,
                 custom_params: dict = None):
        self.base_field = base_field
        self.amplitude = amplitude
        self.frequency = frequency
        self._normalization = normalization
        self.custom_params = custom_params or {}

        self._preset_name = preset_name or "Perlin"
        if self._preset_name in self.COMPLEX_PRESETS:
            self.formula = None
        elif self._preset_name == "Custom":
            self.formula = formula or self.DEFAULT_FORMULA
        else:
            # PRESETS is empty, so this branch is now reached only by a document
            # saved under a removed preset name. `formula` is that object's
            # persisted Formula string; keeping it is what preserves the shape.
            self.formula = self.PRESETS.get(self._preset_name, formula or self.DEFAULT_FORMULA)

    def _lip(self):
        if self._normalization > 0.0:
            return self._normalization
        if self._preset_name in self.COMPLEX_PRESETS:
            factor = self.COMPLEX_PRESETS[self._preset_name].lip_factor
        else:
            factor = 1.0
        return max(1.0 + abs(self.amplitude) * self.frequency * factor, 1.0)

    def _eval_formula(self, px, py, pz):
        if self._preset_name in self.COMPLEX_PRESETS:
            return self.COMPLEX_PRESETS[self._preset_name].python_fn(px, py, pz, self.amplitude, self.frequency)
        ns = dict(_EVAL_NS)
        ns["p"] = _GlslVec3(px, py, pz)
        ns["amp"] = self.amplitude; ns["freq"] = self.frequency
        for k, v in self.custom_params.items():
            ns[k] = v
        try:
            return float(eval(self.formula, {}, ns))
        except Exception as e:
            from freecad.fields.core import fld_logger
            fld_logger.debug_throttled(
                "noise3d_formula_eval_fail",
                f"SdfNoiseField: formula eval failed ({e}); contributing 0.0"
            )
            return 0.0

    def _eval_noise_grid(self, px, py, pz):
        """Vectorized counterpart of _eval_formula for a whole grid at once."""
        if self._preset_name in self.COMPLEX_PRESETS:
            preset = self.COMPLEX_PRESETS[self._preset_name]
            return preset.numpy_fn(px, py, pz, self.amplitude, self.frequency).astype(np.float32)
        try:
            return _eval_formula_grid_np(
                self.formula, self.custom_params,
                {"p": _GlslVec3(px, py, pz), "amp": self.amplitude, "freq": self.frequency},
                px.shape,
            )
        except Exception as e:
            from freecad.fields.core import fld_logger
            n = len(px)
            if n > _MAX_SCALAR_FALLBACK_POINTS:
                fld_logger.error(
                    f"SdfNoiseField: formula not vectorizable ({e}) and grid is "
                    f"{n} points — refusing the per-point fallback (would freeze). "
                    "Noise contributes 0; rewrite the formula using numpy-safe ops."
                )
                return np.zeros(px.shape, dtype=np.float32)
            fld_logger.debug_throttled(
                "noise3d_vectorize_fallback",
                f"SdfNoiseField: formula not vectorizable ({e}), falling back to per-point eval"
            )
            return np.fromiter(
                (self._eval_formula(px[i], py[i], pz[i]) for i in range(len(px))),
                dtype=np.float32, count=len(px),
            )

    def lipschitz(self) -> float:
        """Upper bound on |grad| of the value this field returns.

        The value itself is NOT divided by `_lip()`. Dividing it keeps the ray
        march's step safe and wrecks everything the march compares against a
        constant in millimetres: it declares a hit at `d < 0.5*vmax`, so on a
        field scaled down by L that fires `0.5*vmax*L` mm from the surface — 28 mm
        at amp 100 on a 3.3 mm voxel, in every direction at once, which is the
        "2D noise deforms in x and y as well as z" report. The bound belongs here,
        where the octree, the bake's block scan and the march's STEP divisor read
        it. Same resolution UX-009 took for `SdfCageDeformField`.
        """
        return self.base_field.lipschitz() * self._lip()

    # All three implementations return the SAME value scale — millimetres, the
    # base field's own units. They used to disagree (`to_glsl` divided by _lip()
    # and these two did not, up to 57 mm apart at amp=12/freq=10); they now agree
    # by not dividing at all, and `lipschitz()` above carries the bound instead.
    # The zero set is the same either way (a positive constant divisor never moves
    # it), so no mesh or slice ever came out a different shape — what the scale
    # decides is every consumer that compares a value against a length in mm.
    def evaluate(self, point: FreeCAD.Vector) -> float:
        return (self.base_field.evaluate(point)
                + self._eval_formula(point.x, point.y, point.z))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        import time
        from freecad.fields.core import fld_logger
        t0 = time.perf_counter()
        d = self.base_field.evaluate_grid(points)
        t_base = time.perf_counter()
        noise = self._eval_noise_grid(points[:, 0], points[:, 1], points[:, 2])
        t_noise = time.perf_counter()
        res = (d + noise).astype(np.float32)
        t_total = time.perf_counter() - t0
        fld_logger.debug(
            f"SdfNoiseField.evaluate_grid: points={len(points)}, "
            f"base_eval_time={t_base - t0:.4f}s, "
            f"noise_eval_time={t_noise - t_base:.4f}s, "
            f"total_time={t_total:.4f}s"
        )
        return res

    def bounding_box(self):
        bb_min, bb_max = self.base_field.bounding_box()
        offset = FreeCAD.Vector(self.amplitude, self.amplitude, self.amplitude)
        return (bb_min - offset, bb_max + offset)

    def to_patch_cage(self):
        # Noise perturbs the SDF value, not the coordinate space, so the base
        # field's cage net is still positioned correctly -- forward it as-is
        # rather than falling back to a bounding-box lattice that ignores the
        # wrapped primitive's shape entirely (see design_primitive_cage_descriptor).
        return self.base_field.to_patch_cage()

    def to_glsl(self, ctx, point_var="p"):
        base_glsl = self.base_field.to_glsl(ctx, point_var)
        amp_u = ctx.uniform("float", self.amplitude)
        freq_u = ctx.uniform("float", self.frequency)

        if self._preset_name in self.COMPLEX_PRESETS:
            preset = self.COMPLEX_PRESETS[self._preset_name]
            for dep_name, dep_code in preset.dep_helpers:
                ctx.add_custom_helper(dep_name, dep_code)
            ctx.add_custom_helper(preset.main_fn_name, preset.main_fn_code)
            return f"({base_glsl} + {preset.main_fn_name}({point_var}, {amp_u}, {freq_u}))"

        # Register custom parameters as uniforms
        param_args_decl = []
        param_args_call = []
        for name, val in self.custom_params.items():
            if isinstance(val, bool):
                ptype = "float"
                u_val = ctx.uniform(ptype, 1.0 if val else 0.0)
            elif isinstance(val, int):
                ptype = "int"
                u_val = ctx.uniform(ptype, val)
            else:
                ptype = "float"
                u_val = ctx.uniform(ptype, float(val))
            param_args_decl.append(f"{ptype} {name}")
            param_args_call.append(u_val)

        decl_str = ", ".join([f"vec3 p", "float amp", "float freq"] + param_args_decl)
        call_str = ", ".join([point_var, amp_u, freq_u] + param_args_call)

        fn = f"noise3d_{abs(hash(self.formula)) & 0xFFFFFF:06x}"
        ctx.add_custom_helper(fn,
            f"float {fn}({decl_str}) {{\n"
            f"    return {self.formula};\n"
            f"}}"
        )
        return f"({base_glsl} + {fn}({call_str}))"
