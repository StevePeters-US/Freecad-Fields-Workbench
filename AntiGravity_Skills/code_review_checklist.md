---
name: Code Review Checklist
description: Checklist for reviewing DM workbench code changes, focusing on parent class promotion and architecture quality.
---

# Code Review Checklist

Use this checklist when reviewing code changes or auditing the Direct Modeling workbench for architecture quality.

## Parent Class Promotion

Check these patterns that indicate code should be promoted to a parent class:

### 1. Duplicate Closures
- **Pattern**: Functions defined inside methods (closures) that replicate base class methods
- **Example**: `to_local(p)` closures in `BoxCreator._make_field()` that duplicate `DMBase.to_local()`
- **Fix**: Use `self.method_name()` from the base class instead

### 2. Identical Method Bodies Across Siblings
- **Pattern**: Two or more sibling classes (e.g., `BoxCreator`, `SphereCreator`) implement the same method with identical logic
- **Fix**: Move the shared implementation to the common parent (`PrimitiveCreatorBase` or `DMBase`)

### 3. Empty Intermediate Classes
- **Pattern**: A class exists only as `class Foo(Bar): pass`
- **Example**: `MarchingCubesField(FRepField): pass`
- **Fix**: Remove the class and have children inherit directly, OR add meaningful shared behavior

### 4. Parallel Cleanup Paths
- **Pattern**: Two related classes have independent termination/cleanup methods that partially overlap
- **Example**: `PrimitiveCreatorBase._do_terminate()` vs `NURBSPrimitiveCreator.terminate()`
- **Fix**: Consolidate into a single `_do_terminate()` chain with `super()` calls

## Field Architecture Quality

- [ ] Every `FRepField` subclass overrides `bounding_box()` with a tight bound (not the default ±10km)
- [ ] `evaluate_grid()` uses numpy vectorization (no Python loops)
- [ ] `gradient()` is analytical when a closed-form expression exists
- [ ] `ComposerField` subclasses correctly compose child bounding boxes

## Tool Architecture Quality

- [ ] All tools use `self.terminate()` (which defers via `QTimer.singleShot`) — never call `_do_terminate()` directly
- [ ] `_finished` guard prevents double-finalization
- [ ] Preview objects are cleaned up on both cancel and commit paths
- [ ] Workplane transformations use `self.to_local()` / `self.to_global()` from `DMBase`

## Class Hierarchy Reference

```
DMBase
├── NURBSPrimitiveCreator (curves, surfaces, points)
│   ├── CurveCreator
│   └── WorkPlaneCreator
├── PrimitiveCreatorBase (FRep primitives)
│   ├── BoxCreator
│   ├── SphereCreator
│   └── CylinderCreator
├── EditTool
├── TranslateTool
└── (future tools)

FRepField
├── AnalyticField (formula-based SDFs)
│   ├── AnalyticBoxField
│   ├── AnalyticSphereField
│   ├── AnalyticCylinderField
│   └── AnalyticPlaneField
├── ComposerField
│   ├── UnionField
│   ├── IntersectionField
│   └── SubtractionField
└── (future: VoxelField, NurbsSurfaceField)
```
