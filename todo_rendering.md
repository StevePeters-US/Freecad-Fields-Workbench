# Direct Modeling Workbench — Ray March Renderer Task List

> Tasks are ordered by dependency. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

The renderer must work with any `FRepField` using only its public interface:
`evaluate_grid()`, `gradient_grid()`, `bounding_box()`. No SDF-class-specific code
is permitted anywhere in the rendering pipeline.

### Architecture

```
field.evaluate_grid()
      │
      ▼
  sdf_baker.py                       ← CPU: sample SDF on uniform grid,
  bake_sdf_to_atlas(field, cell_size)    clamp, normalise to uint8,
      │                                  tile z-slices into 2D atlas
      ▼
  SoTexture2 (image field)           ← GPU texture upload (Coin3D)
      │
      ▼
  GLSL fragment shader               ← per-fragment ray march:
  sample_sdf(p)                          manual 8-tap trilinear from atlas,
  + sphere trace loop                    discard on miss, Phong shading,
  + Phong + gl_FragDepth                 correct depth
```

The fragment shader is a **compile-once fixed program**. The only things that change
per-update are: the texture image data, the AABB uniforms, and the atlas dimension uniforms.
No shader recompile ever.

### Atlas Layout

The 3D grid has `(nx+1) × (ny+1) × (nz+1)` samples. The `(nz+1)` z-slices (each
`(nx+1) × (ny+1)` pixels) are tiled into a 2D texture:

```
atz = ceil(sqrt(nz + 1))              # tiles across
aty = ceil((nz + 1) / atz)           # tiles down
total_w = atz * (nx + 1)  pixels
total_h = aty * (ny + 1)  pixels
```

Slice `iz` occupies column `c = iz % atz`, row `r = iz // atz`.
Texel `(ix, iy)` in slice `iz` sits at atlas pixel `(c*(nx+1)+ix, r*(ny+1)+iy)`.

### Trilinear Sampling in GLSL

Sample at exact texel centres (no hardware bilinear bleed between tiles):

```glsl
float sample_texel(float ix, float iy, float iz) {
    float c = floor(mod(iz, float(u_atz)));
    float r = floor(iz / float(u_atz));
    float u = (c * (float(u_nx) + 1.0) + ix + 0.5) / u_atlas_w;
    float v = (r * (float(u_ny) + 1.0) + iy + 0.5) / u_atlas_h;
    return texture2D(u_sdf_tex, vec2(u, v)).r;   // GL_LUMINANCE8 → [0,1]
}

float sample_sdf(vec3 p) {
    if (any(lessThan(p, u_bbox_min)) || any(greaterThan(p, u_bbox_max)))
        return u_max_dist;

    vec3 uvw = (p - u_bbox_min) / (u_bbox_max - u_bbox_min);
    float gx = clamp(uvw.x * float(u_nx), 0.0, float(u_nx));
    float gy = clamp(uvw.y * float(u_ny), 0.0, float(u_ny));
    float gz = clamp(uvw.z * float(u_nz), 0.0, float(u_nz));

    float x0 = floor(gx); float x1 = min(x0+1.0, float(u_nx));
    float y0 = floor(gy); float y1 = min(y0+1.0, float(u_ny));
    float z0 = floor(gz); float z1 = min(z0+1.0, float(u_nz));
    float fx = gx-x0;  float fy = gy-y0;  float fz = gz-z0;

    float s = mix(
        mix(mix(sample_texel(x0,y0,z0), sample_texel(x1,y0,z0), fx),
            mix(sample_texel(x0,y1,z0), sample_texel(x1,y1,z0), fx), fy),
        mix(mix(sample_texel(x0,y0,z1), sample_texel(x1,y0,z1), fx),
            mix(sample_texel(x0,y1,z1), sample_texel(x1,y1,z1), fx), fy),
        fz);
    return (s * 2.0 - 1.0) * u_max_dist;
}
```

Uses only GLSL 1.10 constructs (`mod`, `floor`, `mix`, `texture2D`) — no integer
bitwise ops or `#version 130` features required.

### GLSL Uniforms

| Name | Type | Coin3D class | Purpose |
|------|------|-------------|---------|
| `u_sdf_tex` | `sampler2D` | `SoUniformShaderParameter1i` = 0 | SDF atlas (texture unit 0) |
| `u_nx`, `u_ny`, `u_nz` | `int` | `SoUniformShaderParameter1i` | grid sample counts |
| `u_atz` | `int` | `SoUniformShaderParameter1i` | atlas tile columns |
| `u_atlas_w`, `u_atlas_h` | `float` | `SoUniformShaderParameter1f` | atlas pixel dimensions |
| `u_bbox_min`, `u_bbox_max` | `vec3` | `SoUniformShaderParameter3f` | padded grid AABB |
| `u_max_dist` | `float` | `SoUniformShaderParameter1f` | SDF clamp range (= 4 × cell_size) |

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `field.evaluate_grid(pts)` | `core/frep/frep_field.py:30` | Only SDF API used by renderer |
| `field.bounding_box()` | `core/frep/frep_field.py:20` | Returns `(Vector, Vector)` |
| grid/pad setup pattern | `core/dm_mesher.py:117–143` | Copy this exactly for grid setup |
| `DMViewProvider.attach()` | `core/dm_object.py:400` | Hook for new renderer |
| `DMViewProvider.updateData()` | `core/dm_object.py:435` | Hook for update calls |
| `proxy.FRepField` | set before `execute()` returns | The live field |
| `get_meshing_cell_size()` | `core/dm_object.py:63` | Cell size preference (mm) |

---

## Tier 1 — SDF Baker

### R-001: Create `core/frep/sdf_baker.py`

**File:** `core/frep/sdf_baker.py` — new file

**What:** Evaluate the SDF on a uniform grid, clamp + normalise to uint8, tile z-slices
into a 2D luminance atlas. Returns all the metadata the renderer needs to set up its
uniforms and update the texture.

Uses the identical `_pad` / grid setup from `core/dm_mesher.py:117–143` — copy it verbatim.

```python
"""
core/frep/sdf_baker.py

Bakes any FRepField to a 2D luminance atlas for GPU ray marching.
Only uses field.evaluate_grid() and field.bounding_box() — no primitives.
"""
import math
import numpy as np


def bake_sdf_to_atlas(field, cell_size: float) -> dict:
    """
    Sample the SDF on a uniform grid and pack it as a 2D uint8 atlas.

    Args:
        field:     Any FRepField subclass.
        cell_size: Grid spacing in mm. Controls resolution and clamp range.

    Returns dict with keys:
        atlas_bytes : bytes        — uint8 luminance pixels, row-major
        atlas_w     : int          — atlas pixel width  (= atz * (nx+1))
        atlas_h     : int          — atlas pixel height (= aty * (ny+1))
        nx, ny, nz  : int          — grid cell counts
        atz         : int          — atlas tile columns
        bbox_min    : FreeCAD.Vector
        bbox_max    : FreeCAD.Vector
        max_dist    : float        — SDF clamp range in mm (= 4 * cell_size)
    """
    mn, mx = field.bounding_box()

    # Identical to dm_mesher.py:117–143
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
    vals = field.evaluate_grid(pts).reshape(nx + 1, ny + 1, nz + 1)  # (nx+1,ny+1,nz+1)

    # Clamp and normalise to [0, 255]
    max_dist = cell_size * 4.0
    clamped  = np.clip(vals, -max_dist, max_dist)
    norm     = ((clamped / max_dist + 1.0) * 0.5 * 255.0).astype(np.uint8)

    # Tile z-slices into 2D atlas
    nslices = nz + 1
    atz = max(1, int(math.ceil(math.sqrt(nslices))))
    aty = int(math.ceil(nslices / atz))

    atlas_w = atz * (nx + 1)
    atlas_h = aty * (ny + 1)
    atlas   = np.zeros((atlas_h, atlas_w), dtype=np.uint8)

    for iz in range(nslices):
        col = iz % atz
        row = iz // atz
        x_off = col * (nx + 1)
        y_off = row * (ny + 1)
        # norm is (nx+1, ny+1, nz+1), indexing='ij' → axis 0=x, 1=y, 2=z
        # Atlas rows = y axis, cols = x axis
        slice_xy = norm[:, :, iz]          # shape (nx+1, ny+1)
        atlas[y_off:y_off + ny + 1, x_off:x_off + nx + 1] = slice_xy.T  # transpose: row=y, col=x

    import FreeCAD
    return {
        "atlas_bytes": atlas.tobytes(),
        "atlas_w":     atlas_w,
        "atlas_h":     atlas_h,
        "nx": nx, "ny": ny, "nz": nz,
        "atz":         atz,
        "bbox_min":    FreeCAD.Vector(x0, y0, z0),
        "bbox_max":    FreeCAD.Vector(x0 + nx * cell_size,
                                      y0 + ny * cell_size,
                                      z0 + nz * cell_size),
        "max_dist":    max_dist,
    }
```

---

## Tier 2 — Ray March Renderer

### R-002: Create `core/dm_ray_march_renderer.py`

**File:** `core/dm_ray_march_renderer.py` — new file

**What:** Coin3D scene graph owner. Shader is compiled once in `_setup_nodes()` and never
recompiled. `update(field, cell_size)` bakes the SDF, uploads the new texture bytes,
and pushes updated uniform values.

**Scene graph:**
```
SoSeparator
├── SoShapeHints          (UNKNOWN_ORDERING)
├── SoTexture2            (GL_LUMINANCE8 atlas, wrapS/wrapT = CLAMP)
├── SoShaderProgram
│   ├── SoVertexShader
│   └── SoFragmentShader
│       └── parameters[]  (all SoUniform* nodes, attached once, updated in-place)
├── SoCoordinate3         (8 AABB corners, updated each call)
└── SoIndexedFaceSet      (static 6-face indices)
```

**AABB face index list:**
```python
_FACE_IDX = [0,1,2,3,-1, 4,7,6,5,-1, 0,4,5,1,-1, 1,5,6,2,-1, 2,6,7,3,-1, 0,3,7,4,-1]
```

**AABB corners from bbox:**
```python
def _bbox_corners(mn, mx):
    x0,y0,z0 = mn.x,mn.y,mn.z;  x1,y1,z1 = mx.x,mx.y,mx.z
    return [(x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),
            (x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)]
```

**Vertex shader** (pass world position to fragment stage):
```glsl
varying vec3 v_world_pos;
void main() {
    v_world_pos = gl_Vertex.xyz;
    gl_Position = ftransform();
}
```

**Fragment shader** (full source, compile-once, never changes):
```glsl
varying vec3  v_world_pos;
uniform sampler2D u_sdf_tex;
uniform int   u_nx;
uniform int   u_ny;
uniform int   u_nz;
uniform int   u_atz;
uniform float u_atlas_w;
uniform float u_atlas_h;
uniform vec3  u_bbox_min;
uniform vec3  u_bbox_max;
uniform float u_max_dist;

float sample_texel(float ix, float iy, float iz) {
    float c = floor(mod(iz, float(u_atz)));
    float r = floor(iz / float(u_atz));
    float u = (c * (float(u_nx) + 1.0) + ix + 0.5) / u_atlas_w;
    float v = (r * (float(u_ny) + 1.0) + iy + 0.5) / u_atlas_h;
    return texture2D(u_sdf_tex, vec2(u, v)).r;
}

float sample_sdf(vec3 p) {
    if (any(lessThan(p, u_bbox_min)) || any(greaterThan(p, u_bbox_max)))
        return u_max_dist;
    vec3 uvw = (p - u_bbox_min) / (u_bbox_max - u_bbox_min);
    float gx = clamp(uvw.x * float(u_nx), 0.0, float(u_nx));
    float gy = clamp(uvw.y * float(u_ny), 0.0, float(u_ny));
    float gz = clamp(uvw.z * float(u_nz), 0.0, float(u_nz));
    float x0=floor(gx); float x1=min(x0+1.0,float(u_nx));
    float y0=floor(gy); float y1=min(y0+1.0,float(u_ny));
    float z0=floor(gz); float z1=min(z0+1.0,float(u_nz));
    float fx=gx-x0; float fy=gy-y0; float fz=gz-z0;
    float s = mix(
        mix(mix(sample_texel(x0,y0,z0),sample_texel(x1,y0,z0),fx),
            mix(sample_texel(x0,y1,z0),sample_texel(x1,y1,z0),fx),fy),
        mix(mix(sample_texel(x0,y0,z1),sample_texel(x1,y0,z1),fx),
            mix(sample_texel(x0,y1,z1),sample_texel(x1,y1,z1),fx),fy),
        fz);
    return (s * 2.0 - 1.0) * u_max_dist;
}

vec3 sdf_normal(vec3 p) {
    float h = u_max_dist * 0.015;
    vec2 k = vec2(1.0, -1.0);
    return normalize(
        k.xyy * sample_sdf(p + k.xyy*h) +
        k.yyx * sample_sdf(p + k.yyx*h) +
        k.yxy * sample_sdf(p + k.yxy*h) +
        k.xxx * sample_sdf(p + k.xxx*h));
}

void main() {
    vec3 cam = (gl_ModelViewMatrixInverse * vec4(0.0,0.0,0.0,1.0)).xyz;
    vec3 ro   = v_world_pos;
    vec3 rd   = normalize(v_world_pos - cam);
    float hit_thresh = u_max_dist * 0.04;  // ~10x quantisation step of uint8 data

    float t  = 0.0;
    bool hit = false;
    for (int i = 0; i < 128; i++) {
        vec3 p = ro + t * rd;
        if (any(lessThan(p, u_bbox_min)) || any(greaterThan(p, u_bbox_max))) break;
        float d = sample_sdf(p);
        if (d < hit_thresh) { hit = true; break; }
        t += max(d, hit_thresh);
    }
    if (!hit) discard;

    vec3 hp  = ro + t * rd;
    vec3 n   = sdf_normal(hp);
    vec3 ld  = normalize(gl_LightSource[0].position.xyz - hp);
    float diff = max(dot(n, ld), 0.0);
    vec3 vd    = normalize(cam - hp);
    float spec = pow(max(dot(reflect(-ld, n), vd), 0.0), 32.0);
    vec3 color = vec3(1.0,0.5,0.0)*(0.15 + 0.75*diff) + vec3(0.4)*spec;

    gl_FragColor = vec4(color, 1.0);
    vec4 clip    = gl_ProjectionMatrix * gl_ModelViewMatrix * vec4(hp, 1.0);
    gl_FragDepth = (clip.z / clip.w + 1.0) * 0.5;
}
```

**Uniform setup** — called once in `_setup_nodes()`, values patched on each `update()`:

Create the uniform nodes with placeholder values, store them in a dict
`self._u = {"u_nx": node, ...}`. To update a value later, call `node.value.setValue(...)`.

| Uniform | Node type | Initial value |
|---------|-----------|---------------|
| `u_sdf_tex` | `SoUniformShaderParameter1i` | 0 |
| `u_nx`, `u_ny`, `u_nz` | `SoUniformShaderParameter1i` | 0 |
| `u_atz` | `SoUniformShaderParameter1i` | 1 |
| `u_atlas_w`, `u_atlas_h` | `SoUniformShaderParameter1f` | 1.0 |
| `u_bbox_min`, `u_bbox_max` | `SoUniformShaderParameter3f` | SbVec3f(0,0,0) |
| `u_max_dist` | `SoUniformShaderParameter1f` | 1.0 |

Attach all nodes to `frag.parameter` in `_setup_nodes()`. In `update()`, patch values
with `self._u["u_nx"].value.setValue(baked["nx"])` etc.

**Texture update** — call `self._tex.image.setValue(coin.SbVec2s(w, h), 1, data_bytes)`
where `data_bytes = baked["atlas_bytes"]`. This triggers re-upload to the GPU.

**`update()` method:**
1. Call `bake_sdf_to_atlas(field, cell_size)` → `baked` dict
2. Upload texture: `self._tex.image.setValue(...)`
3. Update all uniforms from `baked`
4. Update AABB corners: `self._coords.point.setNum(0); self._coords.point.setValues(...)`

**Depends on:** R-001

---

## Tier 3 — Integration Cleanup

### R-003: Replace `UsePointCloud` boolean with `RenderMode` int in `core/dm_object.py`

**File:** `core/dm_object.py`

**What:** Remove `get_use_point_cloud()` and `set_use_point_cloud()`. Replace with a
tri-state int and named constants.

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

---

### R-004: Update `DMViewProvider` to use `get_render_mode()` and add ray march branch

**File:** `core/dm_object.py`

**Edit 1 — `attach()`.** Replace the frep branch (currently checks `get_use_point_cloud()`):

```python
            elif hasattr(self.Object, "ShapeType") and self.Object.ShapeType == "frep":
                mode = get_render_mode()
                if mode == RENDER_MODE_POINT_CLOUD:
                    from core.dm_point_cloud_renderer import DMPointCloudRenderer
                    self.point_cloud_renderer = DMPointCloudRenderer(vobj)
                elif mode == RENDER_MODE_RAY_MARCH:
                    from core.dm_ray_march_renderer import DMRayMarchRenderer
                    self.ray_march_renderer = DMRayMarchRenderer(vobj)
                else:
                    self.renderer.setup_frep_mesh_nodes()
```

**Edit 2 — `updateData()`.** Replace the `if prop == "Shape" and ... frep:` block:

```python
        if prop == "Shape" and hasattr(fp, "ShapeType") and fp.ShapeType == "frep":
            proxy = getattr(fp, "Proxy", None)
            field = getattr(proxy, "FRepField", None) if proxy else None
            pc    = getattr(self, "point_cloud_renderer", None)
            rm    = getattr(self, "ray_march_renderer",   None)

            if pc is not None and field is not None:
                pc.update(field, get_meshing_cell_size())
            elif rm is not None and field is not None:
                rm.update(field, get_meshing_cell_size())
            elif proxy and self.renderer:
                self.renderer.update_frep_mesh(
                    getattr(proxy, "_frep_verts", None),
                    getattr(proxy, "_frep_idx",   None),
                )
                if field:
                    self.renderer.update_frep_corners(field)
```

**Depends on:** R-002, R-003

---

### R-005: Replace settings checkbox with render mode combo box in `commands/cmd_settings.py`

**File:** `commands/cmd_settings.py`

**Edit 1 — `__init__()`.** Remove the "Point Cloud Preview" `QCheckBox` block. Add:

```python
        from core.dm_object import get_render_mode
        self._render_mode_combo = QtGui.QComboBox()
        self._render_mode_combo.addItems([
            "Triangle Mesh",
            "Point Cloud",
            "Ray March (GPU)",
        ])
        self._render_mode_combo.setCurrentIndex(get_render_mode())
        self._render_mode_combo.setToolTip(
            "Triangle Mesh: CPU marching cubes — full quality.\n"
            "Point Cloud: fast zero-crossing samples — good for interactive editing.\n"
            "Ray March: GPU sphere tracing — no triangulation, smooth shading.\n"
            "Changes take effect after document reload."
        )
        layout.addRow("F-Rep Renderer:", self._render_mode_combo)
```

**Edit 2 — `_on_accept()`.** Replace `set_use_point_cloud` with `set_render_mode` in the
import and the setter call:

```python
        set_render_mode(self._render_mode_combo.currentIndex())
```

**Depends on:** R-003

---

## Agent Skills

| Skill | Purpose |
|-------|---------|
| `coin3d_shader_api` | `SoShaderProgram` setup, uniform node types, proxy geometry |
| `dm_todo_format` | Task format and conventions for this project |
