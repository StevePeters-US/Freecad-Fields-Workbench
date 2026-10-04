# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Voronoi cellular noise in 3D frame coordinates."""
import math
import numpy as np
from freecad.fields.core.sdf.sdf.noise_node import SdfNoiseNode
from freecad.fields.core.sdf.sdf.noise_utils import (
    _U32, _HASH_SCALE, _pcg3d_py, _pcg3d_np, _u2f, _PCG3D_GLSL,
)


def _vhash33_py(px, py, pz):
    hx, hy, hz = _pcg3d_py(int(px) & _U32, int(py) & _U32, int(pz) & _U32)
    return ((hx >> 8) * _HASH_SCALE,
            (hy >> 8) * _HASH_SCALE,
            (hz >> 8) * _HASH_SCALE)


def _voronoi3d_py(x, y, z, amp, freq):
    x *= freq; y *= freq; z *= freq
    ix = math.floor(x); iy = math.floor(y); iz = math.floor(z)
    fx = x - ix; fy = y - iy; fz = z - iz
    md = 1e9
    for xi in range(-1, 2):
        for yi in range(-1, 2):
            for zi in range(-1, 2):
                hx, hy, hz = _vhash33_py(ix+xi, iy+yi, iz+zi)
                rx = xi - fx + hx; ry = yi - fy + hy; rz = zi - fz + hz
                d = rx*rx + ry*ry + rz*rz
                if d < md: md = d
    return amp * math.sqrt(md) / 0.866025 - amp * 0.5


def _vhash33_np(px, py, pz):
    hx, hy, hz = _pcg3d_np(px, py, pz)
    return _u2f(hx), _u2f(hy), _u2f(hz)


def _voronoi3d_np(x, y, z, amp, freq):
    x = x * freq; y = y * freq; z = z * freq
    ix = np.floor(x); iy = np.floor(y); iz = np.floor(z)
    fx = x - ix; fy = y - iy; fz = z - iz
    md = np.full(x.shape, 1e9, dtype=np.float64)
    for xi in range(-1, 2):
        for yi in range(-1, 2):
            for zi in range(-1, 2):
                hx, hy, hz = _vhash33_np(ix+xi, iy+yi, iz+zi)
                rx = xi - fx + hx; ry = yi - fy + hy; rz = zi - fz + hz
                d = rx*rx + ry*ry + rz*rz
                md = np.minimum(md, d)
    return amp * np.sqrt(md) / 0.866025 - amp * 0.5


_VHASH33_GLSL = """vec3 fld_vhash33(vec3 p) {
    uvec3 h = fld_pcg3d(uvec3(ivec3(p))) >> 8u;
    return vec3(h) * (1.0 / 16777216.0);
}"""

_VORONOI3D_GLSL = """float fld_voronoi3d(vec3 q, float amp, float freq) {
    vec3 p = q * freq;
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


class Voronoi3DNode(SdfNoiseNode):
    name = "Voronoi 3D"
    planar = False
    display = ("# Voronoi cellular noise evaluated in the projection frame (centered)\n"
               "# Output: [-amp/2, +amp/2]")
    lip_factor = 1.5
    peak_factor = 0.5
    dep_helpers = (("fld_pcg3d", _PCG3D_GLSL), ("fld_vhash33", _VHASH33_GLSL))
    main_fn_name = "fld_voronoi3d"
    main_fn_code = _VORONOI3D_GLSL
    python_fn = staticmethod(_voronoi3d_py)
    numpy_fn = staticmethod(_voronoi3d_np)

