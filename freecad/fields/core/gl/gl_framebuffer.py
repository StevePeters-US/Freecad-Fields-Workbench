# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core/gl/gl_framebuffer.py

OpenGL framebuffer object (FBO) manager via ctypes.
Reuses _loader from gl_texture3d.py.
"""
import ctypes
from freecad.fields.core.gl.gl_texture3d import _loader
from freecad.fields.core.gl.gl_constants import (
    GL_TEXTURE_2D, GL_RGBA32F, GL_RGBA16F, GL_R16F, GL_RGBA, GL_RED, GL_FLOAT,
    GL_NEAREST, GL_LINEAR, GL_CLAMP_TO_EDGE,
    GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER, GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T,
)

# Not shared with another gl_*.py module; see gl_constants.py for the rest (CR-039)
GL_FRAMEBUFFER          = 0x8D40
GL_COLOR_ATTACHMENT0    = 0x8CE0
GL_DEPTH_ATTACHMENT     = 0x8D00
GL_RGB16F               = 0x881B
GL_DEPTH_COMPONENT24    = 0x81A5
GL_RGB                  = 0x1907
GL_DEPTH_COMPONENT      = 0x1902
GL_UNSIGNED_INT         = 0x1405

# format-string → (internalFormat, format, type, is_depth)
_FMT = {
    'rgba32f': (GL_RGBA32F,          GL_RGBA,           GL_FLOAT,       False),
    'rgba16f': (GL_RGBA16F,          GL_RGBA,           GL_FLOAT,       False),
    'rgb16f':  (GL_RGB16F,           GL_RGB,            GL_FLOAT,       False),
    'r16f':    (GL_R16F,             GL_RED,            GL_FLOAT,       False),
    'depth24': (GL_DEPTH_COMPONENT24, GL_DEPTH_COMPONENT, GL_UNSIGNED_INT, True),
}


class GLFramebuffer:
    """Owns one FBO and its texture attachments.

    attachment_specs: ordered list of format strings from _FMT, e.g.
        ['rgba16f', 'rgba16f', 'rgba16f', 'depth24']
    Color attachments are assigned GL_COLOR_ATTACHMENT0+N in list order.
    One 'depth24' entry maps to GL_DEPTH_ATTACHMENT.
    """

    def __init__(self):
        self._fbo_id   = 0
        self._textures = []   # list of (tex_id: int, is_depth: bool)
        self._specs    = []
        self._filter_specs = None

    def create(self, width: int, height: int, attachment_specs, filter_specs=None):
        """Allocate FBO + textures. Destroys any existing allocation first."""
        self.destroy()
        self._specs = list(attachment_specs)
        self._filter_specs = list(filter_specs) if filter_specs else None

        glGenFramebuffers      = _loader.get("glGenFramebuffers",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        glBindFramebuffer      = _loader.get("glBindFramebuffer",
            [ctypes.c_uint, ctypes.c_uint], None)
        glGenTextures          = _loader.get("glGenTextures",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        glBindTexture          = _loader.get("glBindTexture",
            [ctypes.c_uint, ctypes.c_uint], None)
        glTexImage2D           = _loader.get("glTexImage2D",
            [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
             ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p], None)
        glTexParameteri        = _loader.get("glTexParameteri",
            [ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)
        glFramebufferTexture2D = _loader.get("glFramebufferTexture2D",
            [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)
        glDrawBuffers          = _loader.get("glDrawBuffers",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)

        fbo = ctypes.c_uint(0)
        glGenFramebuffers(1, ctypes.byref(fbo))
        self._fbo_id = fbo.value
        glBindFramebuffer(GL_FRAMEBUFFER, self._fbo_id)

        color_idx = 0
        color_attachments = []
        for i, spec in enumerate(self._specs):
            ifmt, fmt, dtype, is_depth = _FMT[spec]
            tex = ctypes.c_uint(0)
            glGenTextures(1, ctypes.byref(tex))
            tid = tex.value
            glBindTexture(GL_TEXTURE_2D, tid)
            glTexImage2D(GL_TEXTURE_2D, 0, ifmt, width, height, 0, fmt, dtype, None)
            
            # Determine filter mode
            if filter_specs and i < len(filter_specs):
                f_mode = GL_NEAREST if filter_specs[i] == 'nearest' else GL_LINEAR
            else:
                f_mode = GL_NEAREST if is_depth else GL_LINEAR
                
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, f_mode)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, f_mode)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

            if is_depth:
                attachment = GL_DEPTH_ATTACHMENT
            else:
                attachment = GL_COLOR_ATTACHMENT0 + color_idx
                color_attachments.append(attachment)
                color_idx += 1

            glFramebufferTexture2D(GL_FRAMEBUFFER, attachment, GL_TEXTURE_2D, tid, 0)
            self._textures.append((tid, is_depth))

        if color_attachments:
            arr = (ctypes.c_uint * len(color_attachments))(*color_attachments)
            glDrawBuffers(len(color_attachments), arr)

        glBindTexture(GL_TEXTURE_2D, 0)
        glBindFramebuffer(GL_FRAMEBUFFER, 0)

    @property
    def id(self) -> int:
        """The raw GL framebuffer name, or 0 if this FBO is not allocated.

        Public because a *readback* cannot go through bind(): binding
        GL_FRAMEBUFFER takes the draw binding too, and restoring only
        GL_READ_FRAMEBUFFER afterwards would leave the draw target pointing at
        the wrong FBO. `pick_surface_at` needs the name so it can bind
        GL_READ_FRAMEBUFFER alone.

        This property did not exist until 2026-08-28, and both of
        `pick_surface_at`'s uses of it were written against it anyway. The guard
        spelled it `getattr(fbo, "id", 0)`, which does not raise -- it just
        returns 0 -- so TR-009's GPU pick reported "no g-buffer" on every click
        from the day it was written and silently fell back to the CPU
        projector. Same shape as audit finding 4, one layer down.
        """
        return self._fbo_id

    def bind(self):
        f = _loader.get("glBindFramebuffer", [ctypes.c_uint, ctypes.c_uint], None)
        f(GL_FRAMEBUFFER, self._fbo_id)

    def unbind(self):
        f = _loader.get("glBindFramebuffer", [ctypes.c_uint, ctypes.c_uint], None)
        f(GL_FRAMEBUFFER, 0)

    def color_texture(self, color_slot: int = 0) -> int:
        """Return texture ID for the N-th color attachment (0-indexed)."""
        idx = 0
        for tid, is_depth in self._textures:
            if not is_depth:
                if idx == color_slot:
                    return tid
                idx += 1
        return 0

    def depth_texture(self) -> int:
        """Return texture ID for the depth attachment, or 0 if none."""
        for tid, is_depth in self._textures:
            if is_depth:
                return tid
        return 0

    def resize(self, width: int, height: int):
        """Destroy and recreate at a new resolution."""
        specs = list(self._specs)
        filters = list(self._filter_specs) if getattr(self, "_filter_specs", None) else None
        self.destroy()
        self.create(width, height, specs, filters)

    def destroy(self):
        if not self._fbo_id:
            return
        glDeleteFramebuffers = _loader.get("glDeleteFramebuffers",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        glDeleteTextures = _loader.get("glDeleteTextures",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        fbo_arr = (ctypes.c_uint * 1)(self._fbo_id)
        glDeleteFramebuffers(1, fbo_arr)
        if self._textures:
            ids = [t for t, _ in self._textures]
            arr = (ctypes.c_uint * len(ids))(*ids)
            glDeleteTextures(len(ids), arr)
        self._fbo_id = 0
        self._textures = []
        self._specs = []
        self._filter_specs = None
