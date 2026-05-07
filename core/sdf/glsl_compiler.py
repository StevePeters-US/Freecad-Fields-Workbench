"""
core/sdf/glsl_compiler.py

Compiles an SDF field tree into a GLSL compute shader that evaluates
the field on a 3D grid and writes results directly to a 3D texture.
"""


import uuid

class GlslContext:
    """Collects uniforms and helper functions during SDF→GLSL compilation."""

    def __init__(self, prefix=None):
        self._uniforms = []   # [(name, glsl_type, value)]
        self._counter = 0
        self._helpers = set()
        self._custom_helpers = {}  # name → GLSL function body string
        self._helper_counter = 0   # Separate counter for unique helper names
        if prefix is None:
            self._prefix = f"u_{uuid.uuid4().hex[:8]}_"
        else:
            # Sanitize prefix for GLSL (no dots, etc)
            clean_prefix = prefix.replace(".", "_").replace(" ", "_").replace("-", "_")
            self._prefix = f"u_{clean_prefix}_"

    def get_unique_name(self, base_name: str) -> str:
        """Returns a unique name for a helper or variable, tied to this context's prefix."""
        name = f"{self._prefix}{base_name}_{self._helper_counter}"
        self._helper_counter += 1
        return name

    def uniform(self, glsl_type, value):
        """Register a uniform and return its GLSL name."""
        name = f"{self._prefix}{self._counter}"
        self._counter += 1
        self._uniforms.append((name, glsl_type, value))
        return name

    def need_helper(self, name):
        """Mark a built-in SDF helper function as needed."""
        self._helpers.add(name)

    def add_custom_helper(self, name: str, body: str):
        """Register an inline-generated GLSL helper (e.g. per-polygon functions)."""
        self._custom_helpers[name] = body
        self._helpers.add(name)

    @property
    def uniforms(self):
        return list(self._uniforms)

    @property
    def helpers(self):
        return set(self._helpers)


# GLSL helper function definitions for SDF primitives
GLSL_HELPERS = {
    "sdf_box": """
float sdf_box(vec3 p, vec3 center, vec3 half_size) {
    vec3 d = abs(p - center) - half_size;
    return length(max(d, 0.0)) + min(max(d.x, max(d.y, d.z)), 0.0);
}
""",
    "sdf_sphere": """
float sdf_sphere(vec3 p, vec3 center, float radius) {
    return length(p - center) - radius;
}
""",
    "sdf_cylinder": """
float sdf_cylinder(vec3 p, vec3 base_center, vec3 axis, float radius, float height) {
    vec3 pa = p - base_center;
    float h = dot(pa, axis);
    vec3 radial = pa - axis * h;
    float d_radial = length(radial) - radius;
    float h_center = h - height * 0.5;
    float d_axial = abs(h_center) - abs(height) * 0.5;
    float d_r_pos = max(d_radial, 0.0);
    float d_a_pos = max(d_axial, 0.0);
    return sqrt(d_r_pos * d_r_pos + d_a_pos * d_a_pos) + min(max(d_radial, d_axial), 0.0);
}
""",
    "sdf_plane": """
float sdf_plane(vec3 p, vec3 origin, vec3 normal) {
    return dot(p - origin, normal);
}
""",
    "sdf_torus": """
float sdf_torus(vec3 p, vec3 center, float major_r, float tube_r) {
    vec3 lp = p - center;
    vec2 q = vec2(length(lp.xy) - major_r, lp.z);
    return length(q) - tube_r;
}
""",
    "apply_inv_mat": """
vec3 apply_inv_mat(mat4 m, vec3 p) {
    return (m * vec4(p, 1.0)).xyz;
}
""",
    "smooth_union": """
float smooth_union(float a, float b, float k) {
    float h = clamp(0.5 + 0.5*(b-a)/k, 0.0, 1.0);
    return mix(b, a, h) - k*h*(1.0-h);
}
""",
    "smooth_subtraction": """
float smooth_subtraction(float a, float b, float k) {
    float h = clamp(0.5 - 0.5*(a+b)/k, 0.0, 1.0);
    return mix(a, -b, h) + k*h*(1.0-h);
}
""",
    "smooth_intersection": """
float smooth_intersection(float a, float b, float k) {
    float h = clamp(0.5 - 0.5*(b-a)/k, 0.0, 1.0);
    return mix(b, a, h) + k*h*(1.0-h);
}
""",
    "sdf_nurbs_curve": """
vec3 evaluate_bspline(float t, vec3 poles[32], float knots[32], int degree, int n) {
    int k = degree;
    for (int i = degree; i < n; i++) {
        if (t >= knots[i]) k = i;
    }
    vec3 d[4]; 
    for (int i = 0; i <= 3; i++) {
        if (i <= degree) d[i] = poles[clamp(k - degree + i, 0, n-1)];
    }
    for (int r = 1; r <= 3; r++) {
        if (r > degree) break;
        for (int i = 3; i >= 1; i--) {
            if (i < r || i > degree) continue;
            float den = knots[k + 1 + i - r] - knots[k - degree + i];
            float alpha = (den > 1e-8) ? (t - knots[k - degree + i]) / den : 0.0;
            d[i] = mix(d[i-1], d[i], alpha);
        }
    }
    return d[clamp(degree, 0, 3)];
}

vec3 bspline_deriv(float t, vec3 poles[32], float knots[32], int degree, int n) {
    if (degree < 1) return vec3(0.0);
    int k = degree;
    for (int i = degree; i < n; i++) {
        if (t >= knots[i]) k = i;
    }
    vec3 d[4];
    for (int i = 0; i < degree; i++) {
        float den = knots[k - degree + i + degree + 1] - knots[k - degree + i + 1];
        float alpha = (den > 1e-8) ? float(degree) / den : 0.0;
        d[i] = (poles[k - degree + i + 1] - poles[k - degree + i]) * alpha;
    }
    // Now evaluate this B-spline of degree-1
    int deg1 = degree - 1;
    for (int r = 1; r <= 2; r++) {
        if (r > deg1) break;
        for (int i = 2; i >= 1; i--) {
            if (i < r || i > deg1) continue;
            float den = knots[k + 1 + i - r] - knots[k - deg1 + i];
            float alpha = (den > 1e-8) ? (t - knots[k - deg1 + i]) / den : 0.0;
            d[i] = mix(d[i-1], d[i], alpha);
        }
    }
    return d[clamp(deg1, 0, 2)];
}

float sdf_nurbs_curve(vec3 p, vec3 poles[32], float knots[32], int degree, int n, float u0, float u1, float r) {
    float min_d2 = 1e18;
    float best_t = u0;
    for (int i = 0; i <= 16; i++) {
        float ut = u0 + (u1 - u0) * float(i) / 16.0;
        vec3 q = evaluate_bspline(ut, poles, knots, degree, n);
        float d2 = dot(p - q, p - q);
        if (d2 < min_d2) { min_d2 = d2; best_t = ut; }
    }
    float t = best_t;
    for (int i = 0; i < 4; i++) {
        vec3 q = evaluate_bspline(t, poles, knots, degree, n);
        vec3 dq = bspline_deriv(t, poles, knots, degree, n);
        float d2 = dot(dq, dq);
        if (d2 > 1e-8) t = clamp(t - dot(q - p, dq) / d2, u0, u1);
    }
    vec3 final_q = evaluate_bspline(t, poles, knots, degree, n);
    return length(p - final_q) - r;
}
""",
    "sdf_nurbs_surface": """
vec3 evaluate_bspline_surf(vec2 uv, vec3 poles[256], float u_knots[32], float v_knots[32], int u_deg, int v_deg, int nu, int nv) {
    int ku = u_deg;
    for (int i = u_deg; i < nu; i++) if (uv.x >= u_knots[i]) ku = i;
    int kv = v_deg;
    for (int i = v_deg; i < nv; i++) if (uv.y >= v_knots[i]) kv = i;

    vec3 temp_v[4];
    for (int j = 0; j <= 3; j++) {
        if (j > v_deg) break;
        int v_idx = clamp(kv - v_deg + j, 0, nv - 1);
        
        vec3 d[4];
        for (int i = 0; i <= 3; i++) {
            if (i > u_deg) break;
            int u_idx = clamp(ku - u_deg + i, 0, nu - 1);
            d[i] = poles[u_idx * 16 + v_idx]; // Assuming max_v = 16
        }
        
        for (int r = 1; r <= 3; r++) {
            if (r > u_deg) break;
            for (int i = 3; i >= 1; i--) {
                if (i < r || i > u_deg) continue;
                float den = u_knots[ku + 1 + i - r] - u_knots[ku - u_deg + i];
                float alpha = (den > 1e-8) ? (uv.x - u_knots[ku - u_deg + i]) / den : 0.0;
                d[i] = mix(d[i-1], d[i], alpha);
            }
        }
        temp_v[j] = d[clamp(u_deg, 0, 3)];
    }

    for (int r = 1; r <= 3; r++) {
        if (r > v_deg) break;
        for (int j = 3; j >= 1; j--) {
            if (j < r || j > v_deg) continue;
            float den = v_knots[kv + 1 + j - r] - v_knots[kv - v_deg + j];
            float alpha = (den > 1e-8) ? (uv.y - v_knots[kv - v_deg + j]) / den : 0.0;
            temp_v[j] = mix(temp_v[j-1], temp_v[j], alpha);
        }
    }
    return temp_v[clamp(v_deg, 0, 3)];
}

float sdf_nurbs_surface(vec3 p, vec3 poles[256], float u_knots[32], float v_knots[32], int u_deg, int v_deg, int nu, int nv, vec2 uv0, vec2 uv1) {
    float min_d2 = 1e18;
    vec2 best_uv = uv0;
    for (int i = 0; i <= 8; i++) {
        for (int j = 0; j <= 8; j++) {
            vec2 uv = uv0 + (uv1 - uv0) * vec2(float(i)/8.0, float(j)/8.0);
            vec3 q = evaluate_bspline_surf(uv, poles, u_knots, v_knots, u_deg, v_deg, nu, nv);
            float d2 = dot(p - q, p - q);
            if (d2 < min_d2) { min_d2 = d2; best_uv = uv; }
        }
    }
    
    vec2 uv = best_uv;
    for (int i = 0; i < 4; i++) {
        vec3 q = evaluate_bspline_surf(uv, poles, u_knots, v_knots, u_deg, v_deg, nu, nv);
        float eps = 1e-4;
        vec3 qu = (evaluate_bspline_surf(uv + vec2(eps, 0), poles, u_knots, v_knots, u_deg, v_deg, nu, nv) - q) / eps;
        vec3 qv = (evaluate_bspline_surf(uv + vec2(0, eps), poles, u_knots, v_knots, u_deg, v_deg, nu, nv) - q) / eps;
        
        vec2 b = vec2(dot(p - q, qu), dot(p - q, qv));
        mat2 A = mat2(dot(qu, qu), dot(qv, qu), dot(qu, qv), dot(qv, qv));
        float det = A[0][0]*A[1][1] - A[0][1]*A[1][0];
        if (abs(det) > 1e-10) {
            vec2 duv = vec2(A[1][1]*b.x - A[0][1]*b.y, -A[1][0]*b.x + A[0][0]*b.y) / det;
            uv = clamp(uv + duv, uv0, uv1);
        }
    }
    
    vec3 final_q = evaluate_bspline_surf(uv, poles, u_knots, v_knots, u_deg, v_deg, nu, nv);
    vec3 final_normal = normalize(cross(
        (evaluate_bspline_surf(uv + vec2(1e-4, 0), poles, u_knots, v_knots, u_deg, v_deg, nu, nv) - final_q),
        (evaluate_bspline_surf(uv + vec2(0, 1e-4), poles, u_knots, v_knots, u_deg, v_deg, nu, nv) - final_q)
    ));
    float dist = length(p - final_q);
    return (dot(p - final_q, final_normal) >= 0.0 ? 1.0 : -1.0) * dist;
}
""",
    "sdf_box2d": """
float sdf_box2d(vec2 p, vec2 h) {
    vec2 d = abs(p) - h;
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
}
""",
}


def compile_field_to_glsl(field, prefix=None):
    """Compile an SDF field tree to a GLSL expression.

    Returns:
        (glsl_expression: str, ctx: GlslContext)
    """
    ctx = GlslContext(prefix=prefix)
    expr = field.to_glsl(ctx, "p")
    return expr, ctx


def build_compute_shader(expression, ctx):
    """Build a complete compute shader source from a compiled SDF expression.

    Returns the full GLSL compute shader source string.
    """
    # Uniform declarations
    uniform_decls = "\n".join(
        f"uniform {glsl_type} {name};"
        for name, glsl_type, _ in ctx.uniforms
    )

    # Merge static helpers with any inline-generated helpers (e.g. per-polygon)
    all_helper_bodies = {**GLSL_HELPERS, **ctx._custom_helpers}
    # Static helpers in any order; custom helpers in insertion order (topological —
    # child helpers like sdf_poly64_xxx are added before parents that call them).
    static_needed = sorted(h for h in ctx.helpers if h in GLSL_HELPERS)
    custom_in_order = [h for h in ctx._custom_helpers if h in ctx.helpers]
    helper_defs = "\n".join(
        all_helper_bodies[h] for h in (static_needed + custom_in_order) if h in all_helper_bodies
    )

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

def build_multi_raymarch_fragment_shader(fields_data):
    """Build a complete fragment shader that evaluates multiple analytical SDF fields.
    
    fields_data: list of dicts with keys:
        - "expr": str (the GLSL expression)
        - "ctx": GlslContext
        - "is_subtractive": bool
        - "bbox_min": vec3
        - "bbox_max": vec3
    """
    all_uniforms = []
    all_helpers = set()
    all_custom_helpers = {}
    for fd in fields_data:
        all_uniforms.extend(fd["ctx"].uniforms)
        all_helpers.update(fd["ctx"].helpers)
        all_custom_helpers.update(fd["ctx"]._custom_helpers)

    seen_uniforms = set()
    uniform_decls_list = []
    for name, glsl_type, _ in all_uniforms:
        if name not in seen_uniforms:
            uniform_decls_list.append(f"uniform {glsl_type} {name};")
            seen_uniforms.add(name)
    uniform_decls = "\n".join(uniform_decls_list)

    all_helper_bodies = {**GLSL_HELPERS, **all_custom_helpers}
    # Static helpers in any order; custom helpers in insertion order (topological).
    static_needed = sorted(h for h in all_helpers if h in GLSL_HELPERS)
    custom_in_order = [h for h in all_custom_helpers if h in all_helpers]
    helper_defs = "\n".join(
        all_helper_bodies[h] for h in (static_needed + custom_in_order) if h in all_helper_bodies
    )
    
    # Generate per-field eval functions
    eval_funcs = ""
    for i, fd in enumerate(fields_data):
        eval_funcs += f"""
float sdf_eval_{i}(vec3 p) {{
    return {fd['expr']};
}}

vec3 sdf_normal_{i}(vec3 p) {{
    float h = 1.0;
    vec2 k = vec2(1.0, -1.0);
    vec3 n = k.xyy * sdf_eval_{i}(p + k.xyy*h)
           + k.yyx * sdf_eval_{i}(p + k.yyx*h)
           + k.yxy * sdf_eval_{i}(p + k.yxy*h)
           + k.xxx * sdf_eval_{i}(p + k.xxx*h);
    float len2 = dot(n, n);
    return (len2 > 1e-10) ? n * inversesqrt(len2) : vec3(0.0, 0.0, 1.0);
}}
"""

    # Generate the main sphere trace field loop
    num_fields = len(fields_data)
    
    field_bboxes = ""
    for i, fd in enumerate(fields_data):
        bmin = fd["bbox_min"]
        bmax = fd["bbox_max"]
        field_bboxes += f"    vec3 bmin_{i} = vec3({bmin.x}, {bmin.y}, {bmin.z});\n"
        field_bboxes += f"    vec3 bmax_{i} = vec3({bmax.x}, {bmax.y}, {bmax.z});\n"

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
            if (abs(d_{i}) < 0.005) {{ hit = true; hit_field = {i}; break; }}
            min_d = min(min_d, abs(d_{i}));
        }} else if (t < ftn[{i}]) {{
            min_d = min(min_d, ftn[{i}] - t);
        }}
"""

    normal_switch = "    vec3 n = vec3(0, 0, 1);\n"
    for i in range(num_fields):
        normal_switch += f"    if (hit_field == {i}) n = sdf_normal_{i}(hp);\n"
        
    color_switch = "    vec3 base_color = vec3(1.0, 0.5, 0.0);\n"
    for i, fd in enumerate(fields_data):
        color = "vec3(0.3, 0.5, 1.0)" if fd["is_subtractive"] else "vec3(1.0, 0.5, 0.0)"
        color_switch += f"    if (hit_field == {i}) base_color = {color};\n"

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
""" + ("\n".join(f"    scene_min = min(scene_min, bmin_{i});\n    scene_max = max(scene_max, bmax_{i});" for i in range(1, num_fields))) + f"""
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
        t += max(min_d * 0.9, 0.005);
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

