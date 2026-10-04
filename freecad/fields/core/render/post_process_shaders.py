# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/post_process_shaders.py

GLSL source for the renderer's screen-space post-process passes: the shared
pass-through vertex shader and the SSAO, blur and composite fragment shaders.

Split out of fld_scene_voxel_renderer.py -- these are data, not logic, and they
were 10% of that file. The march itself is not here: it lives in
voxel_march_shader.py, because it is a compute shader and is compiled through a
different path.
"""

_VERT_PASSTHROUGH = """
#version 330 compatibility
out vec2 v_uv;
void main() {
    v_uv = gl_Vertex.xy;
    gl_Position = vec4(gl_Vertex.xy, 0.0, 1.0);
}
"""

_FRAG_SSAO = """
#version 330 compatibility
in vec2 v_uv;

uniform sampler2D u_pos;
uniform sampler2D u_normal;
uniform sampler2D u_noise;
uniform vec3      u_samples[64];
uniform mat4      u_proj;
uniform vec2      u_noise_scale;
uniform float     u_res_scale;

const float RADIUS = 50.0;
const float BIAS   = 0.025;

void main() {
    // G-buffer textures are allocated at native window size but only the
    // bottom-left (content_uv * u_res_scale) sub-rect holds this frame's
    // render — the rest is stale/garbage from a previous resolution. See
    // u_res_scale usage in the compute/composite passes for the same scheme.
    vec2 content_uv = v_uv * 0.5 + 0.5;
    vec2 uv = content_uv * u_res_scale;

    vec3 frag_pos = texture(u_pos, uv).xyz;
    vec3 n_raw    = texture(u_normal, uv).rgb;

    if (dot(n_raw, n_raw) < 0.25) {
        gl_FragColor = vec4(1.0);
        return;
    }

    vec3 normal   = normalize(n_raw);
    vec3 rand_vec = normalize(texture(u_noise, content_uv * u_noise_scale).rgb);

    vec3 tangent   = normalize(rand_vec - normal * dot(rand_vec, normal));
    vec3 bitangent = cross(normal, tangent);
    mat3 TBN       = mat3(tangent, bitangent, normal);

    float occlusion = 0.0;
    for (int i = 0; i < 64; i++) {
        vec3 s = frag_pos + TBN * u_samples[i] * RADIUS;

        vec4 offset = u_proj * vec4(s, 1.0);
        offset.xyz /= offset.w;
        offset.xyz  = offset.xyz * 0.5 + 0.5;
        vec2 sample_uv = clamp(offset.xy, 0.0, 1.0) * u_res_scale;

        float sample_depth = texture(u_pos, sample_uv).z;
        vec3  sample_n     = texture(u_normal, sample_uv).rgb;
        if (dot(sample_n, sample_n) > 0.25) {
            float range_check  = smoothstep(0.0, 1.0, RADIUS / abs(frag_pos.z - sample_depth));
            occlusion += (sample_depth >= s.z + BIAS ? 1.0 : 0.0) * range_check;
        }
    }

    float ao = 1.0 - (occlusion / 64.0);
    gl_FragColor = vec4(ao, ao, ao, 1.0);
}
"""

_FRAG_BLUR = """
#version 330 compatibility
in vec2 v_uv;

uniform sampler2D u_ssao;
uniform vec2      u_texel_size;
uniform float     u_res_scale;

void main() {
    vec2 uv = (v_uv * 0.5 + 0.5) * u_res_scale;
    float result = 0.0;
    for (int x = -2; x < 2; x++) {
        for (int y = -2; y < 2; y++) {
            vec2 offset = vec2(float(x), float(y)) * u_texel_size;
            result += texture(u_ssao, uv + offset).r;
        }
    }
    gl_FragColor = vec4(result / 16.0);
}
"""

_FRAG_COMP = """#version 430 compatibility
// 430, not 330, so this pass can read the same FieldMeta rows the march does.
// The outline colour is per object (ViewObject.SelectionColor) and there is no
// second copy of it: no palette uniform, no lookup texture beside the SSBO.
// A runtime-indexed uniform ARRAY would have been the other way to do it and is
// the one thing that must not come back here -- CP-014, NVIDIA register spill.
// The #version line stays first: comments before it are legal GLSL and are also
// the kind of thing a driver gets wrong.
in vec2 v_uv;

uniform sampler2D u_color;
uniform sampler2D u_pos;
uniform sampler2D u_occlusion;
uniform sampler2D u_depth;
uniform sampler2D u_norm;      // .w is the surface id the march wrote (TR-008)
uniform int       u_outline_size;
uniform int       u_field_count;
uniform float     u_res_scale;
uniform bool      u_any_selected;  // false → skip outline loop entirely (VR-030)

struct FieldMeta {
    vec4 bmin;
    vec4 bmax;
    vec4 diffuse;
    vec4 specular;
    vec4 flags;         // .x = selected (0/1), .yzw = selection outline colour
};
layout(std430, binding = 3) readonly buffer FieldMetaBlock { FieldMeta u_fields[]; };

// The id volume can outlive the table that was uploaded with it -- delete a
// field and the table shrinks while a clean volume is not rebaked -- so an id
// read back from the g-buffer is bounds-checked before it indexes the SSBO.
// Out of range means no outline, never a read past the end of the buffer.
bool outline_color_at(ivec2 pos, out vec3 col) {
    float sid = texelFetch(u_norm, pos, 0).w;
    int fid = int(sid + 0.5);
    if (sid < 0.0 || fid >= u_field_count) return false;
    col = u_fields[fid].flags.yzw;
    return true;
}

void main() {
    vec2 uv    = (v_uv * 0.5 + 0.5) * u_res_scale;
    ivec2 tex_size = textureSize(u_color, 0);
    // Only the bottom-left (tex_size * u_res_scale) sub-rect holds this frame's
    // content — clamp neighbor lookups to that, not the full backing texture,
    // or edge detection near the downscaled frame's border reads stale/unrelated data.
    ivec2 content_size = ivec2(vec2(tex_size) * u_res_scale);
    ivec2 center_pos = ivec2(gl_FragCoord.xy * u_res_scale);
    vec4 color = texelFetch(u_color, center_pos, 0);

    // img_color.a is the selection channel: 1.0 unselected, 0.5 selected.
    // It used to carry a third code, 0.3, meaning "selected AND subtractive",
    // read only to choose between two hardcoded outline colours. The colour now
    // comes from the selected object's own FieldMeta row, so that code had no
    // remaining reader and the march no longer writes it.
    float center_sel = (color.a > 0.2 && color.a < 0.6) ? 1.0 : 0.0;
    float edge = 0.0;
    ivec2 sel_pos = center_pos;   // the SELECTED side of the edge; its row wins

    // Only perform selection edge detection if the pixel is not on an unselected object (alpha ~1.0).
    // u_any_selected is false when the last-marched SSBO had no selected fields, so the
    // 289-iteration neighbourhood sweep is an identity and can be skipped entirely (VR-030).
    if (u_any_selected && color.a < 0.8) {
        int size = clamp(u_outline_size, 0, 8);
        for (int dx = -8; dx <= 8; dx++) {
            if (abs(dx) > size) continue;
            for (int dy = -8; dy <= 8; dy++) {
                if (abs(dy) > size) continue;
                if (float(dx*dx + dy*dy) > float(size*size) + 0.5) continue;
                if (dx == 0 && dy == 0) continue;
                ivec2 offset_pos = clamp(ivec2((gl_FragCoord.xy + vec2(float(dx), float(dy))) * u_res_scale), ivec2(0), content_size - ivec2(1));
                float neighbor_a = texelFetch(u_color, offset_pos, 0).a;
                float neighbor_sel = (neighbor_a > 0.2 && neighbor_a < 0.6) ? 1.0 : 0.0;
                if (center_sel != neighbor_sel) {
                    edge = 1.0;
                    sel_pos = (center_sel > 0.5) ? center_pos : offset_pos;
                    break;
                }
            }
            if (edge > 0.5) break;
        }
    }

    if (edge > 0.5) {
        vec3 outline_color;
        if (outline_color_at(sel_pos, outline_color)) {
            gl_FragColor = vec4(outline_color, 1.0);
            gl_FragDepth = texture(u_depth, uv).r;
            return;
        }
    }

    if (color.a < 0.2) discard;

    float ao    = texture(u_occlusion, uv).r;
    vec3 result = color.rgb * ao;
    result      = pow(result, vec3(1.0 / 2.2));

    float vis_alpha = texelFetch(u_pos, center_pos, 0).w;
    bool transparent = (vis_alpha < 0.95);
    gl_FragColor = vec4(result, transparent ? vis_alpha : 1.0);
    gl_FragDepth = transparent ? 0.9999 : texture(u_depth, uv).r;
}
"""
