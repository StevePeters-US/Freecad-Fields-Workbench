---
name: GLSL Lighting in Coin3D (Eye vs World Space)
description: Reference for correct lighting calculations in custom GLSL shaders running under Coin3D. Covers coordinate space conventions, gl_LightSource transforms, and FreeCAD headlight behaviour. Required reading before modifying shading code in DMRayMarchRenderer.
---

# GLSL Lighting in Coin3D (Eye vs World Space)

Custom fragment shaders in Coin3D (GLSL 1.20 compatibility profile) can access scene
lights via the built-in `gl_LightSource[i]` array. However, the coordinate space of
these values is **not** world space — getting this wrong produces dark or incorrectly
lit surfaces.

---

## Coordinate Spaces

| Space | Description | How to get there |
|-------|-------------|------------------|
| **Object/Model** | Vertex positions as authored | Raw `gl_Vertex` |
| **World** | After model matrix transform | `gl_ModelViewMatrixInverse * eye_pos` |
| **Eye/View** | Camera at origin, looking down -Z | After ModelView transform |
| **Clip/NDC** | After projection, [-1,1]^3 | After `gl_ModelViewProjectionMatrix` |

---

## `gl_LightSource[i].position` — Eye Space

OpenGL (and Coin3D) transforms light positions by the **current ModelView matrix** at
the time `glLight()` is called. By the time the fragment shader runs:

```
gl_LightSource[i].position  →  already in EYE space
gl_LightSource[i].position.w:
    0.0 → directional light (xyz is direction in eye space)
    1.0 → positional light  (xyz is position in eye space)
```

FreeCAD's default scene uses a **directional headlight** (`w == 0.0`). Its direction
follows the camera (always roughly "from the viewer"), so `.xyz` is a direction
vector in eye space, typically near `(0, 0, 1)`.

---

## The Bug: Mixing Eye-Space Light with World-Space Hit Point

If the ray march shader unprojects NDC to **world space** (via
`gl_ModelViewProjectionMatrixInverse`), the hit point `hp` is in world space.
Computing:

```glsl
// WRONG — mixes eye-space light with world-space position
vec3 ld = normalize(gl_LightSource[0].position.xyz - hp);
```

produces a nonsensical direction. The `dot(n, ld)` diffuse term evaluates to near-zero
for most fragments, making the surface uniformly dark.

---

## Correct Approach: Transform Light to World Space

```glsl
vec4 light_eye = gl_LightSource[0].position;
vec3 ld;
if (light_eye.w < 0.5) {
    // Directional: transform direction from eye to world
    ld = normalize((gl_ModelViewMatrixInverse * vec4(light_eye.xyz, 0.0)).xyz);
} else {
    // Positional: transform position from eye to world
    vec3 light_world = (gl_ModelViewMatrixInverse * light_eye).xyz;
    ld = normalize(light_world - hp);
}
```

The `gl_ModelViewMatrixInverse` is a built-in GLSL 1.20 uniform provided by Coin3D.

---

## Alternative: Work Entirely in Eye Space

Instead of transforming the light to world space, transform `hp` and `n` to eye space:

```glsl
vec3 hp_eye = (gl_ModelViewMatrix * vec4(hp, 1.0)).xyz;
vec3 n_eye  = normalize((gl_ModelViewMatrix * vec4(n, 0.0)).xyz);
// Now light_eye.xyz and hp_eye are in the same space
```

Both approaches are correct. The DMRayMarchRenderer uses the "light to world" approach
because `hp`, `n`, `cam`, and `rd` are all already in world space.

---

## Specular Highlights

The view direction for specular must also be in the same space as the light direction
and normal:

```glsl
// World space (consistent with light-to-world approach)
vec3 vd   = normalize(cam - hp);  // cam is already world space
float spec = pow(max(dot(reflect(-ld, n), vd), 0.0), shininess);
```

---

## FreeCAD Headlight Properties

FreeCAD provides `gl_LightSource[0]` automatically. Additional properties available:

```glsl
gl_LightSource[0].ambient   // vec4, typically (0,0,0,1)
gl_LightSource[0].diffuse   // vec4, typically (1,1,1,1) for headlight
gl_LightSource[0].specular  // vec4, typically (1,1,1,1) for headlight
```

These can be used for physically-based shading but are not required for basic
diffuse+specular.

---

## Files to Read Before Editing

1. `core/dm_ray_march_renderer.py` — the fragment shader string containing the shading code
2. `.agents/skills/coin3d_shader_api/SKILL.md` — shader setup and uniform types
3. `.agents/skills/coin3d_fullscreen_quad/SKILL.md` — NDC unprojection to world space
