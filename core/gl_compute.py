"""
core/gl_compute.py

OpenGL compute shader compilation and dispatch via ctypes.
Uses the same GLFunctionLoader from gl_texture3d.py for GL function loading.
"""
import ctypes
from core.gl_texture3d import _loader

# GL constants for compute shaders
GL_COMPUTE_SHADER = 0x91B9
GL_COMPILE_STATUS = 0x8B81
GL_LINK_STATUS = 0x8B82
GL_INFO_LOG_LENGTH = 0x8B84
GL_SHADER_IMAGE_ACCESS_BARRIER_BIT = 0x00000020
GL_TEXTURE_3D = 0x806F
GL_WRITE_ONLY = 0x88B9
GL_R32F = 0x822E
GL_TRUE = 1


class GLComputeShader:
    """Compiles and dispatches an OpenGL compute shader."""

    def __init__(self):
        self._program = 0
        self._source = None

    def compile(self, source):
        """Compile compute shader source. Must be called within GL context."""
        self._source = source

        glCreateShader = _loader.get("glCreateShader",
            [ctypes.c_uint], ctypes.c_uint)
        glShaderSource = _loader.get("glShaderSource",
            [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p), ctypes.c_void_p], None)
        glCompileShader = _loader.get("glCompileShader",
            [ctypes.c_uint], None)
        glGetShaderiv = _loader.get("glGetShaderiv",
            [ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_int)], None)
        glGetShaderInfoLog = _loader.get("glGetShaderInfoLog",
            [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.c_char_p], None)
        glCreateProgram = _loader.get("glCreateProgram", [], ctypes.c_uint)
        glAttachShader = _loader.get("glAttachShader",
            [ctypes.c_uint, ctypes.c_uint], None)
        glLinkProgram = _loader.get("glLinkProgram",
            [ctypes.c_uint], None)
        glGetProgramiv = _loader.get("glGetProgramiv",
            [ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_int)], None)
        glGetProgramInfoLog = _loader.get("glGetProgramInfoLog",
            [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.c_char_p], None)
        glDeleteShader = _loader.get("glDeleteShader",
            [ctypes.c_uint], None)

        # Clean up old program
        if self._program != 0:
            glDeleteProgram = _loader.get("glDeleteProgram",
                [ctypes.c_uint], None)
            glDeleteProgram(self._program)
            self._program = 0

        # Create and compile shader
        shader = glCreateShader(GL_COMPUTE_SHADER)
        src = source.encode('utf-8')
        src_ptr = ctypes.c_char_p(src)
        glShaderSource(shader, 1, ctypes.byref(src_ptr), None)
        glCompileShader(shader)

        # Check compilation
        status = ctypes.c_int(0)
        glGetShaderiv(shader, GL_COMPILE_STATUS, ctypes.byref(status))
        if status.value == 0:
            log_len = ctypes.c_int(0)
            glGetShaderiv(shader, GL_INFO_LOG_LENGTH, ctypes.byref(log_len))
            log = ctypes.create_string_buffer(max(log_len.value, 1))
            glGetShaderInfoLog(shader, log_len.value, None, log)
            glDeleteShader(shader)
            raise RuntimeError(
                f"Compute shader compilation failed:\n{log.value.decode()}")

        # Create program and link
        program = glCreateProgram()
        glAttachShader(program, shader)
        glLinkProgram(program)
        glDeleteShader(shader)

        # Check linking
        glGetProgramiv(program, GL_LINK_STATUS, ctypes.byref(status))
        if status.value == 0:
            log_len = ctypes.c_int(0)
            glGetProgramiv(program, GL_INFO_LOG_LENGTH, ctypes.byref(log_len))
            log = ctypes.create_string_buffer(max(log_len.value, 1))
            glGetProgramInfoLog(program, log_len.value, None, log)
            glDeleteProgram = _loader.get("glDeleteProgram",
                [ctypes.c_uint], None)
            glDeleteProgram(program)
            raise RuntimeError(
                f"Compute shader link failed:\n{log.value.decode()}")

        self._program = program

    def use(self):
        """Bind this program. Must be called before setting uniforms."""
        glUseProgram = _loader.get("glUseProgram",
            [ctypes.c_uint], None)
        glUseProgram(self._program)

    def _get_loc(self, name):
        glGetUniformLocation = _loader.get("glGetUniformLocation",
            [ctypes.c_uint, ctypes.c_char_p], ctypes.c_int)
        return glGetUniformLocation(self._program, name.encode('utf-8'))

    def set_int(self, name, value):
        loc = self._get_loc(name)
        if loc >= 0:
            glUniform1i = _loader.get("glUniform1i",
                [ctypes.c_int, ctypes.c_int], None)
            glUniform1i(loc, int(value))

    def set_float(self, name, value):
        loc = self._get_loc(name)
        if loc >= 0:
            glUniform1f = _loader.get("glUniform1f",
                [ctypes.c_int, ctypes.c_float], None)
            glUniform1f(loc, float(value))

    def set_vec3(self, name, value):
        loc = self._get_loc(name)
        if loc >= 0:
            glUniform3f = _loader.get("glUniform3f",
                [ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_float], None)
            glUniform3f(loc, float(value[0]), float(value[1]), float(value[2]))

    def set_ivec3(self, name, value):
        loc = self._get_loc(name)
        if loc >= 0:
            glUniform3i = _loader.get("glUniform3i",
                [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int], None)
            glUniform3i(loc, int(value[0]), int(value[1]), int(value[2]))

    def set_mat4(self, name, value):
        """Set a mat4 uniform. value is a 4x4 nested list (row-major numpy style)."""
        loc = self._get_loc(name)
        if loc >= 0:
            import numpy as np
            arr = np.array(value, dtype=np.float32).flatten()
            c_arr = (ctypes.c_float * 16)(*arr)
            glUniformMatrix4fv = _loader.get("glUniformMatrix4fv",
                [ctypes.c_int, ctypes.c_int, ctypes.c_ubyte,
                 ctypes.POINTER(ctypes.c_float)], None)
            # transpose=GL_TRUE: numpy is row-major, GL expects column-major
            glUniformMatrix4fv(loc, 1, GL_TRUE, c_arr)

    def set_uniforms_from_ctx(self, ctx_uniforms):
        """Set all uniforms from a GlslContext.uniforms list."""
        for name, glsl_type, value in ctx_uniforms:
            if glsl_type == "float":
                self.set_float(name, value)
            elif glsl_type == "vec3":
                self.set_vec3(name, value)
            elif glsl_type == "mat4":
                self.set_mat4(name, value)
            elif glsl_type == "int":
                self.set_int(name, value)

    def dispatch(self, gx, gy, gz):
        """Dispatch compute work groups. Program must be bound via use()."""
        glDispatchCompute = _loader.get("glDispatchCompute",
            [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint], None)
        glDispatchCompute(gx, gy, gz)

    def destroy(self):
        if self._program != 0:
            glDeleteProgram = _loader.get("glDeleteProgram",
                [ctypes.c_uint], None)
            glDeleteProgram(self._program)
            self._program = 0

    @property
    def is_compiled(self):
        return self._program != 0


def bind_image_texture(unit, tex_id, access=GL_WRITE_ONLY):
    """Bind a 3D texture as an image for compute shader imageStore."""
    glBindImageTexture = _loader.get("glBindImageTexture",
        [ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_ubyte,
         ctypes.c_int, ctypes.c_uint, ctypes.c_uint], None)
    # unit, texture, level, layered=GL_TRUE, layer=0, access, format=GL_R32F
    glBindImageTexture(unit, tex_id, 0, GL_TRUE, 0, access, GL_R32F)


def memory_barrier():
    """Issue a shader image access memory barrier."""
    glMemoryBarrier = _loader.get("glMemoryBarrier",
        [ctypes.c_uint], None)
    glMemoryBarrier(GL_SHADER_IMAGE_ACCESS_BARRIER_BIT)


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
    except Exception:
        pass
    return False
