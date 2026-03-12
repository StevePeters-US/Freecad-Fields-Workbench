---
name: DM F-Rep Boolean Architecture
description: Reference for how F-Rep boolean operations compose FRepField trees and how primitives are shortcuts for adding/subtracting from arbitrary SDF shapes. Required reading before modifying cmd_boolean.py or primitive tools to work with F-Rep fields.
---

# DM F-Rep Boolean Architecture

F-Rep objects represent arbitrary signed distance fields. Primitive tools (box, sphere,
cylinder) are convenience wrappers that create specific `FRepField` subclasses, but the
underlying system operates on **arbitrary field trees**.

---

## Core Principle

> A DM frep object IS an `FRepField` tree. The tree is stored on the proxy as
> `proxy.FRepField`. Primitives build leaf nodes; booleans compose trees.

---

## FRepField Hierarchy

```
FRepField                          core/frep/frep_field.py
├── SdfField                       core/frep/sdf/sdf_field.py  (leaf base)
│   ├── SdfBoxField                core/frep/sdf/box.py
│   ├── SdfSphereField             core/frep/sdf/sphere.py
│   ├── SdfCylinderField           core/frep/sdf/cylinder.py
│   └── SdfPlaneField              core/frep/sdf/plane.py
└── ComposerField                  core/frep/frep_composer.py  (binary op base)
    ├── UnionField                 min(a, b)
    ├── IntersectionField          max(a, b)
    └── SubtractionField           max(a, -b)
```

Every node implements:
- `evaluate(point) → float` — single-point SDF
- `evaluate_grid(points) → ndarray` — batch SDF `(N,3) → (N,)`
- `bounding_box() → (Vector, Vector)` — AABB

ComposerField holds `self.a` and `self.b` (both `FRepField`).

---

## How Primitives Create Fields

`tools/primitive_tool.py` — `PrimitiveCreatorBase`:

1. `_get_preview_field()` — returns field for live preview (called on mouse move)
2. `_get_final_field()` — returns field for committed object (called on final click)
3. `__do_commit(name, field, points)` — sets `proxy.FRepField = field`

The field is stored on the proxy, NOT serialized. On recompute, `execute()` reads
`self.FRepField` and meshes it (if render mode requires meshing).

---

## How Booleans Should Work (F-Rep)

When both selected objects have `ShapeType == "frep"`:

1. Extract `field_a = sel[0].Proxy.FRepField`
2. Extract `field_b = sel[1].Proxy.FRepField`
3. Compose: `result_field = UnionField(field_a, field_b)` (or Subtraction/Intersection)
4. Create new frep object: `result.Proxy.FRepField = result_field`

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

## DMObjectProxy F-Rep Data Flow

```
primitive_tool._get_final_field()
        │
        ▼
proxy.FRepField = field          ← stored on proxy, NOT a FreeCAD property
        │
        ▼
proxy.execute(fp)
  ├── if render mode needs mesh:
  │     mesher.mesh(field, cell_size) → (verts, idx)
  │     proxy._frep_verts = verts
  │     proxy._frep_idx = idx
  └── fp.Shape = Part.Shape()    ← triggers updateData()
        │
        ▼
DMViewProvider.updateData(fp, "Shape")
  ├── RENDER_MODE_MESH:       renderer.update_frep_mesh(verts, idx)
  ├── RENDER_MODE_POINT_CLOUD: pc.update(field, cell_size)
  └── RENDER_MODE_RAY_MARCH:   rm.update(field, cell_size)
```

---

## Key Gotcha: BRep Booleans vs F-Rep Booleans

The **current** `cmd_boolean.py` uses `Part.Shape.fuse/cut/common` — BRep operations
that operate on the Part shape, NOT the F-Rep field. This means:

- Boolean results lose the SDF tree
- Results cannot be further boolean'd with F-Rep precision
- Results cannot be rendered via ray marching

The refactor replaces this with `ComposerField` composition for frep objects,
preserving the SDF tree for downstream GPU rendering and CNC export.

---

## Files to Read Before Editing

1. `core/frep/frep_composer.py` — `UnionField`, `IntersectionField`, `SubtractionField`
2. `commands/cmd_boolean.py` — current BRep boolean implementation
3. `core/dm_object.py` — `DMObjectProxy.execute()` (frep branch at line 336),
   `DMViewProvider.attach()` (line 411), `DMViewProvider.updateData()` (line 454)
4. `tools/primitive_tool.py` — how fields are created and assigned to proxies
