# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Perlin gradient noise on the (u, w) plane -- constant along `direction`."""
import math
import numpy as np
from freecad.fields.core.sdf.sdf.noise_node import SdfNoiseNode
from freecad.fields.core.sdf.sdf.noise_utils import (
    _U32, _HASH_SCALE, _fade, _lerp, _fade_np, _lerp_np,
    _pcg2d_py, _pcg2d_np, _u2f, _PCG2D_GLSL,
)


def _ghash2_py(u, w):
    hx, hy = _pcg2d_py(int(u) & _U32, int(w) & _U32)
    return (-1.0 + 2.0 * ((hx >> 8) * _HASH_SCALE),
            -1.0 + 2.0 * ((hy >> 8) * _HASH_SCALE))


def _perlin2d_py(x, y, z, amp, freq):
    # z ignored: the pattern is constant along the direction axis.
    x *= freq; y *= freq
    ix = math.floor(x); iy = math.floor(y)
    fx = x - ix; fy = y - iy
    ux = _fade(fx); uy = _fade(fy)
    def n(dx, dy):
        gx, gy = _ghash2_py(ix+dx, iy+dy)
        return gx*(fx-dx) + gy*(fy-dy)
    return amp * _lerp(_lerp(n(0,0), n(1,0), ux), _lerp(n(0,1), n(1,1), ux), uy)


def _ghash2_np(x, y):
    hx, hy = _pcg2d_np(x, y)
    return -1.0 + 2.0 * _u2f(hx), -1.0 + 2.0 * _u2f(hy)


def _perlin2d_np(x, y, z, amp, freq):
    # z ignored: the pattern is constant along the direction axis.
    x = x * freq; y = y * freq
    ix = np.floor(x); iy = np.floor(y)
    fx = x - ix; fy = y - iy
    ux = _fade_np(fx); uy = _fade_np(fy)
    def n(dx, dy):
        gx, gy = _ghash2_np(ix+dx, iy+dy)
        return gx*(fx-dx) + gy*(fy-dy)
    return amp * _lerp_np(_lerp_np(n(0,0), n(1,0), ux), _lerp_np(n(0,1), n(1,1), ux), uy)


_GHASH2_GLSL = """vec2 fld_ghash2(vec2 p) {
    uvec2 h = fld_pcg2d(uvec2(ivec2(p))) >> 8u;
    return -1.0 + 2.0 * (vec2(h) * (1.0 / 16777216.0));
}"""

_PERLIN2D_GLSL = """float fld_perlin2d(vec3 q, float amp, float freq) {
    vec2 qq = q.xy * freq;
    vec2 i = floor(qq);
    vec2 f = fract(qq);
    vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
    float n00 = dot(fld_ghash2(i),              f);
    float n10 = dot(fld_ghash2(i+vec2(1.,0.)), f-vec2(1.,0.));
    float n01 = dot(fld_ghash2(i+vec2(0.,1.)), f-vec2(0.,1.));
    float n11 = dot(fld_ghash2(i+vec2(1.,1.)), f-vec2(1.,1.));
    return amp * mix(mix(n00, n10, u.x), mix(n01, n11, u.x), u.y);
}"""


class Perlin2DNode(SdfNoiseNode):
    name = "Perlin 2D"
    planar = True
    display = ("# Classic Perlin gradient noise on the (u, w) plane\n"
               "# Constant along the direction arrow\n"
               "# Output: approx [-amp, +amp]")
    lip_factor = 2.0
    peak_factor = 1.0
    dep_helpers = (("fld_pcg2d", _PCG2D_GLSL), ("fld_ghash2", _GHASH2_GLSL))
    main_fn_name = "fld_perlin2d"
    main_fn_code = _PERLIN2D_GLSL
    python_fn = staticmethod(_perlin2d_py)
    numpy_fn = staticmethod(_perlin2d_np)
