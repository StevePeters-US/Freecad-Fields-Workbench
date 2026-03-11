# Direct Modeling Workbench — GPU Ray Marching Task List

> Tasks are ordered by dependency. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

The current F-Rep pipeline tessellates the SDF on CPU (Marching Cubes / Surface Nets /
Dual Contouring) and renders triangles via Coin3D's fixed-function pipeline. This requires
re-meshing on every parameter change and cannot produce smooth silhouettes.

The goal is a **GPU ray marching renderer** that works for **any** `FRepField` regardless
of its internal structure. The SDF is not compiled into the shader — instead it is baked
to a 3D volume texture via the universal `evaluate_grid()` interface, and a fixed, never-
changing GLSL shader sphere-traces against that texture.

### Architecture

```
FRepField.evaluate_grid()           ← works for any SDF
    ↓
bake_sdf_volume()                   ← samples a 3D grid (resolution³ points)
    ↓
pack_sdf_rgba()                     ← encodes each float32 as 4 RGBA bytes (IEEE 754 LE)
    ↓
SoTexture3.image                    ← uploaded as RGBA8 texture, GL_NEAREST filtering
    ↓
Fixed GLSL fragment shader          ← decodes float, sphere-traces, Phong shades
```

The shader is **identical for every SDF** — it has no knowledge of spheres, boxes, or
any other primitive. Only the texture contents and two AABB uniforms change when the field
changes.

### Packed Float Encoding

SDF values are packed as raw IEEE 754 `float32` bytes into an RGBA8 texture
(one voxel = 4 bytes). On x86/x64 (little-endian):

```python
# Python (pack):
rgba = sdf_vals.astype(np.float32).view(np.uint8).reshape(-1, 4)
# byte layout per voxel: R=byte0 (LSB), G=byte1, B=byte2, A=byte3 (MSB)
```

```glsl
// GLSL (unpack, requires #version 130 for uintBitsToFloat):
float unpack_float(vec4 c) {
    uvec4 b    = uvec4(round(c * 255.0));
    uint  bits = b.r | (b.g << 8u) | (b.b << 16u) | (b.a << 24u);
    return uintBitsToFloat(bits);
}
```

The texture must use `GL_NEAREST` filtering — linear interpolation of packed float bytes
produces garbage. This is set via `SoTexture3.minFilter` / `magFilter`.

### Coordinate Mapping

The baked grid covers the field's AABB (plus a small margin). The shader maps a world-space
point `p` to texture UVW coordinates:

```glsl
vec3 uvw = (p - u_bbox_min) / (u_bbox_max - u_bbox_min);
float d  = unpack_float(texture(u_sdf_vol, uvw));
```

`u_bbox_min` and `u_bbox_max` are vec3 uniforms; they match the exact coverage of the baked
grid and the proxy box geometry.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `FRepField.evaluate_grid()` | `core/frep/frep_field.py:30` | Batch SDF eval `(N,3) → (N,)` — works for any field |
| `FRepField.bounding_box()` | `core/frep/frep_field.py:20` | AABB `→ (Vector, Vector)` |
| `DMViewProvider.attach()` | `core/dm_object.py:400` | Hook to instantiate `DMRayMarchRenderer` |
| `DMViewProvider.updateData()` | `core/dm_object.py:435` | Hook to call `renderer.update(field)` |
| `get_*` preference helpers | `core/dm_object.py:29–68` | Pattern for new preferences |
| `proxy.FRepField` | set in `DMObjectProxy.execute()` | The live field to render |

---

## Tier 1 — Volume Baking Utilities

Create the new file `core/dm_ray_march_renderer.py`. All tasks in this file build toward
the `DMRayMarchRenderer` class. Start with the module-level utility functions.

### R-001: Add `pack_sdf_rgba()` and `bake_sdf_volume()` to new file

**File:** `core/dm_ray_march_renderer.py` — new file; add these as module-level functions

**What:** `pack_sdf_rgba` encodes float32 SDF values as raw IEEE 754 bytes into an RGBA
uint8 array. `bake_sdf_volume` evaluates the field on a uniform 3D grid and returns the
packed RGBA volume plus the grid's AABB as two `(x, y, z)` tuples.

**Implementation:**

```python
"""
core/dm_ray_march_renderer.py

GPU ray-marching renderer for F-Rep SDF objects.
Evaluates any FRepField into a 3D RGBA texture via pack_sdf_rgba / bake_sdf_volume,
then sphere-traces it in a fixed GLSL fragment shader.
"""
import numpy as np

try:
    from pivy import coin
except ImportError:
    coin = None


def pack_sdf_rgba(sdf_vals: np.ndarray) -> np.ndarray:
    """
    Encode float32 SDF values as RGBA uint8 using raw IEEE 754 bytes.

    On little-endian x86/x64: R=byte0 (LSB), G=byte1, B=byte2, A=byte3 (MSB).
    The texture must be sampled with GL_NEAREST — linear interpolation of packed
    bytes produces garbage.

    Args:
        sdf_vals: (N,) float32 array of SDF values.
    Returns:
        (N, 4) uint8 array suitable for SoTexture3.image.
    """
    return sdf_vals.astype(np.float32).view(np.uint8).reshape(-1, 4)


def bake_sdf_volume(field, resolution: int = 64):
    """
    Evaluate field on a resolution³ grid covering its bounding box.

    Args:
        field:      Any FRepField subclass.
        resolution: Number of voxels along each axis. Default 64.
    Returns:
        rgba   (np.ndarray): (resolution, resolution, resolution, 4) uint8 packed SDF.
        bbox   (tuple):      ((x0,y0,z0), (x1,y1,z1)) world-space grid coverage.
    """
    import FreeCAD
    mn, mx = field.bounding_box()

    # Add a small margin so the proxy box edges don't clip surface voxels
    dims = [mx.x - mn.x, mx.y - mn.y, mx.z - mn.z]
    margin = max(dims) * 0.02 if any(d > 0 for d in dims) else 1.0
    x0, y0, z0 = mn.x - margin, mn.y - margin, mn.z - margin
    x1, y1, z1 = mx.x + margin, mx.y + margin, mx.z + margin

    x = np.linspace(x0, x1, resolution)
    y = np.linspace(y0, y1, resolution)
    z = np.linspace(z0, z1, resolution)
    X, Y, Z = np.meshgrid(x, y, z, indexing='ij')
    pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]).astype(np.float32)

    sdf_vals = field.evaluate_grid(pts)
    rgba = pack_sdf_rgba(sdf_vals).reshape(resolution, resolution, resolution, 4)

    return rgba, ((x0, y0, z0), (x1, y1, z1))
```

---

## Tier 2 — GLSL Shader Strings

### R-002: Add `_VERT_SRC` and `_FRAG_SRC` constants to `core/dm_ray_march_renderer.py`

**File:** `core/dm_ray_march_renderer.py` — append after the `bake_sdf_volume` function

**What:** Two module-level string constants holding the vertex and fragment shader source.
These never change regardless of which SDF is rendered.

**`_VERT_SRC`** — passes world-space vertex position to the fragment shader:

```python
_VERT_SRC = """
#version 130
out vec3 vWorldPos;
void main() {
    vWorldPos   = gl_Vertex.xyz;   // proxy box vertices are set in world/SDF space
    gl_Position = ftransform();
}
"""
```

**`_FRAG_SRC`** — decodes packed float from 3D texture, AABB-clips the ray, sphere-traces,
computes normal via finite differences, Phong shades, writes depth:

```python
_FRAG_SRC = """
#version 130
in  vec3 vWorldPos;

uniform sampler3D u_sdf_vol;   // packed float32 RGBA volume texture
uniform vec3      u_bbox_min;  // world-space lower corner of texture coverage
uniform vec3      u_bbox_max;  // world-space upper corner of texture coverage

// Decode IEEE 754 float32 packed as RGBA8 bytes (little-endian: R=LSB, A=MSB)
float unpack_float(vec4 c) {
    uvec4 b    = uvec4(round(c * 255.0));
    uint  bits = b.r | (b.g << 8u) | (b.b << 16u) | (b.a << 24u);
    return uintBitsToFloat(bits);
}

float sdf(vec3 p) {
    vec3 uvw = (p - u_bbox_min) / (u_bbox_max - u_bbox_min);
    uvw = clamp(uvw, 0.001, 0.999);
    return unpack_float(texture(u_sdf_vol, uvw));
}

// AABB slab test — returns (tmin, tmax); hit when tmin < tmax
vec2 aabb_hit(vec3 ro, vec3 rd) {
    vec3 inv = 1.0 / rd;
    vec3 t0  = (u_bbox_min - ro) * inv;
    vec3 t1  = (u_bbox_max - ro) * inv;
    vec3 mn  = min(t0, t1);
    vec3 mx  = max(t0, t1);
    return vec2(max(max(mn.x, mn.y), mn.z),
                min(min(mx.x, mx.y), mx.z));
}

const int   MAX_STEPS = 128;
const float EPSILON   = 0.001;

float march(vec3 ro, vec3 rd, float t0, float t1) {
    float t = t0;
    for (int i = 0; i < MAX_STEPS; i++) {
        float d = sdf(ro + t * rd);
        if (d < EPSILON) return t;
        t += abs(d);            // abs() guards against tiny negative overshoots
        if (t > t1) break;
    }
    return -1.0;
}

vec3 normal(vec3 p) {
    float h = (u_bbox_max.x - u_bbox_min.x) / 64.0;  // ~1 voxel width
    return normalize(vec3(
        sdf(p + vec3(h,0,0)) - sdf(p - vec3(h,0,0)),
        sdf(p + vec3(0,h,0)) - sdf(p - vec3(0,h,0)),
        sdf(p + vec3(0,0,h)) - sdf(p - vec3(0,0,h))
    ));
}

void main() {
    // Camera world position (model matrix ≈ identity — vertices in world space)
    vec3 cam = (gl_ModelViewMatrixInverse * vec4(0.0, 0.0, 0.0, 1.0)).xyz;

    // Ray — perspective vs orthographic
    bool is_ortho = (gl_ProjectionMatrix[3][3] > 0.5);
    vec3 ro, rd;
    if (is_ortho) {
        rd = normalize((gl_ModelViewMatrixInverse * vec4(0.0, 0.0, -1.0, 0.0)).xyz);
        ro = vWorldPos;
    } else {
        ro = cam;
        rd = normalize(vWorldPos - cam);
    }

    // Clip to volume
    vec2 ivl = aabb_hit(ro, rd);
    if (ivl.x >= ivl.y) discard;
    float t0 = max(ivl.x, 0.0);

    // Trace
    float t = march(ro, rd, t0, ivl.y);
    if (t < 0.0) discard;

    vec3 hit = ro + t * rd;
    vec3 N   = normal(hit);

    // Phong — matches existing SoMaterial: diffuse=(1,0.5,0), spec=(0.3,0.3,0.3), shin=38
    vec3  Kd = vec3(1.0, 0.5, 0.0);
    vec3  Ks = vec3(0.3, 0.3, 0.3);
    float sh = 38.0;

    vec3 L;
    if (gl_LightSource[0].position.w < 0.5)
        L = normalize((gl_ModelViewMatrixInverse * vec4(gl_LightSource[0].position.xyz, 0.0)).xyz);
    else
        L = normalize((gl_ModelViewMatrixInverse * gl_LightSource[0].position).xyz - hit);

    vec3  V    = normalize(cam - hit);
    vec3  R    = reflect(-L, N);
    float diff = max(dot(N, L), 0.0);
    float spec = pow(max(dot(R, V), 0.0), sh);

    gl_FragColor = vec4(0.2 * Kd + diff * Kd + spec * Ks, 1.0);

    // Write surface depth for correct occlusion with other scene objects
    vec4 clip = gl_ProjectionMatrix * gl_ModelViewMatrix * vec4(hit, 1.0);
    gl_FragDepth = (clip.z / clip.w + 1.0) * 0.5;
}
"""
```

---

## Tier 3 — Coin3D Renderer

### R-003: Add `DMRayMarchRenderer.__init__` and `setup_nodes()` to `core/dm_ray_march_renderer.py`

**File:** `core/dm_ray_march_renderer.py` — append after the shader string constants

**What:** The class skeleton and scene graph constructor. The scene graph layout is:

```
SoSeparator
├── SoShapeHints          — UNKNOWN_ORDERING (no face culling)
├── SoTexture3            — packed float SDF volume; updated on each field change
├── SoShaderProgram       — holds the fixed vertex + fragment shaders
│   ├── SoVertexShader    — emits vWorldPos varying
│   └── SoFragmentShader  — sphere-traces texture; holds all uniforms
│       ├── SoUniformShaderParameter1i  u_sdf_vol   = 0  (texture unit 0)
│       ├── SoUniformShaderParameter3f  u_bbox_min
│       └── SoUniformShaderParameter3f  u_bbox_max
├── SoCoordinate3         — 8 AABB corner vertices in world space (updated on change)
└── SoIndexedFaceSet      — 6 quads forming the proxy bounding box (fixed topology)
```

**Implementation:**

```python
class DMRayMarchRenderer:
    """GPU ray-marching renderer backed by a 3D SDF volume texture."""

    def __init__(self, vobj):
        self._sep        = None
        self._tex3       = None   # SoTexture3
        self._frag       = None   # SoFragmentShader (holds uniforms)
        self._u_bbox_min = None   # SoUniformShaderParameter3f
        self._u_bbox_max = None   # SoUniformShaderParameter3f
        self._coords     = None   # SoCoordinate3 (proxy box corners)
        if coin:
            self._setup_nodes(vobj)

    def _setup_nodes(self, vobj):
        sep = coin.SoSeparator()

        # No face culling — proxy box visible from inside and outside
        hints = coin.SoShapeHints()
        hints.vertexOrdering.setValue(coin.SoShapeHints.UNKNOWN_ORDERING)
        sep.addChild(hints)

        # 3D texture — placeholder; filled by update()
        tex = coin.SoTexture3()
        tex.wrapR.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
        tex.wrapS.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
        tex.wrapT.setValue(coin.SoTexture3.CLAMP_TO_EDGE)
        # GL_NEAREST required: linear interpolation of packed bytes gives garbage
        tex.minFilter.setValue(coin.SoTexture3.NEAREST)
        tex.magFilter.setValue(coin.SoTexture3.NEAREST)
        sep.addChild(tex)

        # Fixed shader program (never recompiled)
        prog = coin.SoShaderProgram()
        vert = coin.SoVertexShader()
        vert.sourceType.setValue(coin.SoShader.GLSL_PROGRAM)
        vert.sourceProgram.setValue(_VERT_SRC)
        frag = coin.SoFragmentShader()
        frag.sourceType.setValue(coin.SoShader.GLSL_PROGRAM)
        frag.sourceProgram.setValue(_FRAG_SRC)

        # Sampler uniform — always texture unit 0
        u_vol = coin.SoUniformShaderParameter1i()
        u_vol.name.setValue("u_sdf_vol")
        u_vol.value.setValue(0)
        frag.parameter.set1Value(0, u_vol)

        # AABB uniforms — updated by update()
        u_min = coin.SoUniformShaderParameter3f()
        u_min.name.setValue("u_bbox_min")
        u_min.value.setValue(coin.SbVec3f(0, 0, 0))
        frag.parameter.set1Value(1, u_min)

        u_max = coin.SoUniformShaderParameter3f()
        u_max.name.setValue("u_bbox_max")
        u_max.value.setValue(coin.SbVec3f(1, 1, 1))
        frag.parameter.set1Value(2, u_max)

        prog.shaderObject.set1Value(0, vert)
        prog.shaderObject.set1Value(1, frag)
        sep.addChild(prog)

        # Proxy bounding box — world-space vertices, fixed quad topology
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, 8, [(0, 0, 0)] * 8)  # placeholder

        faces = coin.SoIndexedFaceSet()
        face_idx = [
            0, 1, 2, 3, -1,   # z-min face
            4, 7, 6, 5, -1,   # z-max face
            0, 4, 5, 1, -1,   # y-min face
            1, 5, 6, 2, -1,   # x-max face
            2, 6, 7, 3, -1,   # y-max face
            0, 3, 7, 4, -1,   # x-min face
        ]
        faces.coordIndex.setValues(0, len(face_idx), face_idx)
        sep.addChild(coords)
        sep.addChild(faces)

        self._sep        = sep
        self._tex3       = tex
        self._frag       = frag
        self._u_bbox_min = u_min
        self._u_bbox_max = u_max
        self._coords     = coords

        vobj.RootNode.addChild(sep)
```

**Depends on:** R-001, R-002

---

### R-004: Add `DMRayMarchRenderer.update()` to `core/dm_ray_march_renderer.py`

**File:** `core/dm_ray_march_renderer.py` — append method to `DMRayMarchRenderer`

**What:** Bakes the field to a volume texture, uploads it via `SoTexture3.image`, updates
the AABB uniforms, and resizes the proxy box corners to match the baked grid coverage.

**Implementation:**

```python
    def update(self, field, resolution: int = 64):
        """
        Re-bake the SDF volume and refresh all Coin3D nodes.

        Args:
            field:      Any FRepField — evaluated via evaluate_grid(), no other requirements.
            resolution: Voxels per axis. Default 64 (262 144 evaluations).
                        Increase to 128 for higher quality at the cost of ~8× bake time.
        """
        if not coin or self._tex3 is None:
            return

        from core import dm_logger
        dm_logger.debug(f"DMRayMarchRenderer.update: baking {resolution}³ volume")

        rgba, (mn, mx) = bake_sdf_volume(field, resolution)

        # Upload to SoTexture3 as RGBA8 (4 bytes per voxel)
        size = coin.SbVec3s(resolution, resolution, resolution)
        self._tex3.image.setValue(size, 4, rgba.tobytes())

        # Update AABB uniforms
        self._u_bbox_min.value.setValue(coin.SbVec3f(*mn))
        self._u_bbox_max.value.setValue(coin.SbVec3f(*mx))

        # Resize proxy box to match baked grid coverage
        x0, y0, z0 = mn
        x1, y1, z1 = mx
        corners = [
            (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
            (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
        ]
        self._coords.point.setValues(0, 8, corners)
```

**Depends on:** R-001, R-003

---

## Tier 4 — Integration

### R-005: Add `get_use_ray_march()` and `get_ray_march_resolution()` to `core/dm_object.py`

**File:** `core/dm_object.py` — add after `set_meshing_cell_size()` (after line 68)

**What:** Follow the exact pattern of the existing accessors. `_PARAM_PATH` is already defined
at line 26.

```python
def get_use_ray_march() -> bool:
    """Return True to use GPU ray marching instead of CPU meshing for F-Rep objects."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("UseRayMarching", False)

def set_use_ray_march(val: bool):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("UseRayMarching", bool(val))

def get_ray_march_resolution() -> int:
    """Return the voxel resolution per axis for the SDF volume texture (default 64)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("RayMarchResolution", 64)

def set_ray_march_resolution(val: int):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("RayMarchResolution", int(val))
```

---

### R-006: Wire `DMRayMarchRenderer` into `DMViewProvider`

**File:** `core/dm_object.py`

**Edit 1 — `attach()` at line 414.**

Replace the existing bare `self.renderer.setup_frep_mesh_nodes()` line with:

```python
            elif hasattr(self.Object, "ShapeType") and self.Object.ShapeType == "frep":
                if get_use_ray_march():
                    from core.dm_ray_march_renderer import DMRayMarchRenderer
                    self.ray_march_renderer = DMRayMarchRenderer(vobj)
                else:
                    self.renderer.setup_frep_mesh_nodes()
```

Add `from core.dm_object import get_use_ray_march` at the top of `attach()` (or at the
module level if preferred — follow the existing import style in the file).

**Edit 2 — `updateData()` at lines 441–451.**

Replace the existing `if prop == "Shape" and ... ShapeType == "frep":` block with:

```python
        if prop == "Shape" and hasattr(fp, "ShapeType") and fp.ShapeType == "frep":
            proxy = getattr(fp, "Proxy", None)
            field = getattr(proxy, "FRepField", None) if proxy else None

            ray_march = getattr(self, "ray_march_renderer", None)
            if ray_march is not None and field is not None:
                resolution = get_ray_march_resolution()
                ray_march.update(field, resolution)
            elif proxy and self.renderer:
                self.renderer.update_frep_mesh(
                    getattr(proxy, "_frep_verts", None),
                    getattr(proxy, "_frep_idx",   None),
                )
                if field:
                    self.renderer.update_frep_corners(field)
```

Add `from core.dm_object import get_ray_march_resolution` alongside the other import added
in Edit 1, or at module level.

**Note:** `proxy.FRepField` must be assigned on the proxy object before `fp.Shape` is set
in `DMObjectProxy.execute()`. Verify this is the case. If `FRepField` is not stored on the
proxy, store it there before the `fp.Shape = Part.Shape()` line.

**Depends on:** R-003, R-004, R-005

---

## Agent Skills

See `.agents/skills/` for project-specific knowledge:

| Skill | Purpose |
|-------|---------|
| `coin3d_shader_api` | SoShaderProgram, SoTexture3, uniform nodes, proxy geometry, built-in GLSL matrices |
| `dm_renderer_architecture` | Full Coin3D scene graph layout, ShapeType branching, where to hook new renderers |
| `dm_logging` | Logging conventions (`dm_logger.info`, `dm_logger.debug`) |

See `.agents/workflows/` for executable workflows:

| Workflow | Purpose |
|----------|---------|
| `/fix-task` | Fix a single task from this list by ID (e.g. `/fix-task R-003`) |
