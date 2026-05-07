---
name: DM SVG Path to Cubic Bezier Conversion
description: Reference for converting SVG path data (M L H V C S Q T A Z) and shape elements (rect, circle, ellipse, line, polyline, polygon) into cubic Bezier segments with no rasterization. Required reading before implementing svg_importer.py.
---

# DM SVG Path → Cubic Bezier

The Direct Modeling SVG importer is **rasterization-free**: every SVG
shape becomes one or more cubic Bezier segments. Lines are degenerate
cubics, quadratic Beziers lift exactly to cubics, and arcs decompose
into cubic Beziers via the standard 90°-segment approximation.

## Output Format

`parse_path_d(d_str)` returns:
```
list[                          # subpaths
    list[                      # segments in subpath
        tuple(p0, p1, p2, p3)  # one cubic Bezier; each pi is (x, y)
    ]
]
```

Each subpath corresponds to one closed loop. Open subpaths are closed
by appending a degenerate-cubic line from end → start.

## Coordinate Conventions

- **SVG Y axis points down**, FreeCAD profiles assume Y up.
  The parser leaves coordinates as-is; callers are responsible for any
  axis flip (`y → -y`).
- Numbers may be space-, comma-, or sign-delimited:
  `M10,20 L-5.3.2` is `M 10 20 L -5.3 0.2`.
- A command letter may be followed by multiple coordinate sets, which
  are interpreted as repeated commands (`M` after the first pair becomes
  implicit `L`; `L L L` etc.).
- Lower-case commands are **relative** to the current point;
  upper-case are **absolute**.

## State Variables (during parse)

```
cp           = (0.0, 0.0)   # current point
sp           = (0.0, 0.0)   # subpath start (for Z)
prev_ctrl    = None         # last cubic control point (for S/s)
prev_q_ctrl  = None         # last quadratic control point (for T/t)
```

`prev_ctrl` is reset to `None` after any non-cubic command. `prev_q_ctrl`
is reset after any non-quadratic command.

## Line Segments — degenerate cubic

```python
def _line_as_cubic(p0, p1):
    return (
        p0,
        (p0[0] + (p1[0] - p0[0]) / 3.0, p0[1] + (p1[1] - p0[1]) / 3.0),
        (p0[0] + 2 * (p1[0] - p0[0]) / 3.0, p0[1] + 2 * (p1[1] - p0[1]) / 3.0),
        p1,
    )
```

Used for `L l H h V v` and the implicit close-line from `Z`.

## Quadratic → Cubic (exact)

A quadratic Bezier `(P0, P1, P2)` is identical to the cubic
`(P0, P0 + 2/3·(P1-P0), P2 + 2/3·(P1-P2), P2)`.

```python
def _quad_to_cubic(p0, p1, p2):
    return (
        p0,
        (p0[0] + 2.0 / 3.0 * (p1[0] - p0[0]), p0[1] + 2.0 / 3.0 * (p1[1] - p0[1])),
        (p2[0] + 2.0 / 3.0 * (p1[0] - p2[0]), p2[1] + 2.0 / 3.0 * (p1[1] - p2[1])),
        p2,
    )
```

## Smooth Continuations — `S/s` and `T/t`

The implicit first control point is the reflection of the previous
segment's last control point about the current point.

```python
# For S/s (cubic smooth): implicit P1 is reflection of prev_ctrl about cp.
if prev_ctrl is not None:
    x1 = 2 * cp[0] - prev_ctrl[0]
    y1 = 2 * cp[1] - prev_ctrl[1]
else:
    x1, y1 = cp   # spec: if no previous cubic, use current point

# For T/t (quad smooth): implicit Q1 is reflection of prev_q_ctrl about cp.
if prev_q_ctrl is not None:
    qctrl = (2 * cp[0] - prev_q_ctrl[0], 2 * cp[1] - prev_q_ctrl[1])
else:
    qctrl = cp
```

## Arc → Cubic Bezier — full algorithm

SVG arc syntax: `A rx ry x_axis_rot_deg large_arc_flag sweep_flag x y`.

### Step 1 — Endpoint → Center parameterization

(Per SVG 1.1 implementation notes B.2.4.)

```python
def _arc_endpoint_to_center(p1, p2, rx, ry, phi_deg, fa, fs):
    """Return (cx, cy, theta1, dtheta, rx, ry, phi).
    Or None if the arc collapses to a line (degenerate)."""
    if rx == 0 or ry == 0 or (p1[0] == p2[0] and p1[1] == p2[1]):
        return None    # treat as line
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)

    # 1. Compute (x1', y1')
    dx = (p1[0] - p2[0]) / 2.0
    dy = (p1[1] - p2[1]) / 2.0
    x1p =  cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    # 2. Correct out-of-range radii (per SVG spec F.6.6.2)
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s; ry *= s

    # 3. Compute (cx', cy')
    sign = -1.0 if fa == fs else 1.0
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coeff = sign * math.sqrt(max(num, 0.0) / den) if den > 0 else 0.0
    cxp =  coeff * (rx * y1p) / ry
    cyp = -coeff * (ry * x1p) / rx

    # 4. Compute (cx, cy)
    cx = cos_phi * cxp - sin_phi * cyp + (p1[0] + p2[0]) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (p1[1] + p2[1]) / 2.0

    # 5. Compute theta1 and dtheta
    def _angle(u, v):
        d = u[0] * v[0] + u[1] * v[1]
        l = math.sqrt((u[0]**2 + u[1]**2) * (v[0]**2 + v[1]**2))
        a = math.acos(max(-1.0, min(1.0, d / l))) if l > 0 else 0.0
        if u[0] * v[1] - u[1] * v[0] < 0:
            a = -a
        return a

    v1 = ((x1p - cxp) / rx, (y1p - cyp) / ry)
    v2 = ((-x1p - cxp) / rx, (-y1p - cyp) / ry)
    theta1 = _angle((1.0, 0.0), v1)
    dtheta = _angle(v1, v2)
    if not fs and dtheta > 0:
        dtheta -= 2.0 * math.pi
    elif fs and dtheta < 0:
        dtheta += 2.0 * math.pi

    return (cx, cy, theta1, dtheta, rx, ry, phi)
```

### Step 2 — Split into ≤ π/2 sub-arcs and emit cubic Bezier per sub-arc

```python
def _arc_to_cubics(p1, p2, rx, ry, phi_deg, fa, fs):
    """Return a list of cubic Bezier tuples approximating the SVG arc."""
    params = _arc_endpoint_to_center(p1, p2, rx, ry, phi_deg, fa, fs)
    if params is None:
        return [_line_as_cubic(p1, p2)]
    cx, cy, theta1, dtheta, rx, ry, phi = params
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)

    n = max(1, int(math.ceil(abs(dtheta) / (math.pi / 2.0))))
    delta = dtheta / n
    alpha = (4.0 / 3.0) * math.tan(delta / 4.0)

    segs = []
    cur_theta = theta1
    # Initial point on unit circle (then scaled+rotated+translated)
    def _on_arc(theta):
        x = math.cos(theta); y = math.sin(theta)
        # Tangent direction (rotated derivative)
        return x, y

    for i in range(n):
        t1 = cur_theta
        t2 = cur_theta + delta
        cos1, sin1 = math.cos(t1), math.sin(t1)
        cos2, sin2 = math.cos(t2), math.sin(t2)

        # Unit-circle control points
        u_p0 = (cos1, sin1)
        u_p1 = (cos1 - alpha * sin1, sin1 + alpha * cos1)
        u_p2 = (cos2 + alpha * sin2, sin2 - alpha * cos2)
        u_p3 = (cos2, sin2)

        def _xform(u):
            x = u[0] * rx
            y = u[1] * ry
            xr = cos_phi * x - sin_phi * y
            yr = sin_phi * x + cos_phi * y
            return (xr + cx, yr + cy)

        seg = (_xform(u_p0), _xform(u_p1), _xform(u_p2), _xform(u_p3))
        segs.append(seg)
        cur_theta = t2

    return segs
```

## Shape Elements → Path 'd' Strings

| Tag | Equivalent `d` |
|-----|----------------|
| `<rect x y width w height h>` | `M x y h w v h h -w z` (no rounded corners; if rx/ry attrs present, fall back to 4 arcs at the corners — out of scope for v1) |
| `<circle cx cy r>` | `M (cx-r) cy a r r 0 1 0 (2r) 0 a r r 0 1 0 (-2r) 0 z` |
| `<ellipse cx cy rx ry>` | `M (cx-rx) cy a rx ry 0 1 0 (2rx) 0 a rx ry 0 1 0 (-2rx) 0 z` |
| `<line x1 y1 x2 y2>` | `M x1 y1 L x2 y2` |
| `<polyline points="...">` | `M x0 y0 L x1 y1 L ...` |
| `<polygon points="...">` | `M x0 y0 L x1 y1 L ... Z` |

Two-arc circle/ellipse decomposition is preferred over a single 360°
arc because most arc-to-Bezier libraries (and this one) treat full
revolutions as ambiguous.

## Tokenizer Notes

The path-data tokenizer must handle:
- **Implicit decimal repetition:** `0.5.3` is two numbers `0.5` and `.3`.
- **Implicit sign delimitation:** `1-2` is two numbers `1` and `-2`.
- **Exponents:** `1e3`, `1.5e-2`.

The regex `[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?` covers all
three. Use `finditer` rather than splitting on whitespace.

## Out of Scope (v1)

These will fail with `NotImplementedError` or be silently skipped:
- `<g>` group transforms
- `transform="..."` attributes on shapes
- CSS class/style selectors (no fill/stroke parsing — geometry only)
- `<defs>` references (`<use>`, `<symbol>`)
- Text elements
- `rect` with `rx`/`ry` (rounded corners)

If users need any of these, route them through Inkscape's "Object to
Path + Flatten Transforms" before importing.

## Files to Read Before Editing

1. `core/sdf/sdf2d/bezier_curve.py:130-200` — the `Sdf2dBezierCurve`
   constructor signature; the parser must produce data shaped like
   `[(p0, p1, p2, p3), ...]` where each `pi` is a 2-tuple of floats.
