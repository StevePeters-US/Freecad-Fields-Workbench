#version 330 compatibility
in vec2 v_uv;

uniform vec3 u_light_dir;

uniform vec3 u_f0;
uniform float u_f1;

layout(location = 0) out vec4 out_color;
layout(location = 1) out vec4 out_vspos;
layout(location = 2) out vec4 out_vsnorm;


float sdf_sphere(vec3 p, vec3 center, float radius) {
    return length(p - center) - radius;
}



float sdf_eval_0(vec3 p) {
    return sdf_sphere(p, u_f0, u_f1);
}

vec3 sdf_normal_0(vec3 p) {
    vec2 e = vec2(0.001, 0.0);
    vec3 n = sdf_eval_0(p) - vec3(
        sdf_eval_0(p - e.xyy),
        sdf_eval_0(p - e.yxy),
        sdf_eval_0(p - e.yyx)
    );
    return normalize(n);
}


vec2 intersect_aabb(vec3 ro, vec3 rd, vec3 bmin, vec3 bmax) {
    vec3 t1 = (bmin - ro) / rd;
    vec3 t2 = (bmax - ro) / rd;
    vec3 tmin_ = min(t1, t2);
    vec3 tmax_ = max(t1, t2);
    return vec2(max(max(tmin_.x, tmin_.y), tmin_.z),
                min(min(tmax_.x, tmax_.y), tmax_.z));
}

void main() {
    vec4 ndc_near = vec4(v_uv, -1.0, 1.0);
    vec4 world_near = gl_ModelViewProjectionMatrixInverse * ndc_near;
    world_near /= world_near.w;
    vec4 ndc_far = vec4(v_uv, 1.0, 1.0);
    vec4 world_far = gl_ModelViewProjectionMatrixInverse * ndc_far;
    world_far /= world_far.w;

    vec3 ro = world_near.xyz;
    vec3 rd = normalize(world_far.xyz - world_near.xyz);
    float ray_tmax = length(world_far.xyz - world_near.xyz);

    vec3 bmin_0 = vec3(-10.0, -10.0, -10.0);
    vec3 bmax_0 = vec3(10.0, 10.0, 10.0);


    vec3 scene_min = bmin_0;
    vec3 scene_max = bmax_0;

    vec2 tBox = intersect_aabb(ro, rd, scene_min, scene_max);
    float tNear = max(tBox.x, 0.0);
    float tFar  = min(tBox.y, ray_tmax);
    
    if (tNear > tFar) {
        out_color = vec4(0.0); out_vspos = vec4(0.0); out_vsnorm = vec4(0.0);
        return;
    }

    float ftn[1]; float ftf[1];
    vec2 fi_int_0 = intersect_aabb(ro, rd, bmin_0, bmax_0);
    ftn[0] = max(fi_int_0.x, 0.0);
    ftf[0] = min(fi_int_0.y, ray_tmax);


    float t = tNear; 
    bool hit = false;
    int hit_field = 0;
    
    for (int i = 0; i < 512; i++) {
        vec3 p = ro + t * rd;
        float min_d = 1.0e10;
        

        if (t >= ftn[0] && t <= ftf[0]) {
            float d_0 = sdf_eval_0(p);
            if (abs(d_0) < 0.005) { hit = true; hit_field = 0; break; }
            min_d = min(min_d, abs(d_0));
        } else if (t < ftn[0]) {
            min_d = min(min_d, ftn[0] - t);
        }


        if (hit) break;
        t += max(min_d * 0.9, 0.005);
        if (t > tFar) break;
    }

    if (!hit) {
        out_color = vec4(0.0); out_vspos = vec4(0.0); out_vsnorm = vec4(0.0);
        return;
    }

    vec3 hp = ro + t * rd;
    vec3 n = vec3(0, 0, 1);
    if (hit_field == 0) n = sdf_normal_0(hp);

    vec3 vd = normalize(-rd);

    float diff = max(dot(n, u_light_dir), 0.0);
    float spec = pow(max(dot(reflect(-u_light_dir, n), vd), 0.0), 32.0);

    vec3 base_color = vec3(1.0, 0.5, 0.0);
    if (hit_field == 0) base_color = vec3(1.0, 0.5, 0.0);

    vec3 color = base_color * (0.25 + 0.70 * diff) + vec3(0.3) * spec;

    vec4 vs   = gl_ModelViewMatrix * vec4(hp, 1.0);
    vec3 vs_n = normalize(mat3(gl_ModelViewMatrix) * n);

    out_color  = vec4(color, 1.0);
    out_vspos  = vec4(vs.xyz, 1.0);
    out_vsnorm = vec4(vs_n,   1.0);

    vec4 clip   = gl_ModelViewProjectionMatrix * vec4(hp, 1.0);
    float ndc_z = clip.z / clip.w;
    gl_FragDepth = gl_DepthRange.near + gl_DepthRange.diff * (ndc_z * 0.5 + 0.5);
}

