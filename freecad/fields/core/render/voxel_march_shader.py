# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Fixed compute shader for the scene voxel path (VX-006).

The source is a constant. It does not depend on the scene, so it compiles once
per process and no scene edit can ever trigger a recompile. Keep it that way:
anything that varies belongs in a uniform.
"""

VOXEL_MARCH_SRC = """#version 430
layout(local_size_x = 8, local_size_y = 8) in;

layout(rgba16f, binding = 0) writeonly uniform image2D img_color;
layout(rgba16f, binding = 1) writeonly uniform image2D img_vspos;
layout(rgba32f, binding = 2) writeonly uniform image2D img_vsnorm;
layout(r32f,    binding = 3) writeonly uniform image2D img_depth;

uniform sampler3D u_volume;
uniform usampler3D u_id_volume;
uniform sampler3D u_norm_volume;
uniform sampler2DArray u_matcaps;

struct FieldMeta {
    vec4 bmin;          // .w = reserved
    vec4 bmax;          // .w = vis_alpha
    vec4 diffuse;       // .w = subtractive flag (0/1)
    vec4 specular;      // .w = shininess
    vec4 flags;         // .x = selected (0/1), .yzw = selection outline colour
    vec4 matcap;        // .x = layer (<0 = off), .y = data layer (no hatch), .z = uv_source, .w = ao_bypass
};
layout(std430, binding = 3) readonly buffer FieldMetaBlock { FieldMeta u_fields[]; };

uniform int   u_max_steps;
uniform vec3  u_vol_min;
uniform vec3  u_vol_max;
uniform vec3  u_voxel_step;
uniform float u_band;
uniform float u_step_div;    // max lipschitz() over the baked fields; >= 1.0
uniform vec3  u_base_color;
uniform vec3  u_spec_color;
uniform float u_shininess;
uniform vec3  u_light_dir;
uniform ivec2 u_resolution;
uniform mat4  u_inv_mvp;
uniform mat4  u_mv;
uniform mat4  u_proj;
uniform vec3  u_hatch_color;    // crosshatch line colour
uniform float u_hatch_period;   // stripe period in MARCH pixels; <= 0 disables
uniform float u_hatch_strength; // 0..1 mix of hatch colour over body colour
uniform float u_curv_scale;     // curvature (1/mm) that saturates the curvature ramp

float sample_sdf(vec3 p) {
    vec3 uvw = (p - u_vol_min) / (u_vol_max - u_vol_min);
    return texture(u_volume, uvw).r;
}

vec3 sample_normal(vec3 p) {
    vec3 uvw = (p - u_vol_min) / (u_vol_max - u_vol_min);
    vec3 n = texture(u_norm_volume, uvw).xyz;
    float len2 = dot(n, n);
    return (len2 > 1e-6) ? n * inversesqrt(len2) : vec3(0.0, 0.0, 1.0);
}

// Mean curvature = 0.5 * div(n), by central differences of the baked normal volume.
// The normals are already unit length and smoother than the Laplacian of the raw
// distance volume, which is markedly noisier. Six taps per HIT pixel, not per step.
float mean_curvature(vec3 p) {
    vec3 e = u_voxel_step;
    float dnx = sample_normal(p + vec3(e.x, 0.0, 0.0)).x
              - sample_normal(p - vec3(e.x, 0.0, 0.0)).x;
    float dny = sample_normal(p + vec3(0.0, e.y, 0.0)).y
              - sample_normal(p - vec3(0.0, e.y, 0.0)).y;
    float dnz = sample_normal(p + vec3(0.0, 0.0, e.z)).z
              - sample_normal(p - vec3(0.0, 0.0, e.z)).z;
    return 0.5 * (dnx / (2.0 * e.x) + dny / (2.0 * e.y) + dnz / (2.0 * e.z));
}

vec2 intersect_aabb(vec3 ro, vec3 rd, vec3 bmin, vec3 bmax) {
    vec3 t1 = (bmin - ro) / rd;
    vec3 t2 = (bmax - ro) / rd;
    vec3 tmin_ = min(t1, t2);
    vec3 tmax_ = max(t1, t2);
    return vec2(max(max(tmin_.x, tmin_.y), tmin_.z),
                min(min(tmax_.x, tmax_.y), tmax_.z));
}

void write_miss(ivec2 pixel) {
    imageStore(img_color,  pixel, vec4(0.0));
    imageStore(img_vspos,  pixel, vec4(0.0));
    imageStore(img_vsnorm, pixel, vec4(0.0, 0.0, 0.0, 65535.0));
    imageStore(img_depth,  pixel, vec4(1.0, 0.0, 0.0, 0.0));
}

void main() {
    ivec2 pixel = ivec2(gl_GlobalInvocationID.xy);
    if (pixel.x >= u_resolution.x || pixel.y >= u_resolution.y) return;

    vec2 uv = (vec2(pixel) + 0.5) / vec2(u_resolution) * 2.0 - 1.0;

    vec4 world_near = u_inv_mvp * vec4(uv, -1.0, 1.0);
    world_near /= world_near.w;
    vec4 world_far = u_inv_mvp * vec4(uv, 1.0, 1.0);
    world_far /= world_far.w;

    vec3 ro = world_near.xyz;
    vec3 rd = normalize(world_far.xyz - world_near.xyz);
    float ray_tmax = length(world_far.xyz - world_near.xyz);

    vec2 tBox = intersect_aabb(ro, rd, u_vol_min, u_vol_max);
    float t    = max(tBox.x, 0.0);
    float tFar = min(tBox.y, ray_tmax);
    if (t > tFar) { write_miss(pixel); return; }

    float vmax = max(u_voxel_step.x, max(u_voxel_step.y, u_voxel_step.z));
    // The volume stores each field's own value, in millimetres. `eps` and the
    // polish below are therefore real lengths and must stay undivided: a field
    // that pre-scaled itself by its Lipschitz bound L would trip this test
    // 0.5*vmax*L mm out, fattening the solid along every axis at once (28 mm on
    // 2D noise at amp 100 -- the "deforms in x and y as well as z" report).
    // What the bound divides is the STEP, below. UX-009.
    float eps = 0.5 * vmax;
    float step_div = max(u_step_div, 1.0);

    bool hit = false;
    for (int i = 0; i < u_max_steps; i++) {
        vec3 p = ro + t * rd;
        float d = sample_sdf(p);
        if (d < eps) { hit = true; break; }
        // Empty-space skipping -- outside the narrow band the stored value
        // is saturated, so step the band rather than the (meaningless) value.
        // Either way it is a field value, and a field value only bounds the
        // distance to the surface from above by L (a warp stretches space), so
        // the sphere trace divides by L or it steps through the wall. The
        // half-voxel floor keeps the loop finite when L is large.
        float step_dist = max(((d >= u_band - 1e-3) ? u_band : d) / step_div,
                              0.5 * vmax);
        t += step_dist;
        if (t > tFar) break;
    }

    if (!hit) { write_miss(pixel); return; }

    // Refine onto the isosurface via Newton-Raphson along the ray using the normal:
    // dt = d / max(-dot(n, rd), 0.1)
    for (int r = 0; r < 3; r++) {
        vec3 p_cur = ro + t * rd;
        float d_cur = sample_sdf(p_cur);
        vec3 n_cur = sample_normal(p_cur);
        float slope = max(-dot(n_cur, rd), 0.1);
        float dt = clamp(d_cur / slope, -vmax, 2.0 * vmax);
        t += dt;
    }

    vec3 hp = ro + t * rd;
    vec3 n  = sample_normal(hp);
    vec3 vd = normalize(-rd);

    // Sample field ID slightly inside the surface along -normal to avoid fetching empty air
    vec3 hp_in = hp - n * (0.75 * vmax);
    ivec3 vc = ivec3(clamp((hp_in - u_vol_min) / u_voxel_step, vec3(0.0),
                           vec3(textureSize(u_id_volume, 0)) - 1.0));
    uint fid = texelFetch(u_id_volume, vc, 0).r;
    vec3  base_color = (fid == 65535u) ? u_base_color : u_fields[fid].diffuse.rgb;
    vec3  spec_col   = (fid == 65535u) ? u_spec_color : u_fields[fid].specular.rgb;
    float shininess  = (fid == 65535u) ? u_shininess  : u_fields[fid].specular.w;
    float vis_alpha  = (fid == 65535u) ? 1.0          : u_fields[fid].bmax.w;

    // vis_alpha is real opacity and must stay exactly 1.0 for opaque fields:
    // the composite pass treats anything < 0.95 as transparent and forces
    // gl_FragDepth to 0.9999, which destroys depth ordering. Do not fold a
    // coverage/AA term into it. Silhouette coverage would have to come from the
    // *minimum* distance seen along the ray, tracked during the march -- the
    // distance at the hit point is ~0 on every surface pixel and carries no
    // edge information at all.

    // img_color.a is the selection channel the composite pass edge-detects on:
    // 1.0 = unselected, 0.5 = selected. It is not opacity -- that rides in
    // img_vspos.w.
    //
    // There was a third code, 0.3, for "selected AND subtractive", and its only
    // reader chose between two hardcoded outline colours. The outline colour is
    // now the object's own, read from its FieldMeta row through the surface id in
    // img_vsnorm.w, so the code has no reader and is gone rather than kept in
    // case something wants it back.
    float alpha = (fid != 65535u && u_fields[fid].flags.x != 0.0) ? 0.5 : 1.0;

    vec4 vs   = u_mv * vec4(hp, 1.0);
    vec3 vs_n = normalize(mat3(u_mv) * n);

    vec4  mc_row   = (fid == 65535u) ? vec4(-1.0, 0.0, 0.0, 0.0) : u_fields[fid].matcap;
    float mc_layer = mc_row.x;

    vec3 color;
    if (mc_layer < 0.0) {
        // No matcap: the fixed-direction Lambert + Blinn path.
        float diff = max(dot(n, u_light_dir), 0.0);
        vec3  h    = normalize(u_light_dir + vd);
        float spec = pow(max(dot(n, h), 0.0), shininess);
        color = base_color * (0.35 + 0.60 * diff) + spec_col * 0.3 * spec;
    } else {
        vec2 mc_uv;
        if (mc_row.z < 0.5) {
            // Spherical environment map UV. NOT vs_n.xy * 0.5 + 0.5: that form
            // ignores the per-fragment view vector, so off-axis objects shade as
            // though centre-frame, and it lands exactly on the texture border at
            // the silhouette. Holds under both camera types.
            //
            // The view ray in view space. NOT normalize(vs.xyz): that is the
            // eye-to-point direction, the ray only under perspective, and FreeCAD
            // opens with an orthographic camera. rd is the march ray itself,
            // unprojected from the near/far planes, so it is right in both.
            vec3  vs_rd = normalize(mat3(u_mv) * rd);
            // Voxel normals at the silhouette can tip just past grazing, facing
            // away from the camera. The SEM lookup is discontinuous there: a
            // back-facing normal reflects to the OPPOSITE side of the disc, so the
            // bottom limb samples the sky and the top limb the ground -- a one-pixel
            // inverted rim. Clamp to just in front of grazing, for the lookup only;
            // vs_n itself is what the g-buffer stores for SSAO and the outline id.
            vec3  ln = vs_n;
            float nv = dot(ln, vs_rd);
            if (nv > 0.0) {
                vec3  t  = ln - vs_rd * nv;      // the part across the view ray
                float tl = length(t);
                ln = (tl > 1e-4) ? normalize(t / tl - vs_rd * 1e-3) : -vs_rd;
            }
            vec3  r = reflect(vs_rd, ln);
            float m = 2.0 * sqrt(r.x * r.x + r.y * r.y + (r.z + 1.0) * (r.z + 1.0));
            vec2  q = r.xy / max(m, 1e-4);
            // |q| <= 0.5 in exact arithmetic, and 0.5 is the disc border. Pull it in
            // by one texel so bilinear filtering never reaches the transparent
            // black outside the disc (MATCAP_SIZE = 256).
            float ql = length(q);
            if (ql > 0.496) q *= 0.496 / ql;
            mc_uv = q + 0.5;
        } else {
            // The same layer read as a 1D ramp along its centre row: 0.5 is flat,
            // below is concave, above is convex.
            float k = mean_curvature(hp);
            mc_uv = vec2(clamp(0.5 + 0.5 * k / max(u_curv_scale, 1e-6), 0.0, 1.0), 0.5);
        }
        // The matcap owns the colour: ShapeColor shades only the Matcap = None path
        // above. Colour comes from the material or a baked variant, whose
        // highlights stay white where a multiply would tint them.
        color = texture(u_matcaps, vec3(mc_uv, mc_layer)).rgb;
    }

    // Subtractive shapes are marked by screen-space diagonal lines rather than by
    // hue (the body colour is the user's, set on the object) and rather than by
    // translucency (which would drive every such pixel through the transparency
    // branch in _FRAG_COMP and force gl_FragDepth = 0.9999, destroying depth
    // order -- see the comment on view-space position above).
    //
    // ONE family of diagonals, not two. The crossed version was reported as hard
    // on the eyes on 2026-08-28, and crossing is what did it: two families at 90
    // degrees beat against the pixel grid and against each other, so the moire
    // moves whenever the camera does. A single family has no second frequency to
    // beat with, and it is the ordinary drafting convention for a cut face.
    //
    // Antialiased without fwidth, which compute shaders do not portably provide:
    // in screen space the phase gradient of a stripe of period P pixels is exactly
    // 1/P, so the smoothstep width is known analytically.
    //
    // Skipped for data layers (matcap.y): on a zebra readout the hatch is a second
    // stripe family beating against the first, and on a curvature ramp it injects
    // colours the ramp does not define. It DOES draw on a subtractive lighting matcap.
    if (fid != 65535u && u_fields[fid].diffuse.w != 0.0
        && u_fields[fid].matcap.y < 0.5
        && u_hatch_period > 0.0 && u_hatch_strength > 0.0) {
        vec2  pf   = vec2(pixel);
        float invp = 1.0 / u_hatch_period;
        float a = abs(fract(dot(pf, vec2(0.70711, 0.70711)) * invp) - 0.5);

        // `a` is the distance from a stripe centre in phase units and runs 0..0.5,
        // so the line covers 2*HW of the period and its two border edges 2*BW more:
        // 2*(HW + BW) = 30% ink at these values. Both are FRACTIONS of the period,
        // not pixel counts, so the line and its border thin out together as the
        // period tightens and the one size preference still controls the look.
        // At the default 8 px period that is a 1.2 px line inside a 0.6 px border.
        //
        // The border is black and sits OUTSIDE the line, which is the whole reason
        // it exists: the body colour belongs to the user, and a bare blue line on a
        // blue object is invisible. A dark edge separates the marking from any
        // colour underneath without tinting the body itself.
        const float HW = 0.075;   // half-width of the line
        const float BW = 0.075;   // border thickness on each side of it
        float w = 0.5 * invp;                       // AA ramp, ~1 px across
        float line   = 1.0 - smoothstep(HW - w, HW + w, a);
        float outer  = 1.0 - smoothstep(HW + BW - w, HW + BW + w, a);
        // Border first, line over it: painted the other way round the line's own
        // antialiased edge would be darkened by the border it is supposed to sit in.
        color = mix(color, vec3(0.0),      (outer - line) * u_hatch_strength);
        color = mix(color, u_hatch_color,  line          * u_hatch_strength);
    }

    imageStore(img_color,  pixel, vec4(color, alpha));
    imageStore(img_vspos,  pixel, vec4(vs.xyz, vis_alpha));
    // .w is the surface-id pick channel (TR-008), read back by pick_surface_at.
    // It is `rgba32f` and not `rgba16f` precisely for this: half-float represents
    // integers exactly only to 2048, and ids run to 65534. 65535.0 is the miss
    // sentinel and is not even finite in fp16 (max 65504).
    imageStore(img_vsnorm, pixel, vec4(vs_n,   float(fid)));

    vec4 clip = u_proj * vs;
    float depth_val = (vis_alpha >= 0.95) ? (clip.z / clip.w * 0.5 + 0.5) : 1.0;
    imageStore(img_depth, pixel, vec4(depth_val, 0.0, 0.0, 0.0));
}
"""
