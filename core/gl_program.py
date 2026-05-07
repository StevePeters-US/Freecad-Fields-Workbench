"""core/gl_program.py

Thin ctypes wrapper for OpenGL shader programs.
Reuses the GLFunctionLoader from gl_texture3d.py.
"""
import ctypes
from core.gl_texture3d import _loader

GL_VERTEX_SHADER   = 0x8B31
GL_FRAGMENT_SHADER = 0x8B30
GL_COMPUTE_SHADER  = 0x91B9
GL_COMPILE_STATUS  = 0x8B81
GL_LINK_STATUS     = 0x8B82
GL_INFO_LOG_LENGTH = 0x8B84

# Image / compute constants
GL_WRITE_ONLY = 0x88B9
GL_RGBA16F    = 0x881A
GL_R32F       = 0x822E
GL_SHADER_IMAGE_ACCESS_BARRIER_BIT = 0x00000020
GL_TEXTURE_FETCH_BARRIER_BIT       = 0x00000008


class GLProgram:
    """Manages a compiled GLSL vertex+fragment shader program."""

    def __init__(self):
        self._prog_id = 0

    def compile(self, vert_src: str, frag_src: str):
        """Compile and link shaders. Must be called inside an active GL context."""
        glCreateShader     = _loader.get("glCreateShader",     [ctypes.c_uint], ctypes.c_uint)
        glShaderSource     = _loader.get("glShaderSource",     [ctypes.c_uint, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_int)], None)
        glCompileShader    = _loader.get("glCompileShader",    [ctypes.c_uint], None)
        glGetShaderiv      = _loader.get("glGetShaderiv",      [ctypes.c_uint, ctypes.c_uint,
                                ctypes.POINTER(ctypes.c_int)], None)
        glGetShaderInfoLog = _loader.get("glGetShaderInfoLog", [ctypes.c_uint, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_int), ctypes.c_char_p], None)
        glCreateProgram    = _loader.get("glCreateProgram",    [], ctypes.c_uint)
        glAttachShader     = _loader.get("glAttachShader",     [ctypes.c_uint, ctypes.c_uint], None)
        glLinkProgram      = _loader.get("glLinkProgram",      [ctypes.c_uint], None)
        glGetProgramiv     = _loader.get("glGetProgramiv",     [ctypes.c_uint, ctypes.c_uint,
                                ctypes.POINTER(ctypes.c_int)], None)
        glGetProgramInfoLog = _loader.get("glGetProgramInfoLog", [ctypes.c_uint, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_int), ctypes.c_char_p], None)
        glDeleteShader     = _loader.get("glDeleteShader",     [ctypes.c_uint], None)

        def _compile(shader_type, src):
            sid = glCreateShader(shader_type)
            src_bytes = src.encode('utf-8')
            src_p = ctypes.c_char_p(src_bytes)
            glShaderSource(sid, 1, ctypes.byref(src_p), None)
            glCompileShader(sid)
            status = ctypes.c_int(0)
            glGetShaderiv(sid, GL_COMPILE_STATUS, ctypes.byref(status))
            if not status.value:
                buf_len = ctypes.c_int(0)
                glGetShaderiv(sid, GL_INFO_LOG_LENGTH, ctypes.byref(buf_len))
                buf = ctypes.create_string_buffer(max(buf_len.value, 256))
                glGetShaderInfoLog(sid, buf_len.value, None, buf)
                raise RuntimeError(f"Shader compile error:\n{buf.value.decode(errors='replace')}")
            return sid

        if self._prog_id:
            self.destroy()

        vsid = _compile(GL_VERTEX_SHADER,   vert_src)
        fsid = _compile(GL_FRAGMENT_SHADER, frag_src)
        prog = glCreateProgram()
        glAttachShader(prog, vsid)
        glAttachShader(prog, fsid)
        glLinkProgram(prog)

        status = ctypes.c_int(0)
        glGetProgramiv(prog, GL_LINK_STATUS, ctypes.byref(status))
        if not status.value:
            buf_len = ctypes.c_int(0)
            glGetProgramiv(prog, GL_INFO_LOG_LENGTH, ctypes.byref(buf_len))
            buf = ctypes.create_string_buffer(max(buf_len.value, 256))
            glGetProgramInfoLog(prog, buf_len.value, None, buf)
            raise RuntimeError(f"Program link error:\n{buf.value.decode(errors='replace')}")

        glDeleteShader(vsid)
        glDeleteShader(fsid)
        self._prog_id = prog

    def use(self):
        glUseProgram = _loader.get("glUseProgram", [ctypes.c_uint], None)
        glUseProgram(self._prog_id)

    def _loc(self, name: str) -> int:
        glGetUniformLocation = _loader.get("glGetUniformLocation",
            [ctypes.c_uint, ctypes.c_char_p], ctypes.c_int)
        return glGetUniformLocation(self._prog_id, name.encode('utf-8'))

    def set_1i(self, name: str, v: int):
        f = _loader.get("glUniform1i", [ctypes.c_int, ctypes.c_int], None)
        f(self._loc(name), int(v))

    def set_1f(self, name: str, v: float):
        f = _loader.get("glUniform1f", [ctypes.c_int, ctypes.c_float], None)
        f(self._loc(name), float(v))

    def set_2f(self, name: str, x: float, y: float):
        f = _loader.get("glUniform2f", [ctypes.c_int, ctypes.c_float, ctypes.c_float], None)
        f(self._loc(name), float(x), float(y))

    def set_3f(self, name: str, x: float, y: float, z: float):
        f = _loader.get("glUniform3f",
            [ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_float], None)
        f(self._loc(name), float(x), float(y), float(z))

    def set_3fv(self, name: str, count: int, flat_list):
        """Set a vec3 array. flat_list is [x0,y0,z0, x1,y1,z1, ...] (count*3 floats)."""
        f = _loader.get("glUniform3fv",
            [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_float)], None)
        arr = (ctypes.c_float * len(flat_list))(*flat_list)
        f(self._loc(name), count, arr)

    def set_2i(self, name: str, x: int, y: int):
        f = _loader.get("glUniform2i", [ctypes.c_int, ctypes.c_int, ctypes.c_int], None)
        f(self._loc(name), int(x), int(y))

    def set_mat4(self, name: str, mat16):
        """Set a mat4 from a flat 16-float column-major sequence."""
        f = _loader.get("glUniformMatrix4fv",
            [ctypes.c_int, ctypes.c_int, ctypes.c_bool,
             ctypes.POINTER(ctypes.c_float)], None)
        arr = (ctypes.c_float * 16)(*mat16)
        f(self._loc(name), 1, False, arr)

    def set_int(self, name: str, v: int):
        f = _loader.get("glUniform1i", [ctypes.c_int, ctypes.c_int], None)
        f(self._loc(name), int(v))

    def set_uniforms_from_ctx(self, ctx_uniforms):
        """Set all uniforms from a GlslContext.uniforms list."""
        for name, glsl_type, value in ctx_uniforms:
            if glsl_type == "float":
                self.set_1f(name, value)
            elif glsl_type == "vec2":
                self.set_2f(name, value[0], value[1])
            elif glsl_type == "vec3":
                self.set_3f(name, value[0], value[1], value[2])
            elif glsl_type == "mat4":
                # Ensure value is flat 16-float
                if isinstance(value[0], list):
                    # Flatten 4x4 row-major list but transpose to column-major for GLProgram
                    flat = [0.0] * 16
                    for r in range(4):
                        for c in range(4):
                            flat[c * 4 + r] = value[r][c]
                    self.set_mat4(name, flat)
                else:
                    self.set_mat4(name, value)
            elif glsl_type == "int":
                self.set_int(name, value)

    def compile_compute(self, src: str):
        """Compile and link a compute-only shader program. Requires GL 4.3+."""
        glCreateShader      = _loader.get("glCreateShader",      [ctypes.c_uint], ctypes.c_uint)
        glShaderSource      = _loader.get("glShaderSource",      [ctypes.c_uint, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_int)], None)
        glCompileShader     = _loader.get("glCompileShader",     [ctypes.c_uint], None)
        glGetShaderiv       = _loader.get("glGetShaderiv",       [ctypes.c_uint, ctypes.c_uint,
                                ctypes.POINTER(ctypes.c_int)], None)
        glGetShaderInfoLog  = _loader.get("glGetShaderInfoLog",  [ctypes.c_uint, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_int), ctypes.c_char_p], None)
        glCreateProgram     = _loader.get("glCreateProgram",     [], ctypes.c_uint)
        glAttachShader      = _loader.get("glAttachShader",      [ctypes.c_uint, ctypes.c_uint], None)
        glLinkProgram       = _loader.get("glLinkProgram",       [ctypes.c_uint], None)
        glGetProgramiv      = _loader.get("glGetProgramiv",      [ctypes.c_uint, ctypes.c_uint,
                                ctypes.POINTER(ctypes.c_int)], None)
        glGetProgramInfoLog = _loader.get("glGetProgramInfoLog", [ctypes.c_uint, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_int), ctypes.c_char_p], None)
        glDeleteShader      = _loader.get("glDeleteShader",      [ctypes.c_uint], None)

        if self._prog_id:
            self.destroy()

        sid = glCreateShader(GL_COMPUTE_SHADER)
        src_bytes = src.encode('utf-8')
        src_p = ctypes.c_char_p(src_bytes)
        glShaderSource(sid, 1, ctypes.byref(src_p), None)
        glCompileShader(sid)
        status = ctypes.c_int(0)
        glGetShaderiv(sid, GL_COMPILE_STATUS, ctypes.byref(status))
        if not status.value:
            buf_len = ctypes.c_int(0)
            glGetShaderiv(sid, GL_INFO_LOG_LENGTH, ctypes.byref(buf_len))
            buf = ctypes.create_string_buffer(max(buf_len.value, 256))
            glGetShaderInfoLog(sid, buf_len.value, None, buf)
            raise RuntimeError(f"Compute shader compile error:\n{buf.value.decode(errors='replace')}")

        prog = glCreateProgram()
        glAttachShader(prog, sid)
        glLinkProgram(prog)
        status = ctypes.c_int(0)
        glGetProgramiv(prog, GL_LINK_STATUS, ctypes.byref(status))
        if not status.value:
            buf_len = ctypes.c_int(0)
            glGetProgramiv(prog, GL_INFO_LOG_LENGTH, ctypes.byref(buf_len))
            buf = ctypes.create_string_buffer(max(buf_len.value, 256))
            glGetProgramInfoLog(prog, buf_len.value, None, buf)
            raise RuntimeError(f"Compute program link error:\n{buf.value.decode(errors='replace')}")

        glDeleteShader(sid)
        self._prog_id = prog

    def dispatch_compute(self, nx: int, ny: int, nz: int = 1):
        """Dispatch this compute program. Must call use() first."""
        glDispatchCompute = _loader.get("glDispatchCompute",
            [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint], None)
        glDispatchCompute(nx, ny, nz)

    def draw_fullscreen_quad(self):
        """Draw a CCW triangle strip covering NDC [-1,1]×[-1,1]."""
        glBegin   = _loader.get("glBegin",    [ctypes.c_uint], None)
        glEnd     = _loader.get("glEnd",      [], None)
        glVertex2f = _loader.get("glVertex2f", [ctypes.c_float, ctypes.c_float], None)
        glBegin(0x0008)      # GL_TRIANGLE_STRIP
        glVertex2f(-1.0, -1.0)
        glVertex2f( 1.0, -1.0)
        glVertex2f(-1.0,  1.0)
        glVertex2f( 1.0,  1.0)
        glEnd()

    def destroy(self):
        if self._prog_id:
            f = _loader.get("glDeleteProgram", [ctypes.c_uint], None)
            f(self._prog_id)
            self._prog_id = 0


def bind_image_texture(unit: int, tex_id: int, access: int, fmt: int):
    """Bind a texture to an image unit for compute shader imageLoad/imageStore.
    Pass tex_id=0 to unbind.
    """
    glBindImageTexture = _loader.get("glBindImageTexture",
        [ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_uint,
         ctypes.c_int, ctypes.c_uint, ctypes.c_uint], None)
    # (unit, texture, level=0, layered=GL_FALSE, layer=0, access, format)
    glBindImageTexture(unit, tex_id, 0, 0, 0, access, fmt)


def memory_barrier(bits: int):
    """Insert a GL memory barrier for compute→sampler synchronization."""
    glMemoryBarrier = _loader.get("glMemoryBarrier", [ctypes.c_uint], None)
    glMemoryBarrier(bits)
