# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.sdf.sdf.noise_utils

Lattice hashing and interpolation shared by every hash-based noise node. Every symbol
here exists in three synchronized forms -- Python scalar, NumPy, GLSL -- and the whole
point of the integer mixers is that all three produce the SAME BITS. A change to one
without the other two is a bug even when both halves look right on their own; it shows
up as a slice contour that sits millimetres off the surface on screen
(`bug_noise_formula_namespace_drift`, `test_noise_cpu_gpu_hash_parity.py`).
"""
import numpy as np


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

