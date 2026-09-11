# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/gl/gl_texture3d.py

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
from freecad.fields.core import fld_logger
from freecad.fields.core.gl.gl_constants import (
    GL_TEXTURE_3D, GL_TEXTURE0, GL_RGBA, GL_UNSIGNED_BYTE,
    GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER, GL_NEAREST, GL_LINEAR,
    GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T, GL_TEXTURE_WRAP_R, GL_CLAMP_TO_EDGE,
    GL_R16F, GL_R32F, GL_RGBA32F, GL_RED, GL_FLOAT, GL_HALF_FLOAT,
)



# --- Load libGL ---
_gl = None
# Common library names across Windows (opengl32), Linux (GL, libGL), and MacOS (OpenGL)
_lib_names = ["GL", "opengl32", "libGL", "libGL.so.1", "OpenGL"]

for lib_name in _lib_names:
    _gl_path = ctypes.util.find_library(lib_name)
    if _gl_path:
        try:
            _gl = ctypes.cdll.LoadLibrary(_gl_path)
            break
        except OSError:
            continue

if _gl is None:
    # Final fallback for cases where find_library fails but direct load might work
    for name in ["opengl32.dll", "libGL.so.1", "libGL.so"]:
        try:
            _gl = ctypes.cdll.LoadLibrary(name)
            break
        except OSError:
            continue

if _gl is None:
    raise RuntimeError(
        "Could not load OpenGL library. Tried: " + ", ".join(_lib_names)
    )

# --- GL constants not shared with another gl_*.py module; see gl_constants.py
# for the rest (CR-039) ---
GL_LINEAR_MIPMAP_NEAREST = 0x2701
GL_TEXTURE_MAX_LEVEL = 0x813D
GL_UNPACK_ALIGNMENT = 0x0CF5


class GLFunctionLoader:
    """Lazy-loads OpenGL functions, using wglGetProcAddress on Windows for modern GL."""

    def __init__(self, gl_lib):
        self._gl = gl_lib
        self._is_win = (ctypes.sizeof(ctypes.c_void_p) == 8 or True) and hasattr(ctypes, "windll")
        self._funcs = {}
        self._wglGetProcAddress = None

    def _setup_wgl(self):
        if self._is_win and self._wglGetProcAddress is None:
            try:
                self._wglGetProcAddress = self._gl.wglGetProcAddress
                self._wglGetProcAddress.argtypes = [ctypes.c_char_p]
                self._wglGetProcAddress.restype = ctypes.c_void_p
            except AttributeError as e:
                fld_logger.debug(f"_setup_wgl attribute error: {e}")

    def get(self, name, argtypes, restype):
        if name in self._funcs:
            return self._funcs[name]

        # 1. Try static export from DLL/SO
        try:
            func = getattr(self._gl, name)
            func.argtypes = argtypes
            func.restype = restype
            self._funcs[name] = func
            return func
        except AttributeError:
            # 2. On Windows, try wglGetProcAddress for functions > OpenGL 1.1
            if self._is_win:
                self._setup_wgl()
                if self._wglGetProcAddress:
                    addr = self._wglGetProcAddress(name.encode('ascii'))
                    if addr:
                        # Create function prototype (stdcall/WINFUNCTYPE for opengl32 functions)
                        proto = ctypes.WINFUNCTYPE(restype, *argtypes)
                        func = proto(addr)
                        self._funcs[name] = func
                        return func

        raise RuntimeError(f"Could not find OpenGL function: {name}")


# Global loader instance
_loader = GLFunctionLoader(_gl)


class GLTexture3D:
    """Manages a GL_TEXTURE_3D with direct OpenGL calls.

    Supports two formats:
    - 'r32f': One float32 per texel (R32F). Used with compute shaders and
              simplified fragment shader (texelFetch returns float in .r).
    - 'rgba8': Four uint8 per texel (RGBA8). Legacy format where float32 is
               reinterpreted as 4 bytes. Fragment shader must decode.
    """

    def __init__(self, fmt='r32f'):
        self._tex_id = 0
        self._width = 0
        self._height = 0
        self._depth = 0
        self._data = None       # Keep a reference to prevent GC
        self._np_ref = None
        self._needs_upload = False
        self._needs_allocate = False
        self._pending_slice = None   # (z_offset, width, height, depth, data) or None
        self._pending_level = 0
        self._needs_partial  = False
        self._fmt = fmt
        self.max_level = 0

        # SoCallback node — add this to the scene graph BEFORE the shader.
        # On each render traversal it binds the texture to unit 0.
        self.callback_node = coin.SoCallback()
        self.callback_node.setCallback(self._gl_callback)

    @property
    def tex_id(self):
        return self._tex_id

    def _gl_format_params(self):
        """Return (internalformat, format, type) for the current format mode."""
        if self._fmt == 'r16f':
            return GL_R16F, GL_RED, GL_HALF_FLOAT
        if self._fmt == 'r32f':
            return GL_R32F, GL_RED, GL_FLOAT
        if self._fmt == 'rgba32f':
            return GL_RGBA32F, GL_RGBA, GL_FLOAT
        return GL_RGBA, GL_RGBA, GL_UNSIGNED_BYTE

    def allocate(self, width, height, depth):
        """Queue texture allocation without data (for compute shader to fill)."""
        self._width = width
        self._height = height
        self._depth = depth
        self._data = None
        self._needs_allocate = True
        self._needs_upload = False
        self._needs_partial = False

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
        self._needs_allocate = False
        self._needs_partial = False # Full upload supercedes partial

    def _ensure_tex_id(self):
        """Generate texture ID if needed. Must be called in GL context."""
        if self._tex_id == 0:
            glGenTextures = _loader.get("glGenTextures",
                [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
            tex_id = ctypes.c_uint(0)
            glGenTextures(1, ctypes.byref(tex_id))
            self._tex_id = tex_id.value

    def _gl_callback(self, userdata=None, action=None):
        """Called by Coin3D during scene graph traversal."""
        # Only act during GL render actions
        if action is not None and hasattr(action, "isOfType"):
            if not action.isOfType(coin.SoGLRenderAction.getClassTypeId()):
                return

        # Fetch required functions (lazy-loaded during the first callback with context)
        glBindTexture = _loader.get("glBindTexture", [ctypes.c_uint, ctypes.c_uint], None)
        glActiveTexture = _loader.get("glActiveTexture", [ctypes.c_uint], None)
        glTexImage3D = _loader.get("glTexImage3D", [
            ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p
        ], None)
        glTexSubImage3D = _loader.get("glTexSubImage3D", [
            ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p
        ], None)
        glTexParameteri = _loader.get("glTexParameteri", [ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)
        glPixelStorei = _loader.get("glPixelStorei", [ctypes.c_uint, ctypes.c_int], None)

        ifmt, fmt, dtype = self._gl_format_params()

        # Generate texture ID on first use
        if self._tex_id == 0 and (self._data is not None or self._needs_allocate):
            self._ensure_tex_id()

        if self._tex_id == 0:
            return

        # Bind to texture unit 0
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_3D, self._tex_id)

        def _set_tex_params():
            min_filter = GL_LINEAR_MIPMAP_NEAREST if self.max_level > 0 else GL_LINEAR
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MIN_FILTER, min_filter)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_R, GL_CLAMP_TO_EDGE)
            if self.max_level > 0:
                glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MAX_LEVEL, self.max_level)

        # Allocate without data (for compute shader path)
        if self._needs_allocate:
            glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
            glTexImage3D(
                GL_TEXTURE_3D, 0, ifmt,
                self._width, self._height, self._depth,
                0, fmt, dtype, None,
            )
            _set_tex_params()
            self._needs_allocate = False

        # Upload data if pending
        elif self._needs_upload and self._data is not None:
            glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
            level = getattr(self, "_pending_level", 0)
            glTexImage3D(
                GL_TEXTURE_3D, level, ifmt,
                self._width, self._height, self._depth,
                0, fmt, dtype,
                ctypes.cast(self._data, ctypes.c_void_p),
            )
            _set_tex_params()
            self._pending_level = 0
            self._needs_upload = False
            self._needs_partial = False

        elif self._needs_partial and self._pending_slice is not None and self._tex_id != 0:
            z_off, w, h, d, data = self._pending_slice
            glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
            glTexSubImage3D(
                GL_TEXTURE_3D, 0,
                0, 0, z_off,
                w, h, d,
                fmt, dtype,
                ctypes.cast(data, ctypes.c_void_p),
            )
            self._pending_slice = None
            self._needs_partial = False

    def destroy(self):
        """Delete the GL texture."""
        if self._tex_id != 0:
            glDeleteTextures = _loader.get("glDeleteTextures", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
            tex_id = ctypes.c_uint(self._tex_id)
            glDeleteTextures(1, ctypes.byref(tex_id))
            self._tex_id = 0
        self._data = None
        self._np_ref = None

    @property
    def id(self):
        """Return the OpenGL texture ID."""
        return self._tex_id
