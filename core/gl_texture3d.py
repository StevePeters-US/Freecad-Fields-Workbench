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
            except AttributeError:
                pass

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

        # Fetch required functions (lazy-loaded during the first callback with context)
        glGenTextures = _loader.get("glGenTextures", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
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

        # Generate texture ID on first use
        if self._tex_id == 0 and self._data is not None:
            tex_id = ctypes.c_uint(0)
            glGenTextures(1, ctypes.byref(tex_id))
            self._tex_id = tex_id.value

        if self._tex_id == 0:
            return

        # Bind to texture unit 0
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_3D, self._tex_id)

        # Upload data if pending
        if self._needs_upload and self._data is not None:
            glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
            glTexImage3D(
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
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_R, GL_CLAMP_TO_EDGE)
            self._needs_upload = False
            self._needs_partial = False
        
        elif self._needs_partial and self._pending_slice is not None and self._tex_id != 0:
            z_off, w, h, d, data = self._pending_slice
            glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
            glTexSubImage3D(
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
            glDeleteTextures = _loader.get("glDeleteTextures", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
            tex_id = ctypes.c_uint(self._tex_id)
            glDeleteTextures(1, ctypes.byref(tex_id))
            self._tex_id = 0
        self._data = None
