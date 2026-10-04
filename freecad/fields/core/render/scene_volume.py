# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Scene-wide voxel distance volume for the voxel render path (VX-003, VX-004, VX-005).

Three GL_TEXTURE_3Ds covering the union of all visible field bounding boxes:
r16f distance, r16ui field ID, and rgba8_snorm baked analytic normal (VR-005).
Distances are clamped to +/- band.
"""
import ctypes
import FreeCAD
from freecad.fields.core import fld_logger
from freecad.fields.core.gl.gl_texture3d import _loader
from freecad.fields.core.gl.gl_program import GLProgram, bind_image_texture, memory_barrier, GL_SHADER_IMAGE_ACCESS_BARRIER_BIT

GL_TEXTURE_3D   = 0x806F
GL_R32F         = 0x822E
GL_R16F         = 0x822D
GL_R16UI        = 0x8234
GL_RGBA8_SNORM  = 0x8F97
GL_RED          = 0x1903
GL_FLOAT        = 0x1406
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_TEXTURE_WRAP_S = 0x2802
GL_TEXTURE_WRAP_T = 0x2803
GL_TEXTURE_WRAP_R = 0x8072
GL_NEAREST      = 0x2600
GL_LINEAR       = 0x2601
GL_CLAMP_TO_EDGE = 0x812F
GL_READ_ONLY   = 0x88B8
GL_WRITE_ONLY  = 0x88B9
GL_READ_WRITE  = 0x88BA

from freecad.fields.core.render.volume_fit import (
    MAX_RESOLUTION, VOLUME_SLACK_FRAC, VOLUME_SHRINK_FRAC, _fit_box,
)
from freecad.fields.core.render.tree_item_status import _mark_tree_item_failed
# Re-exported for test_scene_volume.py, test_march_value_scale.py,
# and bench_lipschitz_reach_run.py, which import these directly from
# scene_volume rather than from volume_bake (SVS-003 moved their definitions).
from freecad.fields.core.render.volume_bake import SCAN_BLOCK, field_lipschitz

_CLEAR_SRC = """#version 430
layout(local_size_x = 8, local_size_y = 8, local_size_z = 8) in;
layout(r16f, binding = 0) writeonly uniform image3D u_volume;
layout(r16ui, binding = 1) writeonly uniform uimage3D u_id_volume;
layout(rgba8_snorm, binding = 2) writeonly uniform image3D u_norm_volume;
uniform float u_value;
uniform uint  u_id_value;
uniform ivec3 u_sub_offset;
uniform ivec3 u_sub_count;
void main() {
    ivec3 lid = ivec3(gl_GlobalInvocationID);
    if (any(greaterThanEqual(lid, u_sub_count))) return;
    ivec3 gid = u_sub_offset + lid;
    ivec3 size = imageSize(u_volume);
    if (any(lessThan(gid, ivec3(0))) || any(greaterThanEqual(gid, size))) return;
    imageStore(u_volume, gid, vec4(u_value, 0.0, 0.0, 0.0));
    imageStore(u_id_volume, gid, uvec4(u_id_value, 0u, 0u, 0u));
    imageStore(u_norm_volume, gid, vec4(0.0, 0.0, 0.0, 0.0));
}
"""


class SceneVolume:
    _clear_prog = None

    def __init__(self):
        from freecad.fields.core.render.compute_program_cache import ComputeProgramCache
        self.tex_id = 0
        self.id_tex_id = 0
        self.norm_tex_id = 0
        self.nx = self.ny = self.nz = 0
        self.vol_min = FreeCAD.Vector(0, 0, 0)
        self.vol_max = FreeCAD.Vector(0, 0, 0)
        self.step = FreeCAD.Vector(1, 1, 1)
        self.band = 0.0
        # Largest lipschitz() among the fields last baked. The volume stores each
        # field's own value in millimetres, and a field may over-state distance by
        # up to its bound, so the march divides its STEP (never its hit test) by
        # this. 1.0 until something is baked.
        self.lip = 1.0
        # True when nothing in the texture can be trusted, so the next bake must
        # be a full one.
        self.dirty = False
        # What the current box was fitted for. A change in either invalidates the
        # fit even when the scene bbox still sits inside the box.
        self._fit_res = 0
        self._fit_band_voxels = 0
        self._fit_slack_frac = -1.0
        self._bake_cache = ComputeProgramCache(max_entries=256, dump_path="/tmp/fld_scene_bake_debug.glsl")
        self._scan_cache = ComputeProgramCache(max_entries=256, dump_path="/tmp/fld_scene_scan_debug.glsl")
        self.active_fields = []
        self.active_data = []

    def voxel_of(self, world_pt):
        """Floor voxel index containing a world point (may be out of range)."""
        import math
        return (int(math.floor((world_pt.x - self.vol_min.x) / self.step.x)),
                int(math.floor((world_pt.y - self.vol_min.y) / self.step.y)),
                int(math.floor((world_pt.z - self.vol_min.z) / self.step.z)))

    def ensure(self, bbox_min, bbox_max, resolution, band_voxels,
               slack_frac=VOLUME_SLACK_FRAC):
        """(Re)allocate the texture for this scene box. Returns True if geometry changed."""
        
        # Check current fit
        extent = FreeCAD.Vector(bbox_max.x - bbox_min.x,
                                bbox_max.y - bbox_min.y,
                                bbox_max.z - bbox_min.z)
        longest = max(extent.x, extent.y, extent.z, 1e-6)
        cell = longest / resolution
        pad = cell * (band_voxels + 1)
        req_min = FreeCAD.Vector(bbox_min.x - pad, bbox_min.y - pad, bbox_min.z - pad)
        req_max = FreeCAD.Vector(bbox_max.x + pad, bbox_max.y + pad, bbox_max.z + pad)

        if (self._fit_res and resolution == self._fit_res
                and band_voxels == self._fit_band_voxels
                and slack_frac == self._fit_slack_frac
                and req_min.x >= self.vol_min.x and req_min.y >= self.vol_min.y
                and req_min.z >= self.vol_min.z
                and req_max.x <= self.vol_max.x and req_max.y <= self.vol_max.y
                and req_max.z <= self.vol_max.z):
            box_longest = max(self.vol_max.x - self.vol_min.x,
                              self.vol_max.y - self.vol_min.y,
                              self.vol_max.z - self.vol_min.z, 1e-6)
            req_longest = max(req_max.x - req_min.x, req_max.y - req_min.y,
                              req_max.z - req_min.z)
            if req_longest >= VOLUME_SHRINK_FRAC * box_longest:
                # Mapping unchanged: every voxel still means what it meant last
                # frame, so self.dirty is left exactly as the caller found it.
                return False

        # Refitting. Sizing is computed via _fit_box.
        vmin, vmax, nx, ny, nz, step = _fit_box(bbox_min, bbox_max, resolution, band_voxels, slack_frac=slack_frac)

        changed = (nx, ny, nz) != (self.nx, self.ny, self.nz)
        moved = ((vmin.x, vmin.y, vmin.z) != (self.vol_min.x, self.vol_min.y, self.vol_min.z)
                 or (vmax.x, vmax.y, vmax.z) != (self.vol_max.x, self.vol_max.y, self.vol_max.z))

        self.nx, self.ny, self.nz = nx, ny, nz
        self.vol_min, self.vol_max = vmin, vmax
        self.step = step
        self.band = band_voxels * max(self.step.x, self.step.y, self.step.z)

        scene_longest = max(bbox_max.x - bbox_min.x,
                            bbox_max.y - bbox_min.y,
                            bbox_max.z - bbox_min.z, 1e-6)
        box_longest = max(vmax.x - vmin.x, vmax.y - vmin.y, vmax.z - vmin.z, 1e-6)
        fill = scene_longest / box_longest
        fld_logger.render_debug(
            f"SceneVolume: refit {nx}x{ny}x{nz} @ res {resolution}, "
            f"voxel {max(self.step.x, self.step.y, self.step.z):.3f} mm, "
            f"scene fills {fill * 100.0:.0f}% of the box axis "
            f"({fill ** 3 * 100.0:.0f}% of its volume)")

        if moved:
            self.dirty = True
        if changed or not self.tex_id:
            self._allocate()
            fld_logger.render_debug(
                f"SceneVolume: {nx}x{ny}x{nz} r16f+rgba8_snorm "
                f"({nx*ny*nz*8/1048576.0:.1f} MB), voxel "
                f"{self.step.x:.3f}x{self.step.y:.3f}x{self.step.z:.3f} mm, "
                f"band {self.band:.3f} mm")
        # Recorded last: _allocate -> destroy_textures clears the fit, so setting
        # it any earlier would be undone and every call would refit.
        self._fit_res = resolution
        self._fit_band_voxels = band_voxels
        self._fit_slack_frac = slack_frac
        return changed or moved

    def _allocate(self):
        try:
            glGenTextures  = _loader.get("glGenTextures", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
            glBindTexture  = _loader.get("glBindTexture", [ctypes.c_uint, ctypes.c_uint], None)
            glTexParameteri = _loader.get("glTexParameteri", [ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)
            glTexStorage3D = _loader.get("glTexStorage3D",
                [ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int], None)
        except Exception:
            glGenTextures = None
            glBindTexture = None
            glTexParameteri = None
            glTexStorage3D = None

        self.destroy_textures()
        if glGenTextures and glTexStorage3D:
            tids = (ctypes.c_uint * 3)()
            glGenTextures(3, tids)
            self.tex_id = tids[0]
            self.id_tex_id = tids[1]
            self.norm_tex_id = tids[2]

            glBindTexture(GL_TEXTURE_3D, self.tex_id)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_R, GL_CLAMP_TO_EDGE)
            glTexStorage3D(GL_TEXTURE_3D, 1, GL_R16F, self.nx, self.ny, self.nz)

            glBindTexture(GL_TEXTURE_3D, self.id_tex_id)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_R, GL_CLAMP_TO_EDGE)
            glTexStorage3D(GL_TEXTURE_3D, 1, GL_R16UI, self.nx, self.ny, self.nz)

            glBindTexture(GL_TEXTURE_3D, self.norm_tex_id)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_3D, GL_TEXTURE_WRAP_R, GL_CLAMP_TO_EDGE)
            glTexStorage3D(GL_TEXTURE_3D, 1, GL_RGBA8_SNORM, self.nx, self.ny, self.nz)

            glBindTexture(GL_TEXTURE_3D, 0)
        self.dirty = True

    def clear(self, sub_offset=None, sub_count=None):
        """Fill the volume (or a sub-box) with +band."""
        if not self.tex_id:
            return
        if SceneVolume._clear_prog is None:
            p = GLProgram()
            p.compile_compute(_CLEAR_SRC)
            SceneVolume._clear_prog = p
        prog = SceneVolume._clear_prog
        prog.use()
        prog.set_1f("u_value", float(self.band))
        prog.set_1ui("u_id_value", 65535)

        if sub_offset is not None and sub_count is not None:
            off = list(sub_offset)
            cnt = list(sub_count)
        else:
            off = [0, 0, 0]
            cnt = [self.nx, self.ny, self.nz]

        prog.set_3iv("u_sub_offset", 1, off)
        prog.set_3iv("u_sub_count", 1, cnt)

        bind_image_texture(0, self.tex_id, GL_READ_WRITE, GL_R16F, layered=True)
        bind_image_texture(1, self.id_tex_id, GL_READ_WRITE, GL_R16UI, layered=True)
        bind_image_texture(2, self.norm_tex_id, GL_READ_WRITE, GL_RGBA8_SNORM, layered=True)
        prog.dispatch_compute((cnt[0] + 7) // 8, (cnt[1] + 7) // 8, (cnt[2] + 7) // 8)
        memory_barrier(GL_SHADER_IMAGE_ACCESS_BARRIER_BIT)
        bind_image_texture(0, 0, GL_READ_WRITE, GL_R16F)
        bind_image_texture(1, 0, GL_READ_WRITE, GL_R16UI)
        bind_image_texture(2, 0, GL_READ_WRITE, GL_RGBA8_SNORM)

    def _sub_box(self, world_min, world_max, pad=0.0):
        """World AABB -> (ox, oy, oz, cx, cy, cz) clipped to the volume, or None.

        The cleared region must be a superset of every region any field may write.
        Both are derived from the same padded-bbox function, so they cannot drift.
        """
        lo = FreeCAD.Vector(world_min.x - pad, world_min.y - pad, world_min.z - pad)
        hi = FreeCAD.Vector(world_max.x + pad, world_max.y + pad, world_max.z + pad)
        ox, oy, oz = self.voxel_of(lo)
        hx, hy, hz = self.voxel_of(hi)
        ox, oy, oz = max(ox, 0), max(oy, 0), max(oz, 0)
        cx = min(hx + 2, self.nx) - ox
        cy = min(hy + 2, self.ny) - oy
        cz = min(hz + 2, self.nz) - oz
        if cx <= 0 or cy <= 0 or cz <= 0:
            return None
        return (ox, oy, oz, cx, cy, cz)

    def bake(self, analytical_data, renderer=None, region=None):
        """Bake visible fields into the volume (full or incremental region).

        See volume_bake.bake_scene -- this method exists only so callers keep
        calling SceneVolume().bake(...) unchanged.
        """
        from freecad.fields.core.render.volume_bake import bake_scene
        bake_scene(self, analytical_data, renderer=renderer, region=region)

    def destroy_textures(self):
        """Free the 3D textures only. Cached bake programs stay valid — they are
        keyed by source and the grid dimensions are uniforms, not literals."""
        # Forget the fit as well, or ensure() would reuse a box whose texture no
        # longer exists and return early without reallocating it. _allocate()
        # calls this before making the new textures and then records the fit
        # again, so the ordering there is unaffected.
        self._fit_res = 0
        self._fit_band_voxels = 0
        if self.tex_id or self.id_tex_id or self.norm_tex_id:
            glDeleteTextures = _loader.get("glDeleteTextures",
                [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
            if glDeleteTextures:
                tids = (ctypes.c_uint * 3)(self.tex_id, self.id_tex_id, self.norm_tex_id)
                glDeleteTextures(3, tids)
            self.tex_id = 0
            self.id_tex_id = 0
            self.norm_tex_id = 0

    @classmethod
    def destroy_programs(cls):
        """Drop the shared compute programs. Class-level, because that is where
        they live -- assigning through an instance only shadows them."""
        for name in ("_clear_prog",):
            prog = getattr(cls, name, None)
            if prog is not None:
                try:
                    if hasattr(prog, "destroy"):
                        prog.destroy()
                except Exception as e:
                    fld_logger.render_debug(f"SceneVolume.destroy_programs: {name} destroy failed: {e}")
            setattr(cls, name, None)

    def destroy(self):
        if getattr(self, "_bake_cache", None) is not None:
            self._bake_cache.destroy_all()
        if getattr(self, "_scan_cache", None) is not None:
            self._scan_cache.destroy_all()
        SceneVolume.destroy_programs()
        self.destroy_textures()
