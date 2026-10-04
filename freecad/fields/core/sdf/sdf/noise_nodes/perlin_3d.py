# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Perlin gradient noise in 3D frame coordinates."""
import math
import numpy as np
from freecad.fields.core.sdf.sdf.noise_node import SdfNoiseNode
from freecad.fields.core.sdf.sdf.noise_utils import (
    _U32, _HASH_SCALE, _fade, _lerp, _fade_np, _lerp_np,
    _pcg3d_py, _pcg3d_np, _u2f, _PCG3D_GLSL,
)


def _ghash3_py(px, py, pz):
    hx, hy, hz = _pcg3d_py(int(px) & _U32, int(py) & _U32, int(pz) & _U32)
    return (
        -1.0 + 2.0 * ((hx >> 8) * _HASH_SCALE),
        -1.0 + 2.0 * ((hy >> 8) * _HASH_SCALE),
        -1.0 + 2.0 * ((hz >> 8) * _HASH_SCALE),
    )


def _perlin3d_py(x, y, z, amp, freq):
    x *= freq; y *= freq; z *= freq
    ix = math.floor(x); iy = math.floor(y); iz = math.floor(z)
    fx = x - ix; fy = y - iy; fz = z - iz
    ux = _fade(fx); uy = _fade(fy); uz = _fade(fz)
    def n(dx, dy, dz):
        gx, gy, gz = _ghash3_py(ix+dx, iy+dy, iz+dz)
        return gx*(fx-dx) + gy*(fy-dy) + gz*(fz-dz)
    v = _lerp(
        _lerp(_lerp(n(0,0,0), n(1,0,0), ux), _lerp(n(0,1,0), n(1,1,0), ux), uy),
        _lerp(_lerp(n(0,0,1), n(1,0,1), ux), _lerp(n(0,1,1), n(1,1,1), ux), uy), uz)
    return amp * v


def _ghash3_np(px, py, pz):
    hx, hy, hz = _pcg3d_np(px, py, pz)
    return (-1.0 + 2.0 * _u2f(hx),
            -1.0 + 2.0 * _u2f(hy),
            -1.0 + 2.0 * _u2f(hz))


def _perlin3d_np(x, y, z, amp, freq):
    x = x * freq; y = y * freq; z = z * freq
    ix = np.floor(x); iy = np.floor(y); iz = np.floor(z)
    fx = x - ix; fy = y - iy; fz = z - iz
    ux = _fade_np(fx); uy = _fade_np(fy); uz = _fade_np(fz)
    def n(dx, dy, dz):
        gx, gy, gz = _ghash3_np(ix+dx, iy+dy, iz+dz)
        return gx*(fx-dx) + gy*(fy-dy) + gz*(fz-dz)
    v = _lerp_np(
        _lerp_np(_lerp_np(n(0,0,0), n(1,0,0), ux), _lerp_np(n(0,1,0), n(1,1,0), ux), uy),
        _lerp_np(_lerp_np(n(0,0,1), n(1,0,1), ux), _lerp_np(n(0,1,1), n(1,1,1), ux), uy), uz)
    return amp * v


_GHASH3_GLSL = """vec3 fld_ghash3(vec3 p) {
    uvec3 h = fld_pcg3d(uvec3(ivec3(p))) >> 8u;
    return -1.0 + 2.0 * (vec3(h) * (1.0 / 16777216.0));
}"""

_PERLIN3D_GLSL = """float fld_perlin3d(vec3 q, float amp, float freq) {
    vec3 p = q * freq;
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


class Perlin3DNode(SdfNoiseNode):
    name = "Perlin 3D"
    planar = False
    display = ("# Classic Perlin gradient noise evaluated in the projection frame\n"
               "# Output: approx [-amp, +amp]")
    lip_factor = 2.0
    peak_factor = 1.0
    dep_helpers = (("fld_pcg3d", _PCG3D_GLSL), ("fld_ghash3", _GHASH3_GLSL))
    main_fn_name = "fld_perlin3d"
    main_fn_code = _PERLIN3D_GLSL
    python_fn = staticmethod(_perlin3d_py)
    numpy_fn = staticmethod(_perlin3d_np)

