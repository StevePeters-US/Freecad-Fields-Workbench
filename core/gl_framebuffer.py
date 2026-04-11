"""core/gl_framebuffer.py

OpenGL framebuffer object (FBO) manager via ctypes.
Reuses _loader from gl_texture3d.py.
"""
import ctypes
from core.gl_texture3d import _loader

GL_FRAMEBUFFER          = 0x8D40
GL_COLOR_ATTACHMENT0    = 0x8CE0
GL_DEPTH_ATTACHMENT     = 0x8D00
GL_TEXTURE_2D           = 0x0DE1
GL_RGBA16F              = 0x881A
GL_RGB16F               = 0x881B
GL_R16F                 = 0x822D
GL_DEPTH_COMPONENT24    = 0x81A5
GL_RGBA                 = 0x1908
GL_RGB                  = 0x1907
GL_RED                  = 0x1903
GL_DEPTH_COMPONENT      = 0x1902
GL_FLOAT                = 0x1406
GL_UNSIGNED_INT         = 0x1405
GL_NEAREST              = 0x2600
GL_CLAMP_TO_EDGE        = 0x812F
GL_TEXTURE_MIN_FILTER   = 0x2801
GL_TEXTURE_MAG_FILTER   = 0x2800
GL_TEXTURE_WRAP_S       = 0x2802
GL_TEXTURE_WRAP_T       = 0x2803

# format-string → (internalFormat, format, type, is_depth)
_FMT = {
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

    def create(self, width: int, height: int, attachment_specs):
        """Allocate FBO + textures. Destroys any existing allocation first."""
        self.destroy()
        self._specs = list(attachment_specs)

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
        for spec in self._specs:
            ifmt, fmt, dtype, is_depth = _FMT[spec]
            tex = ctypes.c_uint(0)
            glGenTextures(1, ctypes.byref(tex))
            tid = tex.value
            glBindTexture(GL_TEXTURE_2D, tid)
            glTexImage2D(GL_TEXTURE_2D, 0, ifmt, width, height, 0, fmt, dtype, None)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
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
        self.destroy()
        self.create(width, height, specs)

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
