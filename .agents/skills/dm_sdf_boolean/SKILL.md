---
name: DM SDF Boolean Architecture
description: Reference for how SDF boolean operations compose SdfField trees and how primitives are shortcuts for adding/subtracting from arbitrary SDF shapes. Required reading before modifying cmd_boolean.py or primitive tools to work with SDF fields.
---

# DM SDF Boolean Architecture

SDF objects represent arbitrary signed distance fields. Primitive tools (box, sphere,
cylinder) are convenience wrappers that create specific `SdfField` subclasses, but the
underlying system operates on **arbitrary field trees**.

---

## Core Principle

> A DM sdf object IS an `SdfField` tree. The tree is stored on the proxy as
> `proxy.SdfField`. Primitives build leaf nodes; booleans compose trees.

---

## SdfField Hierarchy

```
SdfField                          core/sdf/sdf_field.py
├── SdfField                       core/sdf/sdf/sdf_field.py  (leaf base)
│   ├── SdfBoxField                core/sdf/sdf/box.py
│   ├── SdfSphereField             core/sdf/sdf/sphere.py
│   ├── SdfCylinderField           core/sdf/sdf/cylinder.py
│   └── SdfPlaneField              core/sdf/sdf/plane.py
└── ComposerField                  core/sdf/sdf_composer.py  (binary op base)
    ├── UnionField                 min(a, b)
    ├── IntersectionField          max(a, b)
    └── SubtractionField           max(a, -b)
```

Every node implements:
- `evaluate(point) → float` — single-point SDF
- `evaluate_grid(points) → ndarray` — batch SDF `(N,3) → (N,)`
- `bounding_box() → (Vector, Vector)` — AABB

ComposerField holds `self.a` and `self.b` (both `SdfField`).

---

## How Primitives Create Fields

`tools/primitive_tool.py` — `PrimitiveCreatorBase`:

1. `_get_preview_field()` — returns field for live preview (called on mouse move)
2. `_get_final_field()` — returns field for committed object (called on final click)
3. `__do_commit(name, field, points)` — sets `proxy.SdfField = field`

The field is stored on the proxy, NOT serialized. On recompute, `execute()` reads
`self.SdfField` and meshes it (if render mode requires meshing).

---

## How Booleans Should Work (SDF)

When both selected objects have `ShapeType == "sdf"`:

1. Extract `field_a = sel[0].Proxy.SdfField`
2. Extract `field_b = sel[1].Proxy.SdfField`
3. Compose: `result_field = UnionField(field_a, field_b)` (or Subtraction/Intersection)
4. Create new sdf object: `result.Proxy.SdfField = result_field`

The composed field is a tree — `UnionField` holds references to children.
`evaluate_grid()` on the union calls `evaluate_grid()` on both children and takes `min()`.
No data is copied. The tree composes lazily.

### Important: Deep-copy vs Reference

Currently, `ComposerField.__init__` stores **references** to `field_a` and `field_b`.
This means:
- Hiding the originals is safe (field objects are Python, not FreeCAD Part shapes)
- Deleting the original FreeCAD objects is safe IF the field objects are still reachable
  from the result's proxy
- Modifying original fields after composition WILL change the composed result (this is
  intentional — it enables parametric updates)

---

## DMObjectProxy SDF Data Flow

```
primitive_tool._get_final_field()
        │
        ▼
proxy.SdfField = field          ← stored on proxy, NOT a FreeCAD property
        │
        ▼
proxy.execute(fp)
  ├── if render mode needs mesh:
  │     mesher.mesh(field, cell_size) → (verts, idx)
  │     proxy._sdf_verts = verts
  │     proxy._sdf_idx = idx
  └── fp.Shape = Part.Shape()    ← triggers updateData()
        │
        ▼
DMViewProvider.updateData(fp, "Shape")
  ├── RENDER_MODE_MESH:       renderer.update_sdf_mesh(verts, idx)
  ├── RENDER_MODE_POINT_CLOUD: pc.update(field, cell_size)
  └── RENDER_MODE_RAY_MARCH:   rm.update(field, cell_size)
```

---

## Key Gotcha: BRep Booleans vs SDF Booleans

The **current** `cmd_boolean.py` uses `Part.Shape.fuse/cut/common` — BRep operations
that operate on the Part shape, NOT the SDF field. This means:

- Boolean results lose the SDF tree
- Results cannot be further boolean'd with SDF precision
- Results cannot be rendered via ray marching

The refactor replaces this with `ComposerField` composition for sdf objects,
preserving the SDF tree for downstream GPU rendering and CNC export.

---

## Files to Read Before Editing

1. `core/sdf/sdf_composer.py` — `UnionField`, `IntersectionField`, `SubtractionField`
2. `commands/cmd_boolean.py` — current BRep boolean implementation
3. `core/dm_object.py` — `DMObjectProxy.execute()` (sdf branch at line 336),
   `DMViewProvider.attach()` (line 411), `DMViewProvider.updateData()` (line 454)
4. `tools/primitive_tool.py` — how fields are created and assigned to proxies
