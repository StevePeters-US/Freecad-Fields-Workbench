# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""GL texture cache for heightmaps (images and 2D arrays).

Extracted from FldSceneVoxelRenderer (RS-010). Owns GL texture allocation,
uploading, sampler binding planning, and reachability garbage collection.
"""
import ctypes
import hashlib
import numpy as np
from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf_field import SdfField
from freecad.fields.core.sdf.sdf.heightmap import load_heightmap_grid


def get_all_heightmap_paths(field, _seen=None):
    """Every texture path anything in this field tree samples."""
    paths = set()
    if field is None:
        return paths
    if _seen is None:
        _seen = set()
    if id(field) in _seen:
        return paths
    _seen.add(id(field))

    for name in ("heightmap_path", "image_path"):
        val = getattr(field, name, None)
        if val:
            paths.add(val)

    def _walk(val):
        if isinstance(val, SdfField):
            paths.update(get_all_heightmap_paths(val, _seen))
        elif isinstance(val, (list, tuple)):
            for item in val:
                _walk(item)

    for val in getattr(field, "__dict__", {}).values():
        _walk(val)
    return paths


class HeightmapTextureCache:
    def __init__(self):
        self._textures = {}               # key -> OpenGL texture ID
        self._pending_paths = {}          # path -> path
        self._pending_arrays = {}         # key -> grid32 array
        self._bindings_to_make = []       # [(sampler_name, key), ...]
        self._active_bindings = []        # [(sampler_name, tex_id), ...]

    @property
    def textures(self):
        """Read-only view for backward compatibility / tests."""
        return self._textures

    @property
    def active_bindings(self):
        return self._active_bindings

    @property
    def bindings_to_make(self):
        return self._bindings_to_make

    def queue_path(self, path):
        if path:
            self._pending_paths[path] = path

    def queue_array(self, key, arr):
        if key and arr is not None:
            self._pending_arrays[key] = arr

    def clear_bindings_to_make(self):
        self._bindings_to_make = []

    def plan_bindings(self, analytical_data):
        """Collect sampler names and texture keys from analytical_data, queuing pending arrays/paths."""
        hmap_bindings = []
        for qd in analytical_data:
            for sname, path in qd["ctx"].sampler2d_paths.items():
                if path:
                    if path not in self._textures and path not in self._pending_paths:
                        self._pending_paths[path] = path
                    hmap_bindings.append((sname, path))
            for sname, arr in qd["ctx"].sampler2d_data_map.items():
                if arr is not None:
                    grid32 = np.ascontiguousarray(arr, dtype=np.float32)
                    digest = hashlib.sha1(grid32.tobytes()).hexdigest()[:16]
                    key = f"hmap_{grid32.shape[0]}x{grid32.shape[1]}_{digest}"
                    if key not in self._textures and key not in self._pending_arrays:
                        self._pending_arrays[key] = grid32
                    hmap_bindings.append((sname, key))
        self._bindings_to_make = hmap_bindings

        self._active_bindings = [
            (sname, self._textures[key])
            for sname, key in hmap_bindings
            if key in self._textures and self._textures[key]
        ]

    def upload_pending(self):
        """Upload queued heightmap images/arrays as GL textures. Must be called inside GL context."""
        if not self._pending_paths and not self._pending_arrays:
            return
        from freecad.fields.core.gl.gl_texture3d import _loader

        GL_TEXTURE_2D         = 0x0DE1
        GL_RED                = 0x1903
        GL_R32F               = 0x822E
        GL_FLOAT              = 0x1406
        GL_LINEAR             = 0x2601
        GL_CLAMP_TO_EDGE      = 0x812F
        GL_TEXTURE_MIN_FILTER = 0x2801
        GL_TEXTURE_MAG_FILTER = 0x2800
        GL_TEXTURE_WRAP_S     = 0x2802
        GL_TEXTURE_WRAP_T     = 0x2803

        glGenTextures    = _loader.get("glGenTextures",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        glDeleteTextures = _loader.get("glDeleteTextures",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        glBindTexture    = _loader.get("glBindTexture",
            [ctypes.c_uint, ctypes.c_uint], None)
        glTexImage2D     = _loader.get("glTexImage2D",
            [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
             ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p], None)
        glTexParameteri  = _loader.get("glTexParameteri",
            [ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)

        def _upload_data(key, grid_z):
            if key in self._textures:
                old_id = self._textures.pop(key)
                arr = (ctypes.c_uint * 1)(old_id)
                if glDeleteTextures:
                    glDeleteTextures(1, arr)
            if grid_z is None:
                return
            h, w = grid_z.shape
            data = np.ascontiguousarray(grid_z, dtype=np.float32)
            data_ptr = data.ctypes.data_as(ctypes.c_void_p)

            tex = ctypes.c_uint(0)
            glGenTextures(1, ctypes.byref(tex))
            tex_id = tex.value
            glBindTexture(GL_TEXTURE_2D, tex_id)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_R32F, w, h, 0, GL_RED, GL_FLOAT, data_ptr)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glBindTexture(GL_TEXTURE_2D, 0)
            self._textures[key] = tex_id

        for path in list(self._pending_paths.keys()):
            if not path:
                continue
            try:
                grid_z = load_heightmap_grid(path)
                if grid_z is None:
                    continue
                _upload_data(path, grid_z)
            except Exception as e:
                fld_logger.warn(f"HeightmapTextureCache: failed to load heightmap {path}: {e}")

        for key, grid_z in list(self._pending_arrays.items()):
            if not key or grid_z is None:
                continue
            try:
                _upload_data(key, grid_z)
            except Exception as e:
                fld_logger.warn(f"HeightmapTextureCache: failed to upload heightmap array for {key}: {e}")

        self._pending_paths = {}
        self._pending_arrays = {}

    def gc(self, registered_fields):
        """Delete GL textures for heightmaps no longer referenced by any registered field or binding."""
        needed_paths = set()
        for field, _ in registered_fields:
            needed_paths.update(get_all_heightmap_paths(field))
        needed_paths.update(
            path for _, path in self._bindings_to_make if path)

        from freecad.fields.core.gl.gl_texture3d import _loader
        glDeleteTextures = _loader.get("glDeleteTextures",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)

        for path in list(self._textures.keys()):
            if path not in needed_paths:
                tex_id = self._textures.pop(path)
                arr = (ctypes.c_uint * 1)(tex_id)
                if glDeleteTextures:
                    try:
                        glDeleteTextures(1, arr)
                    except Exception as e:
                        fld_logger.render_debug(f"HeightmapTextureCache: failed to delete texture {tex_id} for path {path}: {e}")

    def destroy_all(self):
        """Delete all textures on teardown."""
        from freecad.fields.core.gl.gl_texture3d import _loader
        glDeleteTextures = _loader.get("glDeleteTextures",
            [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        if glDeleteTextures:
            _failed = 0
            for tex_id in self._textures.values():
                try:
                    arr = (ctypes.c_uint * 1)(tex_id)
                    glDeleteTextures(1, arr)
                except Exception as e:
                    _failed += 1
                    _last_err = e
            if _failed:
                fld_logger.warn(
                    f"HeightmapTextureCache.destroy_all: {_failed} of "
                    f"{len(self._textures)} glDeleteTextures calls failed "
                    f"(last: {_last_err}); those textures leak on the GPU."
                )
        self._textures.clear()
        self._pending_paths.clear()
        self._pending_arrays.clear()
        self._bindings_to_make.clear()
        self._active_bindings.clear()
