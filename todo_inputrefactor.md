# Direct Modeling — Qt-Native Input System Refactor Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

The current dual-pipeline input system (Qt event filter + Coin3D `event_cb`) causes intractable bugs with event swallowing, double-firing, and FreeCAD native UI interference. Attempting to balance "natural event flow" in Coin3D vs "FreeCAD suppression" in Qt is too fragile.

Inspired by how UI-overriding add-ons (like PieMenu) work, we will move to a **Single-Owner Qt-Native** event pipeline. 
`DMInputManager` will aggressively filter all application events, suppress FreeCAD defaults by returning `True`, and manually invoke DM tool methods. Coin3D `event_cb` will be completely removed.

---

## Tier 1 — Core Pipeline Shift (Do First)

### R-001: Architect DMInputManager as Sole Event Provider
**File:** `core/input_manager.py`
**What:** Expand `eventFilter` to intercept all mouse and keyboard events and forward them to the active tool directly.
**Implementation:**
1. Intercept `MouseMove`. Update `_last_qt_pos`.
2. Intercept `MouseButtonRelease`. Update button states.
3. For `MouseMove`, `MouseButtonPress`, `MouseButtonRelease`, `KeyPress`, `KeyRelease`, and `ContextMenu`:
   - If `DMToolManager.get_instance().get_active_tool()` exists, construct an `event_dict` (using standardized Qt Top-Left coordinates).
   - Call new standard methods on the tool: `on_mouse_press()`, `on_mouse_release()`, `on_mouse_move()`, `on_key_press()`, `on_key_release()`, `on_context_menu()`.
   - Aggressively `return True` (after potentially calling `event.accept()`) to completely block FreeCAD from seeing the events.
4. Delete `so_event_to_dict` as it will no longer be used.

### R-002: Define New Tool Input Interface
**File:** `tools/dm_base.py`
**What:** Strip out Coin3D event registration and add standard Qt-driven methods.
**Implementation:**
1. Remove `self.callback = self.view.addEventCallback("SoEvent", self.event_cb)` from `__init__` and its removal in `_do_terminate()`.
2. Delete the `event_cb()` method.
3. Add stub methods: `on_mouse_press(self, event_dict)`, `on_mouse_release(self, event_dict)`, `on_mouse_move(self, event_dict)`, `on_key_press(self, event_dict)`, `on_key_release(self, event_dict)`, `on_context_menu(self, event_dict)`.
4. Route the stub methods to the existing handling logic (e.g., if `event_dict["Button"] == "Left"`, call `on_button1_down()`).

---

## Tier 2 — Tool Migration

### R-003: Standardize Coordinate Mapping
**Files:** `core/input_manager.py`, `core/view_projector.py`
**What:** Ensure all code expects Qt (Top-Left, Y=Down) coordinates.
**Implementation:**
1. Remove the Y-axis flipping hack from `get_mouse_pos()` in `input_manager.py`. It should simply return `_last_qt_pos`.
2. Check `core/view_projector.py` and ensure `get_mouse_plane_pt` and `get_mouse_world_pos` correctly map the Qt Y-coordinate to Ray directions (the camera ray logic already does this natively via FreeCAD's `getRay(x, y)` which takes Qt coordinates).

### R-004: Migrate Tools to Qt-Driven Input
**Files:** `tools/primitive_tool.py`, `tools/edit_tool.py`, `tools/manual_nesting_tool.py`, `tools/work_plane_tool.py`
**What:** Update all active tools to expect the new `event_dict` schema.
**Implementation:**
1. The new `event_dict` schema will no longer mimic Coin3D's `BUTTON1` / `DOWN` strings.
2. Adopt a clean schema, e.g.:
   - `event_dict["Button"]` = `QtCore.Qt.LeftButton` / `RightButton` / `MiddleButton`
   - `event_dict["Modifiers"]` = Bitmask from `event.modifiers()`
   - `event_dict["Position"]` = `(x, y)` in Qt space.
3. Replace all checks for `"BUTTON1"`, `"BUTTON3"`, `"ShiftDown"` with the new schema or calls to `DMInputManager.get_instance().is_shift_down()`.

### R-005: Right-Click / Context Menu Fix
**File:** `tools/dm_base.py`
**What:** Leverage the new `on_context_menu()` or strictly tracked `RightButton` press to trigger tool finish logic.
**Implementation:**
1. Move the `finish()` / `terminate()` trigger into `on_mouse_press` (when `RightButton` is detected) OR into `on_context_menu`.
2. Ensure the Qt Event Filter intercepts the right-click sequence completely, returning `True` so the FreeCAD context menu never opens.
