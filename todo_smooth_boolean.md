# Smooth Boolean Edge Blending — Known Issue

## Problem

`SmoothSubtractionField` (and to a lesser extent `SmoothIntersectionField`) blends the
**wrong edges** when the cutter shape protrudes through or passes near the outer surface
of the base shape.

### What users see
- Box with cylinder subtracted: the outer box corners/edges get rounded, not just the
  interior cut rim where the cylinder meets the box wall.
- The smoothing is globally active anywhere in 3D space where both SDFs are within
  `k` of each other — it has no concept of "interior vs exterior" of the operation.

### Root cause
The blend factor `h = clamp(0.5 - 0.5*(a+b)/k, 0, 1)` fires wherever `|a + b| < k`.
This includes:
1. **The interior cut rim** (desired) — box wall ≈ 0, cylinder surface ≈ 0, inside box
2. **The cylinder exit face** (undesired) — box top face ≈ 0, cylinder surface ≈ 0, outside box
3. **Box corners near cylinder wall** (undesired) — if the cylinder outer wall passes within
   `k` of any box surface, that surface gets rounded

The current cubic IQ hermite formula (`core/sdf/glsl_compiler.py` + `core/sdf/sdf_composer.py`)
already limits the influence radius to `k` (down from `4k` with the old polynomial formula),
but cannot prevent blending where the two surfaces are genuinely close on the exterior.

## What we actually want
Smoothing should only apply **at the intersection curve** — the rim where the cutter surface
meets the base shape interior. Outside the base shape, the result should be identical to
the sharp subtraction (`max(a, -b)`).

## Proposed fix: bounded smooth subtraction

Gate the blend on being inside or near the base shape using `a`:

```glsl
float smooth_subtraction(float a, float b, float k) {
    float h     = clamp(0.5 - 0.5*(a+b)/k, 0.0, 1.0);
    float smooth = mix(a, -b, h) + k*h*(1.0-h);
    float sharp  = max(a, -b);
    // Fade blend out as we leave the base shape interior
    // gate = 1 when a <= 0 (inside base), 0 when a >= k (outside by k)
    float gate   = clamp(1.0 - a/k, 0.0, 1.0);
    return mix(sharp, smooth, gate);
}
```

**Trade-off**: introduces a C0 gradient discontinuity at the base surface boundary (`a = 0`)
because we transition from smooth-result to sharp-result over that region. This can
produce a faint visible seam on the outer base surface face, particularly visible in
specular highlights. Whether this is acceptable depends on the use case.

Same gate logic needs to be applied in Python (`evaluate` and `evaluate_grid` in
`SmoothSubtractionField`) and in the GLSL helper in `GLSL_HELPERS["smooth_subtraction"]`.

## Workaround (for now)

Keep `k` smaller than the minimum distance from the cut rim to any exterior surface
feature you don't want smoothed. The cubic formula bounds blending to exactly `k` mm
so a small enough `k` will isolate the effect to the cut rim only.

## Files to change

- `core/sdf/glsl_compiler.py` — `GLSL_HELPERS["smooth_subtraction"]` (and possibly
  `smooth_intersection` which has the same class of issue)
- `core/sdf/sdf_composer.py` — `SmoothSubtractionField.evaluate()`,
  `SmoothSubtractionField.evaluate_grid()` (and `SmoothIntersectionField` equivalents)
