# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/sdf/glsl_compiler.py

Assembles a compiled SDF field tree into a complete GLSL shader source.
The compiler does NOT contain primitive-specific GLSL — every primitive
registers its own helper bodies via `ctx.add_custom_helper(name, body)`.
"""

import uuid


class GlslContext:
    """Collects uniforms and helper functions during SDF→GLSL compilation."""

    def __init__(self, prefix=None):
        self._uniforms = []          # [(name, glsl_type, value)]
        self._counter = 0
        self._custom_helpers = {}    # name → GLSL function body string (insertion-ordered)
        self._helper_counter = 0
        self._sampler2d_names = []   # sampler2D uniform names (bound by renderer, not set_uniforms_from_ctx)
        self._sampler2d_paths = {}   # name → image_path
        self._sampler2d_data = {}    # name → np.ndarray
        self._sampler3d_names = []   # sampler3D uniform names (bound by renderer, not set_uniforms_from_ctx)
        self._sampler3d_providers = {} # name → provider object providing texture3d_data()
        self._sampler3d_uniforms = {}  # sampler name -> {suffix: uniform name}
        self._ssbo_names = []        # SSBO buffer names (bound by renderer/evaluator, not set_uniforms_from_ctx)
        if prefix is None:
            self._prefix = f"u_{uuid.uuid4().hex[:8]}_"
        else:
            clean_prefix = prefix.replace(".", "_").replace(" ", "_").replace("-", "_")
            self._prefix = f"u_{clean_prefix}_"

    @property
    def prefix(self) -> str:
        return self._prefix

    def get_unique_name(self, base_name: str) -> str:
        name = f"{self._prefix}{base_name}_{self._helper_counter}"
        self._helper_counter += 1
        return name

    def uniform(self, glsl_type, value, name=None):
        if name is None:
            name = f"{self._prefix}{self._counter}"
        else:
            # The hint is a readability aid, not an identity. One context compiles a
            # whole field tree, so two fields of the same class asking for "vsize"
            # must not collide -- that is a shader-wide redefinition error.
            name = f"{self._prefix}{name}_{self._counter}"
        self._counter += 1
        self._uniforms.append((name, glsl_type, value))
        return name

    def sampler2d(self, hint: str, image_path: str = "") -> str:
        """Register a sampler2D uniform with a file path. The renderer binds the texture each frame."""
        name = self.get_unique_name(hint)
        self._sampler2d_names.append(name)
        self._sampler2d_paths[name] = image_path
        return name

    def sampler2d_data(self, hint: str, array) -> str:
        """Register a sampler2D uniform with an in-memory numpy array. The renderer binds the texture each frame."""
        name = self.get_unique_name(hint)
        self._sampler2d_names.append(name)
        self._sampler2d_data[name] = array
        return name

    @property
    def sampler2d_names(self):
        return list(self._sampler2d_names)

    @property
    def sampler2d_paths(self):
        return dict(self._sampler2d_paths)

    @property
    def sampler2d_data_map(self):
        return dict(self._sampler2d_data)

    def sampler3d(self, hint: str, provider=None) -> str:
        """Register a sampler3D uniform. The renderer binds the texture each frame.

        `provider` is the object that supplies the texture data -- normally the field
        itself. It must implement `texture3d_data()` (see SB-010). Registering without
        a provider means nothing will bind the sampler, which reads as a silently
        black texture rather than an error, so pass one.
        """
        name = self.get_unique_name(hint)
        self._sampler3d_names.append(name)
        if provider is not None:
            self._sampler3d_providers[name] = provider
        return name

    @property
    def sampler3d_names(self):
        return list(self._sampler3d_names)

    @property
    def sampler3d_providers(self):
        """sampler name -> provider object, for the samplers that declared one."""
        return dict(self._sampler3d_providers)

    def sampler3d_uniform(self, sampler_name, suffix, uniform_name):
        """Bind one uniform to one sampler3D, so the renderer can set it per-frame.

        `suffix` is the key the provider's texture3d_data()["uniforms"] uses. Names are
        NOT matched -- see SB-013 -- because one context compiles a whole tree and two
        fields of the same class register the same suffix.
        """
        self._sampler3d_uniforms.setdefault(sampler_name, {})[suffix] = uniform_name

    @property
    def sampler3d_uniforms(self):
        """sampler name -> {suffix: uniform name}."""
        return {k: dict(v) for k, v in self._sampler3d_uniforms.items()}

    def ssbo(self, hint: str, field, struct_type: str = "FaceRecord") -> str:
        """Register a Shader Storage Buffer Object name. The renderer binds the buffer."""
        name = self.get_unique_name(hint)
        self._ssbo_names.append(name)
        if not hasattr(self, "_ssbo_fields"):
            self._ssbo_fields = {}
        self._ssbo_fields[name] = field
        if not hasattr(self, "_ssbo_types"):
            self._ssbo_types = {}
        self._ssbo_types[name] = struct_type
        return name

    @property
    def ssbo_names(self):
        return list(self._ssbo_names)

    @property
    def ssbo_fields(self):
        return dict(getattr(self, "_ssbo_fields", {}))

    def add_custom_helper(self, name: str, body: str):
        """Register a GLSL helper by name. Subsequent calls with the same
        name are no-ops (deduplication)."""
        if name not in self._custom_helpers:
            self._custom_helpers[name] = body

    @property
    def uniforms(self):
        return list(self._uniforms)


def compile_field_to_glsl(field, prefix=None):
    """Compile an SDF field tree to a GLSL expression.
    Returns: (glsl_expression: str, ctx: GlslContext)
    """
    ctx = GlslContext(prefix=prefix)
    expr = field.to_glsl_sample(ctx, "p")
    return expr, ctx


COMPUTE_STRUCT_DECL = """
struct FaceRecord {
    vec4 type_pad;
    vec4 bmin;
    vec4 bmax;
    vec4 v0;
    vec4 v1;
    vec4 v2;
    vec4 v3;
    vec4 h0;
    vec4 h1;
    vec4 h2;
    vec4 h3;
    vec4 h4;
    vec4 h5;
    vec4 h6;
    vec4 h7;
    vec4 b111;
};
struct GridCell {
    uint offset;
    uint count;
    uint pad0;
    uint pad1;
};
struct GridMeta {
    vec4 vmin_G;
    vec4 inv_cell_pad;
};
struct BvhNode {
    vec4 bmin;
    vec4 bmax;
};
struct RingMeta {
    vec4 org;      // xyz = frame origin,    w = index of this ring's first seg
    vec4 e1;       // xyz = frame e1,        w = segment count
    vec4 e2;       // xyz = frame e2,        w = EMBED
    vec4 box;      // xy  = profile bbox lo, zw = profile bbox hi
    vec4 sweep;    // xyz = sweep_dir * len, w = sweep_len
    vec4 slab;     // xyz = slab origin,     w = slab h0
    vec4 slab2;    // xyz = slab axis,       w = slab length
    vec4 flags;    // x = is_surface, y = has_slab
};
struct RingSeg {
    vec4 ab;       // xy = P0, zw = P1
    vec4 cd;       // xy = P2, zw = P3
};
struct ArrayInstance {
    vec4 inv0;     // xyz = inverse row 0, w = inverse translation x
    vec4 inv1;
    vec4 inv2;
    vec4 sphere;   // xyz = world centre of this copy's bounding sphere, w = radius
    vec4 misc;     // x = scale_i, y = enabled (0/1), zw = reserved
};
"""

_GLSL_FLD_SAMPLE = """
// A Lipschitz bound is deliberately NOT a member here. TR-018 added one and
// TR-019 was to consume it in the block scan; TR-019 measured negative and the
// member went with it (TR-020, 2026-08-28). The reason is worth keeping: every
// site that ever set it set it to `float(self.lipschitz())` -- a Python constant,
// baked into the literal by the base class and passed as a uniform by the four
// modifiers -- and every combinator composed it as `max(a.lip, b.lip)`, which is
// exactly what `ComposerField.lipschitz()` does on the CPU. It was therefore the
// same number `lipschitz()` already returns, transported a second way, with no
// point dependence anywhere and no reader at all.
//
// A bound that varied WITHIN a field -- a twist being 1-Lipschitz except near
// its axis -- is a real idea and is what TR-019 was reaching for, but it needs
// each field to emit a point-dependent expression. It does not follow from
// carrying a constant through the struct. See the dev repo's bench_lipschitz_reach.md:
// the headroom that bench found belongs to `LIPSCHITZ_SAFETY`, not to this.
struct FldSample {
    float d;
    uint  id;
};

FldSample fld_min(FldSample a, FldSample b) {
    FldSample r;
    r.d   = min(a.d, b.d);
    r.id  = (a.d <= b.d) ? a.id : b.id;
    return r;
}

FldSample fld_max(FldSample a, FldSample b) {
    FldSample r;
    r.d   = max(a.d, b.d);
    r.id  = (a.d >= b.d) ? a.id : b.id;
    return r;
}

// Subtraction: max(a, -b). The id of the cut surface is the CUTTER's, so a
// click on the inside of a pocket selects the tool that made it -- which is
// what the user has to be able to name to bevel it.
FldSample fld_sub(FldSample a, FldSample b) {
    FldSample r;
    r.d   = max(a.d, -b.d);
    r.id  = (a.d >= -b.d) ? a.id : b.id;
    return r;
}

// Smooth union. `blend_id` is the surface id of the blend region itself, or
// 65535u to keep the nearer surface's id. A fillet is a face: it gets its own
// id so it can be selected, coloured, and (later) re-bevelled.
FldSample fld_smin(FldSample a, FldSample b, float k, uint blend_id) {
    FldSample r;
    float h = clamp(0.5 + 0.5 * (b.d - a.d) / k, 0.0, 1.0);
    r.d   = mix(b.d, a.d, h) - k * h * (1.0 - h);
    r.id  = (a.d <= b.d) ? a.id : b.id;
    if (blend_id != 65535u && h > 0.001 && h < 0.999) r.id = blend_id;
    return r;
}

FldSample fld_smax(FldSample a, FldSample b, float k, uint blend_id) {
    FldSample r;
    float h = clamp(0.5 - 0.5 * (b.d - a.d) / k, 0.0, 1.0);
    r.d   = mix(b.d, a.d, h) + k * h * (1.0 - h);
    r.id  = (a.d >= b.d) ? a.id : b.id;
    if (blend_id != 65535u && h > 0.001 && h < 0.999) r.id = blend_id;
    return r;
}

FldSample fld_ssub(FldSample a, FldSample b, float k, uint blend_id) {
    FldSample r;
    float h = clamp(0.5 - 0.5 * (a.d + b.d) / k, 0.0, 1.0);
    r.d   = mix(a.d, -b.d, h) + k * h * (1.0 - h);
    r.id  = (a.d >= -b.d) ? a.id : b.id;
    if (blend_id != 65535u && h > 0.001 && h < 0.999) r.id = blend_id;
    return r;
}

FldSample fld_offset(FldSample s, float dist) {
    s.d -= dist;
    return s;
}
"""


def build_compute_shader(expression, ctx, mode):
    """Assemble a complete compute shader source from a compiled SDF expression."""
    uniform_decls_list = [
        f"uniform {glsl_type} {name};"
        for name, glsl_type, _ in ctx.uniforms
    ]
    if mode in ("point", "bake", "scene_bake", "block_scan"):
        for sname in getattr(ctx, "sampler2d_names", []):
            uniform_decls_list.append(f"uniform sampler2D {sname};")
        for sname in getattr(ctx, "sampler3d_names", []):
            uniform_decls_list.append(f"uniform sampler3D {sname};")
    uniform_decls = "\n".join(uniform_decls_list)

    ssbo_names = getattr(ctx, "ssbo_names", [])
    ssbo_decls = []
    ssbo_binding = 4
    ssbo_types = getattr(ctx, "_ssbo_types", {})
    for sname in ssbo_names:
        stype = ssbo_types.get(sname, "FaceRecord")
        ssbo_decls.append(
            f"layout(std430, binding = {ssbo_binding}) buffer {sname}_block {{\n"
            f"    {stype} {sname}[];\n"
            f"}};"
        )
        ssbo_binding += 1

    struct_decl = COMPUTE_STRUCT_DECL if ssbo_decls else ""
    ssbo_decls_str = "\n".join(ssbo_decls)
    fld_sample_decl = _GLSL_FLD_SAMPLE
    helper_defs = "\n".join(ctx._custom_helpers.values())

    if mode == "point":
        return f"""#version 430
layout(local_size_x = 256) in;

layout(std430, binding = 0) buffer PointBuffer {{
    vec4 points[];
}};

layout(std430, binding = 1) buffer DistanceBuffer {{
    float distances[];
}};

uniform int u_num_points;
uniform int u_cage_quad_iters;
uniform int u_cage_tri_iters;

{uniform_decls}

{struct_decl}
{ssbo_decls_str}

{fld_sample_decl}

{helper_defs}

void main() {{
    uint idx = gl_GlobalInvocationID.x;
    if (idx >= u_num_points) return;
    
    vec3 p = points[idx].xyz;
    // `{expression}` is a bare float here, NOT a FldSample: this mode's only
    // caller is GpuFieldEvaluator, which compiles the field with `to_glsl()`
    // rather than `to_glsl_sample()`. The TR restructure added a `.d` to both of
    // this file's `to_glsl`-fed modes without changing those callers, which made
    // every GpuFieldEvaluator construction fail to compile with "invalid swizzle
    // `d'". If this mode is ever moved onto FldSample, move its caller first.
    distances[idx] = {expression};
}}
"""
    elif mode == "bake":
        return f"""#version 430
layout(local_size_x = 8, local_size_y = 8, local_size_z = 8) in;

layout(r32f, binding = 0) writeonly uniform image3D u_volume_image;

uniform vec3 u_bbox_min;
uniform vec3 u_cell_size;
uniform int u_cage_quad_iters;
uniform int u_cage_tri_iters;

{uniform_decls}

{struct_decl}
{ssbo_decls_str}

{fld_sample_decl}

{helper_defs}

// Unlike `scene_bake` this writes the RAW distance -- no band, no clamp. Its
// consumer is `bake_field_grid`, which wants the field itself, not a narrow-band
// approximation of it. Do not add a clamp here to make it look like scene_bake.
void main() {{
    ivec3 size = imageSize(u_volume_image);
    ivec3 coords = ivec3(gl_GlobalInvocationID.xyz);
    if (coords.x >= size.x || coords.y >= size.y || coords.z >= size.z) return;
    
    vec3 p = u_bbox_min + vec3(coords) * u_cell_size;
    // Bare float, not a FldSample -- see the note in mode "point".
    imageStore(u_volume_image, coords, vec4({expression}, 0.0, 0.0, 0.0));
}}
"""
    elif mode == "scene_bake":
        normal_code = f"""            // Tetrahedron finite-difference gradient of the unclamped expression.
            //
            // TR-021/TR-022 added a closed-form `to_glsl_normal()` alongside this
            // for the six primitives where the gradient is one line, and TR-023
            // measured it: no difference, +0.2% at best against a gate of +15%
            // (the dev repo's bench_bake_normals.md). The reason is in that file --
            // only ~3% of volume voxels are inside the band, and the other 97%
            // return before a normal is computed at all, so replacing four
            // evaluations with one in 3% of the work cannot move the bake. The
            // analytic path was reverted on 2026-08-28 and this is the single
            // mechanism again. Do not re-add one without re-running that bench.
            //
            // The four taps run in a LOOP, not as four textual copies of
            // `{{expression}}`. The expression is a call into the field's helper
            // tree and the driver inlines it at every call site, so spelling it out
            // four times multiplies the WHOLE field -- an extrusion stack's
            // per-lobe cubic-Bezier walls included -- by five, in both instruction
            // count and compile time. `u_grad_taps` is a uniform (always 4) for the
            // same reason the stack machine's bound is: a literal 4 lets the driver
            // unroll the loop straight back into four inlined copies.
            vec3 eps = u_voxel_step * 0.01;
            vec3 orig_p = p;
            vec3 n = vec3(0.0);
            for (int k = 0; k < u_grad_taps; ++k) {{
                vec3 kk = vec3((k == 0 || k == 3) ? 1.0 : -1.0,
                               (k >= 2)           ? 1.0 : -1.0,
                               (k == 1 || k == 3) ? 1.0 : -1.0);
                p = orig_p + kk * eps;
                n += kk * ({expression}).d;
            }}
            p = orig_p;

            float len2 = dot(n, n);
            vec3 norm = (len2 > 1e-12) ? n * inversesqrt(len2) : vec3(0.0, 0.0, 1.0);
            imageStore(u_norm_volume, gid, vec4(norm, 1.0));"""

        return f"""#version 430
layout(local_size_x = 8, local_size_y = 8, local_size_z = 8) in;

// These three qualifiers are not free-form: GL requires the format layout
// qualifier to match the format the image unit was bound with, or every
// imageLoad/imageStore through it is undefined. They mirror, exactly,
// `glTexStorage3D` and `bind_image_texture` in `scene_volume.py` --
// GL_R16F, GL_R16UI, GL_RGBA8_SNORM. Changing one means changing both.
layout(r16f,        binding = 0) uniform image3D u_volume;
layout(r16ui,       binding = 1) uniform uimage3D u_id_volume;
layout(rgba8_snorm, binding = 2) uniform image3D u_norm_volume;
// Binding 3, not 8. The field's own SSBOs are bound at `4 + idx` with no cap on
// idx (see build_compute_shader's ssbo_binding), so 8 is the fifth of them; and
// GL only guarantees 8 SSBO binding points (0..7) in the first place. 3 is below
// the field range and inside the guaranteed one. Images 0-2 above are a separate
// namespace and do not collide.
layout(std430, binding = 3) readonly buffer BlockFlagBlock {{
    uint u_block_flag[];
}};

uniform ivec3 u_sub_count;      // voxel extent of the region to bake
uniform ivec3 u_sub_offset;     // voxel index of the region's origin in the full volume
uniform vec3  u_vol_min;        // world position of voxel (0,0,0)
uniform vec3  u_voxel_step;     // world size of one voxel, per axis
uniform float u_band;           // clamp distance (mm)
uniform uint  u_root_surface_id; // this field's ROOT surface id -- the same number
                                // the expression's own FldSample carries at the top
                                // of the tree. It exists so the wholly-inside
                                // branch below can write an id without evaluating
                                // the field a second time (trap 1).
uniform int   u_cage_quad_iters;
uniform int   u_cage_tri_iters;
uniform int   u_grad_taps;      // always 4; a uniform so the tap loop cannot unroll

{uniform_decls}

{struct_decl}
{ssbo_decls_str}

{fld_sample_decl}

{helper_defs}

// `{{expression}}` appears at most TWICE below -- once for this voxel, and once
// inside the gradient tap loop when no analytic normal was supplied -- and that
// count is a performance contract, not an accident. The driver inlines the whole
// field at every call site, so each additional textual copy multiplies the entire
// expression tree, an extrusion stack's per-lobe Bezier walls included.
//
// A per-workgroup saturation skip was added here on 2026-08-10 and removed the
// same day. It was sound (only 18-30% of a region bake's voxels are in the band,
// and |d(centre)| > band + L*radius really does prove the other 82-70% saturate)
// but it was written as a third textual copy guarded by `gl_LocalInvocationIndex
// == 0u`. Source went 11405 -> 16204 chars and the measured full bake at one
// lobe went 15.2 -> 94.8 ms: a divergent branch holding a second live set of the
// stack machine's local arrays wrecks occupancy. It now arrives as the separate
// `block_scan` dispatch below, so this shader gains one SSBO load and no
// instructions. Do not reintroduce a third copy under any guard.
void main() {{
    ivec3 lid = ivec3(gl_GlobalInvocationID);
    if (any(greaterThanEqual(lid, u_sub_count))) return;
    ivec3 gid = lid + u_sub_offset;

    ivec3 size = imageSize(u_volume);
    if (any(lessThan(gid, ivec3(0))) || any(greaterThanEqual(gid, size))) return;

    // VR-021. One SSBO load and no field instructions -- this is the whole
    // constraint the 2026-08-10 attempt violated by inlining the expression a
    // third time. `lid` is relative to u_sub_offset, so the block index is too,
    // and it matches the grid block_scan was dispatched over.
    ivec3 nblk = (u_sub_count + 7) / 8;
    ivec3 blk  = lid / 8;
    uint  bflag = u_block_flag[uint((blk.z * nblk.y + blk.y) * nblk.x + blk.x)];

    // 1 = wholly outside: the clear already wrote +band, nothing to do.
    if (bflag == 1u) return;

    // 2 = wholly inside: -band is the min possible value, so it wins `d < prev`
    // without an evaluation. The id written here MUST be the same root surface id
    // the band voxels get from `s.id`, or the march -- which samples the id 0.75
    // voxels INSIDE the hit point, often landing in one of these blocks -- reads a
    // different key than the one the FieldMeta table was filled at, and the
    // surface renders with another field's colour.
    if (bflag == 2u) {{
        if (-u_band < imageLoad(u_volume, gid).r) {{
            imageStore(u_volume, gid, vec4(-u_band, 0.0, 0.0, 0.0));
            imageStore(u_id_volume, gid, uvec4(u_root_surface_id, 0u, 0u, 0u));
        }}
        return;                         // no normal: |raw_d| >= u_band, nothing reads it
    }}

    vec3 p = u_vol_min + (vec3(gid) + 0.5) * u_voxel_step;
    FldSample s = {expression};
    float raw_d = s.d;
    float d = clamp(raw_d, -u_band, u_band);

    float prev = imageLoad(u_volume, gid).r;
    if (d < prev) {{
        imageStore(u_volume, gid, vec4(d, 0.0, 0.0, 0.0));
        imageStore(u_id_volume, gid, uvec4(s.id, 0u, 0u, 0u));

        // Only voxels whose value does NOT saturate the band get a normal.
        //
        // A voxel with |raw_d| >= u_band stores exactly +/-u_band -- a value
        // that carries no information about where the surface is, so the march
        // can never resolve a hit from it and never samples its normal. That
        // includes the whole INTERIOR of every solid, which is the expensive
        // half: an interior voxel clamps to -u_band, which is less than the
        // cleared +u_band, so it passed `d < prev` and paid four extra field
        // evaluations for a normal nothing would ever read. Measured on the
        // 3-extrude cage this is most of the bake -- the exterior was already
        // free (it clamps to +u_band, fails `d < prev`, and falls straight
        // out), so the interior was carrying the 5x.
        //
        // The one case this changes is a camera strictly inside a solid, where
        // the march hits at t=0 on a saturated voxel: it reads the cleared
        // (0,0,0) and sample_normal falls back to +Z instead of the analytic
        // normal. That view was already degenerate -- the hit point is not on
        // any surface -- and it is not worth four evaluations per interior
        // voxel of every frame to keep it.
        if (abs(raw_d) < u_band) {{
{normal_code}
        }}
    }}
}}
"""

    elif mode == "block_scan":
        return f"""#version 430
layout(local_size_x = 4, local_size_y = 4, local_size_z = 4) in;

layout(std430, binding = 3) buffer BlockFlagBlock {{
    uint u_block_flag[];
}};

uniform vec3  u_vol_min;        // world position of voxel (0,0,0)
uniform vec3  u_voxel_step;     // world size of one voxel, per axis
uniform ivec3 u_sub_offset;     // voxel index of the bake dispatch's origin
uniform ivec3 u_block_count;    // block extent, = ceil(u_sub_count / 8)
uniform float u_reach;          // band + SAFETY * lipschitz * 0.5 * |8*step|
uniform int   u_cage_quad_iters;
uniform int   u_cage_tri_iters;
uniform int   u_grad_taps;

{uniform_decls}

{struct_decl}
{ssbo_decls_str}

{fld_sample_decl}

{helper_defs}

// `{{expression}}` appears exactly ONCE below, and that is the entire point of
// this shader existing separately. See the note in `scene_bake`: a third copy
// there took a one-lobe full bake 15.2 -> 94.8 ms. Do not add a gradient, a
// second tap, or a refinement here -- one evaluation per block, nothing else.
void main() {{
    ivec3 b = ivec3(gl_GlobalInvocationID);
    if (any(greaterThanEqual(b, u_block_count))) return;

    // Centre of the 8x8x8 block: voxel centres run 8b+0.5 .. 8b+7.5, so the
    // midpoint is 8b+4.0 in voxel units, relative to the dispatch origin.
    vec3 p = u_vol_min + (vec3(u_sub_offset) + vec3(b) * 8.0 + 4.0) * u_voxel_step;
    FldSample s = {expression};

    // ONE reach, computed CPU-side and passed in. TR-019 proposed replacing this
    // with a per-block bound read off the FldSample; measured and closed negative
    // on 2026-08-28, because the bound the struct carried was `lipschitz()` itself
    // -- the identical number this uniform is already computed from -- so the
    // substitution could not change a single block's flag. The measurement is in
    // the dev repo's bench_lipschitz_reach.md and it does say where the time is:
    // dropping LIPSCHITZ_SAFETY from 2.0 to 1.0 is worth ~8% of a full bake, and a
    // perfect reach (== u_band, not shippable) ~15%. That is a question about how
    // far `lipschitz()` can be trusted, not about how the bound is transported.
    float reach = u_reach;

    // 0 = evaluate every voxel. 1 = wholly outside, leave the cleared +band.
    // 2 = wholly inside, fill -band without evaluating. These are NOT
    // interchangeable: leaving an interior block cleared makes the march read
    // solid as empty.
    uint flag = 0u;
    if (s.d >  reach) flag = 1u;
    if (s.d < -reach) flag = 2u;

    uint idx = uint((b.z * u_block_count.y + b.y) * u_block_count.x + b.x);
    u_block_flag[idx] = flag;
}}
"""

    else:
        raise ValueError(f"Unknown compute shader mode: {mode}")
