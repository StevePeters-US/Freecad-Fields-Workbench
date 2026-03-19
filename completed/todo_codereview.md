# Direct Modeling Workbench — Code Review Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

The codebase currently functions well but contains several areas of technical debt identified during code review. Key issues include terminology inconsistency ("F-Rep" vs "SDF"), performance bottlenecks in grid evaluation due to Python loops, and brittle UI state management via magic integers. 

The goal of these tasks is to:
1. Standardize terminology around Signed Distance Fields (SDFs).
2. Repurpose splines as 2D curves for SDF extrusion rather than solid boundary representations (B-Reps).
3. Vectorize CPU SDF evaluation via duck-typed NumPy arrays to remove Python loop overhead.
4. Implement a Strategy Pattern for view providers to eliminate hardcoded geometry type branching.
5. Define explicit enumerations for tool states to improve interaction robustness.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `FRepField` | `core/frep/frep_field.py:3` | Base class for all SDFs (to be renamed) |
| `DMObjectProxy` | `core/dm_object.py:133` | Wrapper for FreeCAD Part::FeaturePython |
| `DMViewProvider` | `core/dm_object.py:327` | Manages 3D viewport representation |
| `DMBase` | `tools/dm_base.py:65` | Base tool interaction logic and state machine |

---

## Agent Skills

See `.agents/skills/` for project-specific knowledge:

| Skill | Purpose |
|-------|---------|
| `dm_todo_format` | Format rules for tasks in this project |
| `dm_sdf_primitive_pattern` | Conventions for creating and evaluating SDF fields |

---

## Tier 1 — SDF Terminology & Splines (Do First)

Unify the codebase on the term "SDF" and adjust spline logic to prevent premature solid meshing.

### CR-001: Rename `FRepField` to `SdfField`

**File:** `core/frep/frep_field.py` — line 3

**What:** Rename the base class `FRepField` to `SdfField` and update its docstring to reflect the new terminology.

**Implementation:**
```python
class SdfField:
    """
    Abstract base class for all SDF (Signed Distance Field) fields.
    A field evaluates to a negative number inside the solid, positive outside, and 0 on the surface.
    """
```

### CR-002: Treat Splines as 2D Extrusion Precursors

**File:** `core/dm_object.py` — inside `DMObjectProxy.execute` (line 305)

**What:** Update the handling of curves/splines so they are not processed into solid B-Rep meshes. They should remain lightweight curves intended for eventual SDF extrusion. 

**Implementation:**
```python
            if st == "frep" or st == "curve":
                # SDF objects and pure curves bypass native B-Rep meshing.
                # Curves will eventually be extruded into SDFs in Phase 7.
                fp.Shape = Part.Shape()
                return
```

---

## Tier 2 — Vectorize SDF Evaluation

Eliminate the naive Python loop in `evaluate_grid` by shifting to NumPy duck-typing.

### CR-003: Vectorize `evaluate_grid` Base Implementation

**File:** `core/frep/frep_field.py` — inside `evaluate_grid` (line 29)

**What:** Rewrite the `evaluate_grid` method to pass the `(N, 3)` numpy array directly to `self.evaluate()`, removing the Python loop entirely.

**Implementation:**
```python
    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        """
        Evaluates the field over an (N, 3) numpy array of points.
        The underlying evaluate() method must support duck-typed np.ndarray inputs.
        """
        # Pass the raw Nx3 array directly to evaluate.
        # Subclasses must implement evaluate() using ops that work on both FreeCAD.Vectors and ndarrays.
        return self.evaluate(points)
```

---

## Tier 3 — Abstraction of Render Strategy

Remove hardcoded `ShapeType` logic inside the view provider.

### CR-004: Implement `DMRendererStrategy` Hierarchy

**File:** `core/dm_renderer.py` — append at the end of the file

**What:** Define the base Strategy interface and the concrete implementations for curves and SDFs.

**Implementation:**
```python
class DMRendererStrategy:
    def setup(self, renderer, vobj):
        pass
    def update(self, renderer, fp, prop):
        pass
    def set_display_mode(self, renderer, mode):
        pass

class NURBSRendererStrategy(DMRendererStrategy):
    def setup(self, renderer, vobj):
        renderer.setup_coin_overlay()
        renderer.rebuild_control_cage(vobj.Object)
    def update(self, renderer, fp, prop):
        renderer.rebuild_control_cage(fp)

class SdfRendererStrategy(DMRendererStrategy):
    def setup(self, renderer, vobj):
        pass
    def update(self, renderer, fp, prop):
        pass
    def set_display_mode(self, renderer, mode):
        renderer.set_frep_display_mode(mode)
```

### CR-005: Update `DMViewProvider` to use Strategies

**File:** `core/dm_object.py` — inside `DMViewProvider.attach()` and `updateData()`

**What:** Replace the `if ShapeType == ...` statements with instantiation and delegation to the strategies created in CR-004.

**Implementation:**
In `attach()`: Instantiate `self._strategy = SdfRendererStrategy() if ShapeType == "frep" else NURBSRendererStrategy()`. Call `self._strategy.setup(self.renderer, vobj)`.
In `updateData()`: Use `self._strategy.update(self.renderer, fp, prop)` to handle shape recreation. Delegate display mode changes to `self._strategy.set_display_mode(self.renderer, mode)`.

---

## Tier 4 — Explicit UI State Machine

Refactor primitive editing tools to use an explicit Enum.

### CR-006: Define `ToolState` Enum

**File:** `tools/dm_base.py` — insert at line 59 (before `STATE_IDLE` definitions)

**What:** Create explicit enumerations to replace `STATE_IDLE = 0`, `STATE_ACTIVE = 1`, etc.

**Implementation:**
```python
from enum import IntEnum

class ToolState(IntEnum):
    IDLE = 0
    ACTIVE = 1
    DRAGGING = 2
    FINALIZED = 3
    EDIT_MODE = 4
```

### CR-007: Update `DMBase` to use `ToolState`

**File:** `tools/dm_base.py` — inside `DMBase.__init__` (line 104) and `DMBase.reset_state` (line 702)

**What:** Initialize and reset the state using the new Enum rather than the integer `0`.

**Implementation:**
```python
# In DMBase.__init__:
self.state = ToolState.IDLE

# In DMBase.reset_state:
self.state = ToolState.IDLE
```
