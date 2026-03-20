---
name: DM SDF Slicer Architecture
description: Reference for the marching squares SDF slicer, DM curve compatibility requirements, and the fit_dm_curve contract. Required reading before implementing or modifying sdf_slicer.py.
---

# DM SDF Slicer Architecture

The SDF slicer extracts smooth cross-section curves from SDF SDFs by running marching squares
on a 2D grid projected onto an arbitrary plane, then fitting DM-compatible curves through the
resulting contour points.

---

## Pipeline

```
SdfField                    slice plane (origin, normal)
    │                              │
    ▼                              ▼
slice_sdf()              ← build 2D grid on plane
    │                       evaluate_grid() on 3D points
    │                       marching squares → line segments
    │                       chain segments → contour polylines
    ▼
list[list[Vector]]        ← ordered 3D contour points
    │
    ▼
fit_dm_curve()            ← auto-generate tangent handles
    │
    ▼
dict(Points, HandleIn,    ← pass to create_dm_object("name", "curve", params=result)
     HandleOut, Closed)
```

---

## Slice Plane Coordinate System

Given `origin` and `normal`, the slicer builds an orthonormal basis:

```python
u_axis = normal × up      # where up = (0,0,1) unless parallel to normal
v_axis = normal × u_axis
```

The 2D grid spans `[-extent, extent]` in both `u` and `v`. Each grid point maps to 3D:

```python
world_point = origin + u * u_axis + v * v_axis
```

Grid extent defaults to `0.6 × bounding_box_diagonal`, which ensures full coverage
with some margin.

---

## Marching Squares

Standard 2×2 cell lookup with 16 configurations. The edge table maps each configuration
to edge pairs. Edge midpoints use linear interpolation on SDF values:

```
frac = val_a / (val_a - val_b)
point = corner_a + frac * (corner_b - corner_a)
```

Ambiguous cases (config 5 and 10) use avg-value disambiguation — the simpler approach
that works well for SDFs since they are continuous.

---

## Segment Chaining

The marching squares output is an unordered set of line segments. `_chain_segments()` chains
them into ordered polylines by matching endpoints within tolerance (1e-6 in UV space).
A contour is closed when the first and last points are within the tolerance.

---

## DM Curve Compatibility

DM curves are stored as `Part::FeaturePython` objects with `ShapeType = "curve"` and these
properties:

| Property | Type | Description |
|----------|------|-------------|
| `Points` | `VectorList` | Ordered knot positions |
| `HandleIn` | `VectorList` | Inbound tangent handle positions (one per point) |
| `HandleOut` | `VectorList` | Outbound tangent handle positions (one per point) |
| `Closed` | `Bool` | Whether the curve is periodic |
| `PointTypes` | `IntegerList` | Not used by slicer (set to 0 = Tangent) |
| `HandleTypes` | `IntegerList` | Not used by slicer (set to 0 = Auto) |

**The `fit_dm_curve()` return dict must include `is_closed` (lowercase) as well as `Closed`**,
because `DMObjectProxy.__init__()` reads `params.get("is_closed", False)` for the `Closed`
property.

### Handle Generation

For each point `i`, compute a tangent from its neighbors:

```python
tangent = normalize(pts[(i+1) % n] - pts[(i-1) % n])
handle_in  = pts[i] - tangent * (chord_prev * smooth_factor)
handle_out = pts[i] + tangent * (chord_next * smooth_factor)
```

`smooth_factor = 0.33` gives Catmull-Rom-like smoothness. Handles are actual world positions,
not offsets from the point.

### Decimation

Contour polylines from marching squares often have hundreds of points. Decimate by skipping
points closer than `0.5 × avg_spacing` to the previous kept point.

---

## Files to Read Before Editing

1. `core/sdf/sdf_slicer.py` — the slicer implementation
2. `core/dm_curve.py` — `DMCurve` class (consumers of the curve data)
3. `core/dm_point.py` — `DMPoint` class (handle_in / handle_out semantics)
4. `core/dm_object.py` — `DMObjectProxy.__init__()` curve branch (property setup)
5. `core/dm_object.py` — `create_dm_object()` factory function
