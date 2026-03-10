---
name: DM Mesher Architecture
description: Reference for the mesher class hierarchy, output format, timer instrumentation, and FRepField API. Required reading before modifying any mesher or adding mesh optimization passes.
---

# DM Mesher Architecture

The meshing pipeline lives in `core/dm_mesher.py`. All meshers convert an `FRepField`
(signed distance function) into triangle arrays for Coin3D rendering.

---

## Class Hierarchy

```
DMMesher (abstract base — line 91)
├── MarchingCubesMesher       (line 97)   — uniform grid MC, fully vectorized
├── AdaptiveMCMesher          (line 241)  — stub, falls back to MC
├── SurfaceNetsMesher         (line 248)  — naive surface nets, quad→tri
└── DualContouringMesher      (line 479)  — QEF-based, preserves sharp features
```

### Selection

`get_active_mesher(type_override=None)` at line 731 dispatches by integer index
or string label from FreeCAD preferences:

| Index | Label | Class |
|-------|-------|-------|
| 0 | Marching Cubes | `MarchingCubesMesher` |
| 1 | Adaptive Marching Cubes | `AdaptiveMCMesher` |
| 2 | Surface Nets | `SurfaceNetsMesher` |
| 3 | Dual Contouring | `DualContouringMesher` |

---

## Mesher Contract

```python
class DMMesher:
    def mesh(self, field: FRepField, cell_size: float) -> tuple:
        """Returns (flat_verts, flat_idx) or None if no surface found."""
```

### Output Format

| Array | Shape | Dtype | Description |
|-------|-------|-------|-------------|
| `flat_verts` | `(N*3, 3)` | `float32` | One row per triangle vertex (no sharing) |
| `flat_idx` | `(N*4,)` | `int32` | Coin3D `SoIndexedFaceSet` indices: `[0,1,2,-1, 3,4,5,-1, ...]` |

Vertices are **not deduplicated** — every triangle has its own three vertices.
Index buffer is flat with `-1` sentinel after every triple.

### Return Convention

- Return `None` if no active cells are found (empty field).
- Return the `(flat_verts, flat_idx)` tuple otherwise.
- Do **not** return a `Part.Shape` — the renderer handles conversion.

---

## FRepField API

Located in `core/frep/frep_field.py`:

```python
class FRepField:
    def evaluate(self, point: FreeCAD.Vector) -> float
    def evaluate_grid(self, points: np.ndarray) -> np.ndarray   # (N,3) → (N,)
    def gradient(self, point: FreeCAD.Vector, h=1e-4) -> FreeCAD.Vector
    def bounding_box(self) -> tuple[FreeCAD.Vector, FreeCAD.Vector]
    def sign_at(self, point, tol=1e-5) -> int   # -1/0/+1
```

**SDF convention:** negative inside, positive outside, zero on surface.

**Concrete SDFs** in `core/frep/sdf/`: `SdfBoxField`, `SdfSphereField`,
`SdfCylinderField`, `SdfPlaneField`. Each overrides `evaluate_grid()` with
vectorized NumPy for performance. CSG composition via `core/frep/frep_composer.py`
(`FRepUnion`, `FRepIntersection`, `FRepDifference`).

---

## Timer Instrumentation

`MeshTimer` at line 27 accumulates per-stage timings across multiple `mesh()` calls.
After a tool commit, `mesh_timer.summary(label)` emits a single `[PERF]` log.

### Usage Pattern

```python
from core.dm_mesher import mesh_timer

mesh_timer.tick()                      # once per mesh() call
mesh_timer.start("my_stage_name")
# ... work ...
mesh_timer.stop("my_stage_name")
```

### Stage Naming Conventions

| Mesher | Prefix | Example stages |
|--------|--------|---------------|
| Marching Cubes | (none) | `field_eval`, `cube_index`, `active_filter`, `edge_interp`, `tri_extract`, `mesh_build` |
| Surface Nets | `sn_` | `sn_detect_edges`, `sn_net_cells`, `sn_vertex_compute`, `sn_quad_assembly` |
| Dual Contouring | `dc_` | `dc_detect_edges`, `dc_qef_collect`, `dc_qef_solve`, `dc_quad_assembly` |
| Adaptive MC | `amc_` | (to be added) |

The timer auto-collects any stage name dynamically. No need to register new stages
in `_STAGES`; they will appear in the summary output after the predefined MC stages.

---

## Common Grid Setup Pattern

All meshers follow the same grid-building steps (copy from MC as template):

```python
min_b, max_b = field.bounding_box()

def _pad(lo, hi):
    if hi - lo < 1e-4:
        mid = (lo + hi) / 2
        return mid - 1.0, mid + 1.0
    return lo - cell_size, hi + cell_size

x0, x1 = _pad(min_b.x, max_b.x)
y0, y1 = _pad(min_b.y, max_b.y)
z0, z1 = _pad(min_b.z, max_b.z)

nx = max(1, int(np.ceil((x1 - x0) / cell_size)))
ny = max(1, int(np.ceil((y1 - y0) / cell_size)))
nz = max(1, int(np.ceil((z1 - z0) / cell_size)))

x = np.linspace(x0, x0 + nx * cell_size, nx + 1)
y = np.linspace(y0, y0 + ny * cell_size, ny + 1)
z = np.linspace(z0, z0 + nz * cell_size, nz + 1)

X, Y, Z = np.meshgrid(x, y, z, indexing='ij')
pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
vals = field.evaluate_grid(pts).reshape(nx + 1, ny + 1, nz + 1)
```

One cell_size of padding is added on all sides to guarantee the SDF volume fully
encloses the surface, preventing boundary gaps.

---

## Files to Read Before Editing

1. `core/dm_mesher.py` — all mesher implementations
2. `core/frep/frep_field.py` — base FRepField class
3. `core/frep/sdf/box.py` — reference SDF implementation (vectorized `evaluate_grid`)
4. `core/dm_object.py` — preference accessors (`get_meshing_type`, `get_cell_size`, etc.)
5. `core/dm_renderer.py` — consumes `(flat_verts, flat_idx)` via `update_frep_mesh()`
