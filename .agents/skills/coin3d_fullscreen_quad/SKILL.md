---
name: Coin3D Full-Screen Quad Pipeline
description: Reference for implementing full-screen quad raymarchers in Coin3D bypassing the standard view/projection matrices. Required reading before implementing R-006.
---

# Coin3D Full-Screen Quad Pipeline

To render an SDF using a full-screen quad instead of proxy geometry, we draw a single rectangle spanning the viewport and use the camera matrices in the fragment shader to unproject screen coordinates into 3D world rays.

---

## 1. Proxy Geometry (Full-Screen Quad)

We need exactly 4 vertices spanning `[-1, 1]` in XY. Because Coin3D manages matrices globally, we intercept the vertex shader to place these vertices directly in clip space.

```python
coords = coin.SoCoordinate3()
coords.point.setValues(0, 4, [
    (-1, -1, 0),
    ( 1, -1, 0),
    ( 1,  1, 0),
    (-1,  1, 0)
])

# Use an IndexedFaceSet for two triangles forming the quad
faces = coin.SoIndexedFaceSet()
faces.coordIndex.setValues(0, 7, [0, 1, 2, -1, 0, 2, 3, -1])
```

---

## 2. Vertex Shader (Bypassing Matrices)

The vertex shader completely ignores `gl_ModelViewProjectionMatrix`. It takes the `gl_Vertex.xy` (which is already `[-1, 1]`) and passes it directly to `gl_Position`. It also passes the UV coordinate to the fragment shader.

```glsl
varying vec2 v_uv;

void main() {
    v_uv = gl_Vertex.xy;
    gl_Position = vec4(gl_Vertex.xy, 0.0, 1.0);
}
```

---

## 3. Fragment Shader (Unprojection)

In the fragment shader, `v_uv` gives us the exact Normalized Device Coordinate (NDC) of the pixel `[-1, 1]`.
We use `gl_ModelViewProjectionMatrixInverse` to convert NDC points into 3D world space.

```glsl
varying vec2 v_uv;
// ... uniforms ...

void main() {
    // 1. Unproject near plane point (z = -1.0 in NDC is the near plane)
    vec4 ndc_near = vec4(v_uv, -1.0, 1.0);
    vec4 world_near = gl_ModelViewProjectionMatrixInverse * ndc_near;
    world_near /= world_near.w; 

    // 2. Unproject far plane point (z = 1.0 in NDC is the far plane)
    vec4 ndc_far = vec4(v_uv, 1.0, 1.0);
    vec4 world_far = gl_ModelViewProjectionMatrixInverse * ndc_far;
    world_far /= world_far.w;

    // 3. Compute Ray Origin and Direction
    vec3 ro = world_near.xyz;
    vec3 rd = normalize(world_far.xyz - world_near.xyz);

    // ... standard raymarching loop using 'ro' and 'rd' ...

    // Important: Raymarching must check the bounding box intersecting with the ray
    // because the quad itself is infinite. The SDF evaluation loop should still 
    // break early if `p` exits `u_bbox_min` and `u_bbox_max`.
}
```

---

## 4. Disabling Culling

Make sure to disable backface/frontface culling just in case vertex winding gets flipped.

```python
hints = coin.SoShapeHints()
hints.vertexOrdering.setValue(coin.SoShapeHints.UNKNOWN_ORDERING)
```

## 5. Viewport / Camera Context

Because we rely on `gl_ModelViewProjectionMatrixInverse`, the shader must be placed in a scene graph location where FreeCAD's active camera matrices apply. Returning the `SoSeparator` containing this setup to `vobj.RootNode.addChild()` naturally inherits FreeCAD's camera matrices, so no manual camera uniform passing is strictly needed.

