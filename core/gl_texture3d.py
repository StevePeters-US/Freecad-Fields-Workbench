"""
core/gl_texture3d.py

Direct OpenGL 3D texture upload via ctypes, bypassing Pivy's broken
SoSFImage3.setValue() which only copies the first row of data.

Usage:
    helper = GLTexture3D()
    # Add helper.callback_node to the scene graph BEFORE the shader
    # Then call helper.upload(width, height, depth, rgba_bytes)
"""
import ctypes
import ctypes.util
import pivy.coin as coin


# --- Load libGL ---
_gl_path = ctypes.util.find_library("GL")
if _gl_path:
    _gl = ctypes.cdll.LoadLibrary(_gl_path)
else:
    # Fallback paths for Linux
    for path in ["libGL.so.1", "libGL.so"]:
        try:
            _gl = ctypes.cdll.LoadLibrary(path)
            break
        except OSError:
            continue
    else:
        raise RuntimeError("Could not load libGL")

# --- GL constants ---
GL_TEXTURE_3D = 0x806F
GL_TEXTURE0 = 0x84C0
GL_RGBA = 0x1908
GL_UNSIGNED_BYTE = 0x1401
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_NEAREST = 0x2600
GL_TEXTURE_WRAP_S = 0x2802
GL_TEXTURE_WRAP_T = 0x2803
GL_TEXTURE_WRAP_R = 0x8072
GL_CLAMP_TO_EDGE = 0x812F
GL_UNPACK_ALIGNMENT = 0x0CF5

# --- GL function signatures ---
_gl.glGenTextures.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
_gl.glGenTextures.restype = None

_gl.glDeleteTextures.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
_gl.glDeleteTextures.restype = None

_gl.glBindTexture.argtypes = [ctypes.c_uint, ctypes.c_uint]
_gl.glBindTexture.restype = None

_gl.glActiveTexture.argtypes = [ctypes.c_uint]
_gl.glActiveTexture.restype = None

_gl.glTexImage3D.argtypes = [
    ctypes.c_uint,   # target
    ctypes.c_int,    # level
    ctypes.c_int,    # internalformat
    ctypes.c_int,    # width
    ctypes.c_int,    # height
    ctypes.c_int,    # depth
    ctypes.c_int,    # border
    ctypes.c_uint,   # format
    ctypes.c_uint,   # type
    ctypes.c_void_p, # data
]
_gl.glTexImage3D.restype = None

_gl.glTexSubImage3D.argtypes = [
    ctypes.c_uint,   # target
    ctypes.c_int,    # level
    ctypes.c_int,    # xoffset
    ctypes.c_int,    # yoffset
    ctypes.c_int,    # zoffset
    ctypes.c_int,    # width
    ctypes.c_int,    # height
    ctypes.c_int,    # depth
    ctypes.c_uint,   # format
    ctypes.c_uint,   # type
    ctypes.c_void_p, # pixels
]
_gl.glTexSubImage3D.restype = None

_gl.glTexParameteri.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
_gl.glTexParameteri.restype = None

_gl.glPixelStorei.argtypes = [ctypes.c_uint, ctypes.c_int]
_gl.glPixelStorei.restype = None


class GLTexture3D:
    """Manages a GL_TEXTURE_3D with direct OpenGL calls."""

    def __init__(self):
        self._tex_id = 0
        self._width = 0
        self._height = 0
        self._depth = 0
        self._data = None       # Keep a reference to prevent GC
        self._needs_upload = False
        self._pending_slice = None   # (z_offset, width, height, depth, data) or None
        self._needs_partial  = False

        # SoCallback node — add this to the scene graph BEFORE the shader.
        # On each render traversal it binds the texture to unit 0.
        self.callback_node = coin.SoCallback()
        self.callback_node.setCallback(self._gl_callback)

    def upload(self, width, height, depth, rgba_bytes):
        """Queue a texture upload. The actual GL call happens in the render callback."""
        self._width = width
        self._height = height
        self._depth = depth
        # Keep the bytes alive so the ctypes pointer remains valid
        if isinstance(rgba_bytes, (bytes, bytearray)):
            self._data = (ctypes.c_ubyte * len(rgba_bytes)).from_buffer_copy(rgba_bytes)
        else:
            self._data = (ctypes.c_ubyte * len(rgba_bytes)).from_buffer_copy(bytes(rgba_bytes))
        self._needs_upload = True
        self._needs_partial = False # Full upload supercedes partial

    def update_slice(self, z_offset, width, height, depth, rgba_bytes):
        """Queue a partial z-slice update via glTexSubImage3D.

        The texture must already exist (upload() must have been called at least once
        with the full dimensions). width/height must not exceed the current texture
        width/height.
        """
        if isinstance(rgba_bytes, (bytes, bytearray)):
            data = (ctypes.c_ubyte * len(rgba_bytes)).from_buffer_copy(rgba_bytes)
        else:
            data = (ctypes.c_ubyte * len(rgba_bytes)).from_buffer_copy(bytes(rgba_bytes))
        self._pending_slice = (z_offset, width, height, depth, data)
        self._needs_partial = True

    def _gl_callback(self, userdata, action):
        """Called by Coin3D during scene graph traversal."""
        # Only act during GL render actions
        if not action.isOfType(coin.SoGLRenderAction.getClassTypeId()):
            return

        # Generate texture ID on first use
        if self._tex_id == 0 and self._data is not None:
            tex_id = ctypes.c_uint(0)
            _gl.glGenTextures(1, ctypes.byref(tex_id))
            self._tex_id = tex_id.value

        if self._tex_id == 0:
            return

        # Bind to texture unit 0
        _gl.glActiveTexture(GL_TEXTURE0)
        _gl.glBindTexture(GL_TEXTURE_3D, self._tex_id)

        # Upload data if pending
        if self._needs_upload and self._data is not None:
            _gl.glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
            _gl.glTexImage3D(
                GL_TEXTURE_3D,
                0,              # level
                GL_RGBA,        # internalformat (GL_RGBA8 is selected by driver)
                self._width,
                self._height,
                self._depth,
                0,              # border
                GL_RGBA,        # format
                GL_UNSIGNED_BYTE,
                ctypes.cast(self._data, ctypes.c_void_p),
            )
            # NEAREST filtering — shader does its own trilinear on float32
            _gl.glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
            _gl.glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
            _gl.glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            _gl.glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            _gl.glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_R, GL_CLAMP_TO_EDGE)
            self._needs_upload = False
            self._needs_partial = False
        
        elif self._needs_partial and self._pending_slice is not None and self._tex_id != 0:
            z_off, w, h, d, data = self._pending_slice
            _gl.glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
            _gl.glTexSubImage3D(
                GL_TEXTURE_3D,
                0,          # level
                0, 0, z_off,   # xoffset, yoffset, zoffset
                w, h, d,    # width, height, depth of the sub-region
                GL_RGBA,
                GL_UNSIGNED_BYTE,
                ctypes.cast(data, ctypes.c_void_p),
            )
            self._pending_slice = None
            self._needs_partial = False

    def destroy(self):
        """Delete the GL texture."""
        if self._tex_id != 0:
            tex_id = ctypes.c_uint(self._tex_id)
            _gl.glDeleteTextures(1, ctypes.byref(tex_id))
            self._tex_id = 0
        self._data = None
