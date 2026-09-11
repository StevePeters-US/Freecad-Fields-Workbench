# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""One definition for the GL enum literals independently redefined (as module
constants, and in `gpu_field_eval.py`'s case retyped inside several
method-local scopes) across `gl_texture3d.py`, `gl_framebuffer.py`,
`gl_program.py` and `gpu_field_eval.py` (CR-039).

These are raw ctypes-level OpenGL API constants, not GLSL shader source --
outside this todo file's GLSL-generation exclusion. Only names that were
genuinely duplicated somewhere are centralized here; a constant used by just
one file (e.g. `gl_framebuffer.py`'s `GL_FRAMEBUFFER`, or
`gpu_field_eval.py`'s `GL_SHADER_STORAGE_BUFFER`) stays defined where it's used.
"""

GL_TEXTURE_2D = 0x0DE1
GL_TEXTURE_3D = 0x806F
GL_TEXTURE0   = 0x84C0

GL_RED   = 0x1903
GL_RGBA  = 0x1908
GL_FLOAT = 0x1406
GL_HALF_FLOAT    = 0x140B
GL_UNSIGNED_BYTE = 0x1401

GL_R16F    = 0x822D
GL_R32F    = 0x822E
GL_RGBA16F = 0x881A
GL_RGBA32F = 0x8814

GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_TEXTURE_WRAP_S     = 0x2802
GL_TEXTURE_WRAP_T     = 0x2803
GL_TEXTURE_WRAP_R     = 0x8072
GL_NEAREST       = 0x2600
GL_LINEAR        = 0x2601
GL_CLAMP_TO_EDGE = 0x812F
