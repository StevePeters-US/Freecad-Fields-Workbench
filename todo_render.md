# Direct Modeling Workbench — GPU Render & SDF Slice Task List

> Tasks are ordered by dependency. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

The GPU ray marching renderer has **4 critical bugs** causing a "hairy" render with
thousands of lines poking out:

1. **Buffer overread** — `coordIndex.setValues(0, 10, ...)` with only 8 values
2. **Broken outside-box march** — the early-exit check is never true, rays waste iterations
3. **Hit test accepts negative SDF** — overshooting rays hit inside the surface, inverted normals
4. **No AABB-ray clipping** — rays start from camera near plane, burn steps in empty space

Additionally, the ray march renderer's Coin3D nodes bypass `DMRenderer.vis_switch`, so hiding
the part in the tree doesn't hide the GPU render.

The SDF slice tool creates smooth cross-section curves of F-Rep SDFs on a plane, producing
DM curve objects compatible with the existing curve editing system.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `DMRayMarchRenderer` | `core/dm_ray_march_renderer.py:14` | GPU ray march renderer class |
| `DMRayMarchRenderer._setup_nodes()` | `core/dm_ray_march_renderer.py:24` | Shader + geometry setup |
| `DMRayMarchRenderer.update()` | `core/dm_ray_march_renderer.py:216` | Bake SDF + upload texture |
| `bake_sdf_to_atlas()` | `core/frep/sdf_baker.py:11` | CPU SDF → uint8 atlas |
| `DMViewProvider.onChanged()` | `core/dm_object.py:508` | ViewObject property change handler |
| `DMRenderer.update_visibility()` | `core/dm_renderer.py:51` | Toggle vis_switch |
| `create_dm_object()` | `core/dm_object.py:556` | Factory for DM objects (curve, frep, etc.) |
| `DMCurve` | `core/dm_curve.py:5` | NURBS curve wrapper |
| `DMPoint` | `core/dm_point.py:3` | 3D point with optional handles |
| `FRepField.evaluate_grid()` | `core/frep/frep_field.py:30` | Batch SDF evaluation |
| `FRepField.bounding_box()` | `core/frep/frep_field.py:20` | Returns (Vector, Vector) |

---

## Tier 1 — Fix Critical Render Bugs (Do First)

These fixes eliminate the "hairy lines" artifact and make the GPU render usable.

### [x] G-001: Fix `coordIndex.setValues` buffer overread

**File:** `core/dm_ray_march_renderer.py` — line 213

**What:** The `setValues` call declares 10 values but only provides 8, causing pivy to read
2 garbage indices that create degenerate triangles.

**Implementation:** Replace line 213:
```python
        faceset.coordIndex.setValues(0, 8, [0, 1, 2, -1, 0, 2, 3, -1])
```

---

### [x] G-002: Rewrite fragment shader with proper AABB-ray clipping and hit test

**File:** `core/dm_ray_march_renderer.py` — replace the fragment shader string (lines 47–145)

**What:** Replace the entire fragment shader source with a corrected version that:
1. Computes AABB-ray intersection (`tNear`/`tFar`) before marching
2. Uses `abs(d) < hit_thresh` to reject overshooting rays
3. Reduces hit threshold from `0.04 * max_dist` to `0.001 * max_dist`
4. Increases march steps from 128 to 256
5. Adds bisection refinement after initial hit
6. Scales normal epsilon with cell size instead of max_dist

**Implementation:** Replace the `f_shader.sourceProgram.setValue(...)` call (lines 47–145) with:

```python
        f_shader.sourceProgram.setValue("""
varying vec2  v_uv;
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
    // Scale epsilon with voxel size for stable normals
    float cell = (u_bbox_max.x - u_bbox_min.x) / max(float(u_nx), 1.0);
    float h = cell * 0.5;
    vec2 k = vec2(1.0, -1.0);
    return normalize(
        k.xyy * sample_sdf(p + k.xyy*h) +
        k.yyx * sample_sdf(p + k.yyx*h) +
        k.yxy * sample_sdf(p + k.yxy*h) +
        k.xxx * sample_sdf(p + k.xxx*h));
}

// AABB-ray intersection: returns (tNear, tFar). Miss if tNear > tFar.
vec2 intersect_aabb(vec3 ro, vec3 rd) {
    vec3 inv_rd = 1.0 / rd;
    vec3 t1 = (u_bbox_min - ro) * inv_rd;
    vec3 t2 = (u_bbox_max - ro) * inv_rd;
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

    vec3 ro = world_near.xyz;
    vec3 rd = normalize(world_far.xyz - world_near.xyz);
    vec3 cam = (gl_ModelViewMatrixInverse * vec4(0.0,0.0,0.0,1.0)).xyz;

    // 2. AABB-ray intersection — skip rays that miss the bounding box
    vec2 tBox = intersect_aabb(ro, rd);
    float tNear = max(tBox.x, 0.0);  // clamp to ray origin
    float tFar  = tBox.y;
    if (tNear > tFar) discard;        // ray misses box entirely

    // 3. Sphere-trace from tNear to tFar
    float hit_thresh = u_max_dist * 0.001;
    float min_step   = hit_thresh;
    float t = tNear;
    bool hit = false;
    float d;

    for (int i = 0; i < 256; i++) {
        vec3 p = ro + t * rd;
        d = sample_sdf(p);
        if (abs(d) < hit_thresh) { hit = true; break; }
        t += max(abs(d), min_step);
        if (t > tFar) break;
    }
    if (!hit) discard;

    // 4. Bisection refinement for sub-voxel accuracy
    float t_lo = t - min_step;
    float t_hi = t;
    for (int j = 0; j < 8; j++) {
        float t_mid = (t_lo + t_hi) * 0.5;
        float d_mid = sample_sdf(ro + t_mid * rd);
        if (d_mid < 0.0) {
            t_hi = t_mid;
        } else {
            t_lo = t_mid;
        }
    }
    t = (t_lo + t_hi) * 0.5;

    // 5. Shading
    vec3 hp  = ro + t * rd;
    vec3 n   = sdf_normal(hp);
    vec3 ld  = normalize(gl_LightSource[0].position.xyz - hp);
    float diff = max(dot(n, ld), 0.0);
    vec3 vd    = normalize(cam - hp);
    float spec = pow(max(dot(reflect(-ld, n), vd), 0.0), 32.0);
    vec3 color = vec3(1.0,0.5,0.0)*(0.15 + 0.75*diff) + vec3(0.4)*spec;

    gl_FragColor = vec4(color, 1.0);

    // 6. Correct depth write
    vec4 clip    = gl_ProjectionMatrix * gl_ModelViewMatrix * vec4(hp, 1.0);
    gl_FragDepth = (clip.z / clip.w + 1.0) * 0.5;
}
""")
```

**Depends on:** G-001

---

### [x] G-003: Add `set_visible()` method to `DMRayMarchRenderer`

**File:** `core/dm_ray_march_renderer.py` — append after `update()` method (after line 237)

**What:** Add a method to toggle the visibility of the ray march renderer's root node,
using an `SoSwitch` wrapper. Also modify `__init__` to wrap `self.root` in an `SoSwitch`.

**Implementation:**

Step 1 — Modify `__init__` (line 15–22). Replace:
```python
    def __init__(self, vobj):
        self.vobj = vobj  # FreeCAD ViewProvider
        self.root = coin.SoSeparator()
        self._u = {}      # Uniform nodes
        self._tex = None
        self._coords = None
        self._setup_nodes()
        vobj.RootNode.addChild(self.root)
```
with:
```python
    def __init__(self, vobj):
        self.vobj = vobj  # FreeCAD ViewProvider
        self.root = coin.SoSeparator()
        self._switch = coin.SoSwitch()
        self._switch.addChild(self.root)
        self._switch.whichChild = 0  # visible by default
        self._u = {}      # Uniform nodes
        self._tex = None
        self._coords = None
        self._setup_nodes()
        vobj.RootNode.addChild(self._switch)
```

Step 2 — Add after `update()`:
```python
    def set_visible(self, visible):
        """Toggle visibility of the ray march render."""
        self._switch.whichChild = 0 if visible else -1
```

---

### [x] G-004: Wire ray march renderer visibility to `DMViewProvider.onChanged`

**File:** `core/dm_object.py` — modify `onChanged()` method (lines 508–512)

**What:** When the ViewObject's `Visibility` property changes, also toggle the ray march
renderer if present.

**Implementation:** Replace lines 508–512:
```python
    def onChanged(self, vobj, prop):
        """Called when a property of the ViewObject changes (e.g. Visibility)."""
        if prop == "Visibility":
            if self.renderer:
                self.renderer.update_visibility(vobj.Visibility)
            rm = getattr(self, "ray_march_renderer", None)
            if rm:
                rm.set_visible(vobj.Visibility)
            pc = getattr(self, "point_cloud_renderer", None)
            if pc and hasattr(pc, "set_visible"):
                pc.set_visible(vobj.Visibility)
```

**Depends on:** G-003

---

## Tier 2 — Texture Quality

Reduce banding from uint8 quantization.

### [x] G-005: Upgrade SDF baker to uint16 atlas

**File:** `core/frep/sdf_baker.py` — modify `bake_sdf_to_atlas()` (lines 54–57, 80)

**What:** Switch from uint8 (256 values, severe banding) to uint16 (65536 values, smooth
gradients). Also increase clamp range from `4 * cell_size` to `8 * cell_size`.

**Implementation:**

Step 1 — Replace lines 54–57:
```python
    # Clamp and normalise to [0, 65535] (uint16 for smooth gradients)
    max_dist = cell_size * 8.0
    clamped  = np.clip(vals, -max_dist, max_dist)
    norm     = ((clamped / max_dist + 1.0) * 0.5 * 65535.0).astype(np.uint16)
```

Step 2 — Replace line 80 (`"atlas_bytes": atlas.tobytes(),`):
```python
        # Pack uint16 as two-channel uint8 (high byte, low byte) for GL_LUMINANCE_ALPHA
        high = (atlas >> 8).astype(np.uint8)
        low  = (atlas & 0xFF).astype(np.uint8)
        # Interleave: [h0, l0, h1, l1, ...] for LUMINANCE_ALPHA format
        packed = np.empty((atlas_h, atlas_w, 2), dtype=np.uint8)
        packed[:, :, 0] = high
        packed[:, :, 1] = low
```
And change the return `atlas_bytes` to:
```python
        "atlas_bytes": packed.tobytes(),
```

**Depends on:** G-002

---

### [x] G-006: Update ray march renderer for uint16 texture

**File:** `core/dm_ray_march_renderer.py` — modify `_setup_nodes()` and `update()`

**What:** Upload the 2-channel (LUMINANCE_ALPHA) texture and update the shader's
`sample_texel()` to reconstruct the full 16-bit value from two 8-bit channels.

**Implementation:**

Step 1 — In the fragment shader's `sample_texel()` function (inside the string from G-002),
replace:
```glsl
    return texture2D(u_sdf_tex, vec2(u, v)).r;
```
with:
```glsl
    vec4 t = texture2D(u_sdf_tex, vec2(u, v));
    return t.r + t.a / 256.0;  // reconstruct uint16 from high (r) + low (a) channels
```

Step 2 — In `update()` (line 220), change the texture upload from 1 component to 2:
```python
        self._tex.image.setValue(coin.SbVec2s(baked["atlas_w"], baked["atlas_h"]), 2, baked["atlas_bytes"])
```

**Depends on:** G-005

---

## Tier 3 — SDF Slice Curve Tool

Extract smooth cross-section curves from F-Rep SDFs.

### [x] G-007: Create `core/frep/sdf_slicer.py` — marching squares and curve fitting

**File:** `core/frep/sdf_slicer.py` — new file

**What:** Implements marching squares on a 2D grid to extract SDF zero-crossing contours on
an arbitrary plane. Fits the resulting polyline into a DM curve with auto-generated handles.

> **Required reading:** `.agents/skills/dm_sdf_slicer/SKILL.md`

**Implementation:**

```python
"""
core/frep/sdf_slicer.py

Extracts smooth cross-section curves from F-Rep SDFs using marching squares.
Outputs DM-curve-compatible dicts for create_dm_object().
"""
import math
import numpy as np
import FreeCAD

# ── Marching squares edge table ──────────────────────────────────────────────
# For each of the 16 cell configurations, list of edge pairs to connect.
# Edges: 0=bottom, 1=right, 2=top, 3=left
_MS_EDGES = [
    [],             # 0000
    [(3, 0)],       # 0001
    [(0, 1)],       # 0010
    [(3, 1)],       # 0011
    [(1, 2)],       # 0100
    [(1, 0), (3, 2)],  # 0101 (ambiguous — use average)
    [(0, 2)],       # 0110
    [(3, 2)],       # 0111
    [(2, 3)],       # 1000
    [(2, 0)],       # 1001
    [(0, 1), (2, 3)],  # 1010 (ambiguous)
    [(2, 1)],       # 1011
    [(1, 3)],       # 1100
    [(1, 0)],       # 1101
    [(0, 3)],       # 1110
    [],             # 1111
]


def slice_sdf(field, origin, normal, resolution=1.0, extent=None):
    """
    Extract zero-crossing contours of an SDF field on a plane.

    Args:
        field:      Any FRepField subclass.
        origin:     FreeCAD.Vector — point on the slice plane.
        normal:     FreeCAD.Vector — plane normal (will be normalized).
        resolution: float — grid spacing in mm on the plane.
        extent:     float or None — half-size of the sampling grid. If None,
                    computed from field.bounding_box().

    Returns:
        list[list[FreeCAD.Vector]] — one list of ordered 3D points per contour.
        Closed contours have first == last point.
    """
    normal = FreeCAD.Vector(normal)
    normal.normalize()

    # Build orthonormal basis on the plane
    up = FreeCAD.Vector(0, 0, 1)
    if abs(normal.dot(up)) > 0.99:
        up = FreeCAD.Vector(1, 0, 0)
    u_axis = normal.cross(up)
    u_axis.normalize()
    v_axis = normal.cross(u_axis)
    v_axis.normalize()

    # Determine grid extent from bounding box
    if extent is None:
        mn, mx = field.bounding_box()
        diag = (mx - mn).Length
        extent = diag * 0.6

    n = max(1, int(math.ceil(2 * extent / resolution)))
    half = extent

    # Sample SDF on the plane grid
    us = np.linspace(-half, half, n + 1).astype(np.float32)
    vs = np.linspace(-half, half, n + 1).astype(np.float32)
    U, V = np.meshgrid(us, vs, indexing='ij')

    # Convert 2D grid to 3D world points
    pts_3d = np.zeros((U.size, 3), dtype=np.float32)
    pts_3d[:, 0] = origin.x + U.ravel() * u_axis.x + V.ravel() * v_axis.x
    pts_3d[:, 1] = origin.y + U.ravel() * u_axis.y + V.ravel() * v_axis.y
    pts_3d[:, 2] = origin.z + U.ravel() * u_axis.z + V.ravel() * v_axis.z

    vals = field.evaluate_grid(pts_3d).reshape(n + 1, n + 1)

    # ── Marching squares ──
    segments = []
    for i in range(n):
        for j in range(n):
            # Corner values: bottom-left, bottom-right, top-right, top-left
            v0 = vals[i, j]
            v1 = vals[i + 1, j]
            v2 = vals[i + 1, j + 1]
            v3 = vals[i, j + 1]

            idx = 0
            if v0 < 0: idx |= 1
            if v1 < 0: idx |= 2
            if v2 < 0: idx |= 4
            if v3 < 0: idx |= 8

            edges = _MS_EDGES[idx]
            if not edges:
                continue

            # Edge midpoints with linear interpolation
            corners_u = [us[i], us[i + 1], us[i + 1], us[i]]
            corners_v = [vs[j], vs[j], vs[j + 1], vs[j + 1]]
            corner_vals = [v0, v1, v2, v3]

            def edge_point(e):
                a = e
                b = (e + 1) % 4
                va = corner_vals[a]
                vb = corner_vals[b]
                denom = va - vb
                if abs(denom) < 1e-12:
                    frac = 0.5
                else:
                    frac = va / denom
                eu = corners_u[a] + frac * (corners_u[b] - corners_u[a])
                ev = corners_v[a] + frac * (corners_v[b] - corners_v[a])
                return (eu, ev)

            for e0, e1 in edges:
                p0 = edge_point(e0)
                p1 = edge_point(e1)
                segments.append((p0, p1))

    # ── Chain segments into contours ──
    contours_2d = _chain_segments(segments)

    # ── Convert to 3D ──
    contours_3d = []
    for contour in contours_2d:
        pts = []
        for (eu, ev) in contour:
            p = FreeCAD.Vector(
                origin.x + eu * u_axis.x + ev * v_axis.x,
                origin.y + eu * u_axis.y + ev * v_axis.y,
                origin.z + eu * u_axis.z + ev * v_axis.z,
            )
            pts.append(p)
        if pts:
            contours_3d.append(pts)

    return contours_3d


def _chain_segments(segments, tol=1e-6):
    """Chain unordered line segments into ordered polylines."""
    if not segments:
        return []

    remaining = list(segments)
    contours = []

    while remaining:
        seg = remaining.pop(0)
        chain = [seg[0], seg[1]]

        changed = True
        while changed:
            changed = False
            for k in range(len(remaining) - 1, -1, -1):
                s = remaining[k]
                d0_end = abs(chain[-1][0] - s[0][0]) + abs(chain[-1][1] - s[0][1])
                d1_end = abs(chain[-1][0] - s[1][0]) + abs(chain[-1][1] - s[1][1])
                d0_start = abs(chain[0][0] - s[1][0]) + abs(chain[0][1] - s[1][1])
                d1_start = abs(chain[0][0] - s[0][0]) + abs(chain[0][1] - s[0][1])

                if d0_end < tol:
                    chain.append(s[1])
                    remaining.pop(k)
                    changed = True
                elif d1_end < tol:
                    chain.append(s[0])
                    remaining.pop(k)
                    changed = True
                elif d0_start < tol:
                    chain.insert(0, s[0])
                    remaining.pop(k)
                    changed = True
                elif d1_start < tol:
                    chain.insert(0, s[1])
                    remaining.pop(k)
                    changed = True

        contours.append(chain)

    return contours


def fit_dm_curve(contour_points, closed=False, smooth_factor=0.33):
    """
    Fit a DM curve through a list of 3D points.

    Args:
        contour_points: list[FreeCAD.Vector] — ordered 3D points.
        closed:         bool — whether the contour is closed.
        smooth_factor:  float — handle length as fraction of chord.

    Returns:
        dict with keys: Points, HandleIn, HandleOut, Closed
        Compatible with create_dm_object(name, "curve", params=result).
    """
    pts = list(contour_points)

    # Remove near-duplicate last point if closed
    if closed and len(pts) > 2:
        if (pts[0] - pts[-1]).Length < 0.01:
            pts = pts[:-1]

    # Decimate: skip points that are too close together
    if len(pts) > 3:
        decimated = [pts[0]]
        min_dist = max(0.1, (pts[0] - pts[-1]).Length / max(len(pts), 1) * 0.5)
        for p in pts[1:]:
            if (p - decimated[-1]).Length >= min_dist:
                decimated.append(p)
        pts = decimated

    n = len(pts)
    if n < 2:
        return {"Points": pts, "HandleIn": pts[:], "HandleOut": pts[:],
                "Closed": closed, "is_closed": closed}

    handles_in = []
    handles_out = []

    for i in range(n):
        prev_p = pts[(i - 1) % n] if (closed or i > 0) else pts[i]
        next_p = pts[(i + 1) % n] if (closed or i < n - 1) else pts[i]

        tangent = next_p - prev_p
        chord_prev = (pts[i] - prev_p).Length
        chord_next = (next_p - pts[i]).Length

        if tangent.Length > 1e-6:
            tangent.normalize()
        else:
            tangent = FreeCAD.Vector(1, 0, 0)

        handle_in = pts[i] - tangent * (chord_prev * smooth_factor)
        handle_out = pts[i] + tangent * (chord_next * smooth_factor)

        handles_in.append(handle_in)
        handles_out.append(handle_out)

    return {
        "Points": pts,
        "HandleIn": handles_in,
        "HandleOut": handles_out,
        "Closed": closed,
        "is_closed": closed,
    }
```

---

### [x] G-008: Create `commands/cmd_sdf_slice.py` — FreeCAD slice command

**File:** `commands/cmd_sdf_slice.py` — new file

**What:** FreeCAD command that slices the selected F-Rep object on the active workplane
(or XY plane if none) and creates DM curve objects from the resulting contours.

> **Required reading:** `.agents/skills/dm_sdf_slicer/SKILL.md`

**Implementation:**

```python
"""
commands/cmd_sdf_slice.py

Slice an F-Rep SDF on a plane and create DM curve objects.
"""
import FreeCAD
import FreeCADGui
from core import dm_logger


class SDFSliceCommand:
    """Slice an F-Rep object to create cross-section curves."""

    def GetResources(self):
        return {
            'Pixmap': 'Part_CrossSections',
            'MenuText': 'SDF Slice',
            'ToolTip': 'Slice an F-Rep SDF on a plane to create cross-section curves.\n'
                       'Select an F-Rep object first. Uses the active workplane or XY plane.',
        }

    def Activated(self):
        try:
            sel = FreeCADGui.Selection.getSelection()
            if not sel:
                dm_logger.warning("SDFSlice: No object selected")
                return
            obj = sel[0]
            proxy = getattr(obj, "Proxy", None)
            field = getattr(proxy, "FRepField", None) if proxy else None
            if field is None:
                dm_logger.warning("SDFSlice: Selected object has no FRepField")
                return

            # Get slice plane from active workplane or default to XY
            origin = FreeCAD.Vector(0, 0, 0)
            normal = FreeCAD.Vector(0, 0, 1)
            try:
                from core.dm_workplane import get_active_workplane
                wp = get_active_workplane()
                if wp:
                    placement = wp.Placement
                    origin = placement.Base
                    normal = placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
            except Exception:
                pass  # Use default XY plane

            from core.frep.sdf_slicer import slice_sdf, fit_dm_curve
            from core.dm_object import create_dm_object, get_meshing_cell_size

            resolution = get_meshing_cell_size()
            contours = slice_sdf(field, origin, normal, resolution=resolution)

            if not contours:
                dm_logger.info("SDFSlice: No contours found on the slice plane")
                return

            doc = FreeCAD.activeDocument()
            for i, contour in enumerate(contours):
                if len(contour) < 2:
                    continue
                # Detect if closed (first ≈ last point)
                is_closed = (contour[0] - contour[-1]).Length < resolution * 2
                params = fit_dm_curve(contour, closed=is_closed)
                name = f"{obj.Label}_Slice{i}"
                create_dm_object(name, "curve", params=params)

            doc.recompute()
            dm_logger.info(f"SDFSlice: Created {len(contours)} slice curve(s)")

        except Exception as e:
            dm_logger.exception(f"SDFSlice: Error: {e}")

    def IsActive(self):
        return FreeCAD.activeDocument() is not None


FreeCADGui.addCommand('DM_SDFSlice', SDFSliceCommand())
```

**Depends on:** G-007

---

### [x] G-009: Register `DM_SDFSlice` in toolbar and menu

**File:** `InitGui.py` — add to the command lists

**What:** Add `'DM_SDFSlice'` to the toolbar and menu so users can access it.

**Implementation:**

1. Add `import commands.cmd_sdf_slice` alongside the other command imports.
2. Add `'DM_SDFSlice'` to the toolbar list (after the existing export commands).
3. Add `'DM_SDFSlice'` to the menu list.

**Depends on:** G-008

---

## Tier 4 — Polish

### [x] G-010: Add Bounding Box Proxy to prevent View Clipping

**File:** `core/dm_ray_march_renderer.py`

**What:** The full-screen quad is at `Z=0` covering `[-1, 1]` in XY. Because it's the only geometry in the scene, Coin3D calculates a 0-depth bounding box. This causes FreeCAD's camera near/far clip planes to squish tightly around `Z=0`, generating enormous floating-point errors in the ray unprojection matrix, which leads to degenerate triangles, tearing, and view clipping.
Fix this by providing an invisible proxy shape with the exact SDF bounding box to force correct camera planes.

**Implementation:**

Step 1 — In `_setup_nodes()`, right after the `_switch` setup (or just before returning), add:
```python
        # 5. Bounding box proxy to fix Coin3D near/far clipping
        self._bbox_sep = coin.SoSeparator()
        bbox_style = coin.SoDrawStyle()
        bbox_style.style.setValue(coin.SoDrawStyle.INVISIBLE)
        self._bbox_sep.addChild(bbox_style)
        
        self._bbox_coords = coin.SoCoordinate3()
        self._bbox_sep.addChild(self._bbox_coords)
        
        bbox_pts = coin.SoPointSet()
        bbox_pts.numPoints.setValue(8)
        self._bbox_sep.addChild(bbox_pts)
        
        self.root.addChild(self._bbox_sep)
```

Step 2 — In `update()`, inside the uniform update block, set the 8 corners:
```python
        mn, mx = baked["bbox_min"], baked["bbox_max"]
        self._bbox_coords.point.setValues(0, 8, [
            (mn.x, mn.y, mn.z), (mx.x, mn.y, mn.z),
            (mn.x, mx.y, mn.z), (mx.x, mx.y, mn.z),
            (mn.x, mn.y, mx.z), (mx.x, mn.y, mx.z),
            (mn.x, mx.y, mx.z), (mx.x, mx.y, mx.z)
        ])
```

**Depends on:** G-006

---

### G-011: Fix uint16 texture reconstruction precision (Terracing)

**File:** `core/dm_ray_march_renderer.py` — modify `sample_texel()` in fragment shader

**What:** The current uint16 reconstruction formula (`t.r + t.a / 256.0`) is mathematically imprecise, mapping to `257/256` instead of `65535/65535`. This creates value discontinuities ("steps") when the low byte wraps, causing visible terracing and bumpy sides on flat F-Rep objects.

**Implementation:** In the fragment shader string, replace the reconstruction in `sample_texel()`:
```glsl
    vec4 t = texture2D(u_sdf_tex, vec2(u, v));
    // Mathematically exact uint16 reconstruction for OpenGL
    return (t.r * 65280.0 + t.a * 255.0) / 65535.0;
```

**Depends on:** G-006

---

### G-012: Add edge anti-aliasing to fragment shader

---

## Agent Skills

| Skill | Purpose |
|-------|---------|
| `coin3d_shader_api` | `SoShaderProgram` setup, uniform node types, proxy geometry |
| `coin3d_fullscreen_quad` | Full-screen quad rendering pipeline and unprojection math |
| `dm_sdf_slicer` | Marching squares, DM curve compatibility, `fit_dm_curve` contract |
| `dm_todo_format` | Task format and conventions for this project |

## Workflows

| Workflow | Purpose |
|----------|---------|
| `/fix-task` | Fix a single task by ID (e.g. `/fix-task G-003`) |
