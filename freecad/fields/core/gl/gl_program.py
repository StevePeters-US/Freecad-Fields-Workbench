# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core/gl/gl_program.py

Thin ctypes wrapper for OpenGL shader programs.
Reuses the GLFunctionLoader from gl_texture3d.py.
"""
import ctypes
from freecad.fields.core.gl.gl_texture3d import _loader
from freecad.fields.core import fld_logger
from freecad.fields.core.gl.gl_constants import GL_RGBA16F, GL_R32F

GL_VERTEX_SHADER   = 0x8B31
GL_FRAGMENT_SHADER = 0x8B30
GL_COMPUTE_SHADER  = 0x91B9
GL_COMPILE_STATUS  = 0x8B81
GL_LINK_STATUS     = 0x8B82
GL_INFO_LOG_LENGTH = 0x8B84

# Image / compute constants not shared with another gl_*.py module (CR-039)
GL_WRITE_ONLY = 0x88B9
GL_SHADER_IMAGE_ACCESS_BARRIER_BIT = 0x00000020
GL_TEXTURE_FETCH_BARRIER_BIT       = 0x00000008


def _compile_shader_stage(shader_type, src, error_label="Shader"):
    """Compile one shader stage; raises RuntimeError with the GL info log on failure.

    Shared by `GLProgram.compile()` (vertex+fragment, via its local `_compile`
    wrapper) and `.compile_compute()` (compute-only), which independently
    reimplemented the identical create/source/compile/GL_COMPILE_STATUS-check
    sequence (CR-037). `error_label` keeps each caller's own error-message
    wording ("Shader" vs "Compute shader").
    """
    glCreateShader     = _loader.get("glCreateShader",     [ctypes.c_uint], ctypes.c_uint)
    glShaderSource     = _loader.get("glShaderSource",     [ctypes.c_uint, ctypes.c_int,
                            ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_int)], None)
    glCompileShader    = _loader.get("glCompileShader",    [ctypes.c_uint], None)
    glGetShaderiv      = _loader.get("glGetShaderiv",      [ctypes.c_uint, ctypes.c_uint,
                            ctypes.POINTER(ctypes.c_int)], None)
    glGetShaderInfoLog = _loader.get("glGetShaderInfoLog", [ctypes.c_uint, ctypes.c_int,
                            ctypes.POINTER(ctypes.c_int), ctypes.c_char_p], None)

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
        raise RuntimeError(f"{error_label} compile error:\n{buf.value.decode(errors='replace')}")
    return sid


class GLProgram:
    """Manages a compiled GLSL vertex+fragment shader program."""

    def __init__(self):
        self._prog_id = 0

    def compile(self, vert_src: str, frag_src: str):
        """Compile and link shaders. Must be called inside an active GL context."""
        glCreateProgram    = _loader.get("glCreateProgram",    [], ctypes.c_uint)
        glAttachShader     = _loader.get("glAttachShader",     [ctypes.c_uint, ctypes.c_uint], None)
        glLinkProgram      = _loader.get("glLinkProgram",      [ctypes.c_uint], None)
        glGetProgramiv     = _loader.get("glGetProgramiv",     [ctypes.c_uint, ctypes.c_uint,
                                ctypes.POINTER(ctypes.c_int)], None)
        glGetProgramInfoLog = _loader.get("glGetProgramInfoLog", [ctypes.c_uint, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_int), ctypes.c_char_p], None)
        glDeleteShader     = _loader.get("glDeleteShader",     [ctypes.c_uint], None)

        if self._prog_id:
            self.destroy()

        vsid = _compile_shader_stage(GL_VERTEX_SHADER,   vert_src)
        fsid = _compile_shader_stage(GL_FRAGMENT_SHADER, frag_src)
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

    def set_1ui(self, name: str, v: int):
        f = _loader.get("glUniform1ui", [ctypes.c_int, ctypes.c_uint], None)
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

    def set_1fv(self, name: str, count: int, float_list):
        """Set a float array. float_list is [v0, v1, ...] (count floats)."""
        f = _loader.get("glUniform1fv",
            [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_float)], None)
        arr = (ctypes.c_float * len(float_list))(*float_list)
        f(self._loc(name), count, arr)

    def set_3iv(self, name: str, count: int, flat_list):
        """Set an ivec3 array. flat_list is [x0,y0,z0, x1,y1,z1, ...] (count*3 ints)."""
        f = _loader.get("glUniform3iv",
            [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)], None)
        arr = (ctypes.c_int * len(flat_list))(*[int(v) for v in flat_list])
        f(self._loc(name), count, arr)

    def set_uniform(self, name: str, glsl_type: str, value):
        """Set a single uniform by name, glsl_type, and value."""
        if glsl_type == "float":
            self.set_1f(name, value)
        elif glsl_type == "vec2":
            self.set_2f(name, value[0], value[1])
        elif glsl_type == "vec3":
            self.set_3f(name, value[0], value[1], value[2])
        elif glsl_type == "mat4":
            if isinstance(value[0], list):
                flat = [0.0] * 16
                for r in range(4):
                    for c in range(4):
                        flat[c * 4 + r] = value[r][c]
                self.set_mat4(name, flat)
            else:
                self.set_mat4(name, value)
        elif glsl_type == "int":
            self.set_int(name, value)
        elif glsl_type == "uint":
            self.set_1ui(name, value)
        elif glsl_type.startswith("vec3["):
            self.set_3fv(name, len(value) // 3, value)
        elif glsl_type.startswith("ivec3["):
            self.set_3iv(name, len(value) // 3, value)
        elif glsl_type.startswith("float["):
            self.set_1fv(name, len(value), value)
        else:
            fld_logger.error(
                f"GLProgram.set_uniform: no setter for GLSL type {glsl_type!r} "
                f"(uniform {name!r}); the uniform keeps its previous value.")

    def set_uniforms_from_ctx(self, ctx_uniforms):
        """Set all uniforms from a GlslContext.uniforms list."""
        for name, glsl_type, value in ctx_uniforms:
            self.set_uniform(name, glsl_type, value)

    def compile_compute(self, src: str):
        """Compile and link a compute-only shader program. Requires GL 4.3+."""
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

        sid = _compile_shader_stage(GL_COMPUTE_SHADER, src, error_label="Compute shader")

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


def bind_image_texture(unit: int, tex_id: int, access: int, fmt: int, layered: bool = False):
    """Bind a texture to an image unit for compute shader imageLoad/imageStore.
    Pass tex_id=0 to unbind.

    layered must be True to bind a whole 3D texture as an `image3D` written with
    3D coordinates; with layered=False only the single 2D slice at layer 0 is
    bound (correct for 2D image targets, wrong for a 3D volume bake — every
    coords.z != 0 store would be dropped).
    """
    glBindImageTexture = _loader.get("glBindImageTexture",
        [ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_uint,
         ctypes.c_int, ctypes.c_uint, ctypes.c_uint], None)
    # (unit, texture, level=0, layered, layer=0, access, format)
    glBindImageTexture(unit, tex_id, 0, 1 if layered else 0, 0, access, fmt)


def memory_barrier(bits: int):
    """Insert a GL memory barrier for compute→sampler synchronization."""
    glMemoryBarrier = _loader.get("glMemoryBarrier", [ctypes.c_uint], None)
    glMemoryBarrier(bits)
