# Direct Modeling Workbench — Input Pipeline Refactor Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

The workbench has a **dual event pipeline**: a Qt-level event filter (`DMInputManager.eventFilter`) and a Coin3D-level callback (`DMBase.event_cb`). Both fire for the same user input, creating duplicated logic, inconsistent modifier state, and fragile tool lifecycle management.

The PieMenu add-on (see `freecad_ui_overriding` skill) demonstrates the correct pattern: a Qt event filter should only **suppress FreeCAD defaults** (context menus, shortcut stealing) and **track state** (mouse position, modifiers). All tool-specific logic belongs in the Coin3D callback where tools have full context.

**Current state:** Right-click finish logic is duplicated in both pipelines. Modifier keys are tracked in two places. Middle-mouse state is tracked independently by both the Qt filter and each tool instance. SDF hit-testing runs inside the global Qt filter on every LMB click.

**Goal state:** Each event type has exactly **one owner**. The Qt filter is a thin state-tracker and FreeCAD-suppressor. All tool logic (finish, menu, click handling) lives exclusively in Coin3D callbacks. Modifier state has a single source of truth.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `DMInputManager` | `core/input_manager.py:7` | Qt event filter singleton; tracks mouse pos, modifiers |
| `DMInputManager.eventFilter` | `core/input_manager.py:30` | Qt-level event interception |
| `DMBase` | `tools/dm_base.py:18` | Base class for all DM tools |
| `DMBase.event_cb` | `tools/dm_base.py:235` | Coin3D event dispatcher |
| `DMBase.on_button3_down` | `tools/dm_base.py:208` | Right-click handler (finish logic) |
| `DMBase.handle_keyboard` | `tools/dm_base.py:278` | Key event dispatcher |
| `DMToolManager` | `core/dm_tool_manager.py:21` | Singleton active-tool registry |
| `DMSelectionObserver` | `core/dm_tool_manager.py:4` | Blocks FreeCAD selection while tool active |
| `ViewProjector.get_sdf_hit` | `core/view_projector.py:380` | SDF ray-march hit test |

---

## Agent Skills

| Skill | Purpose |
|-------|---------|
| `dm_input_refactor` | Target architecture, single-owner principle, modifier contract, right-click fix strategy |
| `dm_event_pipeline` | Current event flow reference (channels, Y-flip, hotkey pattern) |

---

## Tier 1 — Eliminate Dual Right-Click (Do First)

Remove duplicated right-click finish logic so only one code path handles tool finishing on right-click.

### I-001: Remove finish logic from Qt event filter right-click block

**File:** `core/input_manager.py` — lines 74-90 (the right-click suppression block)

**What:** Strip the tool finish logic from the Qt filter. Keep only the context-menu suppression (return True) and delegate to `on_button3_down` via a synthetic event dict so Coin3D's handler remains the single owner.

**Implementation:**

```python
            # [Event Owner: Qt Event Filter] Right-click suppression: when a tool is active,
            # consume the right mouse button press so FreeCAD's NavigationStyle never opens
            # its context menu.  Delegate finish logic to tool's on_button3_down (Coin3D path).
            if event.type() == QtCore.QEvent.MouseButtonPress:
                if event.button() == QtCore.Qt.RightButton:
                    from core.dm_tool_manager import DMToolManager
                    tool = DMToolManager.get_instance().get_active_tool()
                    if tool and not self._middle_mouse_down:
                        # Build a synthetic event_dict so on_button3_down has full context
                        synthetic = {
                            "Button": "BUTTON3",
                            "State": "DOWN",
                            "QtPosition": (event.pos().x(), event.pos().y()),
                            "ShiftDown": bool(event.modifiers() & QtCore.Qt.ShiftModifier),
                        }
                        tool.on_button3_down(synthetic)
                        return True  # suppress FreeCAD context menu
```

Replace lines 74-90 with the code above. The key change: no direct `tool._finish_scheduled` / `QTimer.singleShot(0, tool.finish)` — that logic stays exclusively in `DMBase.on_button3_down`.

---

### I-002: Remove `_finish_scheduled` guard from `on_button3_down`

**File:** `tools/dm_base.py` — lines 208-224 (`on_button3_down` method)

**What:** Since right-click now flows through a single path (I-001 delegates to `on_button3_down`), the `_finish_scheduled` guard is no longer needed to prevent double-fire. Simplify the method.

**Implementation:**

```python
    def on_button3_down(self, event_dict):
        # If Middle Mouse or Shift is held, it's likely a view rotation chord. Do not finish!
        if DMInputManager.get_instance()._middle_mouse_down or event_dict.get("ShiftDown", False):
            return False

        if hasattr(self, 'on_tool_menu') and self.on_tool_menu():
            return True

        if not getattr(self, '_finish_scheduled', False):
            self._finish_scheduled = True
            QtCore.QTimer.singleShot(0, self.finish)
        return True  # Consume Press
```

Note: We keep `_finish_scheduled` as a safety net for now — the guard still protects against rapid repeated clicks. The change is that we use `DMInputManager.get_instance()._middle_mouse_down` instead of `self._middle_mouse_down` (per-tool tracking) for the middle-mouse check. Remove the line `global_middle_down = DMInputManager.get_instance()._middle_mouse_down` and `is_middle_down = getattr(self, "_middle_mouse_down", False) or global_middle_down` — use the singleton directly.

**Depends on:** I-001

---

## Tier 2 — Unify Modifier State

Ensure all code reads modifier keys from a single source (DMInputManager), eliminating per-tool duplication.

### I-003: Remove per-tool `_middle_mouse_down` tracking from `event_cb`

**File:** `tools/dm_base.py` — lines 244 and 252 inside `event_cb`

**What:** Remove the lines that set `self._middle_mouse_down` on BUTTON2 down/up. The Qt filter already tracks this globally in `DMInputManager._middle_mouse_down`.

**Implementation:**

In `event_cb`, lines 243-244 currently read:
```python
                if state == "DOWN":
                    if btn == "BUTTON2": self._middle_mouse_down = True
```

Change to:
```python
                if state == "DOWN":
                    pass  # Middle-mouse tracked globally by DMInputManager
```

And lines 251-252 currently read:
```python
                elif state == "UP":
                    if btn == "BUTTON2": self._middle_mouse_down = False
```

Change to:
```python
                elif state == "UP":
                    pass  # Middle-mouse tracked globally by DMInputManager
```

Then remove the `pass` lines entirely — the `if btn ==` dispatch lines below will execute directly.

Actually, the cleanest approach: just delete the two `if btn == "BUTTON2": self._middle_mouse_down = ...` lines entirely. The surrounding `if state == "DOWN":` / `elif state == "UP":` blocks contain other button dispatches that must stay.

**Depends on:** I-002

---

### I-004: Update all `_middle_mouse_down` reads to use DMInputManager

**File:** `tools/dm_base.py` — `on_button3_down` method (line ~210)

**What:** Replace any remaining `self._middle_mouse_down` or `getattr(self, "_middle_mouse_down", False)` reads with `DMInputManager.get_instance()._middle_mouse_down`.

**Implementation:**

Search for `self._middle_mouse_down` in `tools/dm_base.py`. After I-002, `on_button3_down` already uses the singleton. Verify no other methods reference `self._middle_mouse_down`. If `handle_click` (line 409) or any other method reads it, replace with:

```python
DMInputManager.get_instance()._middle_mouse_down
```

Also search all files in `tools/` for `_middle_mouse_down` and update any that read the per-tool attribute.

**Depends on:** I-003

---

### I-005: Add `is_shift_down()` and `is_ctrl_down()` accessors to DMInputManager

**File:** `core/input_manager.py` — after `get_qt_cursor_pos` method (line 204)

**What:** Add clean accessor methods so tools don't reach into private attributes. This establishes the modifier contract: all modifier queries go through these methods.

**Implementation:**

```python
    def is_shift_down(self):
        """Single source of truth for Shift key state."""
        return self._shift_down

    def is_ctrl_down(self):
        """Single source of truth for Control key state."""
        return self._control_down

    def is_left_mouse_down(self):
        """Single source of truth for left mouse button state."""
        return self._left_mouse_down

    def is_middle_mouse_down(self):
        """Single source of truth for middle mouse button state."""
        return self._middle_mouse_down
```

---

### I-006: Replace direct `event_dict["ShiftDown"]` reads with DMInputManager

**File:** Multiple files in `tools/` — search for `event_dict.get("ShiftDown"` or `event_dict["ShiftDown"]`

**What:** Any tool code that reads shift state from the Coin3D event dict should instead call `DMInputManager.get_instance().is_shift_down()`. This ensures consistency between the Qt-tracked state and what tools see.

**Implementation:**

For each occurrence, replace:
```python
# Old
event_dict.get("ShiftDown", False)
```

With:
```python
# New
DMInputManager.get_instance().is_shift_down()
```

Files to check (grep for `ShiftDown`):
- `tools/dm_base.py`
- `tools/translate_tool.py`
- `tools/edit_tool.py`
- `tools/work_plane_tool.py`
- `tools/curve_tool.py`

**Depends on:** I-005

---

## Tier 3 — Extract SDF Selection From Qt Filter

Move the expensive SDF hit-testing out of the global Qt event filter into a dedicated handler.

### I-007: Create `DMSelectionManager` class

**File:** `core/dm_selection_manager.py` — new file

**What:** Extract SDF-click selection logic from `DMInputManager.eventFilter` (lines 55-72) into a dedicated class. This separates concerns: the Qt filter tracks state, the selection manager handles picking.

**Implementation:**

```python
import FreeCAD
import FreeCADGui
from core import dm_logger


class DMSelectionManager:
    """Handles SDF object selection on left-click when no tool is active."""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = DMSelectionManager()
        return cls._instance

    def try_sdf_selection(self, qt_pos):
        """Attempt to select an SDF object at the given Qt screen position.

        Args:
            qt_pos: tuple (x, y) in Qt coordinates (Y=0 at top)

        Returns:
            True if an SDF object was selected, False otherwise.
        """
        try:
            view = FreeCADGui.ActiveDocument.ActiveView if FreeCADGui.ActiveDocument else None
            if not view:
                return False

            from core.view_projector import ViewProjector
            proj = ViewProjector(view)
            sdf_result = proj.get_sdf_hit({"QtPosition": qt_pos})
            if sdf_result:
                _, _, sdf_obj = sdf_result
                FreeCADGui.Selection.clearSelection()
                FreeCADGui.Selection.addSelection(sdf_obj)
                return True
        except Exception as e:
            dm_logger.debug(f"SDF selection failed: {e}")
        return False
```

---

### I-008: Wire `DMSelectionManager` into the Qt event filter

**File:** `core/input_manager.py` — lines 54-72 (LMB press SDF selection block)

**What:** Replace the inline SDF selection code with a call to `DMSelectionManager.try_sdf_selection()`.

**Implementation:**

Replace lines 54-72 with:

```python
            # SDF object selection on LMB press when no tool is active
            if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                from core.dm_tool_manager import DMToolManager
                if not DMToolManager.get_instance().has_active_tool():
                    from core.dm_selection_manager import DMSelectionManager
                    DMSelectionManager.get_instance().try_sdf_selection(
                        (event.pos().x(), event.pos().y())
                    )
                    # Do NOT return True — let FreeCAD's navigation also handle this click
```

**Depends on:** I-007

---

## Tier 4 — Clean Up Tool Lifecycle

Simplify the multi-layered finish/terminate chain.

### I-009: Add `_finish_scheduled` reset in `_do_terminate`

**File:** `tools/dm_base.py` — inside `_do_terminate` method (line 116)

**What:** When a tool terminates, reset `_finish_scheduled = False` so a reused tool instance (if any) starts clean. Currently `_finish_scheduled` is set True on right-click but never reset on terminate.

**Implementation:**

Add at the start of `_do_terminate`, after `tool_mgr.set_active_tool(None)` (line 120):

```python
        self._finish_scheduled = False
```

---

### I-010: Guard `NURBSPrimitiveCreator.finish()` against double invocation

**File:** `tools/dm_base.py` — `NURBSPrimitiveCreator.finish` method (line 664)

**What:** The `finish()` method schedules `_do_finish()` via QTimer but doesn't check `_finished` first. If `finish()` is called twice rapidly (e.g. Enter key + right-click), two `_do_finish` invocations get queued. Add an early guard.

**Implementation:**

```python
    def finish(self):
        """Schedule the finalization to happen safely outside the event loop."""
        if self._finished:
            return
        QtCore.QTimer.singleShot(0, self._do_finish)
```

---

### I-011: Remove dead code in `handle_click` right-click branch

**File:** `tools/dm_base.py` — `handle_click` method, lines 414-420

**What:** `handle_click` contains a BUTTON3 branch that calls `finish()` or `terminate()` directly. But `handle_click` is only called from `on_button1_down` (line 203), which only fires on BUTTON1. The BUTTON3 branch in `handle_click` is dead code — `on_button3_down` handles right-clicks.

**Implementation:**

Remove lines 414-420:
```python
            # Right-click (BUTTON3) to finish or drop
            if btn == "BUTTON3":
                if self.state > 1:
                    self.finish()
                else:
                    self.terminate()
                return True
```

---

## Tier 5 — Harden Event Filter

Minor improvements to make the Qt filter more robust.

### I-012: Debounce SDF selection to avoid per-click ray-marching

**File:** `core/dm_selection_manager.py` — in `DMSelectionManager` class (after I-007)

**What:** SDF ray-marching is expensive. If the user clicks rapidly (double-click, etc.), avoid running multiple ray-marches. Add a simple time-based debounce.

**Implementation:**

Add to `DMSelectionManager.__init__`:
```python
    def __init__(self):
        self._last_selection_time = 0.0
```

Add debounce check at the top of `try_sdf_selection`:
```python
    def try_sdf_selection(self, qt_pos):
        import time
        now = time.monotonic()
        if now - self._last_selection_time < 0.1:  # 100ms debounce
            return False
        self._last_selection_time = now
        # ... rest of method
```

**Depends on:** I-007

---

### I-013: Add `[Event Owner]` comments to all event handling blocks

**File:** `core/input_manager.py` — all major blocks in `eventFilter`

**What:** Each block in the event filter should have a comment tag indicating the event owner. Some already have `[Event Owner: Qt Event Filter]`. Ensure all blocks follow this pattern for maintainability.

**Implementation:**

Add `# [Event Owner: Qt Event Filter]` comment before:
- Line 38: coordinate tracking block
- Line 41: modifier tracking block
- Line 47: button state tracking block
- Line 55: SDF selection block (after I-008, becomes `# [Event Owner: DMSelectionManager]`)

These comments already exist on some blocks (lines 74, 94, 147). Extend the pattern to all blocks.

---

### I-014: Document the single-owner contract in `dm_base.py` module docstring

**File:** `tools/dm_base.py` — line 1 (module docstring)

**What:** Add a module-level docstring explaining the event ownership contract so future developers don't re-introduce dual handling.

**Implementation:**

Replace line 1:
```python
"""Base classes for DM primitive creators."""
```

With:
```python
"""Base classes for DM primitive creators.

Event Ownership Contract
========================
- Qt filter (DMInputManager): state tracking, FreeCAD suppression, global hotkeys.
  Never calls tool methods except via synthetic event_dict to on_button3_down.
- Coin3D callback (event_cb): ALL tool logic — clicks, moves, keyboard, finish.
- Modifiers: always read from DMInputManager (is_shift_down, is_ctrl_down, etc.).
- Drag: QTimer polls DMInputManager._last_qt_pos. Coin3D location events are
  suppressed during LMB hold, so always use the timer pattern.
"""
```

---

## Tier 6 — Stretch: Centralize Event Dispatch

Optional improvements for a cleaner architecture long-term.

### I-015: Move global hotkey handling (D/E) from Qt filter to DMToolManager

**File:** `core/input_manager.py` — lines 113-145 (global hotkey block) and `core/dm_tool_manager.py`

**What:** The Qt filter currently handles D (context menu) and E (edit object) when no tool is active. This mixes UI orchestration into the event filter. Move this logic to `DMToolManager.handle_global_hotkey(key)` and have the filter call that method.

**Implementation:**

1. Add to `DMToolManager`:

```python
    def handle_global_hotkey(self, key_text):
        """Handle hotkeys when no tool is active. Returns True if handled."""
        if self.has_active_tool():
            return False

        if key_text == 'd':
            from core.dm_menu import DMMenuManager
            mgr = DMMenuManager.get_instance()
            if not mgr.is_menu_active() and not mgr._ignore_hotkeys:
                mgr.show_context_menu()
                return True

        if key_text == 'e':
            from core.dm_menu import DMMenuManager
            if DMMenuManager.get_instance().is_menu_active():
                return False
            import FreeCADGui
            sel = FreeCADGui.Selection.getSelection()
            if sel:
                obj = sel[0]
                proxy_name = getattr(getattr(obj, "Proxy", None), "__class__", type(None)).__name__
                if proxy_name == "DMWorkPlane":
                    from tools.work_plane_tool import WorkPlaneCreator
                    WorkPlaneCreator()
                    return True
                elif proxy_name == "DMObjectProxy":
                    from tools.edit_tool import EditTool
                    tool = EditTool()
                    tool.activate()
                    return True

        return False
```

2. Replace lines 115-145 in `input_manager.py` with:

```python
            if event.type() == QtCore.QEvent.KeyPress:
                text = event.text().lower() if hasattr(event, "text") else ""
                from core.dm_tool_manager import DMToolManager
                if DMToolManager.get_instance().handle_global_hotkey(text):
                    return True
```

**Depends on:** I-001

---

### I-016: Add `DMInputManager.get_modifier_state()` returning a frozen dict

**File:** `core/input_manager.py` — after `is_middle_mouse_down` method (after I-005)

**What:** Provide a single call that returns all modifier/button state as an immutable snapshot, useful for tools that need multiple checks without worrying about state changing mid-frame.

**Implementation:**

```python
    def get_modifier_state(self):
        """Returns a snapshot of all modifier and button states."""
        return {
            "shift": self._shift_down,
            "ctrl": self._control_down,
            "lmb": self._left_mouse_down,
            "mmb": self._middle_mouse_down,
        }
```

**Depends on:** I-005
