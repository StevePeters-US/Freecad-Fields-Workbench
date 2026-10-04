# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/ssao_resources.py

GL resource construction for the post-process pipeline: the three screen-space
programs, the SSAO hemisphere kernel and its rotation-noise texture, and the
framebuffer set the passes render into.

Every function here must be called inside an active GL context (i.e. from a
SoCallback). None of them touch the renderer -- they build and return, and the
caller decides what to assign and when. That is what lets the resize path in
`_render_gl_callback_inner` build a replacement FBO set and only swap it in
after Pass 1-3 have successfully rendered into it.
"""
import ctypes
import math
import random
from types import SimpleNamespace

from freecad.fields.core.render.post_process_shaders import (
    _VERT_PASSTHROUGH, _FRAG_SSAO, _FRAG_BLUR, _FRAG_COMP)

GL_TEXTURE_2D         = 0x0DE1
GL_RGB32F             = 0x8815
GL_RGB                = 0x1907
GL_R32F               = 0x822E
GL_RED                = 0x1903
GL_FLOAT              = 0x1406
GL_NEAREST            = 0x2600
GL_REPEAT             = 0x2901
GL_CLAMP_TO_EDGE      = 0x812F
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_TEXTURE_WRAP_S     = 0x2802
GL_TEXTURE_WRAP_T     = 0x2803

GL_MAX_COMPUTE_TEXTURE_IMAGE_UNITS = 0x9138

SSAO_KERNEL_SIZE = 64


def load_buffer_entrypoints():
    """The five glBuffer* entry points the SSBO paths need, as one namespace.

    Any of them may be None on a driver that does not export it, so every call
    site guards on the one it is about to use -- `glGenBuffers` standing in for
    the set, since without it there is no buffer to do anything else to.
    """
    from freecad.fields.core.gl.gl_texture3d import _loader
    return SimpleNamespace(
        glGenBuffers     = _loader.get("glGenBuffers",     [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None),
        glBindBuffer     = _loader.get("glBindBuffer",     [ctypes.c_uint, ctypes.c_uint], None),
        glBufferData     = _loader.get("glBufferData",     [ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint], None),
        glBindBufferBase = _loader.get("glBindBufferBase", [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint], None),
        glDeleteBuffers  = _loader.get("glDeleteBuffers",  [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None),
    )


def no_buffer_entrypoints():
    """The same namespace with every entry point None.

    What the renderer holds before its first frame: `_detach` and
    `unregister_field` both free SSBOs and can run before any GL context has
    existed, and they guard on these being non-None.
    """
    return SimpleNamespace(glGenBuffers=None, glBindBuffer=None, glBufferData=None,
                           glBindBufferBase=None, glDeleteBuffers=None)


def max_compute_texture_units(default=32):
    """GL_MAX_COMPUTE_TEXTURE_IMAGE_UNITS, or `default` if it cannot be read."""
    from freecad.fields.core.gl.gl_texture3d import _loader
    out = (ctypes.c_int * 1)(default)
    glGetIntegerv = _loader.get("glGetIntegerv", [ctypes.c_uint, ctypes.POINTER(ctypes.c_int)], None)
    if glGetIntegerv:
        glGetIntegerv(GL_MAX_COMPUTE_TEXTURE_IMAGE_UNITS, out)
    return out[0]


def build_post_process_programs():
    """Compile the SSAO, blur and composite programs. Returns them in that order."""
    from freecad.fields.core.gl.gl_program import GLProgram

    progs = []
    for frag in (_FRAG_SSAO, _FRAG_BLUR, _FRAG_COMP):
        prog = GLProgram()
        prog.compile(_VERT_PASSTHROUGH, frag)
        progs.append(prog)
    return tuple(progs)


def make_ssao_kernel():
    """64 hemisphere samples as one flat list of 192 floats, packed toward the
    origin so that near-surface occluders dominate."""
    kernel = []
    for i in range(SSAO_KERNEL_SIZE):
        s = [random.uniform(-1.0, 1.0),
             random.uniform(-1.0, 1.0),
             random.uniform(0.0, 1.0)]
        length = math.sqrt(sum(x * x for x in s))
        if length < 1e-8:
            length = 1.0
        s = [x / length for x in s]
        scale = i / float(SSAO_KERNEL_SIZE)
        scale = 0.1 + scale * scale * 0.9   # lerp(0.1, 1.0, scale^2)
        kernel.extend([x * scale for x in s])
    return kernel


def make_noise_texture():
    """4x4 GL_REPEAT texture of random XY rotation vectors for the SSAO tangent
    frame. Returns the texture id."""
    from freecad.fields.core.gl.gl_texture3d import _loader

    noise_data = []
    for _ in range(16):
        angle = random.uniform(0.0, 2.0 * math.pi)
        noise_data.extend([math.cos(angle), math.sin(angle), 0.0])
    noise_arr = (ctypes.c_float * len(noise_data))(*noise_data)

    glGenTextures   = _loader.get("glGenTextures",
        [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
    glBindTexture   = _loader.get("glBindTexture",
        [ctypes.c_uint, ctypes.c_uint], None)
    glTexImage2D    = _loader.get("glTexImage2D",
        [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int,
         ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
         ctypes.c_void_p], None)
    glTexParameteri = _loader.get("glTexParameteri",
        [ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)

    tex = ctypes.c_uint(0)
    glGenTextures(1, ctypes.byref(tex))
    tex_id = tex.value
    glBindTexture(GL_TEXTURE_2D, tex_id)
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB32F, 4, 4, 0,
                 GL_RGB, GL_FLOAT, noise_arr)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT)
    glBindTexture(GL_TEXTURE_2D, 0)
    return tex_id


def build_fbos(w: int, h: int):
    """Build a brand-new set of FBOs + depth texture at size (w, h).

    Returns (gbuf_fbo, ssao_fbo, blur_fbo, depth_tex) as new GL objects --
    it does NOT touch whatever the renderer currently has assigned. The caller
    only swaps these in after Pass 1-3 successfully render into them, so a
    failed/partial resize never destroys the last good frame's content (which
    would otherwise leave Pass 4 with nothing valid to composite, i.e. the SDF
    overlay vanishing).
    """
    from freecad.fields.core.gl.gl_framebuffer import GLFramebuffer
    from freecad.fields.core.gl.gl_texture3d import _loader

    gbuf_fbo = GLFramebuffer()
    ssao_fbo = GLFramebuffer()
    blur_fbo = GLFramebuffer()
    # G-buffer: Phong color + view-space pos + view-space normal + depth
    # Only the Phong color needs linear scaling; VS pos/normal/depth must remain nearest.
    gbuf_fbo.create(w, h, ['rgba16f', 'rgba16f', 'rgba32f', 'depth24'], ['linear', 'nearest', 'nearest', 'nearest'])
    # SSAO: raw occlusion float
    ssao_fbo.create(w, h, ['r16f'], ['nearest'])
    # Blur: blurred occlusion float (needs linear scaling for smooth scaling up)
    blur_fbo.create(w, h, ['r16f'], ['linear'])

    # r32f depth image written by the compute shader (window depth in [0,1])
    glGenTextures    = _loader.get("glGenTextures",
        [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
    glBindTexture    = _loader.get("glBindTexture",
        [ctypes.c_uint, ctypes.c_uint], None)
    glTexImage2D     = _loader.get("glTexImage2D",
        [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
         ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p], None)
    glTexParameteri  = _loader.get("glTexParameteri",
        [ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)

    tex = ctypes.c_uint(0)
    glGenTextures(1, ctypes.byref(tex))
    depth_tex = tex.value
    glBindTexture(GL_TEXTURE_2D, depth_tex)
    glTexImage2D(GL_TEXTURE_2D, 0, GL_R32F, w, h, 0, GL_RED, GL_FLOAT, None)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
    glBindTexture(GL_TEXTURE_2D, 0)

    return gbuf_fbo, ssao_fbo, blur_fbo, depth_tex


def destroy_fbo_set(gbuf_fbo, ssao_fbo, blur_fbo, depth_tex):
    """Free GL resources for a (gbuf, ssao, blur, depth_tex) tuple from build_fbos."""
    from freecad.fields.core.gl.gl_texture3d import _loader
    for fbo in (gbuf_fbo, ssao_fbo, blur_fbo):
        if fbo is not None:
            fbo.destroy()
    if depth_tex:
        glDeleteTextures = _loader.get("glDeleteTextures",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        arr = (ctypes.c_uint * 1)(depth_tex)
        glDeleteTextures(1, arr)
