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

### [x] G-011: Fix uint16 texture reconstruction precision (Terracing)

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

## Tier 5 — Critical Render Bug Fixes 

### [x] G-013: Fix Bounding Box Proxy (Fixes Viewplane Clipping)

**File:** `core/dm_ray_march_renderer.py`

**What:** In task G-010, `SoDrawStyle.INVISIBLE` was used on the bounding box proxy to hide it. Unfortunately, Open Inventor/Coin3D ignores invisible nodes during `SoGetBoundingBoxAction`. Therefore, the proxy did not expand the scene bounding box, and FreeCAD's camera near/far planes still squish tightly around the Z=0 quad, destroying projection matrix precision and causing clipping.
To fix this, we replace the `SoDrawStyle` with an `SoMaterial` having 100% transparency, and use an `SoIndexedLineSet` that actually has geometric edges, plus an `SoPickStyle` to keep it unpickable.

**Implementation:** In `_setup_nodes()`, replace the existing `# 5. Bounding box proxy...` section with:
```python
        # 5. Bounding box proxy to fix Coin3D near/far clipping
        self._bbox_sep = coin.SoSeparator()
        
        # Transparent material (Coin3D BBox action ignores INVISIBLE draw style)
        mat = coin.SoMaterial()
        mat.transparency.setValue(1.0)
        self._bbox_sep.addChild(mat)
        
        # Prevent picking
        pick = coin.SoPickStyle()
        pick.style.setValue(coin.SoPickStyle.UNPICKABLE)
        self._bbox_sep.addChild(pick)
        
        self._bbox_coords = coin.SoCoordinate3()
        self._bbox_sep.addChild(self._bbox_coords)
        
        # Ensure we have actual bounded edges (12 edges of a box = 36 indices)
        bbox_lines = coin.SoIndexedLineSet()
        bbox_lines.coordIndex.setValues(0, 36, [
            0,1,-1, 1,3,-1, 3,2,-1, 2,0,-1,
            4,5,-1, 5,7,-1, 7,6,-1, 6,4,-1,
            0,4,-1, 1,5,-1, 2,6,-1, 3,7,-1
        ])
        self._bbox_sep.addChild(bbox_lines)
        
        self.root.addChild(self._bbox_sep)
```
*(No changes needed in `update()`; it already sets the 8 corners)*

**Depends on:** G-010

---

### [x] G-014: Fix Sphere Tracing Math (Fixes "Lines" and "Wrong Triangles")

**File:** `core/dm_ray_march_renderer.py` — modify `sample_sdf()` and `main()` in fragment shader

**What:** The ray marcher has two mathematical flaws causing the SDF faces to tear into lines and noisy triangles:
1. `sample_sdf` checks `any(lessThan(p, u_bbox_min))` and returns `u_max_dist`. Floating precision on `tNear` pushes rays slightly outside the box, causing them to immediately return `u_max_dist` and jump past the geometry, destroying the surface exactly at the box bounds.
2. The bisection refinement loops between `t - min_step` and `t`. However, the loop stops when `abs(d) < hit_thresh` which often occurs BEFORE crossing the boundary (`d > 0`). Bracketing between two outside points converges on pure noise. 

**Implementation:** 

Step 1 — In `sample_sdf()`, delete the strict bounding box check. The texture clamping is mathematically safe and sufficient. Change the start of `sample_sdf()` from:
```glsl
float sample_sdf(vec3 p) {
    if (any(lessThan(p, u_bbox_min)) || any(greaterThan(p, u_bbox_max)))
        return u_max_dist;
    vec3 uvw = (p - u_bbox_min) / (u_bbox_max - u_bbox_min);
```
To just:
```glsl
float sample_sdf(vec3 p) {
    vec3 uvw = (p - u_bbox_min) / (u_bbox_max - u_bbox_min);
```

Step 2 — In `main()`, remove the 8-step bisection refinement completely. It's conceptually invalid for `abs(d) < thresh` exits. Remove this block:
```glsl
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
```

**Depends on:** G-002

---

## Tier 6 — Scene Graph & Texture Fixes

> **Root cause analysis:** The "faces rendered as lines" artifact is caused by the
> Coin3D scene graph structure. The `SoShaderProgram` is a direct child of
> `self.root` (the main `SoSeparator`), so **every renderable child** of root
> inherits the ray march shader — including the bounding-box proxy's
> `SoIndexedLineSet`. When Coin3D traverses the bbox proxy, the vertex shader
> does `gl_Position = vec4(gl_Vertex.xy, 0.0, 1.0)`, treating world-space box
> coordinates (e.g. `(-20, -20)`) as NDC clip coords. After GPU viewport
> clipping, fragments that survive execute the ray march fragment shader as
> 1-pixel-wide **lines** — producing the characteristic "faces rendered as
> lines" artifact seen in the screenshots.

### [x] G-015: Isolate shader scope from bbox proxy (PRIMARY FIX)

**File:** `core/dm_ray_march_renderer.py` — restructure `_setup_nodes()`

**What:** The `SoShaderProgram`, texture, quad coordinates, and `SoIndexedFaceSet`
must be inside their own `SoSeparator` so the shader does **not** leak into the
bounding-box proxy. The bbox proxy must remain a direct child of `self.root`
(outside the shader separator) so its world-space coordinates participate in
Coin3D's bounding-box computation without being rendered by the ray march shader.

> **Required reading:** `.agents/skills/coin3d_fullscreen_quad/SKILL.md`

**Implementation:**

Restructure `_setup_nodes()` so the scene graph looks like this:

```
self.root (SoSeparator)
├── self._bbox_sep (SoSeparator)         ← bbox proxy (NO shader here)
│   ├── SoMaterial (transparency=1.0)
│   ├── SoPickStyle (UNPICKABLE)
│   ├── _bbox_coords (SoCoordinate3)
│   └── SoIndexedLineSet (12 edges)
└── self._shader_sep (SoSeparator)       ← NEW: shader-scoped group
    ├── SoTexture2
    ├── SoShaderProgram (vertex + fragment)
    ├── SoShapeHints (UNKNOWN_ORDERING)
    ├── SoCoordinate3 (quad: (-1,-1)..(1,1))
    └── SoIndexedFaceSet (2 triangles)
```

Step 1 — Create `self._shader_sep = coin.SoSeparator()` and add the texture,
shader program, shape hints, quad coords, and face set as children of
`_shader_sep` instead of `self.root`.

Step 2 — Add `self._bbox_sep` to `self.root` **first**, then add
`self._shader_sep` to `self.root` **second**. This ensures the bbox coordinates
are traversed for bounding-box purposes but never rendered with the shader.

Step 3 — No changes needed in `update()` — it already sets `_bbox_coords` and
uniforms independently.

**Depends on:** G-013

---

### [x] G-016: Force NEAREST texture filtering on SDF atlas

**File:** `core/dm_ray_march_renderer.py` — modify `_setup_nodes()`

**What:** Coin3D defaults to `GL_LINEAR` (bilinear) texture filtering. Since the
shader performs its own trilinear interpolation via `sample_texel()`, the hardware
bilinear filter is redundant and **harmful**: at tile boundaries in the atlas, the
2×2 sampling kernel bleeds values from adjacent z-slice tiles, corrupting the SDF.
Force `GL_NEAREST` filtering by inserting an `SoComplexity` node with
`textureQuality = 0.0` before the texture node.

**Implementation:** In `_setup_nodes()`, inside `_shader_sep`, add before the
`SoTexture2` node:

```python
        complexity = coin.SoComplexity()
        complexity.textureQuality.setValue(0.0)   # GL_NEAREST / GL_NEAREST
        self._shader_sep.addChild(complexity)
```

**Depends on:** G-015

---

### [x] G-017: Add shader debug colour mode

**File:** `core/dm_ray_march_renderer.py` — modify fragment shader + add uniform

**What:** Add a `u_debug_mode` integer uniform that overrides the final fragment
colour to visualize diagnostic data. This makes it easy to diagnose future
rendering issues without modifying the shader source each time.

| `u_debug_mode` | Output |
|:-:|:-|
| 0 | Normal shading (default) |
| 1 | SDF value heat-map (blue=negative → red=positive) |
| 2 | Surface normal as RGB |
| 3 | Ray march iteration count as grayscale |

**Implementation:**

Step 1 — Add a `uniform int u_debug_mode;` declaration in the fragment shader.

Step 2 — After shading (section 5), add:
```glsl
    if (u_debug_mode == 1) {
        float v = sample_sdf(hp) / u_max_dist * 0.5 + 0.5;
        gl_FragColor = vec4(v, 0.0, 1.0 - v, 1.0);
    } else if (u_debug_mode == 2) {
        gl_FragColor = vec4(n * 0.5 + 0.5, 1.0);
    } else if (u_debug_mode == 3) {
        // Need to track iteration count in the march loop
        gl_FragColor = vec4(vec3(float(march_iters) / 256.0), 1.0);
    }
```
(Also change the march loop to track `march_iters`.)

Step 3 — Add the uniform node in Python:
```python
        self._u["u_debug_mode"] = coin.SoShaderParameter1i()
        self._u["u_debug_mode"].name.setValue("u_debug_mode")
        self._u["u_debug_mode"].value.setValue(0)
```

Step 4 — Add a `set_debug_mode(mode)` method on `DMRayMarchRenderer`.

**Depends on:** G-015

---

### G-012: Add edge anti-aliasing to fragment shader

**Depends on:** G-015

---

## Tier 7 — Lighting & Camera Fixes

### [x] G-018: Fix scene lighting in fragment shader (coordinate space mismatch)

**File:** `core/dm_ray_march_renderer.py` — modify section 5 (Shading) in the fragment shader string (around line 193)

**What:** The shader computes `ld = normalize(gl_LightSource[0].position.xyz - hp)`, but
`gl_LightSource[0].position` is in **eye space** (pre-transformed by the ModelView matrix by
OpenGL convention) while `hp` is in **world space** (computed via `gl_ModelViewProjectionMatrixInverse`
unprojection). This coordinate space mismatch produces a near-zero or nonsensical light direction,
making the surface appear uniformly dark with no diffuse response.

FreeCAD's default headlight is a directional light (`position.w == 0.0`), so its `.xyz` is a
direction in eye space, not a position. We must transform it to world space before dotting with
the world-space normal.

> **Required reading:** `.agents/skills/dm_glsl_lighting/SKILL.md`

**Implementation:** In the fragment shader, replace the shading block (section 5):

```glsl
    // 5. Shading
    vec3 hp  = ro + t * rd;
    vec3 n   = sdf_normal(hp);
    vec3 ld  = normalize(gl_LightSource[0].position.xyz - hp);
    float diff = max(dot(n, ld), 0.0);
    vec3 vd    = normalize(cam - hp);
    float spec = pow(max(dot(reflect(-ld, n), vd), 0.0), 32.0);
    vec3 color = vec3(1.0,0.5,0.0)*(0.15 + 0.75*diff) + vec3(0.4)*spec;

    gl_FragColor = vec4(color, 1.0);
```

with:

```glsl
    // 5. Shading — transform light from eye space to world space
    vec3 hp  = ro + t * rd;
    vec3 n   = sdf_normal(hp);

    // gl_LightSource[0].position is in EYE space (OpenGL convention).
    // For directional lights (w==0), .xyz is the light direction in eye space.
    // For positional lights (w==1), .xyz is the position in eye space.
    vec4 light_eye = gl_LightSource[0].position;
    vec3 ld;
    if (light_eye.w < 0.5) {
        // Directional light: transform direction to world space
        ld = normalize((gl_ModelViewMatrixInverse * vec4(light_eye.xyz, 0.0)).xyz);
    } else {
        // Positional light: transform position to world space, then direction
        vec3 light_world = (gl_ModelViewMatrixInverse * light_eye).xyz;
        ld = normalize(light_world - hp);
    }

    float diff = max(dot(n, ld), 0.0);
    vec3 vd    = normalize(cam - hp);
    float spec = pow(max(dot(reflect(-ld, n), vd), 0.0), 32.0);
    vec3 color = vec3(1.0,0.5,0.0)*(0.15 + 0.75*diff) + vec3(0.4)*spec;

    gl_FragColor = vec4(color, 1.0);
```

**Depends on:** G-015

---

### [x] G-019: Add near clip distance setting to DM Settings

**File:** `core/dm_object.py` — add getter/setter after `set_deduplicate_enabled()` (line 130)
**File:** `commands/cmd_settings.py` — add UI control and apply logic

**What:** FreeCAD's auto-computed camera near plane can clip F-Rep objects rendered via the
ray march shader, especially when the camera is close to large objects. Add a configurable
near clip distance override. When set to 0 (default), FreeCAD's automatic near plane is used.
When set to a positive value, the camera's `nearDistance` is forced to that value after each
recompute.

**Implementation:**

Step 1 — In `core/dm_object.py`, after `set_deduplicate_enabled()` (line 130), add:
```python
def get_near_clip_distance():
    """Return the near clip distance override in mm (0 = auto)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("NearClipDistance", 0.0)

def set_near_clip_distance(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("NearClipDistance", float(val))

def apply_near_clip_override():
    """Apply near clip distance override to the active camera, if set."""
    dist = get_near_clip_distance()
    if dist <= 0.0:
        return  # auto mode
    try:
        import FreeCADGui
        view = FreeCADGui.ActiveDocument.ActiveView
        cam = view.getCameraNode()
        cam.nearDistance.setValue(dist)
    except Exception:
        pass
```

Step 2 — In `commands/cmd_settings.py`, in `_SettingsDialog.__init__()`, after the
Curvature Threshold row (line 141), add:
```python
        # Near Clip Distance spinbox
        from core.dm_object import get_near_clip_distance
        self._near_clip_spin = QtGui.QDoubleSpinBox()
        self._near_clip_spin.setRange(0.0, 10000.0)
        self._near_clip_spin.setSingleStep(1.0)
        self._near_clip_spin.setDecimals(1)
        self._near_clip_spin.setValue(get_near_clip_distance())
        self._near_clip_spin.setToolTip(
            "Override camera near clipping distance in mm.\n"
            "Set to 0 for automatic (FreeCAD default).\n"
            "Increase if F-Rep objects are clipped when zoomed in."
        )
        layout.addRow("Near Clip Distance (mm):", self._near_clip_spin)
```

Step 3 — In `commands/cmd_settings.py`, in `_on_accept()`, after the existing setters
(around line 178), add:
```python
        from core.dm_object import set_near_clip_distance, apply_near_clip_override
        set_near_clip_distance(self._near_clip_spin.value())
        apply_near_clip_override()
```

**Depends on:** None (independent of shader tasks)

---

### [x] G-020: Apply near clip override on document recompute

**File:** `core/dm_object.py` — modify `DMViewProvider.updateData()` or `onChanged()`

**What:** The near clip override from G-019 must be re-applied whenever the scene
changes, because FreeCAD's navigation resets camera parameters on zoom/pan.
Hook into `DMViewProvider.onChanged()` to call `apply_near_clip_override()` whenever
the `Visibility` property changes or the document recomputes.

**Implementation:** In `DMViewProvider.onChanged()`, after the existing visibility handling,
add:
```python
        # Re-apply near clip override (FreeCAD navigation resets camera params)
        from core.dm_object import apply_near_clip_override
        apply_near_clip_override()
```

**Depends on:** G-019

---

## Tier 8 — Depth Ordering Fixes

> **Root cause analysis:** The ray march renderer's `_shader_sep` has no explicit
> `SoMaterial` node. It inherits material state from the parent ViewProvider context.
> Coin3D classifies geometry as **opaque** (transparency=0) or **transparent**
> (transparency>0) based on the active material. Opaque geometry renders first with
> depth writes enabled; transparent geometry renders second with depth writes
> **disabled**. If the inherited material has any transparency, the full-screen quad
> is classified as transparent — its `gl_FragDepth` values never reach the depth
> buffer. Result: the work plane (also transparent, rendered after opaques) cannot
> depth-test against the SDF, and multiple SDF quads overwrite each other in scene
> graph order rather than per-pixel depth order.

### [x] G-021: Add explicit opaque material to shader separator (PRIMARY DEPTH FIX)

**File:** `core/dm_ray_march_renderer.py` — modify `_setup_nodes()` (after line 57)

**What:** Add an `SoMaterial` with `transparency=0.0` as the first child of `_shader_sep`
to guarantee the full-screen quad is classified as opaque geometry. This ensures Coin3D
renders it in the opaque pass with depth writes enabled, making `gl_FragDepth` values
participate in the depth buffer for correct ordering with all other scene geometry
(work plane, NURBS objects, other SDF volumes).

> **Required reading:** `.agents/skills/dm_coin3d_depth_ordering/SKILL.md`

**Implementation:** In `_setup_nodes()`, immediately after creating `_shader_sep`
(line 57: `self._shader_sep = coin.SoSeparator()`), add:

```python
        # Force opaque classification — without this, the quad inherits the parent
        # ViewProvider's material, which may have transparency > 0, causing Coin3D
        # to render the quad in the transparent pass with depth writes DISABLED.
        quad_mat = coin.SoMaterial()
        quad_mat.transparency.setValue(0.0)
        self._shader_sep.addChild(quad_mat)
```

The resulting scene graph for `_shader_sep` becomes:
```
_shader_sep (SoSeparator)
├── SoMaterial (transparency=0.0)     ← NEW
├── SoComplexity (textureQuality=0.0)
├── SoTexture2
├── SoShaderProgram
├── SoShapeHints
├── SoCoordinate3 (quad)
└── SoIndexedFaceSet (2 triangles)
```

**Depends on:** G-015

---

### G-022: Add explicit depth buffer control to shader separator

**File:** `core/dm_ray_march_renderer.py` — modify `_setup_nodes()`

**What:** Add an `SoDepthBuffer` node (if available in pivy) to explicitly enable depth
testing and depth writing for the ray march quad. This is a belt-and-suspenders fix:
G-021 should ensure opaque classification, but `SoDepthBuffer` provides an explicit
guarantee regardless of Coin3D's material-based classification logic.

> **Required reading:** `.agents/skills/dm_coin3d_depth_ordering/SKILL.md`

**Implementation:** In `_setup_nodes()`, after the `SoMaterial` added in G-021, add:

```python
        # Explicit depth buffer control (belt-and-suspenders with opaque material)
        try:
            depth_buf = coin.SoDepthBuffer()
            depth_buf.test.setValue(True)   # GL_DEPTH_TEST enabled
            depth_buf.write.setValue(True)  # glDepthMask(GL_TRUE)
            self._shader_sep.addChild(depth_buf)
        except AttributeError:
            pass  # SoDepthBuffer not available in this Coin3D/pivy version
```

**Depends on:** G-021

---

### G-023: Use `gl_DepthRange` in `gl_FragDepth` formula for Coin3D compatibility

**File:** `core/dm_ray_march_renderer.py` — modify section 6 in the fragment shader string

**What:** The current depth formula `(clip.z / clip.w + 1.0) * 0.5` assumes the standard
`glDepthRange(0, 1)`. While this is the OpenGL default, Coin3D or FreeCAD may configure a
different depth range (e.g. for multi-pass rendering or depth partitioning). Use the GLSL
built-in `gl_DepthRange` struct to ensure the depth value matches whatever range Coin3D
has actually configured.

> **Required reading:** `.agents/skills/dm_coin3d_depth_ordering/SKILL.md`

**Implementation:** In the fragment shader, replace section 6:

```glsl
    // 6. Correct depth write
    vec4 clip    = gl_ProjectionMatrix * gl_ModelViewMatrix * vec4(hp, 1.0);
    gl_FragDepth = (clip.z / clip.w + 1.0) * 0.5;
```

with:

```glsl
    // 6. Depth write — use gl_DepthRange for Coin3D compatibility
    vec4 clip     = gl_ModelViewProjectionMatrix * vec4(hp, 1.0);
    float ndc_z   = clip.z / clip.w;
    gl_FragDepth  = gl_DepthRange.near
                  + gl_DepthRange.diff * (ndc_z * 0.5 + 0.5);
```

Note: `gl_DepthRange.diff` = `gl_DepthRange.far - gl_DepthRange.near`. When the depth
range is the standard `[0, 1]`, this produces the same result as the original formula.
Also changed `gl_ProjectionMatrix * gl_ModelViewMatrix` to the equivalent pre-multiplied
`gl_ModelViewProjectionMatrix` (one less matrix multiply per fragment).

**Depends on:** G-021

---

## Tier 9 — Scene-Level Renderer (Single Quad, Combined Bake)

> **Problem:** Each F-Rep object creates its own full-screen quad with its own baked
> texture. With N objects, N quads fight for depth at every pixel — later scene graph
> entries overwrite earlier ones regardless of actual depth. The work plane (transparent
> pass) cannot depth-test against multiple independent opaque quads correctly. See
> screenshot: overlapping spheres and box rendered on top of each other, work plane
> bleeds through.
>
> **Solution:** Replace per-object `DMRayMarchRenderer` with a single
> `DMSceneRayMarchRenderer` singleton. It maintains a registry of active F-Rep fields,
> combines them via `UnionField`, bakes the combined SDF into one texture, and renders
> with one full-screen quad. One quad = one `gl_FragDepth` per pixel = correct depth
> compositing with everything in the scene.
>
> **Key constraint:** The combined baking uses `evaluate_grid()` on the union tree —
> this is generic and works with ANY SDF. No per-primitive GLSL formulas. No analytical
> shader generation. The existing `bake_sdf_to_atlas()` and fragment shader are reused
> unchanged.

### G-024: Create `DMSceneRayMarchRenderer` singleton

**File:** `core/dm_scene_ray_march_renderer.py` — new file

**What:** A scene-level singleton that owns one full-screen quad (attached to the
viewer's scene graph root, not to any ViewProvider). It maintains a dict of registered
F-Rep fields. When any field changes, it builds a `UnionField` tree of all visible
fields, calls `bake_sdf_to_atlas()` on the combined field, and uploads the single
texture. The Coin3D scene graph structure, shader, and uniforms are identical to the
per-object `DMRayMarchRenderer` — just attached to the viewer scene graph instead of a
ViewProvider's `RootNode`.

> **Required reading:** `.agents/skills/dm_scene_ray_march/SKILL.md`,
> `.agents/skills/dm_coin3d_depth_ordering/SKILL.md`,
> `.agents/skills/dm_ray_march_scene_graph/SKILL.md`

**Implementation:**

```python
"""
core/dm_scene_ray_march_renderer.py

Scene-level GPU ray march renderer. One full-screen quad renders ALL F-Rep
fields combined via a single baked 3D texture atlas. Generic — works with any
FRepField subclass via evaluate_grid(). No per-primitive GLSL formulas.
"""
import FreeCAD
import FreeCADGui
import pivy.coin as coin
from core import dm_logger
from core.frep.sdf_baker import bake_sdf_to_atlas
from core.frep.frep_composer import UnionField


class DMSceneRayMarchRenderer:
    """Singleton scene-level ray march renderer."""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def destroy(cls):
        if cls._instance is not None:
            cls._instance._detach()
            cls._instance = None

    def __init__(self):
        self._fields = {}          # label -> (field, visible)
        self._attached = False
        self._u = {}               # uniform nodes
        self._tex = None
        self._bbox_coords = None
        self._root = coin.SoSeparator()
        self._switch = coin.SoSwitch()
        self._switch.addChild(self._root)
        self._switch.whichChild = -1
        self._setup_nodes()

    def _attach(self):
        if self._attached:
            return
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            sg = view.getSceneGraph()
            sg.addChild(self._switch)
            self._attached = True
        except Exception as e:
            dm_logger.debug(f"SceneRayMarch: attach failed: {e}")

    def _detach(self):
        if not self._attached:
            return
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            sg = view.getSceneGraph()
            sg.removeChild(self._switch)
        except Exception:
            pass
        self._attached = False

    def _setup_nodes(self):
        # 1. Bounding box proxy (outside shader sep for correct near/far clipping)
        self._bbox_sep = coin.SoSeparator()
        mat = coin.SoMaterial()
        mat.transparency.setValue(1.0)
        self._bbox_sep.addChild(mat)
        pick = coin.SoPickStyle()
        pick.style.setValue(coin.SoPickStyle.UNPICKABLE)
        self._bbox_sep.addChild(pick)
        self._bbox_coords = coin.SoCoordinate3()
        self._bbox_sep.addChild(self._bbox_coords)
        bbox_lines = coin.SoIndexedLineSet()
        bbox_lines.coordIndex.setValues(0, 36, [
            0,1,-1, 1,3,-1, 3,2,-1, 2,0,-1,
            4,5,-1, 5,7,-1, 7,6,-1, 6,4,-1,
            0,4,-1, 1,5,-1, 2,6,-1, 3,7,-1
        ])
        self._bbox_sep.addChild(bbox_lines)
        self._root.addChild(self._bbox_sep)

        # 2. Shader-scoped separator (isolates shader from bbox proxy)
        self._shader_sep = coin.SoSeparator()

        # Force opaque classification for correct depth writes
        quad_mat = coin.SoMaterial()
        quad_mat.transparency.setValue(0.0)
        self._shader_sep.addChild(quad_mat)

        # Explicit depth buffer control
        try:
            depth_buf = coin.SoDepthBuffer()
            depth_buf.test.setValue(True)
            depth_buf.write.setValue(True)
            self._shader_sep.addChild(depth_buf)
        except AttributeError:
            pass

        # GL_NEAREST filtering (shader does its own trilinear)
        complexity = coin.SoComplexity()
        complexity.textureQuality.setValue(0.0)
        self._shader_sep.addChild(complexity)

        # Texture Atlas
        self._tex = coin.SoTexture2()
        self._tex.model.setValue(coin.SoTexture2.REPLACE)
        self._tex.wrapS.setValue(coin.SoTexture2.CLAMP)
        self._tex.wrapT.setValue(coin.SoTexture2.CLAMP)
        self._shader_sep.addChild(self._tex)

        # Shader Program — identical vertex+fragment shader to DMRayMarchRenderer
        shader = coin.SoShaderProgram()
        v_shader = coin.SoVertexShader()
        v_shader.sourceType.setValue(coin.SoShaderObject.GLSL_PROGRAM)
        f_shader = coin.SoFragmentShader()
        f_shader.sourceType.setValue(coin.SoShaderObject.GLSL_PROGRAM)

        v_shader.sourceProgram.setValue("""
varying vec2 v_uv;
void main() {
    v_uv = gl_Vertex.xy;
    gl_Position = vec4(gl_Vertex.xy, 0.0, 1.0);
}
""")

        # Fragment shader: identical to per-object DMRayMarchRenderer
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
uniform int   u_debug_mode;

float sample_texel(float ix, float iy, float iz) {
    float c = floor(mod(iz, float(u_atz)));
    float r = floor(iz / float(u_atz));
    float u = (c * (float(u_nx) + 1.0) + ix + 0.5) / u_atlas_w;
    float v = (r * (float(u_ny) + 1.0) + iy + 0.5) / u_atlas_h;
    vec4 t = texture2D(u_sdf_tex, vec2(u, v));
    return (t.r * 65280.0 + t.a * 255.0) / 65535.0;
}

float sample_sdf(vec3 p) {
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
    vec4 ndc_near = vec4(v_uv, -1.0, 1.0);
    vec4 world_near = gl_ModelViewProjectionMatrixInverse * ndc_near;
    world_near /= world_near.w;
    vec4 ndc_far = vec4(v_uv, 1.0, 1.0);
    vec4 world_far = gl_ModelViewProjectionMatrixInverse * ndc_far;
    world_far /= world_far.w;
    vec3 ro = world_near.xyz;
    vec3 rd = normalize(world_far.xyz - world_near.xyz);
    vec3 cam = (gl_ModelViewMatrixInverse * vec4(0.0,0.0,0.0,1.0)).xyz;

    vec2 tBox = intersect_aabb(ro, rd);
    float tNear = max(tBox.x, 0.0);
    float tFar  = tBox.y;
    if (tNear > tFar) discard;

    float hit_thresh = u_max_dist * 0.001;
    float min_step   = hit_thresh;
    float t = tNear;
    bool hit = false;
    float d;
    int march_iters = 0;

    for (int i = 0; i < 256; i++) {
        march_iters = i;
        vec3 p = ro + t * rd;
        d = sample_sdf(p);
        if (abs(d) < hit_thresh) { hit = true; break; }
        t += max(abs(d), min_step);
        if (t > tFar) break;
    }
    if (!hit) discard;

    vec3 hp  = ro + t * rd;
    vec3 n   = sdf_normal(hp);
    vec4 light_eye = gl_LightSource[0].position;
    vec3 ld;
    if (light_eye.w < 0.5) {
        ld = normalize((gl_ModelViewMatrixInverse * vec4(light_eye.xyz, 0.0)).xyz);
    } else {
        vec3 light_world = (gl_ModelViewMatrixInverse * light_eye).xyz;
        ld = normalize(light_world - hp);
    }
    float diff = max(dot(n, ld), 0.0);
    vec3 vd    = normalize(cam - hp);
    float spec = pow(max(dot(reflect(-ld, n), vd), 0.0), 32.0);
    vec3 color = vec3(1.0,0.5,0.0)*(0.15 + 0.75*diff) + vec3(0.4)*spec;
    gl_FragColor = vec4(color, 1.0);

    if (u_debug_mode == 1) {
        float v = sample_sdf(hp) / u_max_dist * 0.5 + 0.5;
        gl_FragColor = vec4(v, 0.0, 1.0 - v, 1.0);
    } else if (u_debug_mode == 2) {
        gl_FragColor = vec4(n * 0.5 + 0.5, 1.0);
    } else if (u_debug_mode == 3) {
        gl_FragColor = vec4(vec3(float(march_iters) / 256.0), 1.0);
    }

    vec4 clip     = gl_ModelViewProjectionMatrix * vec4(hp, 1.0);
    float ndc_z   = clip.z / clip.w;
    gl_FragDepth  = gl_DepthRange.near
                  + gl_DepthRange.diff * (ndc_z * 0.5 + 0.5);
}
""")

        # Uniforms
        u_sdf_tex = coin.SoShaderParameter1i()
        u_sdf_tex.name.setValue("u_sdf_tex")
        u_sdf_tex.value.setValue(0)

        self._u["u_nx"] = coin.SoShaderParameter1i()
        self._u["u_nx"].name.setValue("u_nx")
        self._u["u_nx"].value.setValue(0)

        self._u["u_ny"] = coin.SoShaderParameter1i()
        self._u["u_ny"].name.setValue("u_ny")
        self._u["u_ny"].value.setValue(0)

        self._u["u_nz"] = coin.SoShaderParameter1i()
        self._u["u_nz"].name.setValue("u_nz")
        self._u["u_nz"].value.setValue(0)

        self._u["u_atz"] = coin.SoShaderParameter1i()
        self._u["u_atz"].name.setValue("u_atz")
        self._u["u_atz"].value.setValue(1)

        self._u["u_atlas_w"] = coin.SoShaderParameter1f()
        self._u["u_atlas_w"].name.setValue("u_atlas_w")
        self._u["u_atlas_w"].value.setValue(1.0)

        self._u["u_atlas_h"] = coin.SoShaderParameter1f()
        self._u["u_atlas_h"].name.setValue("u_atlas_h")
        self._u["u_atlas_h"].value.setValue(1.0)

        self._u["u_bbox_min"] = coin.SoShaderParameter3f()
        self._u["u_bbox_min"].name.setValue("u_bbox_min")
        self._u["u_bbox_min"].value.setValue(coin.SbVec3f(0, 0, 0))

        self._u["u_bbox_max"] = coin.SoShaderParameter3f()
        self._u["u_bbox_max"].name.setValue("u_bbox_max")
        self._u["u_bbox_max"].value.setValue(coin.SbVec3f(0, 0, 0))

        self._u["u_max_dist"] = coin.SoShaderParameter1f()
        self._u["u_max_dist"].name.setValue("u_max_dist")
        self._u["u_max_dist"].value.setValue(1.0)

        self._u["u_debug_mode"] = coin.SoShaderParameter1i()
        self._u["u_debug_mode"].name.setValue("u_debug_mode")
        self._u["u_debug_mode"].value.setValue(0)

        f_shader.parameter.setNum(0)
        f_shader.parameter.set1Value(0, u_sdf_tex)
        for i, name in enumerate(["u_nx", "u_ny", "u_nz", "u_atz", "u_atlas_w",
                                   "u_atlas_h", "u_bbox_min", "u_bbox_max",
                                   "u_max_dist", "u_debug_mode"]):
            f_shader.parameter.set1Value(i + 1, self._u[name])

        shader.shaderObject.set1Value(0, v_shader)
        shader.shaderObject.set1Value(1, f_shader)
        self._shader_sep.addChild(shader)

        # Quad Geometry
        hints = coin.SoShapeHints()
        hints.vertexOrdering.setValue(coin.SoShapeHints.UNKNOWN_ORDERING)
        self._shader_sep.addChild(hints)
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, 4, [
            (-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)
        ])
        self._shader_sep.addChild(coords)
        faceset = coin.SoIndexedFaceSet()
        faceset.coordIndex.setValues(0, 8, [0, 1, 2, -1, 0, 2, 3, -1])
        self._shader_sep.addChild(faceset)
        self._root.addChild(self._shader_sep)

    # -- Public API --

    def register_field(self, label, field):
        """Register a new F-Rep field. Triggers combined re-bake."""
        self._fields[label] = (field, True)
        self._attach()
        self._rebuild()

    def unregister_field(self, label):
        """Remove a field. Hides renderer if no fields remain."""
        self._fields.pop(label, None)
        if not self._fields:
            self._switch.whichChild = -1
        else:
            self._rebuild()

    def set_field_visible(self, label, visible):
        """Toggle a field's visibility. Triggers combined re-bake."""
        if label in self._fields:
            field, _ = self._fields[label]
            self._fields[label] = (field, visible)
            self._rebuild()

    def update_field(self, label, field):
        """Update (or register) a field. Triggers combined re-bake."""
        visible = self._fields.get(label, (None, True))[1]
        self._fields[label] = (field, visible)
        if not self._attached:
            self._attach()
        self._rebuild()

    def set_debug_mode(self, mode):
        """Set debug colour mode: 0=normal, 1=SDF heat-map, 2=normals, 3=iterations."""
        self._u["u_debug_mode"].value.setValue(int(mode))

    # -- Internal --

    def _build_combined_field(self):
        """Build a single FRepField tree from all visible fields."""
        visible = [f for f, vis in self._fields.values() if vis and f is not None]
        if not visible:
            return None
        result = visible[0]
        for f in visible[1:]:
            result = UnionField(result, f)
        return result

    def _rebuild(self):
        """Re-bake combined SDF and upload texture + uniforms."""
        from core.dm_object import get_meshing_cell_size
        combined = self._build_combined_field()
        if combined is None:
            self._switch.whichChild = -1
            return

        cell_size = get_meshing_cell_size()
        baked = bake_sdf_to_atlas(combined, cell_size)

        # Texture upload (LUMINANCE_ALPHA, 2 channels)
        self._tex.image.setValue(
            coin.SbVec2s(baked["atlas_w"], baked["atlas_h"]),
            2, baked["atlas_bytes"])

        # Uniforms
        self._u["u_nx"].value.setValue(int(baked["nx"]))
        self._u["u_ny"].value.setValue(int(baked["ny"]))
        self._u["u_nz"].value.setValue(int(baked["nz"]))
        self._u["u_atz"].value.setValue(int(baked["atz"]))
        self._u["u_atlas_w"].value.setValue(float(baked["atlas_w"]))
        self._u["u_atlas_h"].value.setValue(float(baked["atlas_h"]))
        mn, mx = baked["bbox_min"], baked["bbox_max"]
        self._u["u_bbox_min"].value.setValue(coin.SbVec3f(mn.x, mn.y, mn.z))
        self._u["u_bbox_max"].value.setValue(coin.SbVec3f(mx.x, mx.y, mx.z))
        self._u["u_max_dist"].value.setValue(float(baked["max_dist"]))

        # Bbox proxy corners
        self._bbox_coords.point.setValues(0, 8, [
            (mn.x, mn.y, mn.z), (mx.x, mn.y, mn.z),
            (mn.x, mx.y, mn.z), (mx.x, mx.y, mn.z),
            (mn.x, mn.y, mx.z), (mx.x, mn.y, mx.z),
            (mn.x, mx.y, mx.z), (mx.x, mx.y, mx.z)
        ])

        self._switch.whichChild = 0
        dm_logger.debug(f"SceneRayMarch: rebuilt ({len(self._fields)} fields, "
                        f"grid {baked['nx']}x{baked['ny']}x{baked['nz']})")
```

**Depends on:** G-023

---

### G-025: Wire `DMViewProvider` to scene-level renderer

**File:** `core/dm_object.py` — modify `DMViewProvider.attach()` (line 465), `updateData()` (line 504), `onChanged()` (line 543)

**What:** When render mode is `RENDER_MODE_RAY_MARCH`, instead of creating a per-object
`DMRayMarchRenderer`, store the object label and register with the scene-level
`DMSceneRayMarchRenderer` singleton. On field updates, call `update_field()`. On
visibility changes, call `set_field_visible()`.

> **Required reading:** `.agents/skills/dm_scene_ray_march/SKILL.md`

**Implementation:**

Step 1 — In `attach()` (line 465–467), replace:
```python
                elif mode == RENDER_MODE_RAY_MARCH:
                    from core.dm_ray_march_renderer import DMRayMarchRenderer
                    self.ray_march_renderer = DMRayMarchRenderer(vobj)
```
with:
```python
                elif mode == RENDER_MODE_RAY_MARCH:
                    self._scene_rm_label = vobj.Object.Label
```

Step 2 — In `updateData()` (line 500–505), replace:
```python
            pc    = getattr(self, "point_cloud_renderer", None)
            rm    = getattr(self, "ray_march_renderer",   None)

            if pc is not None and field is not None:
                pc.update(field, get_meshing_cell_size())
            elif rm is not None and field is not None:
                rm.update(field, get_meshing_cell_size())
```
with:
```python
            pc    = getattr(self, "point_cloud_renderer", None)

            if pc is not None and field is not None:
                pc.update(field, get_meshing_cell_size())
            elif hasattr(self, "_scene_rm_label") and field is not None:
                from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
                sr = DMSceneRayMarchRenderer.get_instance()
                sr.update_field(self._scene_rm_label, field)
```

Step 3 — In `updateData()` (line 531–536), replace:
```python
            rm = getattr(self, "ray_march_renderer", None)
            if rm:
                rm.set_visible(vobj.Visibility)
```
with:
```python
            if hasattr(self, "_scene_rm_label"):
                from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
                sr = DMSceneRayMarchRenderer.get_instance()
                sr.set_field_visible(self._scene_rm_label, vobj.Visibility)
```

Step 4 — In `onChanged()` (line 543–545), replace:
```python
            rm = getattr(self, "ray_march_renderer", None)
            if rm:
                rm.set_visible(vobj.Visibility)
```
with:
```python
            if hasattr(self, "_scene_rm_label"):
                from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
                sr = DMSceneRayMarchRenderer.get_instance()
                sr.set_field_visible(self._scene_rm_label, vobj.Visibility)
```

**Depends on:** G-024

---

### G-026: Add `onDelete()` cleanup to `DMViewProvider`

**File:** `core/dm_object.py` — add `onDelete()` method to `DMViewProvider` class (after `onChanged()`, line 551)

**What:** When a F-Rep object is deleted from the document, unregister its field from
the scene-level renderer so the combined SDF is re-baked without it.

**Implementation:**

```python
    def onDelete(self, vobj, subelements):
        """Called when the object is about to be deleted."""
        if hasattr(self, "_scene_rm_label"):
            try:
                from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
                sr = DMSceneRayMarchRenderer.get_instance()
                sr.unregister_field(self._scene_rm_label)
            except Exception:
                pass
        return True
```

**Depends on:** G-025

---

### G-027: Destroy scene renderer on workbench deactivation

**File:** `InitGui.py` — modify `Deactivated()` method (line 129)

**What:** When the Direct Modeling workbench is deactivated, destroy the scene-level
renderer singleton to detach its Coin3D nodes from the viewer scene graph. Without
this, the full-screen quad persists and renders over other workbenches.

**Implementation:** In `Deactivated()` (line 129), add after the `DMInputManager.restore()` call:

```python
        try:
            from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
            DMSceneRayMarchRenderer.destroy()
        except Exception as e:
            from core import dm_logger
            dm_logger.error(f"DM Deactivated SceneRM Error: {e}")
```

The full `Deactivated()` method becomes:
```python
    def Deactivated(self):
        """This function is executed when the workbench is deactivated."""
        try:
            from core.input_manager import DMInputManager
            DMInputManager.get_instance().restore()
        except Exception as e:
            from core import dm_logger
            dm_logger.error(f"DM Deactivated Error: {e}")
        try:
            from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
            DMSceneRayMarchRenderer.destroy()
        except Exception as e:
            from core import dm_logger
            dm_logger.error(f"DM Deactivated SceneRM Error: {e}")
```

**Depends on:** G-024

---

## Tier 10 — Per-Field Independent Rendering (No SDF Blending)

> **Problem:** G-024's `_rebuild()` wraps all visible fields in `UnionField(a, b)`, which
> evaluates `min(sdf_0(p), sdf_1(p))` at every grid point before baking. Two nearby
> objects (e.g. sphere and box) produce a shared surface where their SDFs are close —
> the classic SDF union "join". This is mathematically correct boolean union behaviour,
> but the user wants independent objects that do not merge.
>
> **Solution:** Bake each field into its own rows in a single stacked atlas (no combining
> at texture level). The shader evaluates each field's SDF independently at each march
> step, using the minimum absolute value for the safe step size (necessary for correct
> sphere tracing), but checking each field independently for a surface hit. The field
> that provides the nearest hit wins — its normal and depth are used. No cross-field SDF
> blending possible.
>
> **Constraint:** Maximum `MAX_FIELDS = 8` active fields. GLSL 1.20 uniform arrays are
> used for per-field metadata. The single combined texture keeps one sampler unit.

### G-028: Refactor `_rebuild()` to bake each field independently into a stacked atlas

**File:** `core/dm_scene_ray_march_renderer.py` — replace `_build_combined_field()` and `_rebuild()` (currently at the bottom of the class)

**What:** Remove `UnionField` combination entirely. Bake each visible field independently
with `bake_sdf_to_atlas(field_i, cell_size)`. Stack the resulting atlases vertically into
one combined image: combined width = max of all field atlas widths (padded with zeros),
combined height = sum of all field atlas heights. Each field gets a `row_offset` (the
pixel row where its tiles start). Upload one combined texture and set per-field uniform
arrays.

**Implementation:**

Replace `_build_combined_field()` and `_rebuild()` with:

```python
    MAX_FIELDS = 8

    def _rebuild(self):
        """Bake each field independently and upload a stacked atlas."""
        from core.dm_object import get_meshing_cell_size
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

        # Bake each field independently
        baked_list = [bake_sdf_to_atlas(f, cell_size) for _, f in visible]
        n_fields = len(baked_list)

        # Stacked atlas: each field occupies its own row-band
        max_w = max(b["atlas_w"] for b in baked_list)
        total_h = sum(b["atlas_h"] for b in baked_list)

        combined = np.zeros((total_h, max_w, 2), dtype=np.uint8)
        row_offsets = []
        row = 0
        for b in baked_list:
            h, w = b["atlas_h"], b["atlas_w"]
            # Reshape flat bytes back to (h, w, 2) and place in combined
            tile = np.frombuffer(b["atlas_bytes"], dtype=np.uint8).reshape(h, w, 2)
            combined[row:row + h, :w, :] = tile
            row_offsets.append(row)
            row += h

        # Upload single combined texture
        self._tex.image.setValue(
            coin.SbVec2s(max_w, total_h), 2, combined.tobytes())

        # Per-field uniform arrays (indices 0..MAX_FIELDS-1)
        for fi in range(self.MAX_FIELDS):
            if fi < n_fields:
                b = baked_list[fi]
                mn, mx = b["bbox_min"], b["bbox_max"]
                self._u[f"u_nx[{fi}]"].value.setValue(int(b["nx"]))
                self._u[f"u_ny[{fi}]"].value.setValue(int(b["ny"]))
                self._u[f"u_nz[{fi}]"].value.setValue(int(b["nz"]))
                self._u[f"u_atz[{fi}]"].value.setValue(int(b["atz"]))
                self._u[f"u_field_atlas_w[{fi}]"].value.setValue(float(b["atlas_w"]))
                self._u[f"u_field_atlas_h[{fi}]"].value.setValue(float(b["atlas_h"]))
                self._u[f"u_max_dist[{fi}]"].value.setValue(float(b["max_dist"]))
                self._u[f"u_row_offset[{fi}]"].value.setValue(int(row_offsets[fi]))
                self._u[f"u_bbox_min[{fi}]"].value.setValue(
                    coin.SbVec3f(mn.x, mn.y, mn.z))
                self._u[f"u_bbox_max[{fi}]"].value.setValue(
                    coin.SbVec3f(mx.x, mx.y, mx.z))
            else:
                # Zero out unused slots so the shader skips them
                self._u[f"u_nx[{fi}]"].value.setValue(0)

        self._u["u_num_fields"].value.setValue(n_fields)
        self._u["u_combined_atlas_w"].value.setValue(float(max_w))
        self._u["u_combined_atlas_h"].value.setValue(float(total_h))

        # Combined bbox proxy (union of all visible fields' bboxes)
        all_mn = [baked_list[i]["bbox_min"] for i in range(n_fields)]
        all_mx = [baked_list[i]["bbox_max"] for i in range(n_fields)]
        import FreeCAD
        mn_all = FreeCAD.Vector(min(v.x for v in all_mn),
                                min(v.y for v in all_mn),
                                min(v.z for v in all_mn))
        mx_all = FreeCAD.Vector(max(v.x for v in all_mx),
                                max(v.y for v in all_mx),
                                max(v.z for v in all_mx))
        self._bbox_coords.point.setValues(0, 8, [
            (mn_all.x, mn_all.y, mn_all.z), (mx_all.x, mn_all.y, mn_all.z),
            (mn_all.x, mx_all.y, mn_all.z), (mx_all.x, mx_all.y, mn_all.z),
            (mn_all.x, mn_all.y, mx_all.z), (mx_all.x, mn_all.y, mx_all.z),
            (mn_all.x, mx_all.y, mx_all.z), (mx_all.x, mx_all.y, mx_all.z)
        ])

        self._switch.whichChild = 0
        dm_logger.debug(f"SceneRayMarch: rebuilt ({n_fields} fields, "
                        f"stacked atlas {max_w}x{total_h})")
```

Also update `_setup_nodes()` to create the per-field uniform nodes. Replace the old uniform creation block (after the fragment shader string) with:

```python
        # Scene-level uniforms
        u_sdf_tex = coin.SoShaderParameter1i()
        u_sdf_tex.name.setValue("u_sdf_tex")
        u_sdf_tex.value.setValue(0)

        self._u["u_num_fields"] = coin.SoShaderParameter1i()
        self._u["u_num_fields"].name.setValue("u_num_fields")
        self._u["u_num_fields"].value.setValue(0)

        self._u["u_combined_atlas_w"] = coin.SoShaderParameter1f()
        self._u["u_combined_atlas_w"].name.setValue("u_combined_atlas_w")
        self._u["u_combined_atlas_w"].value.setValue(1.0)

        self._u["u_combined_atlas_h"] = coin.SoShaderParameter1f()
        self._u["u_combined_atlas_h"].name.setValue("u_combined_atlas_h")
        self._u["u_combined_atlas_h"].value.setValue(1.0)

        self._u["u_debug_mode"] = coin.SoShaderParameter1i()
        self._u["u_debug_mode"].name.setValue("u_debug_mode")
        self._u["u_debug_mode"].value.setValue(0)

        # Per-field uniform arrays (Coin3D uses "u_nx[0]" naming for GLSL arrays)
        per_field_scalar_i = ["u_nx", "u_ny", "u_nz", "u_atz", "u_row_offset"]
        per_field_scalar_f = ["u_field_atlas_w", "u_field_atlas_h", "u_max_dist"]
        per_field_vec3     = ["u_bbox_min", "u_bbox_max"]

        for fi in range(self.MAX_FIELDS):
            for name in per_field_scalar_i:
                key = f"{name}[{fi}]"
                node = coin.SoShaderParameter1i()
                node.name.setValue(key)
                node.value.setValue(0)
                self._u[key] = node
            for name in per_field_scalar_f:
                key = f"{name}[{fi}]"
                node = coin.SoShaderParameter1f()
                node.name.setValue(key)
                node.value.setValue(1.0)
                self._u[key] = node
            for name in per_field_vec3:
                key = f"{name}[{fi}]"
                node = coin.SoShaderParameter3f()
                node.name.setValue(key)
                node.value.setValue(coin.SbVec3f(0, 0, 0))
                self._u[key] = node

        # Register all uniforms with the fragment shader
        f_shader.parameter.setNum(0)
        idx = 0
        f_shader.parameter.set1Value(idx, u_sdf_tex); idx += 1
        for key in ["u_num_fields", "u_combined_atlas_w", "u_combined_atlas_h",
                    "u_debug_mode"]:
            f_shader.parameter.set1Value(idx, self._u[key]); idx += 1
        for fi in range(self.MAX_FIELDS):
            for name in (per_field_scalar_i + per_field_scalar_f + per_field_vec3):
                f_shader.parameter.set1Value(idx, self._u[f"{name}[{fi}]"]); idx += 1
        f_shader.parameter.setNum(idx)
```

**Depends on:** G-027

---

### G-029: Replace fragment shader with per-field independent sphere tracer

**File:** `core/dm_scene_ray_march_renderer.py` — replace `f_shader.sourceProgram.setValue(...)` in `_setup_nodes()` with the multi-field shader below

**What:** The new shader evaluates all `u_num_fields` active fields independently at
each march step. Step size = `min(|sdf_0|, |sdf_1|, ...)` (safe sphere-trace step).
Hit = first step where ANY field's `|sdf_fi| < hit_thresh`. The winning field's
texture is used for normal computation. No cross-field SDF blending.
`sample_sdf_field(fi, p)` addresses each field's row-band in the stacked atlas using
`u_row_offset[fi]` as the base row in the combined texture.

> **GLSL 1.20 note:** Dynamic indexing of `uniform int u_nx[8]` with a variable `fi`
> IS valid in GLSL 1.20. The single sampler `u_sdf_tex` samples the combined stacked
> atlas; `u_row_offset[fi]` shifts the V coordinate into the correct band.

**Implementation:**

```python
        f_shader.sourceProgram.setValue("""
varying vec2  v_uv;
uniform sampler2D u_sdf_tex;
uniform int   u_num_fields;
uniform float u_combined_atlas_w;
uniform float u_combined_atlas_h;
uniform int   u_debug_mode;

uniform int   u_nx[8];
uniform int   u_ny[8];
uniform int   u_nz[8];
uniform int   u_atz[8];
uniform float u_field_atlas_w[8];
uniform float u_field_atlas_h[8];
uniform float u_max_dist[8];
uniform int   u_row_offset[8];
uniform vec3  u_bbox_min[8];
uniform vec3  u_bbox_max[8];

float sample_texel_field(int fi, float ix, float iy, float iz) {
    float c = floor(mod(iz, float(u_atz[fi])));
    float r = floor(iz / float(u_atz[fi]));
    // atlas UV: x within this field's column layout, y offset by row_offset
    float u = (c * (float(u_nx[fi]) + 1.0) + ix + 0.5) / u_combined_atlas_w;
    float v = (float(u_row_offset[fi]) + r * (float(u_ny[fi]) + 1.0) + iy + 0.5)
              / u_combined_atlas_h;
    vec4 t = texture2D(u_sdf_tex, vec2(u, v));
    return (t.r * 65280.0 + t.a * 255.0) / 65535.0;
}

float sample_sdf_field(int fi, vec3 p) {
    // Outside this field's bbox → return max_dist (miss)
    if (any(lessThan(p, u_bbox_min[fi])) || any(greaterThan(p, u_bbox_max[fi])))
        return u_max_dist[fi];
    vec3 uvw = (p - u_bbox_min[fi]) / (u_bbox_max[fi] - u_bbox_min[fi]);
    float gx = clamp(uvw.x * float(u_nx[fi]), 0.0, float(u_nx[fi]));
    float gy = clamp(uvw.y * float(u_ny[fi]), 0.0, float(u_ny[fi]));
    float gz = clamp(uvw.z * float(u_nz[fi]), 0.0, float(u_nz[fi]));
    float x0=floor(gx); float x1=min(x0+1.0,float(u_nx[fi]));
    float y0=floor(gy); float y1=min(y0+1.0,float(u_ny[fi]));
    float z0=floor(gz); float z1=min(z0+1.0,float(u_nz[fi]));
    float fx=gx-x0; float fy=gy-y0; float fz=gz-z0;
    float s = mix(
        mix(mix(sample_texel_field(fi,x0,y0,z0),sample_texel_field(fi,x1,y0,z0),fx),
            mix(sample_texel_field(fi,x0,y1,z0),sample_texel_field(fi,x1,y1,z0),fx),fy),
        mix(mix(sample_texel_field(fi,x0,y0,z1),sample_texel_field(fi,x1,y0,z1),fx),
            mix(sample_texel_field(fi,x0,y1,z1),sample_texel_field(fi,x1,y1,z1),fx),fy),
        fz);
    return (s * 2.0 - 1.0) * u_max_dist[fi];
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

// Combined AABB = union of all field AABBs (used for ray culling only)
// Each field has its own AABB; we clip to each field's AABB during sampling.
vec2 intersect_aabb(vec3 ro, vec3 rd, vec3 bmin, vec3 bmax) {
    vec3 inv_rd = 1.0 / rd;
    vec3 t1 = (bmin - ro) * inv_rd;
    vec3 t2 = (bmax - ro) * inv_rd;
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

    // 2. Compute combined AABB for early ray cull (union of all field AABBs)
    vec3 scene_min = u_bbox_min[0];
    vec3 scene_max = u_bbox_max[0];
    for (int fi = 1; fi < 8; fi++) {
        if (fi >= u_num_fields) break;
        scene_min = min(scene_min, u_bbox_min[fi]);
        scene_max = max(scene_max, u_bbox_max[fi]);
    }
    vec2 tBox = intersect_aabb(ro, rd, scene_min, scene_max);
    float tNear = max(tBox.x, 0.0);
    float tFar  = tBox.y;
    if (tNear > tFar) discard;

    // 3. Multi-field sphere trace
    // Step size = min(|sdf_0|, |sdf_1|, ...) — safe step that won't skip any surface.
    // Hit check: first field with |sdf_fi| < hit_thresh wins (no cross-field blending).
    float global_hit_thresh = 0.001;  // relative; refined per-field below
    float t = tNear;
    bool hit = false;
    int hit_field = 0;
    int march_iters = 0;

    for (int i = 0; i < 256; i++) {
        march_iters = i;
        vec3 p = ro + t * rd;

        float min_abs_sdf = 1.0e10;
        for (int fi = 0; fi < 8; fi++) {
            if (fi >= u_num_fields) break;
            float d = sample_sdf_field(fi, p);
            float thresh = u_max_dist[fi] * 0.001;
            if (abs(d) < thresh) {
                hit = true;
                hit_field = fi;
                break;
            }
            min_abs_sdf = min(min_abs_sdf, abs(d));
        }
        if (hit) break;

        t += max(min_abs_sdf, 0.0001);
        if (t > tFar) break;
    }
    if (!hit) discard;

    // 4. Shade using hit field's normal
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
    vec3 vd    = normalize(cam - hp);
    float spec = pow(max(dot(reflect(-ld, n), vd), 0.0), 32.0);
    vec3 color = vec3(1.0,0.5,0.0)*(0.15 + 0.75*diff) + vec3(0.4)*spec;
    gl_FragColor = vec4(color, 1.0);

    // Debug overrides
    if (u_debug_mode == 1) {
        float v = sample_sdf_field(hit_field, hp) / u_max_dist[hit_field] * 0.5 + 0.5;
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

**Depends on:** G-028

---

### G-030: Fix workplane depth occlusion against SDF renders

**File:** `core/dm_workplane.py` — modify `ViewProviderDMWorkPlane.attach()` (line 79)

**What:** The workplane's `grid_sep` has `transparency=0.7` but no `SoDepthBuffer` node.
Coin3D's transparent rendering pass may not reliably enable depth testing against the
opaque SDF quad's `gl_FragDepth` writes. Adding `SoDepthBuffer(test=True, write=False)`
as the first child of `grid_sep` ensures the workplane face and grid lines are always
depth-tested against the opaque SDF surface without competing with it for depth writes.

> **Required reading:** `.agents/skills/dm_coin3d_depth_ordering/SKILL.md`

**Implementation:** In `attach()`, immediately after `self.grid_sep = coin.SoSeparator()`
(line 79), before the `plane_mat` is added, insert:

```python
        # Ensure transparent workplane geometry depth-tests against opaque SDF renders.
        # test=True: discard fragments behind opaque surfaces (e.g. SDF quad).
        # write=False: don't write depth — transparent objects must not occlude each other.
        try:
            wp_depth = coin.SoDepthBuffer()
            wp_depth.test.setValue(True)
            wp_depth.write.setValue(False)
            self.grid_sep.addChild(wp_depth)
        except AttributeError:
            pass  # SoDepthBuffer not available in this Coin3D/pivy version
```

The resulting `grid_sep` children order becomes:
```
grid_sep (SoSeparator)
├── SoDepthBuffer (test=True, write=False)   ← NEW
├── SoMaterial (transparency=0.7)
├── SoCoordinate3 (grid lines)
├── SoLineSet
├── SoCoordinate3 (face corners)
└── SoFaceSet
```

**Depends on:** None (independent fix)

---

## Agent Skills

| Skill | Purpose |
|-------|---------|
| `coin3d_shader_api` | `SoShaderProgram` setup, uniform node types, proxy geometry |
| `coin3d_fullscreen_quad` | Full-screen quad rendering pipeline and unprojection math |
| `dm_ray_march_scene_graph` | Correct Coin3D scene graph structure for the ray march renderer |
| `dm_glsl_lighting` | Eye-space vs world-space coordinate transforms for GLSL lighting in Coin3D |
| `dm_coin3d_depth_ordering` | Coin3D opaque/transparent render passes, depth buffer control, `gl_FragDepth` interop |
| `dm_scene_ray_march` | Scene-level baked SDF renderer: singleton lifecycle, field registry, combined baking |
| `dm_sdf_slicer` | Marching squares, DM curve compatibility, `fit_dm_curve` contract |
| `dm_todo_format` | Task format and conventions for this project |

## Workflows

| Workflow | Purpose |
|----------|---------|
| `/fix-task` | Fix a single task by ID (e.g. `/fix-task G-003`) |
