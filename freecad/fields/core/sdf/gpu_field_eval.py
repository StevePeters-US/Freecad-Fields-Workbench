# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import ctypes
import sys
import numpy as np
from freecad.fields.core.gl.gl_texture3d import _loader
from freecad.fields.core.gl.gl_program import GLProgram, GL_COMPUTE_SHADER
from freecad.fields.core.gl.gl_constants import (
    GL_TEXTURE_2D, GL_TEXTURE_3D, GL_TEXTURE0, GL_RED, GL_RGBA, GL_FLOAT,
    GL_HALF_FLOAT, GL_UNSIGNED_BYTE, GL_R16F, GL_R32F, GL_RGBA32F,
    GL_LINEAR, GL_CLAMP_TO_EDGE, GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER,
    GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T, GL_TEXTURE_WRAP_R,
)
from freecad.fields.core import fld_logger

GL_SHADER_STORAGE_BUFFER = 0x90D2
GL_DYNAMIC_DRAW = 0x88E8
GL_DYNAMIC_READ = 0x88E9
GL_BUFFER_UPDATE_BARRIER_BIT = 0x00000200

# Load OpenGL functions
try:
    glGenBuffers = _loader.get("glGenBuffers", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
    glBindBuffer = _loader.get("glBindBuffer", [ctypes.c_uint, ctypes.c_uint], None)
    glBufferData = _loader.get("glBufferData", [ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint], None)
    glBindBufferBase = _loader.get("glBindBufferBase", [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint], None)
    glGetBufferSubData = _loader.get("glGetBufferSubData", [ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_ssize_t, ctypes.c_void_p], None)
    glDeleteBuffers = _loader.get("glDeleteBuffers", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
except Exception:
    glGenBuffers = None
    glBindBuffer = None
    glBufferData = None
    glBindBufferBase = None
    glGetBufferSubData = None
    glDeleteBuffers = None

_gl_context_resolved = False
_gl_context_func = None
_unknown_platform_warned = False

def has_active_context() -> bool:
    global _gl_context_resolved, _gl_context_func, _unknown_platform_warned
    if glGenBuffers is None:
        return False
    if not _gl_context_resolved:
        if sys.platform == "linux":
            for libname in ["libGL.so.1", "libGL.so"]:
                try:
                    lib = ctypes.CDLL(libname)
                    func = lib.glXGetCurrentContext
                    func.argtypes = []
                    func.restype = ctypes.c_void_p
                    _gl_context_func = func
                    break
                except (OSError, AttributeError) as e:
                    fld_logger.debug(f"Failed to load glXGetCurrentContext from {libname}: {e}")
            if _gl_context_func is None:
                fld_logger.warn("glXGetCurrentContext could not be resolved on Linux")
        elif sys.platform == "darwin":
            try:
                lib = ctypes.CDLL("/System/Library/Frameworks/OpenGL.framework/OpenGL")
                func = lib.CGLGetCurrentContext
                func.argtypes = []
                func.restype = ctypes.c_void_p
                _gl_context_func = func
            except (OSError, AttributeError) as e:
                fld_logger.warn(f"Failed to load CGLGetCurrentContext on macOS: {e}")
        elif sys.platform == "win32":
            try:
                lib = ctypes.windll.opengl32
                func = lib.wglGetCurrentContext
                func.argtypes = []
                func.restype = ctypes.c_void_p
                _gl_context_func = func
            except (AttributeError, OSError) as e:
                fld_logger.warn(f"Failed to load wglGetCurrentContext on Windows: {e}")
        else:
            if not _unknown_platform_warned:
                fld_logger.warn(f"Unknown platform '{sys.platform}' for OpenGL context detection")
                _unknown_platform_warned = True
        _gl_context_resolved = True

    if _gl_context_func is None:
        return False

    try:
        ctx = _gl_context_func()
        return ctx is not None and ctx != 0
    except Exception as e:
        fld_logger.warn(f"Error calling get current context function: {e}")
        return False

class GpuFieldEvaluator:
    # Program cache: source_hash -> GLProgram instance
    _program_cache = {}

    def _bind_textures(self, program, ctx, base_unit=1):
        """Upload and bind sampler2D textures to GL texture units starting at base_unit."""
        sampler2d_names = getattr(ctx, "sampler2d_names", [])
        if not sampler2d_names:
            return []

        glGenTextures    = _loader.get("glGenTextures", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        glBindTexture    = _loader.get("glBindTexture", [ctypes.c_uint, ctypes.c_uint], None)
        glActiveTexture  = _loader.get("glActiveTexture", [ctypes.c_uint], None)
        glTexImage2D     = _loader.get("glTexImage2D",
            [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
             ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p], None)
        glTexParameteri  = _loader.get("glTexParameteri", [ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)

        if not (glGenTextures and glBindTexture and glActiveTexture and glTexImage2D):
            return []

        tex_ids = []
        data_map = getattr(ctx, "sampler2d_data_map", {})
        path_map = getattr(ctx, "sampler2d_paths", {})

        for idx, sname in enumerate(sampler2d_names):
            unit = base_unit + idx
            grid_z = None
            if sname in data_map:
                grid_z = data_map[sname]
            elif sname in path_map and path_map[sname]:
                try:
                    from freecad.fields.core.sdf.sdf.heightmap import load_heightmap_grid
                    grid_z = load_heightmap_grid(path_map[sname])
                except Exception as e:
                    fld_logger.warn(f"GpuFieldEvaluator: failed to load heightmap {path_map[sname]}: {e}")

            if grid_z is None:
                continue

            h, w = grid_z.shape
            data = np.ascontiguousarray(grid_z, dtype=np.float32)
            data_ptr = data.ctypes.data_as(ctypes.c_void_p)

            tex = ctypes.c_uint(0)
            glGenTextures(1, ctypes.byref(tex))
            tex_id = tex.value
            tex_ids.append(tex_id)

            glActiveTexture(GL_TEXTURE0 + unit)
            glBindTexture(GL_TEXTURE_2D, tex_id)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_R32F, w, h, 0, GL_RED, GL_FLOAT, data_ptr)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

            if program is not None:
                program.set_1i(sname, unit)

        return tex_ids

    def _unbind_textures(self, tex_ids, base_unit=1):
        if not tex_ids:
            return
        glDeleteTextures = _loader.get("glDeleteTextures", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        glBindTexture    = _loader.get("glBindTexture", [ctypes.c_uint, ctypes.c_uint], None)
        glActiveTexture  = _loader.get("glActiveTexture", [ctypes.c_uint], None)

        for idx, tex_id in enumerate(tex_ids):
            unit = base_unit + idx
            if glActiveTexture:
                glActiveTexture(GL_TEXTURE0 + unit)
            if glBindTexture:
                glBindTexture(GL_TEXTURE_2D, 0)
            if glDeleteTextures and tex_id:
                arr = (ctypes.c_uint * 1)(tex_id)
                glDeleteTextures(1, arr)

        if glActiveTexture:
            glActiveTexture(GL_TEXTURE0)

    # 3D texture cache: (key, sampler_name) -> {"tex_id": int, "key": Any, "uniforms": dict}
    _tex3d_cache = {}

    def _bind_textures3d(self, program, ctx, base_unit=1):
        """Upload and bind sampler3D textures to GL texture units starting at base_unit."""
        sampler3d_names = getattr(ctx, "sampler3d_names", [])
        if not sampler3d_names:
            return []

        glGenTextures    = _loader.get("glGenTextures", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        glBindTexture    = _loader.get("glBindTexture", [ctypes.c_uint, ctypes.c_uint], None)
        glActiveTexture  = _loader.get("glActiveTexture", [ctypes.c_uint], None)
        glTexImage3D     = _loader.get("glTexImage3D",
            [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
             ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p], None)
        glTexParameteri  = _loader.get("glTexParameteri", [ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)

        if not (glGenTextures and glBindTexture and glActiveTexture and glTexImage3D):
            return []

        bound_units = []
        providers = getattr(ctx, "sampler3d_providers", {})
        uni_maps = getattr(ctx, "sampler3d_uniforms", {})

        for idx, sname in enumerate(sampler3d_names):
            unit = base_unit + idx
            provider = providers.get(sname)
            if provider is None:
                continue

            try:
                key = provider.texture3d_key()
            except Exception as e:
                fld_logger.error(f"GpuFieldEvaluator: {sname} key failed: {e}")
                continue
            if key is None:
                continue

            cache_key = (key, sname)
            entry = GpuFieldEvaluator._tex3d_cache.get(cache_key)

            if entry is not None:
                tex_id = entry["tex_id"]
                tdata_uniforms = entry.get("uniforms", {})
            else:
                try:
                    tdata = provider.texture3d_data()
                except Exception as e:
                    fld_logger.error(f"GpuFieldEvaluator: {sname} provider failed: {e}")
                    continue
                if not tdata:
                    continue

                nx, ny, nz = tdata["nx"], tdata["ny"], tdata["nz"]
                fmt = tdata.get("fmt", "r32f")
                raw_bytes = tdata["bytes"]

                if fmt == "r16f":
                    int_fmt, gl_fmt, gl_type = GL_R16F, GL_RED, GL_HALF_FLOAT
                elif fmt == "rgba32f":
                    int_fmt, gl_fmt, gl_type = GL_RGBA32F, GL_RGBA, GL_FLOAT
                elif fmt == "r32f":
                    int_fmt, gl_fmt, gl_type = GL_R32F, GL_RED, GL_FLOAT
                else:
                    int_fmt, gl_fmt, gl_type = GL_RGBA, GL_RGBA, GL_UNSIGNED_BYTE

                data_arr = (ctypes.c_ubyte * len(raw_bytes)).from_buffer_copy(raw_bytes)
                data_ptr = ctypes.cast(data_arr, ctypes.c_void_p)

                tex = ctypes.c_uint(0)
                glGenTextures(1, ctypes.byref(tex))
                tex_id = tex.value

                glBindTexture(GL_TEXTURE_3D, tex_id)
                glTexImage3D(GL_TEXTURE_3D, 0, int_fmt, nx, ny, nz, 0, gl_fmt, gl_type, data_ptr)
                glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
                glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
                glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
                glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
                glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_R, GL_CLAMP_TO_EDGE)

                tdata_uniforms = tdata.get("uniforms", {})
                entry = {"tex_id": tex_id, "key": key, "uniforms": tdata_uniforms}
                GpuFieldEvaluator._tex3d_cache[cache_key] = entry

            glActiveTexture(GL_TEXTURE0 + unit)
            glBindTexture(GL_TEXTURE_3D, tex_id)
            bound_units.append(unit)

            if program is not None:
                uni_map = uni_maps.get(sname, {})
                for suffix, (utype, val) in tdata_uniforms.items():
                    uname = uni_map.get(suffix)
                    if uname is None:
                        fld_logger.error(
                            f"GpuFieldEvaluator: {sname} supplied uniform '{suffix}' that its "
                            f"to_glsl never registered with ctx.sampler3d_uniform()")
                        continue
                    program.set_uniform(uname, utype, val)
                program.set_1i(sname, unit)

        return bound_units

    def _unbind_textures3d(self, bound_units, base_unit=1):
        if not bound_units:
            return
        glBindTexture    = _loader.get("glBindTexture", [ctypes.c_uint, ctypes.c_uint], None)
        glActiveTexture  = _loader.get("glActiveTexture", [ctypes.c_uint], None)

        for unit in bound_units:
            if glActiveTexture:
                glActiveTexture(GL_TEXTURE0 + unit)
            if glBindTexture:
                glBindTexture(GL_TEXTURE_3D, 0)

        if glActiveTexture:
            glActiveTexture(GL_TEXTURE0)

    def __init__(self, field):
        self.field = field
        self.program = None
        self.ctx = None
        self._source = None
        self._build_shader()

    def _generate_source(self):
        """Regenerate shader source + ctx from the field's current state.
        Fields may bake topology into the source (e.g. SdfCageField), so
        the source can change between evaluate() calls on the same field."""
        from freecad.fields.core.sdf.glsl_compiler import GlslContext, build_compute_shader

        ctx = GlslContext(prefix="gpu_eval")
        expr = self.field.to_glsl(ctx, "p")
        shader_src = build_compute_shader(expr, ctx, mode="point")
        return shader_src, ctx

    def _build_shader(self):
        shader_src, ctx = self._generate_source()
        source_hash = hash(shader_src)
        if source_hash in GpuFieldEvaluator._program_cache:
            self.program = GpuFieldEvaluator._program_cache[source_hash]
        else:
            self.program = GLProgram()
            self.program.compile_compute(shader_src)
            GpuFieldEvaluator._program_cache[source_hash] = self.program

        self.ctx = ctx
        self._source = shader_src

    def evaluate(self, points_np) -> np.ndarray:
        N = len(points_np)
        if N == 0:
            return np.zeros(0, dtype=np.float32)

        # Refresh uniform values (and the program itself if the field's
        # generated source changed, e.g. a cage topology edit)
        shader_src, temp_ctx = self._generate_source()
        if shader_src != self._source:
            source_hash = hash(shader_src)
            if source_hash in GpuFieldEvaluator._program_cache:
                self.program = GpuFieldEvaluator._program_cache[source_hash]
            else:
                self.program = GLProgram()
                self.program.compile_compute(shader_src)
                GpuFieldEvaluator._program_cache[source_hash] = self.program
            self._source = shader_src

        self.program.use()
        self.program.set_uniforms_from_ctx(temp_ctx.uniforms)
        sampler2d_names = getattr(temp_ctx, "sampler2d_names", [])
        tex2d_ids = self._bind_textures(self.program, temp_ctx, base_unit=1)
        base_3d = 1 + len(sampler2d_names)
        tex3d_ids = self._bind_textures3d(self.program, temp_ctx, base_unit=base_3d)

        self.program.set_1i("u_num_points", N)
        self.program.set_1i("u_cage_quad_iters", 10)
        self.program.set_1i("u_cage_tri_iters", 10)
        
        # Pad to vec4
        points_padded = np.hstack([points_np.astype(np.float32), np.ones((N, 1), dtype=np.float32)])
        
        ssbo_names = getattr(temp_ctx, "ssbo_names", [])
        K = len(ssbo_names)
        
        buf_ids = (ctypes.c_uint * (2 + K))()
        glGenBuffers(2 + K, buf_ids)
        ssbo_in = buf_ids[0]
        ssbo_out = buf_ids[1]
        
        glBindBuffer(GL_SHADER_STORAGE_BUFFER, ssbo_in)
        glBufferData(GL_SHADER_STORAGE_BUFFER, points_padded.nbytes, points_padded.ctypes.data, GL_DYNAMIC_DRAW)
        glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 0, ssbo_in)
        
        out_data = np.zeros(N, dtype=np.float32)
        glBindBuffer(GL_SHADER_STORAGE_BUFFER, ssbo_out)
        glBufferData(GL_SHADER_STORAGE_BUFFER, out_data.nbytes, None, GL_DYNAMIC_READ)
        glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 1, ssbo_out)
        
        # Bind SSBOs
        for idx, sname in enumerate(ssbo_names):
            field = temp_ctx.ssbo_fields.get(sname)
            if field:
                # A field that packs its own buffers says so once, through this hook.
                # The name-sniffing chain that used to follow it dispatched to seven
                # `pack_*` methods that lived only on `SdfNurbsSurfaceField`, deleted
                # 2026-09-01 (RA-012 resolved as "retire").
                packer = getattr(field, "ssbo_packer", None)
                if packer is None:
                    fld_logger.warn(
                        f"GpuFieldEvaluator: {sname} was registered with ctx.ssbo() by "
                        f"{type(field).__name__}, which has no ssbo_packer(); "
                        f"the buffer will be left unbound.")
                    continue
                packed_data = packer(sname)
                buf_id = buf_ids[2 + idx]
                glBindBuffer(GL_SHADER_STORAGE_BUFFER, buf_id)
                glBufferData(GL_SHADER_STORAGE_BUFFER, packed_data.nbytes, packed_data.ctypes.data, GL_DYNAMIC_DRAW)
                glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 4 + idx, buf_id)
        
        group_size = 256
        num_groups = (N + group_size - 1) // group_size
        self.program.dispatch_compute(num_groups, 1, 1)
        
        from freecad.fields.core.gl.gl_program import memory_barrier
        memory_barrier(GL_BUFFER_UPDATE_BARRIER_BIT)
        
        glBindBuffer(GL_SHADER_STORAGE_BUFFER, ssbo_out)
        glGetBufferSubData(GL_SHADER_STORAGE_BUFFER, 0, out_data.nbytes, out_data.ctypes.data)
        
        glDeleteBuffers(2 + K, buf_ids)
        self._unbind_textures(tex2d_ids, base_unit=1)
        self._unbind_textures3d(tex3d_ids, base_unit=base_3d)
        
        return out_data

    @staticmethod
    def is_available() -> bool:
        if not has_active_context():
            return False
        from freecad.fields.core.gl.gl_compute import check_compute_support
        return check_compute_support()

    def _generate_bake_source(self):
        """Regenerate shader source + ctx for direct baking to a 3D texture."""
        from freecad.fields.core.sdf.glsl_compiler import GlslContext, build_compute_shader

        ctx = GlslContext(prefix="gpu_bake")
        expr = self.field.to_glsl(ctx, "p")
        shader_src = build_compute_shader(expr, ctx, mode="bake")
        return shader_src, ctx

    def _get_bake_program(self):
        shader_src, ctx = self._generate_bake_source()
        source_hash = hash(shader_src)
        if source_hash in GpuFieldEvaluator._program_cache:
            prog = GpuFieldEvaluator._program_cache[source_hash]
        else:
            prog = GLProgram()
            prog.compile_compute(shader_src)
            GpuFieldEvaluator._program_cache[source_hash] = prog
        return prog, shader_src, ctx

