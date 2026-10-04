# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Voronoi cellular noise on the (u, w) plane -- constant along `direction`."""
import math
import numpy as np
from freecad.fields.core.sdf.sdf.noise_node import SdfNoiseNode
from freecad.fields.core.sdf.sdf.noise_utils import (
    _U32, _HASH_SCALE, _pcg2d_py, _pcg2d_np, _u2f, _PCG2D_GLSL,
)


def _vhash22_py(x, y):
    hx, hy = _pcg2d_py(int(x) & _U32, int(y) & _U32)
    return ((hx >> 8) * _HASH_SCALE, (hy >> 8) * _HASH_SCALE)


def _voronoi2d_py(x, y, z, amp, freq):
    # z ignored: the pattern is constant along the direction axis.
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


def _vhash22_np(x, y):
    hx, hy = _pcg2d_np(x, y)
    return _u2f(hx), _u2f(hy)


def _voronoi2d_np(x, y, z, amp, freq):
    # z ignored: the pattern is constant along the direction axis.
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


_VHASH22_GLSL = """vec2 fld_vhash22(vec2 p) {
    uvec2 h = fld_pcg2d(uvec2(ivec2(p))) >> 8u;
    return vec2(h) * (1.0 / 16777216.0);
}"""

_VORONOI2D_GLSL = """float fld_voronoi2d(vec3 q, float amp, float freq) {
    vec2 qq = q.xy * freq;
    vec2 pi = floor(qq);
    vec2 pf = fract(qq);
    float md = 1e9;
    for(int x=-1; x<=1; x++) for(int y=-1; y<=1; y++) {
        vec2 b = vec2(float(x), float(y));
        vec2 r = b - pf + fld_vhash22(pi + b);
        md = min(md, dot(r, r));
    }
    return amp * sqrt(md) / 0.707107 - amp * 0.5;
}"""


class Voronoi2DNode(SdfNoiseNode):
    name = "Voronoi 2D"
    planar = True
    display = ("# Voronoi cellular noise projected onto plane (centered)\n"
               "# Output: [-amp/2, +amp/2]")
    lip_factor = 1.5
    peak_factor = 0.5
    dep_helpers = (("fld_pcg2d", _PCG2D_GLSL), ("fld_vhash22", _VHASH22_GLSL))
    main_fn_name = "fld_voronoi2d"
    main_fn_code = _VORONOI2D_GLSL
    python_fn = staticmethod(_voronoi2d_py)
    numpy_fn = staticmethod(_voronoi2d_np)
