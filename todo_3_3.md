# Direct Modeling Workbench — OpenGL 3.3 Migration & Render Pipeline Cleanup Task List

> Tasks are ordered by dependency. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

The Direct Modeling workbench renders F-Rep SDFs via GPU ray marching. The current shaders
use legacy GLSL (no `#version` directive, `varying`, `texture2D`, uint16-as-LUMINANCE_ALPHA
hack) to maintain compatibility with ancient OpenGL 2.1 drivers. This limits precision and
prevents use of modern features.

**This migration:**
1. Targets **OpenGL 3.3 compatibility profile** as the minimum (`#version 330 compatibility`)
2. Replaces the 2D texture atlas hack with **native `SoTexture3` 3D textures**
3. Replaces uint16 quantization with **exact float32 SDF packing** via `uintBitsToFloat()`
4. Removes meshing from the render pipeline — meshing becomes an explicit user action via
   the existing SDF-to-Shape tool (`commands/cmd_sdf_export.py`)
5. Removes the point cloud renderer and mesh renderer code paths for F-Rep objects
6. Makes `RENDER_MODE_RAY_MARCH` the only F-Rep render mode

**After all tasks are complete:**
- All F-Rep objects render exclusively via GPU ray marching with float32 precision
- The SDF-to-Shape command is the only path to generate triangle meshes
- No GLSL 1.20 code remains; all shaders use `#version 330 compatibility`
- 2D atlas tiling is eliminated; SDF volumes use native 3D textures

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `bake_sdf_to_atlas()` | `core/frep/sdf_baker.py:11` | Bake SDF to 2D uint16 atlas (to be replaced) |
| `DMRayMarchRenderer` | `core/dm_ray_march_renderer.py:14` | Per-object ray march renderer |
| `DMSceneRayMarchRenderer` | `core/dm_scene_ray_march_renderer.py:16` | Singleton multi-field renderer |
| `DMRenderer.setup_frep_mesh_nodes()` | `core/dm_renderer.py:80` | Coin3D mesh nodes (to be removed) |
| `DMRenderer.update_frep_mesh()` | `core/dm_renderer.py:192` | Mesh upload (to be removed) |
| `DMObjectProxy.execute()` | `core/dm_object.py:369` | Object recompute — calls mesher (to be simplified) |
| `DMViewProvider.attach()` | `core/dm_object.py:455` | Renderer setup dispatch (to be simplified) |
| `DMViewProvider.updateData()` | `core/dm_object.py:506` | Data change handler (to be simplified) |
| `DMViewProvider._swap_renderer()` | `core/dm_object.py:557` | Render mode swap (to be removed) |
| `RENDER_MODE_MESH/POINT_CLOUD/RAY_MARCH` | `core/dm_object.py:114-116` | Mode constants (to be simplified) |
| `DMPointCloudRenderer` | `core/dm_point_cloud_renderer.py:106` | Point cloud renderer (to be removed) |
| `CommandSDFToShape` | `commands/cmd_sdf_export.py:14` | SDF-to-mesh command (stays) |
| `get_active_mesher()` | `core/dm_mesher.py:1323` | Mesher factory (stays, used by SDF-to-Shape) |

---

## Agent Skills

See `.agents/skills/` for project-specific knowledge:

| Skill | Purpose |
|-------|---------|
| `dm_opengl33_shader` | GLSL 3.30 compat syntax, float32 packing, SoTexture3 patterns |
| `dm_sdf_to_mesh_tool` | Where meshing belongs (dedicated tool, not render pipeline) |
| `coin3d_shader_api` | Coin3D shader setup, uniform nodes, texture upload |
| `dm_ray_march_scene_graph` | Scene graph structure for ray march renderers |
| `dm_renderer_architecture` | Overall Coin3D rendering pipeline |

---

## Tier 1 — SDF Baker Migration (Do First)

Replace the uint16 2D atlas baker with a float32 3D volume baker. Both renderers depend on this.

### GL-001: Create `bake_sdf_to_volume()` in `sdf_baker.py`

**File:** `core/frep/sdf_baker.py` — append after `bake_sdf_to_atlas()` (after line 98)

**What:** Add a new function that bakes an SDF field to a float32 3D volume packed as RGBA8 bytes,
suitable for upload to `SoTexture3`. The old `bake_sdf_to_atlas()` is kept temporarily for
backward compatibility until all consumers are migrated.

**Implementation:**

```python
def bake_sdf_to_volume(field, cell_size: float) -> dict:
    """
    Sample the SDF on a uniform grid and pack as a float32 RGBA8 3D volume.

    Args:
        field:     Any FRepField subclass.
        cell_size: Grid spacing in mm.

    Returns dict with keys:
        volume_bytes : bytes          — float32 as RGBA8 bytes, (nz+1)*(ny+1)*(nx+1)*4
        nx, ny, nz   : int           — grid cell counts
        bbox_min     : FreeCAD.Vector
        bbox_max     : FreeCAD.Vector
        max_dist     : float          — informational (= cell_size * 8.0)
    """
    mn, mx = field.bounding_box()

    def _pad(lo, hi):
        if hi - lo < 1e-4:
            mid = (lo + hi) / 2
            return mid - 1.0, mid + 1.0
        return lo - cell_size, hi + cell_size

    x0, x1 = _pad(mn.x, mx.x)
    y0, y1 = _pad(mn.y, mx.y)
    z0, z1 = _pad(mn.z, mx.z)

    nx = max(1, int(math.ceil((x1 - x0) / cell_size)))
    ny = max(1, int(math.ceil((y1 - y0) / cell_size)))
    nz = max(1, int(math.ceil((z1 - z0) / cell_size)))

    xs = np.linspace(x0, x0 + nx * cell_size, nx + 1)
    ys = np.linspace(y0, y0 + ny * cell_size, ny + 1)
    zs = np.linspace(z0, z0 + nz * cell_size, nz + 1)

    X, Y, Z = np.meshgrid(xs, ys, zs, indexing='ij')
    pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]).astype(np.float32)
    vals = field.evaluate_grid(pts).astype(np.float32)

    # Reshape to (nx+1, ny+1, nz+1), then transpose to (nz+1, ny+1, nx+1)
    # for row-major upload where x varies fastest (OpenGL convention).
    vol = vals.reshape(nx + 1, ny + 1, nz + 1).transpose(2, 1, 0)

    # Reinterpret float32 as 4×uint8 (RGBA)
    volume_bytes = vol.astype(np.float32).tobytes()

    import FreeCAD
    return {
        "volume_bytes": volume_bytes,
        "nx": nx, "ny": ny, "nz": nz,
        "bbox_min": FreeCAD.Vector(x0, y0, z0),
        "bbox_max": FreeCAD.Vector(x0 + nx * cell_size,
                                    y0 + ny * cell_size,
                                    z0 + nz * cell_size),
        "max_dist": cell_size * 8.0,
    }
```

---

## Tier 2 — Single-Field Renderer Migration

Migrate `DMRayMarchRenderer` to OpenGL 3.3 with 3D textures and float32 precision.

### GL-002: Replace `SoTexture2` with `SoTexture3` in `DMRayMarchRenderer`

**File:** `core/dm_ray_march_renderer.py` — in `_setup_nodes()` (lines 89-94)

**What:** Replace the `SoTexture2` atlas texture with a `SoTexture3` volume texture.
Update wrap/filter settings for 3D textures. Remove `SoComplexity` hack (no longer needed).

**Implementation:**

Replace lines 83-94 (the `SoComplexity` + `SoTexture2` block) with:

```python
        # 2a. 3D Volume Texture (OpenGL 3.3: native GL_TEXTURE_3D)
        self._tex = coin.SoTexture3()
        self._tex.wrapR.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
        self._tex.wrapS.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
        self._tex.wrapT.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
        # NEAREST: shader does its own trilinear on float32 data
        self._tex.minFilter.setValue(coin.SoTexture3.NEAREST)
        self._tex.magFilter.setValue(coin.SoTexture3.NEAREST)
        self._shader_sep.addChild(self._tex)
```

**Depends on:** GL-001

### GL-003: Update vertex shader to GLSL 330 in `DMRayMarchRenderer`

**File:** `core/dm_ray_march_renderer.py` — in `_setup_nodes()` (lines 103-109)

**What:** Add `#version 330 compatibility` directive. Replace `varying` with `out`.

**Implementation:**

Replace lines 103-109 with:

```python
        v_shader.sourceProgram.setValue("""
#version 330 compatibility
out vec2 v_uv;
void main() {
    v_uv = gl_Vertex.xy;
    gl_Position = vec4(gl_Vertex.xy, 0.0, 1.0);
}
""")
```

### GL-004: Update fragment shader to GLSL 330 with float32 3D texture in `DMRayMarchRenderer`

**File:** `core/dm_ray_march_renderer.py` — in `_setup_nodes()` (lines 111-257)

**What:** Rewrite the fragment shader for GLSL 330 compatibility mode. Use `sampler3D` +
`uintBitsToFloat()` for exact float32 SDF values from a 3D texture. Remove all atlas
tiling logic. Replace `varying` with `in`, `texture2D` with `texture`.

**Implementation:**

Replace lines 111-257 (the entire `f_shader.sourceProgram.setValue("""...""")` block) with:

```python
        f_shader.sourceProgram.setValue("""
#version 330 compatibility
in vec2 v_uv;
uniform sampler3D u_sdf_vol;
uniform int   u_nx;
uniform int   u_ny;
uniform int   u_nz;
uniform vec3  u_bbox_min;
uniform vec3  u_bbox_max;
uniform int   u_debug_mode;

float sample_sdf(vec3 p) {
    vec3 uvw = (p - u_bbox_min) / (u_bbox_max - u_bbox_min);
    uvw = clamp(uvw, vec3(0.0), vec3(1.0));
    vec4 c = texture(u_sdf_vol, uvw);
    uvec4 b = uvec4(round(c * 255.0));
    uint bits = b.r | (b.g << 8u) | (b.b << 16u) | (b.a << 24u);
    return uintBitsToFloat(bits);
}

vec3 sdf_normal(vec3 p) {
    float cell = (u_bbox_max.x - u_bbox_min.x) / max(float(u_nx), 1.0);
    float h = cell * 0.5;
    vec2 k = vec2(1.0, -1.0);
    return normalize(
        k.xyy * sample_sdf(p + k.xyy*h) +
        k.yyx * sample_sdf(p + k.yyx*h) +
        k.yxy * sample_sdf(p + k.yxy*h) +
        k.xxx * sample_sdf(p + k.xxx*h));
}

vec2 intersect_aabb(vec3 ro, vec3 rd) {
    vec3 t1 = (u_bbox_min - ro) / rd;
    vec3 t2 = (u_bbox_max - ro) / rd;
    vec3 tmin = min(t1, t2);
    vec3 tmax = max(t1, t2);
    float tNear = max(max(tmin.x, tmin.y), tmin.z);
    float tFar  = min(min(tmax.x, tmax.y), tmax.z);
    return vec2(tNear, tFar);
}

void main() {
    // 1. Unproject NDC to world-space ray
    vec4 ndc_near = vec4(v_uv, -1.0, 1.0);
    vec4 world_near = gl_ModelViewProjectionMatrixInverse * ndc_near;
    world_near /= world_near.w;

    vec4 ndc_far = vec4(v_uv, 1.0, 1.0);
    vec4 world_far = gl_ModelViewProjectionMatrixInverse * ndc_far;
    world_far /= world_far.w;

    vec3 cam = (gl_ModelViewMatrixInverse * vec4(0.0,0.0,0.0,1.0)).xyz;

    vec3 ro, rd;
    bool is_persp = (gl_ProjectionMatrix[3][3] < 0.5);
    if (is_persp) {
        ro = cam;
        rd = normalize(world_near.xyz - cam);
    } else {
        ro = world_near.xyz;
        rd = normalize(world_far.xyz - world_near.xyz);
    }

    // 2. AABB-ray intersection
    vec2 tBox = intersect_aabb(ro, rd);
    float tNear = is_persp ? max(tBox.x, 0.0) : tBox.x;
    float tFar  = tBox.y;
    if (tNear > tFar) discard;

    // 3. Sphere-trace
    float t = tNear;
    bool hit = false;
    float d;
    int march_iters = 0;
    float cell = (u_bbox_max.x - u_bbox_min.x) / max(float(u_nx), 1.0);
    float hit_thresh = cell * 0.01;
    float min_step = hit_thresh;

    for (int i = 0; i < 256; i++) {
        march_iters = i;
        vec3 p = ro + t * rd;
        d = sample_sdf(p);
        if (abs(d) < hit_thresh) { hit = true; break; }
        t += max(abs(d), min_step);
        if (t > tFar) break;
    }
    if (!hit) discard;

    // 4. Shading
    vec3 hp = ro + t * rd;
    vec3 n  = sdf_normal(hp);

    vec4 light_eye = gl_LightSource[0].position;
    vec3 ld;
    if (light_eye.w < 0.5) {
        ld = normalize((gl_ModelViewMatrixInverse * vec4(light_eye.xyz, 0.0)).xyz);
    } else {
        vec3 light_world = (gl_ModelViewMatrixInverse * light_eye).xyz;
        ld = normalize(light_world - hp);
    }

    float diff = max(dot(n, ld), 0.0);
    vec3 vd   = normalize(cam - hp);
    float spec = pow(max(dot(reflect(-ld, n), vd), 0.0), 32.0);
    vec3 color = vec3(1.0,0.5,0.0)*(0.15 + 0.75*diff) + vec3(0.4)*spec;

    gl_FragColor = vec4(color, 1.0);

    // Debug overrides
    if (u_debug_mode == 1) {
        float max_dist = (u_bbox_max.x - u_bbox_min.x) * 0.5;
        float v = sample_sdf(hp) / max_dist * 0.5 + 0.5;
        gl_FragColor = vec4(v, 0.0, 1.0 - v, 1.0);
    } else if (u_debug_mode == 2) {
        gl_FragColor = vec4(n * 0.5 + 0.5, 1.0);
    } else if (u_debug_mode == 3) {
        gl_FragColor = vec4(vec3(float(march_iters) / 256.0), 1.0);
    }

    // 5. Depth write
    vec4 clip    = gl_ModelViewProjectionMatrix * vec4(hp, 1.0);
    float ndc_z  = clip.z / clip.w;
    gl_FragDepth = gl_DepthRange.near
                 + gl_DepthRange.diff * (ndc_z * 0.5 + 0.5);
}
""")
```

**Depends on:** GL-003

### GL-005: Update uniforms for 3D texture in `DMRayMarchRenderer`

**File:** `core/dm_ray_march_renderer.py` — in `_setup_nodes()` (lines 258-311)

**What:** Remove atlas-specific uniforms (`u_atz`, `u_atlas_w`, `u_atlas_h`, `u_max_dist`).
Change texture sampler name to `u_sdf_vol`. Keep grid and bbox uniforms.

**Implementation:**

Replace lines 258-311 (from `# 2d. Uniforms` to `self._shader_sep.addChild(shader)`) with:

```python
        # 2d. Uniforms
        u_sdf_vol = coin.SoShaderParameter1i()
        u_sdf_vol.name.setValue("u_sdf_vol")
        u_sdf_vol.value.setValue(0)

        self._u["u_nx"] = coin.SoShaderParameter1i()
        self._u["u_nx"].name.setValue("u_nx")
        self._u["u_nx"].value.setValue(0)

        self._u["u_ny"] = coin.SoShaderParameter1i()
        self._u["u_ny"].name.setValue("u_ny")
        self._u["u_ny"].value.setValue(0)

        self._u["u_nz"] = coin.SoShaderParameter1i()
        self._u["u_nz"].name.setValue("u_nz")
        self._u["u_nz"].value.setValue(0)

        self._u["u_bbox_min"] = coin.SoShaderParameter3f()
        self._u["u_bbox_min"].name.setValue("u_bbox_min")
        self._u["u_bbox_min"].value.setValue(coin.SbVec3f(0, 0, 0))

        self._u["u_bbox_max"] = coin.SoShaderParameter3f()
        self._u["u_bbox_max"].name.setValue("u_bbox_max")
        self._u["u_bbox_max"].value.setValue(coin.SbVec3f(0, 0, 0))

        self._u["u_debug_mode"] = coin.SoShaderParameter1i()
        self._u["u_debug_mode"].name.setValue("u_debug_mode")
        self._u["u_debug_mode"].value.setValue(0)

        f_shader.parameter.setNum(0)
        f_shader.parameter.set1Value(0, u_sdf_vol)
        for i, name in enumerate(["u_nx", "u_ny", "u_nz",
                                   "u_bbox_min", "u_bbox_max", "u_debug_mode"]):
            f_shader.parameter.set1Value(i + 1, self._u[name])

        shader.shaderObject.set1Value(0, v_shader)
        shader.shaderObject.set1Value(1, f_shader)
        self._shader_sep.addChild(shader)
```

**Depends on:** GL-004

### GL-006: Update `DMRayMarchRenderer.update()` for 3D volume

**File:** `core/dm_ray_march_renderer.py` — `update()` method (lines 354-387)

**What:** Call `bake_sdf_to_volume()` instead of `bake_sdf_to_atlas()`. Upload data to
`SoTexture3` with `images.setValue(SbVec3s, 4, bytes)`. Update only the grid and bbox
uniforms (atlas uniforms no longer exist).

**Implementation:**

Replace the entire `update()` method (lines 354-387) with:

```python
    def update(self, field, cell_size):
        from core.frep.sdf_baker import bake_sdf_to_volume
        baked = bake_sdf_to_volume(field, cell_size)

        # 1. Upload 3D texture (float32 as RGBA8, 4 channels)
        nx, ny, nz = baked["nx"], baked["ny"], baked["nz"]
        self._tex.images.setValue(
            coin.SbVec3s(nx + 1, ny + 1, nz + 1), 4, baked["volume_bytes"])

        # 2. Update uniforms
        self._u["u_nx"].value.setValue(int(nx))
        self._u["u_ny"].value.setValue(int(ny))
        self._u["u_nz"].value.setValue(int(nz))

        mn, mx = baked["bbox_min"], baked["bbox_max"]
        self._u["u_bbox_min"].value.setValue(coin.SbVec3f(mn.x, mn.y, mn.z))
        self._u["u_bbox_max"].value.setValue(coin.SbVec3f(mx.x, mx.y, mx.z))

        # 3. Update bbox proxy + expansion points
        self._bbox_coords.point.setValues(0, 8, [
            (mn.x, mn.y, mn.z), (mx.x, mn.y, mn.z),
            (mn.x, mx.y, mn.z), (mx.x, mx.y, mn.z),
            (mn.x, mn.y, mx.z), (mx.x, mn.y, mx.z),
            (mn.x, mx.y, mx.z), (mx.x, mx.y, mx.z)
        ])
        self._coords.point.setValues(0, 8, [
            (mn.x, mn.y, mn.z), (mx.x, mn.y, mn.z),
            (mn.x, mx.y, mn.z), (mx.x, mx.y, mn.z),
            (mn.x, mn.y, mx.z), (mx.x, mn.y, mx.z),
            (mn.x, mx.y, mx.z), (mx.x, mx.y, mx.z)
        ])
```

**Depends on:** GL-001, GL-005

### GL-007: Remove old import of `bake_sdf_to_atlas` in `dm_ray_march_renderer.py`

**File:** `core/dm_ray_march_renderer.py` — line 10

**What:** Remove the top-level import `from core.frep.sdf_baker import bake_sdf_to_atlas`.
The new `bake_sdf_to_volume` is imported locally in `update()`.

**Implementation:**

Delete line 10:
```python
from core.frep.sdf_baker import bake_sdf_to_atlas
```

**Depends on:** GL-006

---

## Tier 3 — Scene Renderer Migration

Migrate `DMSceneRayMarchRenderer` to OpenGL 3.3 with per-field 3D textures.

### GL-008: Update vertex shader to GLSL 330 in `DMSceneRayMarchRenderer`

**File:** `core/dm_scene_ray_march_renderer.py` — in `_setup_nodes()` (lines 143-149)

**What:** Add `#version 330 compatibility`. Replace `varying` with `out`.

**Implementation:**

Replace lines 143-149 with:

```python
        v_shader.sourceProgram.setValue("""
#version 330 compatibility
out vec2 v_uv;
void main() {
    v_uv = gl_Vertex.xy;
    gl_Position = vec4(gl_Vertex.xy, 0.0, 1.0);
}
""")
```

### GL-009: Replace `SoTexture2` with `SoTexture3` array in `DMSceneRayMarchRenderer`

**File:** `core/dm_scene_ray_march_renderer.py` — in `_setup_nodes()` (lines 124-134)

**What:** Replace the single combined `SoTexture2` atlas with an array of up to 8
`SoTexture3` volumes (one per field). Each is bound to a separate texture unit.

**Implementation:**

Replace lines 124-134 (the `SoComplexity` + `SoTexture2` block) with:

```python
        # Per-field 3D volume textures (one per texture unit, up to MAX_FIELDS)
        self._textures = []
        for fi in range(self.MAX_FIELDS):
            tex = coin.SoTexture3()
            tex.wrapR.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
            tex.wrapS.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
            tex.wrapT.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
            tex.minFilter.setValue(coin.SoTexture3.NEAREST)
            tex.magFilter.setValue(coin.SoTexture3.NEAREST)
            self._textures.append(tex)
        # Only add the first texture to scene graph — Coin3D binds to unit 0.
        # Additional textures are managed via SoTextureUnit nodes.
        self._shader_sep.addChild(self._textures[0])
```

**Note:** This is a simplified approach. If Coin3D multi-texture support proves
difficult, an alternative is to keep a single combined 3D texture with stacked
z-slices (see GL-009-ALT below). The agent should test `SoTexture3` first and
fall back if needed.

### GL-009-ALT: Combined 3D texture fallback for scene renderer

**File:** `core/dm_scene_ray_march_renderer.py` — in `_setup_nodes()` (replaces GL-009 if multi-texture is problematic)

**What:** Use a single `SoTexture3` with all fields stacked along the z-axis,
similar to the current 2D atlas stacking but in 3D.

**Implementation:**

Replace lines 124-134 with:

```python
        # Single combined 3D texture (all fields stacked along z-axis)
        self._tex = coin.SoTexture3()
        self._tex.wrapR.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
        self._tex.wrapS.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
        self._tex.wrapT.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
        self._tex.minFilter.setValue(coin.SoTexture3.NEAREST)
        self._tex.magFilter.setValue(coin.SoTexture3.NEAREST)
        self._shader_sep.addChild(self._tex)
```

### GL-010: Update scene renderer fragment shader to GLSL 330 with 3D textures

**File:** `core/dm_scene_ray_march_renderer.py` — in `_setup_nodes()` (lines 155-349)

**What:** Rewrite the fragment shader for GLSL 330 compat. Use `sampler3D` with
`uintBitsToFloat()` for float32 precision. Replace `varying` with `in`,
`texture2D` with `texture`. Use per-field UVW coordinate transforms instead of
atlas tiling math.

**Implementation:**

Replace lines 155-349 (entire `f_shader.sourceProgram.setValue("""...""")`) with:

```python
        f_shader.sourceProgram.setValue("""
#version 330 compatibility
in vec2 v_uv;
uniform sampler3D u_sdf_vol;
uniform int   u_num_fields;
uniform int   u_debug_mode;

uniform int   u_nx[8];
uniform int   u_ny[8];
uniform int   u_nz[8];
uniform int   u_z_offset[8];
uniform int   u_z_total;
uniform vec3  u_bbox_min[8];
uniform vec3  u_bbox_max[8];

float sample_sdf_field(int fi, vec3 p) {
    vec3 uvw = (p - u_bbox_min[fi]) / (u_bbox_max[fi] - u_bbox_min[fi]);
    uvw = clamp(uvw, vec3(0.0), vec3(1.0));
    // Map field-local z to combined volume z-range
    float z_lo = float(u_z_offset[fi]) / float(u_z_total);
    float z_hi = float(u_z_offset[fi] + u_nz[fi] + 1) / float(u_z_total);
    uvw.z = mix(z_lo, z_hi, uvw.z);
    vec4 c = texture(u_sdf_vol, uvw);
    uvec4 b = uvec4(round(c * 255.0));
    uint bits = b.r | (b.g << 8u) | (b.b << 16u) | (b.a << 24u);
    return uintBitsToFloat(bits);
}

vec3 sdf_normal_field(int fi, vec3 p) {
    float cell = (u_bbox_max[fi].x - u_bbox_min[fi].x) / max(float(u_nx[fi]), 1.0);
    float h = cell * 0.5;
    vec2 k = vec2(1.0, -1.0);
    return normalize(
        k.xyy * sample_sdf_field(fi, p + k.xyy*h) +
        k.yyx * sample_sdf_field(fi, p + k.yyx*h) +
        k.yxy * sample_sdf_field(fi, p + k.yxy*h) +
        k.xxx * sample_sdf_field(fi, p + k.xxx*h));
}

vec2 intersect_aabb(vec3 ro, vec3 rd, vec3 bmin, vec3 bmax) {
    vec3 t1 = (bmin - ro) / rd;
    vec3 t2 = (bmax - ro) / rd;
    vec3 tmin = min(t1, t2);
    vec3 tmax = max(t1, t2);
    return vec2(max(max(tmin.x, tmin.y), tmin.z),
                min(min(tmax.x, tmax.y), tmax.z));
}

void main() {
    vec4 ndc_near = vec4(v_uv, -1.0, 1.0);
    vec4 world_near = gl_ModelViewProjectionMatrixInverse * ndc_near;
    world_near /= world_near.w;
    vec4 ndc_far = vec4(v_uv, 1.0, 1.0);
    vec4 world_far = gl_ModelViewProjectionMatrixInverse * ndc_far;
    world_far /= world_far.w;
    vec3 cam = (gl_ModelViewMatrixInverse * vec4(0.0,0.0,0.0,1.0)).xyz;

    vec3 ro, rd;
    bool is_persp = (gl_ProjectionMatrix[3][3] < 0.5);
    if (is_persp) {
        ro = cam;
        rd = normalize(world_near.xyz - cam);
    } else {
        ro = world_near.xyz;
        rd = normalize(world_far.xyz - world_near.xyz);
    }

    // Combined AABB early discard
    vec3 scene_min = u_bbox_min[0];
    vec3 scene_max = u_bbox_max[0];
    for (int fi = 1; fi < 8; fi++) {
        if (fi >= u_num_fields) break;
        scene_min = min(scene_min, u_bbox_min[fi]);
        scene_max = max(scene_max, u_bbox_max[fi]);
    }
    vec2 tBox = intersect_aabb(ro, rd, scene_min, scene_max);
    float tNear = is_persp ? max(tBox.x, 0.0) : tBox.x;
    float tFar  = tBox.y;
    if (tNear > tFar) discard;

    // Per-field AABB intervals
    float ftn[8];
    float ftf[8];
    for (int fi = 0; fi < 8; fi++) {
        if (fi < u_num_fields) {
            vec2 fi_int = intersect_aabb(ro, rd, u_bbox_min[fi], u_bbox_max[fi]);
            ftn[fi] = is_persp ? max(fi_int.x, 0.0) : fi_int.x;
            ftf[fi] = fi_int.y;
        } else {
            ftn[fi] =  1.0e10;
            ftf[fi] = -1.0e10;
        }
    }

    float t = tNear;
    bool hit = false;
    int hit_field = 0;
    int march_iters = 0;

    for (int i = 0; i < 256; i++) {
        march_iters = i;
        vec3 p = ro + t * rd;
        float min_d = 1.0e10;

        for (int fi = 0; fi < 8; fi++) {
            if (fi >= u_num_fields) break;
            if (ftn[fi] > ftf[fi]) continue;
            if (t > ftf[fi])       continue;
            if (t < ftn[fi]) {
                min_d = min(min_d, ftn[fi] - t);
                continue;
            }
            float d = sample_sdf_field(fi, p);
            float cell = (u_bbox_max[fi].x - u_bbox_min[fi].x) / max(float(u_nx[fi]), 1.0);
            float thresh = cell * 0.01;
            if (abs(d) < thresh) { hit = true; hit_field = fi; break; }
            min_d = min(min_d, abs(d));
        }
        if (hit) break;

        t += max(min_d, 0.0001);
        if (t > tFar) break;
    }
    if (!hit) discard;

    vec3 hp = ro + t * rd;
    vec3 n  = sdf_normal_field(hit_field, hp);

    vec4 light_eye = gl_LightSource[0].position;
    vec3 ld;
    if (light_eye.w < 0.5) {
        ld = normalize((gl_ModelViewMatrixInverse * vec4(light_eye.xyz, 0.0)).xyz);
    } else {
        vec3 light_world = (gl_ModelViewMatrixInverse * light_eye).xyz;
        ld = normalize(light_world - hp);
    }
    float diff = max(dot(n, ld), 0.0);
    vec3 vd   = normalize(cam - hp);
    float spec = pow(max(dot(reflect(-ld, n), vd), 0.0), 32.0);
    vec3 color = vec3(1.0,0.5,0.0)*(0.15 + 0.75*diff) + vec3(0.4)*spec;
    gl_FragColor = vec4(color, 1.0);

    if (u_debug_mode == 1) {
        float max_dist = (u_bbox_max[hit_field].x - u_bbox_min[hit_field].x) * 0.5;
        float v = sample_sdf_field(hit_field, hp) / max_dist * 0.5 + 0.5;
        gl_FragColor = vec4(v, 0.0, 1.0 - v, 1.0);
    } else if (u_debug_mode == 2) {
        gl_FragColor = vec4(n * 0.5 + 0.5, 1.0);
    } else if (u_debug_mode == 3) {
        gl_FragColor = vec4(vec3(float(march_iters) / 256.0), 1.0);
    }

    vec4 clip    = gl_ModelViewProjectionMatrix * vec4(hp, 1.0);
    float ndc_z  = clip.z / clip.w;
    gl_FragDepth = gl_DepthRange.near
                 + gl_DepthRange.diff * (ndc_z * 0.5 + 0.5);
}
""")
```

**Depends on:** GL-008

### GL-011: Update scene renderer uniforms for 3D volume

**File:** `core/dm_scene_ray_march_renderer.py` — in `_setup_nodes()` (lines 351-408)

**What:** Replace atlas-specific uniforms with volume-specific ones. Remove
`u_combined_atlas_w/h`, `u_atz`, `u_field_atlas_w/h`, `u_max_dist`, `u_row_offset`.
Add `u_z_offset[8]` and `u_z_total`.

**Implementation:**

Replace lines 351-408 (from `# Uniforms` to `f_shader.parameter.setNum(idx)`) with:

```python
        # Uniforms
        u_sdf_vol = coin.SoShaderParameter1i()
        u_sdf_vol.name.setValue("u_sdf_vol")
        u_sdf_vol.value.setValue(0)

        self._u["u_num_fields"] = coin.SoShaderParameter1i()
        self._u["u_num_fields"].name.setValue("u_num_fields")
        self._u["u_num_fields"].value.setValue(0)

        self._u["u_debug_mode"] = coin.SoShaderParameter1i()
        self._u["u_debug_mode"].name.setValue("u_debug_mode")
        self._u["u_debug_mode"].value.setValue(0)

        self._u["u_z_total"] = coin.SoShaderParameter1i()
        self._u["u_z_total"].name.setValue("u_z_total")
        self._u["u_z_total"].value.setValue(1)

        # Per-field uniform arrays
        per_field_int  = ["u_nx", "u_ny", "u_nz", "u_z_offset"]
        per_field_vec3 = ["u_bbox_min", "u_bbox_max"]

        for fi in range(self.MAX_FIELDS):
            for name in per_field_int:
                key = f"{name}[{fi}]"
                node = coin.SoShaderParameter1i()
                node.name.setValue(key)
                node.value.setValue(0)
                self._u[key] = node
            for name in per_field_vec3:
                key = f"{name}[{fi}]"
                node = coin.SoShaderParameter3f()
                node.name.setValue(key)
                node.value.setValue(coin.SbVec3f(0, 0, 0))
                self._u[key] = node

        # Register all uniforms with fragment shader
        f_shader.parameter.setNum(0)
        idx = 0
        f_shader.parameter.set1Value(idx, u_sdf_vol); idx += 1
        for key in ["u_num_fields", "u_debug_mode", "u_z_total"]:
            f_shader.parameter.set1Value(idx, self._u[key]); idx += 1
        for fi in range(self.MAX_FIELDS):
            for name in (per_field_int + per_field_vec3):
                f_shader.parameter.set1Value(idx, self._u[f"{name}[{fi}]"]); idx += 1
        f_shader.parameter.setNum(idx)
```

**Depends on:** GL-010

### GL-012: Update `_rebuild()` for 3D volume stacking

**File:** `core/dm_scene_ray_march_renderer.py` — `_rebuild()` method (lines 508-593)

**What:** Replace the 2D atlas stacking with 3D volume stacking. Bake each field via
`bake_sdf_to_volume()`, stack along the z-axis into a single combined 3D texture,
and update per-field z-offset uniforms.

**Implementation:**

Replace the entire `_rebuild()` method (lines 508-593) with:

```python
    def _rebuild(self):
        """Bake each field independently and upload a stacked 3D volume."""
        from core.dm_object import get_meshing_cell_size
        from core.frep.sdf_baker import bake_sdf_to_volume
        import numpy as np

        visible = [(label, f) for label, (f, vis) in self._fields.items()
                   if vis and f is not None]
        if not visible:
            self._switch.whichChild = -1
            return
        if len(visible) > self.MAX_FIELDS:
            dm_logger.warning(f"SceneRayMarch: {len(visible)} fields exceeds "
                              f"MAX_FIELDS={self.MAX_FIELDS}, truncating")
            visible = visible[:self.MAX_FIELDS]

        cell_size = get_meshing_cell_size()

        # Bake each field to a 3D float32 volume
        baked_list = [bake_sdf_to_volume(f, cell_size) for _, f in visible]
        n_fields = len(baked_list)

        # Stack volumes along z-axis into one combined 3D texture.
        # All fields are padded to the max x/y dimensions.
        max_nx = max(b["nx"] for b in baked_list) + 1  # +1 for sample points
        max_ny = max(b["ny"] for b in baked_list) + 1
        total_nz = sum(b["nz"] + 1 for b in baked_list)

        # Build combined volume (float32, then reinterpret as RGBA8)
        combined = np.zeros((total_nz, max_ny, max_nx), dtype=np.float32)
        z_offsets = []
        z_cursor = 0
        for b in baked_list:
            nx1, ny1, nz1 = b["nx"] + 1, b["ny"] + 1, b["nz"] + 1
            # bake_sdf_to_volume returns bytes in (nz+1, ny+1, nx+1) order
            vol = np.frombuffer(b["volume_bytes"], dtype=np.float32).reshape(nz1, ny1, nx1)
            combined[z_cursor:z_cursor + nz1, :ny1, :nx1] = vol
            z_offsets.append(z_cursor)
            z_cursor += nz1

        # Upload as RGBA8 (float32 reinterpreted as 4×uint8)
        volume_bytes = combined.astype(np.float32).tobytes()
        self._tex.images.setValue(
            coin.SbVec3s(max_nx, max_ny, total_nz), 4, volume_bytes)

        # Update per-field uniforms
        for fi in range(self.MAX_FIELDS):
            if fi < n_fields:
                b = baked_list[fi]
                mn, mx = b["bbox_min"], b["bbox_max"]
                self._u[f"u_nx[{fi}]"].value.setValue(int(b["nx"]))
                self._u[f"u_ny[{fi}]"].value.setValue(int(b["ny"]))
                self._u[f"u_nz[{fi}]"].value.setValue(int(b["nz"]))
                self._u[f"u_z_offset[{fi}]"].value.setValue(int(z_offsets[fi]))
                self._u[f"u_bbox_min[{fi}]"].value.setValue(
                    coin.SbVec3f(mn.x, mn.y, mn.z))
                self._u[f"u_bbox_max[{fi}]"].value.setValue(
                    coin.SbVec3f(mx.x, mx.y, mx.z))
            else:
                self._u[f"u_nx[{fi}]"].value.setValue(0)

        self._u["u_num_fields"].value.setValue(n_fields)
        self._u["u_z_total"].value.setValue(int(total_nz))

        # Combined bbox proxy
        import FreeCAD
        all_mn = [b["bbox_min"] for b in baked_list]
        all_mx = [b["bbox_max"] for b in baked_list]
        mn_all = FreeCAD.Vector(min(v.x for v in all_mn),
                                min(v.y for v in all_mn),
                                min(v.z for v in all_mn))
        mx_all = FreeCAD.Vector(max(v.x for v in all_mx),
                                max(v.y for v in all_mx),
                                max(v.z for v in all_mx))
        pts = [
            (mn_all.x, mn_all.y, mn_all.z), (mx_all.x, mn_all.y, mn_all.z),
            (mn_all.x, mx_all.y, mn_all.z), (mx_all.x, mx_all.y, mn_all.z),
            (mn_all.x, mn_all.y, mx_all.z), (mx_all.x, mn_all.y, mx_all.z),
            (mn_all.x, mx_all.y, mx_all.z), (mx_all.x, mx_all.y, mx_all.z)
        ]
        self._bbox_coords.point.setValues(0, 8, pts)
        self._coords.point.setValues(0, 8, pts)

        self._switch.whichChild = 0
```

**Depends on:** GL-001, GL-011

### GL-013: Remove old `bake_sdf_to_atlas` import in scene renderer

**File:** `core/dm_scene_ray_march_renderer.py` — line 12

**What:** Remove `from core.frep.sdf_baker import bake_sdf_to_atlas`. The new
`bake_sdf_to_volume` is imported locally in `_rebuild()`.

**Implementation:**

Delete line 12:
```python
from core.frep.sdf_baker import bake_sdf_to_atlas
```

**Depends on:** GL-012

---

## Tier 4 — Remove Mesh/PointCloud from Render Pipeline

Remove meshing from `execute()` and eliminate the mesh and point cloud render modes for F-Rep.

### GL-014: Simplify `DMObjectProxy.execute()` — remove meshing

**File:** `core/dm_object.py` — `execute()` method (lines 369-407)

**What:** Remove the mesh/point-cloud branch from `execute()`. For frep objects,
`execute()` should only set `fp.Shape = Part.Shape()` and nothing else.
The mesher is still available via `cmd_sdf_export.py` for explicit meshing.

**Implementation:**

Replace lines 369-407 (entire `execute()`) with:

```python
    def execute(self, fp):
        """Called by FreeCAD to recompute the object."""
        try:
            from . import dm_logger
            st = fp.ShapeType if hasattr(fp, "ShapeType") else "nurbs"

            if st == "frep":
                # F-Rep objects have no BRep shape — render via GPU ray march.
                # Use SDF-to-Shape command for explicit meshing.
                fp.Shape = Part.Shape()
                return

            new_shape = self.build_shape(fp)
            fp.Shape = new_shape

        except Exception:
            from . import dm_logger
            dm_logger.exception(f"DMObject.execute error for {fp.Label}")
```

### GL-015: Simplify `DMViewProvider.attach()` — ray march only for frep

**File:** `core/dm_object.py` — `attach()` method (lines 455-481)

**What:** Remove the render mode dispatch for frep objects. Always use the scene ray
march renderer for frep. Remove point cloud and mesh setup branches.

**Implementation:**

Replace lines 455-481 (entire `attach()`) with:

```python
    def attach(self, vobj):
        from . import dm_logger
        self.Object = vobj.Object

        if coin:
            from core.dm_renderer import DMRenderer
            self.renderer = DMRenderer(vobj)

            if hasattr(self.Object, "ShapeType") and self.Object.ShapeType == "curve":
                self.renderer.setup_coin_overlay()
                self.renderer.rebuild_control_cage(self.Object)
            elif hasattr(self.Object, "ShapeType") and self.Object.ShapeType == "frep":
                # Always use scene ray march renderer
                self._scene_rm_label = vobj.Object.Label
            elif hasattr(self.Object, "ShapeType") and self.Object.ShapeType == "point":
                self.renderer.setup_point_marker_nodes()
                self.renderer.update_point_marker(self.Object)
```

**Depends on:** GL-014

### GL-016: Simplify `DMViewProvider.updateData()` — remove mesh handoff

**File:** `core/dm_object.py` — `updateData()` method (lines 506-555)

**What:** Remove the mesh and point cloud branches from `updateData()`. For frep objects
with "Shape" prop change, always use the scene ray march renderer.

**Implementation:**

Replace the frep `prop == "Shape"` block (lines 512-530) with:

```python
        if prop == "Shape" and hasattr(fp, "ShapeType") and fp.ShapeType == "frep":
            proxy = getattr(fp, "Proxy", None)
            field = getattr(proxy, "FRepField", None) if proxy else None
            if hasattr(self, "_scene_rm_label") and field is not None:
                from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
                sr = DMSceneRayMarchRenderer.get_instance()
                sr.update_field(self._scene_rm_label, field)
```

**Depends on:** GL-015

### GL-017: Remove `_swap_renderer()` method

**File:** `core/dm_object.py` — `_swap_renderer()` method (lines 557-599)

**What:** Delete the entire `_swap_renderer()` method. There is now only one render
mode for frep objects.

**Implementation:**

Delete lines 557-599 (the entire `_swap_renderer` method).

**Depends on:** GL-016

### GL-018: Remove render mode preference functions and constants

**File:** `core/dm_object.py` — lines 114-123

**What:** Remove `RENDER_MODE_MESH`, `RENDER_MODE_POINT_CLOUD`, `RENDER_MODE_RAY_MARCH`,
`get_render_mode()`, and `set_render_mode()`. These are no longer needed since frep always
uses ray marching.

**Implementation:**

Delete lines 114-123:

```python
RENDER_MODE_MESH        = 0
RENDER_MODE_POINT_CLOUD = 1
RENDER_MODE_RAY_MARCH   = 2

def get_render_mode() -> int:
    """Return the active F-Rep render mode (0=Mesh, 1=PointCloud, 2=RayMarch)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("RenderMode", RENDER_MODE_MESH)

def set_render_mode(val: int):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("RenderMode", int(val))
```

Also search the codebase for any remaining references to `get_render_mode`,
`set_render_mode`, `RENDER_MODE_MESH`, `RENDER_MODE_POINT_CLOUD`, or
`RENDER_MODE_RAY_MARCH` and remove them.

**Depends on:** GL-017

### GL-019: Remove `on_prefs_changed()` render mode swap logic

**File:** `core/dm_object.py` — `on_prefs_changed()` method (lines 483-504)

**What:** Remove the render mode change detection block (lines 492-495) that calls
`_swap_renderer()`. Keep the rest of `on_prefs_changed()`.

**Implementation:**

Delete these lines from `on_prefs_changed()`:

```python
        # Check for render mode changes
        new_mode = get_render_mode()
        if self._render_mode is not None and self._render_mode != new_mode:
            self._swap_renderer(new_mode)
```

Also remove `self._render_mode = None` from `__init__` (line 421) and
`self._render_mode = mode` from the old `attach()`.

**Depends on:** GL-018

### GL-020: Remove `DMRenderer.setup_frep_mesh_nodes()` and `update_frep_mesh()`

**File:** `core/dm_renderer.py` — methods at lines 80-240

**What:** Delete `setup_frep_mesh_nodes()` (lines 80-190) and `update_frep_mesh()`
(lines 192-240). These created the Coin3D mesh nodes that are no longer needed for frep.
Keep `update_frep_corners()` only if it is used for non-frep objects; otherwise delete it too.

**Implementation:**

Delete the methods `setup_frep_mesh_nodes()` and `update_frep_mesh()` from the
`DMRenderer` class. Also delete any instance variables they initialize
(`_frep_coords`, `_frep_faces`, `_frep_wire_faces`, `_frep_sep`, etc.).

**Depends on:** GL-016

### GL-021: Remove `dm_point_cloud_renderer.py`

**File:** `core/dm_point_cloud_renderer.py` — entire file

**What:** Delete the file. The point cloud renderer is no longer used. Also remove any
imports of `DMPointCloudRenderer` in other files (search for `dm_point_cloud_renderer`
and `DMPointCloudRenderer`).

**Implementation:**

1. Delete `core/dm_point_cloud_renderer.py`
2. Search for and remove all imports/references:
   - `from core.dm_point_cloud_renderer import DMPointCloudRenderer`
   - `self.point_cloud_renderer`
   - Any `pc = getattr(self, "point_cloud_renderer", None)` blocks

**Depends on:** GL-016

### GL-022: Remove render mode from settings UI

**File:** `commands/cmd_settings.py` — search for `RenderMode` or `render_mode`

**What:** Remove any UI controls for selecting render mode (mesh/point-cloud/ray-march).
The render mode preference is no longer used.

**Implementation:**

Search `commands/cmd_settings.py` for references to `RenderMode`, `render_mode`,
`RENDER_MODE`, or `get_render_mode`/`set_render_mode` and remove the associated
UI elements (combo boxes, labels, etc.).

**Depends on:** GL-018

---

## Tier 5 — Cleanup

### GL-023: Remove old `bake_sdf_to_atlas()` function

**File:** `core/frep/sdf_baker.py` — lines 11-98

**What:** Delete the old `bake_sdf_to_atlas()` function. All consumers now use
`bake_sdf_to_volume()`. Verify no remaining imports by searching for `bake_sdf_to_atlas`.

**Implementation:**

1. Delete lines 11-98 (the entire `bake_sdf_to_atlas` function)
2. Search the entire codebase for `bake_sdf_to_atlas` — should find zero results
3. Update the module docstring (lines 1-6) to reflect the new function

**Depends on:** GL-007, GL-013

### GL-024: Update `dm_formulaic_sdf` skill for GLSL 330

**File:** `.agents/skills/dm_formulaic_sdf/SKILL.md` — "GLSL Version Requirements" section (lines 118-125)

**What:** Update the GLSL version requirements section to reflect GLSL 330 compat:
- Replace `texture2D` requirement with `texture`
- Replace `varying` requirement with `in`/`out`
- Allow `uvec`, `uint`, bitwise operations
- Require `#version 330 compatibility` directive

**Implementation:**

Replace lines 118-125 with:

```markdown
## GLSL Version Requirements

All generated GLSL must use **GLSL 3.30 compatibility** constructs:
- `#version 330 compatibility` directive required
- `texture()` (not `texture2D`)
- `in`/`out` (not `varying`)
- `uvec`, `uint`, bitwise operations allowed
- `uintBitsToFloat()` available for float packing
- Legacy Coin3D built-ins still available (`gl_ModelViewProjectionMatrix`, etc.)
```

**Depends on:** GL-004

### GL-025: Update `dm_ray_march_scene_graph` skill for 3D textures

**File:** `.agents/skills/dm_ray_march_scene_graph/SKILL.md` — "Texture Atlas Format" section (lines 62-78)

**What:** Replace the "Texture Atlas Format" section with "3D Volume Texture Format"
describing the new `SoTexture3` float32 RGBA8 format.

**Implementation:**

Replace lines 62-78 with:

```markdown
## 3D Volume Texture Format

| Property | Value |
|:---------|:------|
| Type | `SoTexture3` (OpenGL `GL_TEXTURE_3D`) |
| Format | RGBA8 (4 bytes/pixel, float32 reinterpreted) |
| Filtering | GL_NEAREST (shader does its own sampling) |
| Wrapping | CLAMP_TO_EDGE (all axes) |
| Dimensions | `(nx+1) × (ny+1) × (nz+1)` voxels |

### Reconstruction in GLSL

```glsl
vec4 c = texture(u_sdf_vol, uvw);
uvec4 b = uvec4(round(c * 255.0));
uint bits = b.r | (b.g << 8u) | (b.b << 16u) | (b.a << 24u);
float sdf_value = uintBitsToFloat(bits);
```

Exact float32 precision — no quantization.
```

**Depends on:** GL-006

### GL-026: Update `coin3d_shader_api` skill for GLSL 330

**File:** `.agents/skills/coin3d_shader_api/SKILL.md` — "Built-in GLSL Matrices" section (lines 212-244)

**What:** Add a note that `#version 330 compatibility` is now required for all shaders
in this project, and that legacy built-ins remain available in compatibility mode.

**Implementation:**

Add after line 213:

```markdown
**GLSL Version:** All shaders in this project use `#version 330 compatibility`.
This enables modern features (`in`/`out`, `uint`, `texture()`, `uintBitsToFloat()`)
while retaining all legacy built-ins listed below.
```

---

## Tier 6 — Formulaic SDF Skill Update (If Applicable)

If the formulaic SDF pipeline (`to_glsl()` / `glsl_assembler.py`) is implemented before
or alongside this migration, its generated GLSL must also target GLSL 330 compatibility.
See GL-024 for the skill update.

**Note:** As of this writing, `glsl_assembler.py` and the `to_glsl()` methods do not
exist yet (they are planned in `completed/todo_sdf.md` but were not implemented). When
they are implemented, they must follow the GLSL 330 conventions in the `dm_opengl33_shader`
and updated `dm_formulaic_sdf` skills.
