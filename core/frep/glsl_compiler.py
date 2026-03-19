"""
core/frep/glsl_compiler.py

Compiles an SDF field tree into a GLSL compute shader that evaluates
the field on a 3D grid and writes results directly to a 3D texture.
"""


class GlslContext:
    """Collects uniforms and helper functions during SDF→GLSL compilation."""

    def __init__(self):
        self._uniforms = []   # [(name, glsl_type, value)]
        self._counter = 0
        self._helpers = set()

    def uniform(self, glsl_type, value):
        """Register a uniform and return its GLSL name."""
        name = f"u_f{self._counter}"
        self._counter += 1
        self._uniforms.append((name, glsl_type, value))
        return name

    def need_helper(self, name):
        """Mark an SDF helper function as needed."""
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
    "apply_inv_mat": """
vec3 apply_inv_mat(mat4 m, vec3 p) {
    return (m * vec4(p, 1.0)).xyz;
}
""",
}


def compile_field_to_glsl(field):
    """Compile an SDF field tree to a GLSL expression.

    Returns:
        (glsl_expression: str, ctx: GlslContext)
    """
    ctx = GlslContext()
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

    # Helper function definitions (sorted for deterministic output)
    helper_defs = "\n".join(
        GLSL_HELPERS[h] for h in sorted(ctx.helpers) if h in GLSL_HELPERS
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
