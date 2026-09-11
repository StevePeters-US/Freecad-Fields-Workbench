# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/gl/gl_compute.py

Compute-shader support detection. Actual compute shader compilation and
dispatch is handled by GLProgram (core/gl/gl_program.py) via
compile_compute()/dispatch_compute() — there is no separate compute-only
program class.
"""
import ctypes
from freecad.fields.core.gl.gl_texture3d import _loader
from freecad.fields.core import fld_logger


def check_compute_support():
    """Check if OpenGL compute shaders are available (requires GL 4.3+).

    Must be called within a valid GL context.
    """
    try:
        glGetString = _loader.get("glGetString",
            [ctypes.c_uint], ctypes.c_char_p)
        version_str = glGetString(0x1F02)  # GL_VERSION
        if version_str:
            parts = version_str.decode().split()
            version = parts[0].split('.')
            major, minor = int(version[0]), int(version[1])
            return (major, minor) >= (4, 3)
    except Exception as e:
        fld_logger.debug(f"check_compute_support check failed: {e}")
    return False
