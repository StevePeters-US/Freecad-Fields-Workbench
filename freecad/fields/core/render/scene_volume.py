# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Scene-wide voxel distance volume for the voxel render path (VX-003, VX-004, VX-005).

Three GL_TEXTURE_3Ds covering the union of all visible field bounding boxes:
r16f distance, r16ui field ID, and rgba8_snorm baked analytic normal (VR-005).
Distances are clamped to +/- band.
"""
import ctypes
import math
import time
import FreeCAD
from freecad.fields.core import fld_logger
from freecad.fields.core.gl.gl_texture3d import _loader
from freecad.fields.core.gl.gl_program import GLProgram, bind_image_texture, memory_barrier, GL_R32F, GL_SHADER_IMAGE_ACCESS_BARRIER_BIT

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

GL_SHADER_STORAGE_BUFFER = 0x90D2
GL_DYNAMIC_DRAW          = 0x88E8

# Load buffer functions if needed for SSBOs during bake
try:
    glGenBuffers = _loader.get("glGenBuffers", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
    glBindBuffer = _loader.get("glBindBuffer", [ctypes.c_uint, ctypes.c_uint], None)
    glBufferData = _loader.get("glBufferData", [ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint], None)
    glBindBufferBase = _loader.get("glBindBufferBase", [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint], None)
    glDeleteBuffers = _loader.get("glDeleteBuffers", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
except Exception:
    glGenBuffers = None
    glBindBuffer = None
    glBufferData = None
    glBindBufferBase = None
    glDeleteBuffers = None

MAX_RESOLUTION = 384

# Block edge, in voxels, for the per-field saturation scan (VR-020/VR-021).
# 8 to match the bake shader's local_size, so one block is exactly one
# workgroup and `lid / 8` is the workgroup index.
SCAN_BLOCK = 8

# SdfField.lipschitz() returns 1.0 on the base class, so a field that is not a
# true distance field and never overrode it reports a bound it does not meet --
# and an overstated bound deletes geometry with nothing logged. Keep this in
# step with the dev repo's test_volume_oracle.py, which proves the criterion
# using the same factor.
LIPSCHITZ_SAFETY = 2.0


def field_lipschitz(fld):
    """`fld.lipschitz()` as a usable number: >= 1.0, never NaN, never raising.

    A field that has no bound, or whose bound blows up on a degenerate parameter,
    must fall back to 1.0 rather than take the bake and the march down with it.
    """
    try:
        lip = float(fld.lipschitz())
    except Exception:
        return 1.0
    if not (lip == lip) or lip in (float("inf"), float("-inf")):
        return 1.0
    return max(lip, 1.0)


GL_SHADER_STORAGE_BARRIER_BIT = 0x00002000
GL_SHADER_IMAGE_ACCESS_BARRIER_BIT = 0x00000020
GL_TEXTURE_FETCH_BARRIER_BIT = 0x00000008


# How much empty margin to leave around the scene when the volume box is
# (re)fitted, as a fraction of the scene's own extent.
#
# The box used to be refitted to the scene bbox exactly, which meant an extrude
# drag -- whose whole job is to grow the bbox -- moved the world->voxel mapping
# on nearly every tick, set `dirty`, and forced a full bake. Growing in slack
# steps instead lets the mapping hold still across most of a drag, which is the
# precondition for a region bake being legal at all.
#
# The cost is resolution: the same voxel budget now spans a box this much larger,
# so voxels are ~SLACK bigger. That is a linear quality loss bought against a
# cubic bake saving.
VOLUME_SLACK_FRAC = 0.25

# Refit when the scene has shrunk to less than this fraction of the box, so a
# deleted or collapsed feature gives its resolution back. It has to sit well
# below 1/(1 + SLACK) or a fresh refit would immediately qualify as too loose.
VOLUME_SHRINK_FRAC = 0.5

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


def _fit_box(bbox_min, bbox_max, resolution, band_voxels, slack_frac=VOLUME_SLACK_FRAC):
    """Pure sizing function returning (vmin, vmax, nx, ny, nz, step)."""
    resolution = max(32, min(int(resolution), MAX_RESOLUTION))
    extent = FreeCAD.Vector(bbox_max.x - bbox_min.x,
                            bbox_max.y - bbox_min.y,
                            bbox_max.z - bbox_min.z)
    longest = max(extent.x, extent.y, extent.z, 1e-6)
    cell = longest / resolution
    pad = cell * (band_voxels + 1)
    req_min = FreeCAD.Vector(bbox_min.x - pad, bbox_min.y - pad, bbox_min.z - pad)
    req_max = FreeCAD.Vector(bbox_max.x + pad, bbox_max.y + pad, bbox_max.z + pad)

    slack = slack_frac * max(req_max.x - req_min.x,
                             req_max.y - req_min.y,
                             req_max.z - req_min.z)
    vmin = FreeCAD.Vector(req_min.x - slack, req_min.y - slack, req_min.z - slack)
    vmax = FreeCAD.Vector(req_max.x + slack, req_max.y + slack, req_max.z + slack)

    cell = max(vmax.x - vmin.x, vmax.y - vmin.y, vmax.z - vmin.z, 1e-6) / resolution

    nx = max(1, min(resolution, int((vmax.x - vmin.x) / cell) + 1))
    ny = max(1, min(resolution, int((vmax.y - vmin.y) / cell) + 1))
    nz = max(1, min(resolution, int((vmax.z - vmin.z) / cell) + 1))

    step = FreeCAD.Vector((vmax.x - vmin.x) / nx,
                          (vmax.y - vmin.y) / ny,
                          (vmax.z - vmin.z) / nz)
    return vmin, vmax, nx, ny, nz, step


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
        glGenTextures  = _loader.get("glGenTextures", [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
        glBindTexture  = _loader.get("glBindTexture", [ctypes.c_uint, ctypes.c_uint], None)
        glTexParameteri = _loader.get("glTexParameteri", [ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)
        glTexStorage3D = _loader.get("glTexStorage3D",
            [ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int], None)

        self.destroy_textures()
        if glGenTextures:
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
        """Bake visible fields into the volume (full or incremental region)."""
        from freecad.fields.core.sdf.glsl_compiler import build_compute_shader
        from freecad.fields.core.sdf.gpu_field_eval import GpuFieldEvaluator

        if len(analytical_data) >= 65535:
            fld_logger.error(f"SceneVolume: field count ({len(analytical_data)}) exceeds r16ui max capacity of 65535")

        t0 = time.perf_counter()

        # A region bake only clears and rewrites its own sub-box; every other
        # voxel keeps whatever it already held. That is only sound while the
        # world->voxel mapping is unchanged, so ensure() vetoes it via self.dirty
        # rather than trusting the caller to notice.
        if region is not None and self.dirty:
            fld_logger.render_debug(
                "SceneVolume.bake: volume geometry changed -> region ignored, baking in full")
            region = None

        reg_box = None
        if region is not None:
            r_min, r_max = region
            reg_box = self._sub_box(r_min, r_max, pad=self.band)
            if reg_box is not None:
                ox, oy, oz, cx, cy, cz = reg_box
                self.clear(sub_offset=(ox, oy, oz), sub_count=(cx, cy, cz))
            else:
                self.clear()
        else:
            self.clear()

        bind_image_texture(0, self.tex_id, GL_READ_WRITE, GL_R16F, layered=True)
        bind_image_texture(1, self.id_tex_id, GL_READ_WRITE, GL_R16UI, layered=True)
        bind_image_texture(2, self.norm_tex_id, GL_READ_WRITE, GL_RGBA8_SNORM, layered=True)

        self.lip = max([field_lipschitz(d["field"]) for d in analytical_data
                        if d.get("field") is not None] or [1.0])

        baked_count = 0
        for f_idx, d in enumerate(analytical_data):
            f_box = self._sub_box(d["bbox_min"], d["bbox_max"], pad=self.band)
            if f_box is None:
                continue
            fox, foy, foz, fcx, fcy, fcz = f_box

            if reg_box is not None:
                rox, roy, roz, rcx, rcy, rcz = reg_box
                # Intersection of field sub-box with dirty region sub-box
                ix0 = max(fox, rox)
                iy0 = max(foy, roy)
                iz0 = max(foz, roz)
                ix1 = min(fox + fcx, rox + rcx)
                iy1 = min(foy + fcy, roy + rcy)
                iz1 = min(foz + fcz, roz + rcz)
                if ix1 <= ix0 or iy1 <= iy0 or iz1 <= iz0:
                    continue  # Field does not intersect dirty region
                ox, oy, oz = ix0, iy0, iz0
                cx, cy, cz = ix1 - ix0, iy1 - iy0, iz1 - iz0
            else:
                ox, oy, oz = fox, foy, foz
                cx, cy, cz = fcx, fcy, fcz

            t_field0 = time.perf_counter()
            expr = d["expr"]
            ctx = d["ctx"]

            nbx, nby, nbz = ((cx + 7) // 8, (cy + 7) // 8, (cz + 7) // 8)
            fld = d.get("field")
            lip = field_lipschitz(fld) if fld is not None else 1.0
            half_diag = 0.5 * math.sqrt(sum((SCAN_BLOCK * s) ** 2 for s in
                                            (self.step.x, self.step.y, self.step.z)))
            reach = float(self.band) + LIPSCHITZ_SAFETY * lip * half_diag

            # Bind textures, 3D warp textures, SSBOs before scan and bake dispatches
            tex_ids = GpuFieldEvaluator._bind_textures(None, None, ctx, base_unit=1)

            # Bind 3D textures for every sampler3D the field's GLSL declared.
            # Units run above the 2D samplers, which occupy 1 .. len(sampler2d_names).
            sampler3d_names = getattr(ctx, "sampler3d_names", [])
            providers = getattr(ctx, "sampler3d_providers", {})
            tex3d_props = {}
            if sampler3d_names and renderer is not None:
                label = d.get("label", getattr(ctx, "prefix", ""))
                action = getattr(renderer, "_last_action", None)
                next_unit = 1 + len(getattr(ctx, "sampler2d_names", []))
                glActiveTexture0 = _loader.get("glActiveTexture", [ctypes.c_uint], None)
                glBindTexture0 = _loader.get("glBindTexture", [ctypes.c_uint, ctypes.c_uint], None)
                GL_TEXTURE0 = 0x84C0
                GL_TEXTURE_3D = 0x806F
                for sname in sampler3d_names:
                    provider = providers.get(sname)
                    if provider is None:
                        fld_logger.error(
                            f"scene_bake: sampler3D {sname} has no provider; it will "
                            f"sample texture unit 0. The field's to_glsl must call "
                            f"ctx.sampler3d(hint, provider=self).")
                        continue
                    entry = renderer._get_field_texture3d(label, sname, provider, action)
                    if not entry or not glActiveTexture0 or not glBindTexture0:
                        continue
                    unit = next_unit
                    next_unit += 1
                    glActiveTexture0(GL_TEXTURE0 + unit)
                    glBindTexture0(GL_TEXTURE_3D, entry["tex"].id)
                    tex3d_props[sname] = ("int", unit)
                    # Which uniform belongs to which sampler is recorded at compile
                    # time (SB-036). Do NOT match on the name: one context compiles a
                    # whole tree, so two cages both spell "wmin" and a name match
                    # writes each provider's bbox over the other's.
                    uni_map = getattr(ctx, "sampler3d_uniforms", {}).get(sname, {})
                    for suffix, (utype, val) in entry.get("uniforms", {}).items():
                        uname = uni_map.get(suffix)
                        if uname is None:
                            fld_logger.error(
                                f"scene_bake: {sname} supplied uniform '{suffix}' that its "
                                f"to_glsl never registered with ctx.sampler3d_uniform(); it "
                                f"will keep its compile-time placeholder value.")
                            continue
                        tex3d_props[uname] = (utype, val)

            # Bind SSBOs if any
            ssbo_names = getattr(ctx, "ssbo_names", [])
            K = len(ssbo_names)
            buf_ids = None
            if K > 0 and glGenBuffers and glBindBuffer and glBufferData and glBindBufferBase:
                buf_ids = (ctypes.c_uint * K)()
                glGenBuffers(K, buf_ids)
                for idx, sname in enumerate(ssbo_names):
                    field = ctx.ssbo_fields.get(sname)
                    if field:
                        # A field that registers an SSBO packs it, via this one hook.
                        # The name-sniffing chain that used to sit here dispatched to
                        # seven `pack_*` methods that existed only on
                        # `SdfNurbsSurfaceField`, deleted 2026-09-01 when RA-012
                        # resolved as "retire". Nothing else ever implemented them,
                        # so a missing packer is a bug in the field, not a fallback.
                        packer = getattr(field, "ssbo_packer", None)
                        if packer is None:
                            fld_logger.warn(
                                f"scene_bake: {sname} was registered with ctx.ssbo() by "
                                f"{type(field).__name__}, which has no ssbo_packer(); "
                                f"the buffer will be left unbound.")
                            continue
                        packed_data = packer(sname)
                        buf_id = buf_ids[idx]
                        glBindBuffer(GL_SHADER_STORAGE_BUFFER, buf_id)
                        glBufferData(GL_SHADER_STORAGE_BUFFER, packed_data.nbytes, packed_data.ctypes.data, GL_DYNAMIC_DRAW)
                        glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 4 + idx, buf_id)

            # Bind block flag SSBO at binding 3 -- below the field SSBOs, which
            # start at 4 and are uncapped, and inside GL's guaranteed 0..7 range.
            flag_buf = None
            if glGenBuffers and glBindBuffer and glBufferData and glBindBufferBase:
                f_id = (ctypes.c_uint * 1)()
                glGenBuffers(1, f_id)
                flag_buf = f_id[0]
                glBindBuffer(GL_SHADER_STORAGE_BUFFER, flag_buf)
                glBufferData(GL_SHADER_STORAGE_BUFFER, nbx * nby * nbz * 4, None, GL_DYNAMIC_DRAW)
                glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 3, flag_buf)

            # 1. Dispatch block scan
            scan_src = build_compute_shader(expr, ctx, mode="block_scan")
            scan_prog = self._scan_cache.get(scan_src)
            scan_prog.use()
            scan_prog.set_uniforms_from_ctx(ctx.uniforms)
            scan_prog.set_3f("u_vol_min", float(self.vol_min.x), float(self.vol_min.y), float(self.vol_min.z))
            scan_prog.set_3f("u_voxel_step", float(self.step.x), float(self.step.y), float(self.step.z))
            scan_prog.set_3iv("u_sub_offset", 1, [ox, oy, oz])
            scan_prog.set_3iv("u_block_count", 1, [nbx, nby, nbz])
            scan_prog.set_1f("u_reach", reach)
            scan_prog.set_1i("u_cage_quad_iters", 10)
            scan_prog.set_1i("u_cage_tri_iters", 10)
            scan_prog.set_1i("u_grad_taps", 4)
            for idx_s, sname in enumerate(getattr(ctx, "sampler2d_names", [])):
                scan_prog.set_1i(sname, 1 + idx_s)
            for uname, (utype, val) in tex3d_props.items():
                scan_prog.set_uniform(uname, utype, val)

            scan_prog.dispatch_compute((nbx + 3) // 4, (nby + 3) // 4, (nbz + 3) // 4)
            memory_barrier(GL_SHADER_STORAGE_BARRIER_BIT)

            # 2. Dispatch scene bake
            # Anything derived from the field that this call needs must be compiled
            # ONCE, with `expr`, and cached beside it in analytical_data -- never
            # computed here. `ctx.uniform()` appends unconditionally, so a per-bake
            # call that registers a uniform grows the cached ctx, changes the source
            # string every frame, and misses `_bake_cache` forever, recompiling the
            # shader on every drag tick. That is not hypothetical: it is what the
            # TR-021 analytic-normal call did before the audit caught it.
            src = build_compute_shader(expr, ctx, mode="scene_bake")
            prog = self._bake_cache.get(src)
            prog.use()
            prog.set_uniforms_from_ctx(ctx.uniforms)
            # The ROOT surface id, not the field index: the wholly-inside branch of
            # scene_bake writes this into u_id_volume, and it has to agree with the
            # `s.id` the band voxels get, or the same solid keys two different rows
            # of the FieldMeta table.
            #
            # There is deliberately no index fallback. `min(f_idx, 65534)` stood
            # here and was the second key that bug is about: a field index written
            # into a table the march reads by surface id, so one field's index
            # silently addressed another field's row. A root with no id writes the
            # 65535 miss sentinel, the march falls through to u_base_color, and the
            # cause says so in the log instead of showing up as a wrong colour.
            root_sid = fld.surface_id if fld is not None else 65535
            if root_sid >= 65535:
                # Once per field, not once per bake: a drag bakes every tick.
                seen = getattr(self, "_no_surface_id_reported", None)
                if seen is None:
                    seen = self._no_surface_id_reported = set()
                if f_idx not in seen:
                    seen.add(f_idx)
                    fld_logger.error(
                        f"scene_bake: field {f_idx} has no surface id; its interior "
                        f"voxels will not key a FieldMeta row")
            prog.set_1ui("u_root_surface_id", root_sid)
            for idx_s, sname in enumerate(getattr(ctx, "sampler2d_names", [])):
                prog.set_1i(sname, 1 + idx_s)
            for uname, (utype, val) in tex3d_props.items():
                prog.set_uniform(uname, utype, val)

            prog.set_3f("u_vol_min", float(self.vol_min.x), float(self.vol_min.y), float(self.vol_min.z))
            prog.set_3f("u_voxel_step", float(self.step.x), float(self.step.y), float(self.step.z))
            prog.set_3iv("u_sub_offset", 1, [ox, oy, oz])
            prog.set_3iv("u_sub_count", 1, [cx, cy, cz])
            prog.set_1f("u_band", float(self.band))
            prog.set_1i("u_cage_quad_iters", 10)
            prog.set_1i("u_cage_tri_iters", 10)
            prog.set_1i("u_grad_taps", 4)

            prog.dispatch_compute((cx + 7) // 8, (cy + 7) // 8, (cz + 7) // 8)
            memory_barrier(GL_SHADER_IMAGE_ACCESS_BARRIER_BIT)

            if buf_ids is not None and glDeleteBuffers:
                glDeleteBuffers(K, buf_ids)
            if flag_buf is not None and glDeleteBuffers:
                f_del = (ctypes.c_uint * 1)(flag_buf)
                glDeleteBuffers(1, f_del)

            GpuFieldEvaluator._unbind_textures(None, tex_ids, base_unit=1)
            baked_count += 1

            t_field = (time.perf_counter() - t_field0) * 1000.0
            if t_field > 5.0:
                fld_logger.render_debug(f"SceneVolume: field bake took {t_field:.1f} ms")

        bind_image_texture(0, 0, GL_READ_WRITE, GL_R16F)
        bind_image_texture(1, 0, GL_READ_WRITE, GL_R16UI)
        bind_image_texture(2, 0, GL_READ_WRITE, GL_RGBA8_SNORM)
        ms = (time.perf_counter() - t0) * 1000.0
        if reg_box is not None:
            fld_logger.render_debug(f"SceneVolume.bake: region rebake, {baked_count}/{len(analytical_data)} fields in {ms:.1f} ms")
        else:
            fld_logger.render_debug(f"SceneVolume.bake: full bake, {len(analytical_data)} fields in {ms:.1f} ms")
        self.dirty = False

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
