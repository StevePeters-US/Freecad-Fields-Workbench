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
        if prefix is None:
            self._prefix = f"u_{uuid.uuid4().hex[:8]}_"
        else:
            clean_prefix = prefix.replace(".", "_").replace(" ", "_").replace("-", "_")
            self._prefix = f"u_{clean_prefix}_"

    def get_unique_name(self, base_name: str) -> str:
        name = f"{self._prefix}{base_name}_{self._helper_counter}"
        self._helper_counter += 1
        return name

    def uniform(self, glsl_type, value):
        name = f"{self._prefix}{self._counter}"
        self._counter += 1
        self._uniforms.append((name, glsl_type, value))
        return name

    def sampler2d(self, hint: str) -> str:
        """Register a sampler2D uniform. The renderer binds the texture each frame."""
        name = self.get_unique_name(hint)
        self._sampler2d_names.append(name)
        return name

    @property
    def sampler2d_names(self):
        return list(self._sampler2d_names)

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
    expr = field.to_glsl(ctx, "p")
    return expr, ctx


def build_compute_shader(expression, ctx):
    """Assemble a complete compute shader source from a compiled SDF expression."""
    uniform_decls = "\n".join(
        f"uniform {glsl_type} {name};"
        for name, glsl_type, _ in ctx.uniforms
    )
    helper_defs = "\n".join(ctx._custom_helpers.values())
    return f"""#version 430
layout(local_size_x = 8, local_size_y = 8, local_size_z = 8) in;
layout(r32f, binding = 0) uniform image3D u_volume;

uniform vec3  u_grid_min;
uniform vec3  u_grid_step;
uniform ivec3 u_grid_count;
uniform int   u_z_offset;

{uniform_decls}

{helper_defs}

float sdf_eval(vec3 p) {{
    return {expression};
}}

void main() {{
    ivec3 gid = ivec3(gl_GlobalInvocationID);
    if (gid.x >= u_grid_count.x || gid.y >= u_grid_count.y || gid.z >= u_grid_count.z)
        return;

    vec3 p = u_grid_min + vec3(gid) * u_grid_step;
    float d = sdf_eval(p);

    imageStore(u_volume, ivec3(gid.x, gid.y, u_z_offset + gid.z), vec4(d, 0.0, 0.0, 0.0));
}}
"""


def build_multi_raymarch_compute_shader(fields_data):
    """Build a GLSL 430 compute shader (8×8 work groups) for analytical SDF ray marching.

    Writes to four image2D bindings:
      binding 0 – rgba16f  color (Phong lit, a=1 on hit)
      binding 1 – rgba16f  view-space position (w=1 on hit)
      binding 2 – rgba16f  view-space normal
      binding 3 – r32f     window depth in [0,1]  (1.0 = no hit)

    Required uniforms set by the renderer each frame:
      ivec2 u_resolution, mat4 u_inv_mvp, mat4 u_mv, mat4 u_proj, vec3 u_light_dir
    """
    all_uniforms = []
    merged_helpers = {}
    for fd in fields_data:
        all_uniforms.extend(fd["ctx"].uniforms)
        for name, body in fd["ctx"]._custom_helpers.items():
            if name not in merged_helpers:
                merged_helpers[name] = body

    seen_uniforms = set()
    uniform_decls_list = []
    for name, glsl_type, _ in all_uniforms:
        if name not in seen_uniforms:
            uniform_decls_list.append(f"uniform {glsl_type} {name};")
            seen_uniforms.add(name)
    # sampler2D uniforms bound by renderer per-frame (not via set_uniforms_from_ctx)
    for fd in fields_data:
        for name in fd["ctx"].sampler2d_names:
            if name not in seen_uniforms:
                uniform_decls_list.append(f"uniform sampler2D {name};")
                seen_uniforms.add(name)
    uniform_decls = "\n".join(uniform_decls_list)
    helper_defs = "\n".join(merged_helpers.values())

    eval_funcs = ""
    for i, fd in enumerate(fields_data):
        eval_funcs += f"""
float sdf_eval_{i}(vec3 p) {{
    return {fd['expr']};
}}

vec3 sdf_normal_{i}(vec3 p) {{
    float h = 0.1;
    vec2 k = vec2(1.0, -1.0);
    vec3 n = k.xyy * sdf_eval_{i}(p + k.xyy*h)
           + k.yyx * sdf_eval_{i}(p + k.yyx*h)
           + k.yxy * sdf_eval_{i}(p + k.yxy*h)
           + k.xxx * sdf_eval_{i}(p + k.xxx*h);
    float len2 = dot(n, n);
    return (len2 > 1e-10) ? n * inversesqrt(len2) : vec3(0.0, 0.0, 1.0);
}}
"""

    num_fields = len(fields_data)

    # bbox + subtractive flag as uniforms — keeps GLSL source stable across value changes
    # (no GPU shader recompile when bbox or group changes; only uniform value updates)
    bbox_uniform_decls = "\n".join(
        f"uniform vec3 u_bmin_{i};\nuniform vec3 u_bmax_{i};" for i in range(num_fields))
    sub_uniform_decls = "\n".join(f"uniform int u_sub_{i};" for i in range(num_fields))
    if bbox_uniform_decls:
        uniform_decls = uniform_decls + "\n" + bbox_uniform_decls
    if sub_uniform_decls:
        uniform_decls = uniform_decls + "\n" + sub_uniform_decls

    field_bboxes = ""
    for i in range(num_fields):
        field_bboxes += f"    vec3 bmin_{i} = u_bmin_{i};\n"
        field_bboxes += f"    vec3 bmax_{i} = u_bmax_{i};\n"

    intersect_bboxes = ""
    for i in range(num_fields):
        intersect_bboxes += f"    vec2 fi_int_{i} = intersect_aabb(ro, rd, bmin_{i}, bmax_{i});\n"
        intersect_bboxes += f"    ftn[{i}] = max(fi_int_{i}.x, 0.0);\n"
        intersect_bboxes += f"    ftf[{i}] = min(fi_int_{i}.y, ray_tmax);\n"

    sample_loop = ""
    for i, fd in enumerate(fields_data):
        sample_loop += f"""
        if (t >= ftn[{i}] && t <= ftf[{i}]) {{
            float d_{i} = sdf_eval_{i}(p);
            if (abs(d_{i}) < 0.2) {{ hit = true; hit_field = {i}; break; }}
            min_d = min(min_d, abs(d_{i}));
        }} else if (t < ftn[{i}]) {{
            min_d = min(min_d, ftn[{i}] - t);
        }}
"""

    normal_switch = "    vec3 n = vec3(0.0, 0.0, 1.0);\n"
    for i in range(num_fields):
        normal_switch += f"    if (hit_field == {i}) n = sdf_normal_{i}(hp);\n"

    color_switch = "    vec3 base_color = vec3(1.0, 0.5, 0.0);\n"
    for i in range(num_fields):
        color_switch += f"    if (hit_field == {i}) base_color = (u_sub_{i} != 0) ? vec3(0.3, 0.5, 1.0) : vec3(1.0, 0.5, 0.0);\n"

    scene_bbox_lines = "\n".join(
        f"    scene_min = min(scene_min, bmin_{i});\n    scene_max = max(scene_max, bmax_{i});"
        for i in range(1, num_fields)
    )

    return f"""#version 430
layout(local_size_x = 8, local_size_y = 8) in;

layout(rgba16f, binding = 0) writeonly uniform image2D img_color;
layout(rgba16f, binding = 1) writeonly uniform image2D img_vspos;
layout(rgba16f, binding = 2) writeonly uniform image2D img_vsnorm;
layout(r32f,    binding = 3) writeonly uniform image2D img_depth;

uniform ivec2 u_resolution;
uniform mat4  u_inv_mvp;
uniform mat4  u_mv;
uniform mat4  u_proj;
uniform vec3  u_light_dir;

{uniform_decls}

{helper_defs}

{eval_funcs}

vec2 intersect_aabb(vec3 ro, vec3 rd, vec3 bmin, vec3 bmax) {{
    vec3 t1 = (bmin - ro) / rd;
    vec3 t2 = (bmax - ro) / rd;
    vec3 tmin_ = min(t1, t2);
    vec3 tmax_ = max(t1, t2);
    return vec2(max(max(tmin_.x, tmin_.y), tmin_.z),
                min(min(tmax_.x, tmax_.y), tmax_.z));
}}

void main() {{
    ivec2 pixel = ivec2(gl_GlobalInvocationID.xy);
    if (pixel.x >= u_resolution.x || pixel.y >= u_resolution.y) return;

    vec2 uv = (vec2(pixel) + 0.5) / vec2(u_resolution) * 2.0 - 1.0;

    vec4 ndc_near  = vec4(uv, -1.0, 1.0);
    vec4 world_near = u_inv_mvp * ndc_near;
    world_near /= world_near.w;
    vec4 ndc_far   = vec4(uv,  1.0, 1.0);
    vec4 world_far  = u_inv_mvp * ndc_far;
    world_far /= world_far.w;

    vec3 ro = world_near.xyz;
    vec3 rd = normalize(world_far.xyz - world_near.xyz);
    float ray_tmax = length(world_far.xyz - world_near.xyz);

{field_bboxes}

    vec3 scene_min = bmin_0;
    vec3 scene_max = bmax_0;
{scene_bbox_lines}
    vec2 tBox = intersect_aabb(ro, rd, scene_min, scene_max);
    float tNear = max(tBox.x, 0.0);
    float tFar  = min(tBox.y, ray_tmax);

    if (tNear > tFar) {{
        imageStore(img_color,  pixel, vec4(0.0));
        imageStore(img_vspos,  pixel, vec4(0.0));
        imageStore(img_vsnorm, pixel, vec4(0.0));
        imageStore(img_depth,  pixel, vec4(1.0, 0.0, 0.0, 0.0));
        return;
    }}

    float ftn[{num_fields}]; float ftf[{num_fields}];
{intersect_bboxes}

    float t = tNear;
    bool  hit = false;
    int   hit_field = 0;

    for (int i = 0; i < 512; i++) {{
        vec3  p     = ro + t * rd;
        float min_d = 1.0e10;

{sample_loop}

        if (hit) break;
        t += max(min_d * 0.9, 0.1);
        if (t > tFar) break;
    }}

    if (!hit) {{
        imageStore(img_color,  pixel, vec4(0.0));
        imageStore(img_vspos,  pixel, vec4(0.0));
        imageStore(img_vsnorm, pixel, vec4(0.0));
        imageStore(img_depth,  pixel, vec4(1.0, 0.0, 0.0, 0.0));
        return;
    }}

    vec3 hp = ro + t * rd;
{normal_switch}
    vec3 vd = normalize(-rd);

    float diff = max(dot(n, u_light_dir), 0.0);
    float spec = pow(max(dot(reflect(-u_light_dir, n), vd), 0.0), 32.0);

{color_switch}
    vec3 color = base_color * (0.25 + 0.70 * diff) + vec3(0.3) * spec;

    vec4 vs   = u_mv * vec4(hp, 1.0);
    vec3 vs_n = normalize(mat3(u_mv) * n);

    imageStore(img_color,  pixel, vec4(color, 1.0));
    imageStore(img_vspos,  pixel, vec4(vs.xyz, 1.0));
    imageStore(img_vsnorm, pixel, vec4(vs_n,   1.0));

    vec4 clip  = u_proj * vs;
    float ndc_z = clip.z / clip.w;
    imageStore(img_depth, pixel, vec4(ndc_z * 0.5 + 0.5, 0.0, 0.0, 0.0));
}}
"""


def build_multi_raymarch_fragment_shader(fields_data):
    """Build a complete fragment shader that evaluates multiple analytical SDF fields."""
    all_uniforms = []
    merged_helpers = {}
    for fd in fields_data:
        all_uniforms.extend(fd["ctx"].uniforms)
        for name, body in fd["ctx"]._custom_helpers.items():
            if name not in merged_helpers:
                merged_helpers[name] = body

    seen_uniforms = set()
    uniform_decls_list = []
    for name, glsl_type, _ in all_uniforms:
        if name not in seen_uniforms:
            uniform_decls_list.append(f"uniform {glsl_type} {name};")
            seen_uniforms.add(name)
    # sampler2D uniforms bound by renderer per-frame (not via set_uniforms_from_ctx)
    for fd in fields_data:
        for name in fd["ctx"].sampler2d_names:
            if name not in seen_uniforms:
                uniform_decls_list.append(f"uniform sampler2D {name};")
                seen_uniforms.add(name)
    uniform_decls = "\n".join(uniform_decls_list)
    helper_defs = "\n".join(merged_helpers.values())

    eval_funcs = ""
    for i, fd in enumerate(fields_data):
        eval_funcs += f"""
float sdf_eval_{i}(vec3 p) {{
    return {fd['expr']};
}}

vec3 sdf_normal_{i}(vec3 p) {{
    float h = 0.1;
    vec2 k = vec2(1.0, -1.0);
    vec3 n = k.xyy * sdf_eval_{i}(p + k.xyy*h)
           + k.yyx * sdf_eval_{i}(p + k.yyx*h)
           + k.yxy * sdf_eval_{i}(p + k.yxy*h)
           + k.xxx * sdf_eval_{i}(p + k.xxx*h);
    float len2 = dot(n, n);
    return (len2 > 1e-10) ? n * inversesqrt(len2) : vec3(0.0, 0.0, 1.0);
}}
"""

    num_fields = len(fields_data)

    # bbox + subtractive flag as uniforms — keeps GLSL source stable across value changes
    bbox_uniform_decls = "\n".join(
        f"uniform vec3 u_bmin_{i};\nuniform vec3 u_bmax_{i};" for i in range(num_fields))
    sub_uniform_decls = "\n".join(f"uniform int u_sub_{i};" for i in range(num_fields))
    if bbox_uniform_decls:
        uniform_decls = uniform_decls + "\n" + bbox_uniform_decls
    if sub_uniform_decls:
        uniform_decls = uniform_decls + "\n" + sub_uniform_decls

    field_bboxes = ""
    for i in range(num_fields):
        field_bboxes += f"    vec3 bmin_{i} = u_bmin_{i};\n"
        field_bboxes += f"    vec3 bmax_{i} = u_bmax_{i};\n"

    intersect_bboxes = ""
    for i in range(num_fields):
        intersect_bboxes += f"    vec2 fi_int_{i} = intersect_aabb(ro, rd, bmin_{i}, bmax_{i});\n"
        intersect_bboxes += f"    ftn[{i}] = max(fi_int_{i}.x, 0.0);\n"
        intersect_bboxes += f"    ftf[{i}] = min(fi_int_{i}.y, ray_tmax);\n"

    sample_loop = ""
    for i, fd in enumerate(fields_data):
        sample_loop += f"""
        if (t >= ftn[{i}] && t <= ftf[{i}]) {{
            float d_{i} = sdf_eval_{i}(p);
            if (abs(d_{i}) < 0.2) {{ hit = true; hit_field = {i}; break; }}
            min_d = min(min_d, abs(d_{i}));
        }} else if (t < ftn[{i}]) {{
            min_d = min(min_d, ftn[{i}] - t);
        }}
"""

    normal_switch = "    vec3 n = vec3(0, 0, 1);\n"
    for i in range(num_fields):
        normal_switch += f"    if (hit_field == {i}) n = sdf_normal_{i}(hp);\n"

    color_switch = "    vec3 base_color = vec3(1.0, 0.5, 0.0);\n"
    for i in range(num_fields):
        color_switch += f"    if (hit_field == {i}) base_color = (u_sub_{i} != 0) ? vec3(0.3, 0.5, 1.0) : vec3(1.0, 0.5, 0.0);\n"

    scene_bbox_lines = "\n".join(
        f"    scene_min = min(scene_min, bmin_{i});\n    scene_max = max(scene_max, bmax_{i});"
        for i in range(1, num_fields)
    )

    return f"""#version 330 compatibility
in vec2 v_uv;

uniform vec3 u_light_dir;

{uniform_decls}

layout(location = 0) out vec4 out_color;
layout(location = 1) out vec4 out_vspos;
layout(location = 2) out vec4 out_vsnorm;

{helper_defs}

{eval_funcs}

vec2 intersect_aabb(vec3 ro, vec3 rd, vec3 bmin, vec3 bmax) {{
    vec3 t1 = (bmin - ro) / rd;
    vec3 t2 = (bmax - ro) / rd;
    vec3 tmin_ = min(t1, t2);
    vec3 tmax_ = max(t1, t2);
    return vec2(max(max(tmin_.x, tmin_.y), tmin_.z),
                min(min(tmax_.x, tmax_.y), tmax_.z));
}}

void main() {{
    vec4 ndc_near = vec4(v_uv, -1.0, 1.0);
    vec4 world_near = gl_ModelViewProjectionMatrixInverse * ndc_near;
    world_near /= world_near.w;
    vec4 ndc_far = vec4(v_uv, 1.0, 1.0);
    vec4 world_far = gl_ModelViewProjectionMatrixInverse * ndc_far;
    world_far /= world_far.w;

    vec3 ro = world_near.xyz;
    vec3 rd = normalize(world_far.xyz - world_near.xyz);
    float ray_tmax = length(world_far.xyz - world_near.xyz);

{field_bboxes}

    vec3 scene_min = bmin_0;
    vec3 scene_max = bmax_0;
{scene_bbox_lines}
    vec2 tBox = intersect_aabb(ro, rd, scene_min, scene_max);
    float tNear = max(tBox.x, 0.0);
    float tFar  = min(tBox.y, ray_tmax);

    if (tNear > tFar) {{
        out_color = vec4(0.0); out_vspos = vec4(0.0); out_vsnorm = vec4(0.0);
        return;
    }}

    float ftn[{num_fields}]; float ftf[{num_fields}];
{intersect_bboxes}

    float t = tNear;
    bool hit = false;
    int hit_field = 0;

    for (int i = 0; i < 512; i++) {{
        vec3 p = ro + t * rd;
        float min_d = 1.0e10;

{sample_loop}

        if (hit) break;
        t += max(min_d * 0.9, 0.1);
        if (t > tFar) break;
    }}

    if (!hit) {{
        out_color = vec4(0.0); out_vspos = vec4(0.0); out_vsnorm = vec4(0.0);
        return;
    }}

    vec3 hp = ro + t * rd;
{normal_switch}
    vec3 vd = normalize(-rd);

    float diff = max(dot(n, u_light_dir), 0.0);
    float spec = pow(max(dot(reflect(-u_light_dir, n), vd), 0.0), 32.0);

{color_switch}
    vec3 color = base_color * (0.25 + 0.70 * diff) + vec3(0.3) * spec;

    vec4 vs   = gl_ModelViewMatrix * vec4(hp, 1.0);
    vec3 vs_n = normalize(mat3(gl_ModelViewMatrix) * n);

    out_color  = vec4(color, 1.0);
    out_vspos  = vec4(vs.xyz, 1.0);
    out_vsnorm = vec4(vs_n,   1.0);

    vec4 clip   = gl_ModelViewProjectionMatrix * vec4(hp, 1.0);
    float ndc_z = clip.z / clip.w;
    gl_FragDepth = gl_DepthRange.near + gl_DepthRange.diff * (ndc_z * 0.5 + 0.5);
}}
"""
