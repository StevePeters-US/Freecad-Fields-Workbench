# todo_input.md — Input Pipeline Refactor and Bug Fixes

Read `.agents/skills/dm_qt_input_architecture/SKILL.md` before starting any task.

---

## Task 1: Restrict input capturing to the 3D viewport `Gemini Flash`

- **Goal**: Ensure that DMInputManager only captures and overrides input when the mouse is over the FreeCAD 3D viewport, preventing bugs where toolbar or menu clicks are intercepted and interpreted as tool clicks with incorrect coordinates.
- **Files to read**: `core/input_manager.py`
- **Files to modify**: `core/input_manager.py`
- **Steps**:
  1. In `DMInputManager.eventFilter(self, obj, event)`, before processing events, verify that `obj` is the viewport widget (e.g. `QuarterWidget`, `SoOpenGLWidget`, `SoQtRenderArea`, or checking against `FreeCADGui.activeView()`).
  2. Alternatively, use standard Qt coordinate mapping (`obj.mapToGlobal(event.pos())`) to ensure the event is taking place within the active 3D view's geometry and discard events outside of it.
  3. Ensure that modifier state tracking (Shift, Ctrl) remains reliable even if the mouse is outside the viewport when the key is pressed.
- **Acceptance**: Clicking on a FreeCAD toolbar button while a tool is active does not place a point or trigger tool logic. Input is only captured over the 3D viewport.

---

## Task 2: Fix mouse pointer offset bug `Gemini Flash`

- **Goal**: Fix the bug where the placed point and mouse pointer are offset, and the offset increases as the mouse moves up and left from the bottom right.
- **Files to read**: `core/input_manager.py` (lines 40-55, 228-268)
- **Files to modify**: `core/input_manager.py`
- **Steps**:
  1. Investigate the `devicePixelRatio` handling in `DMInputManager.eventFilter` and `_get_vp_size`.
  2. The issue occurs because the Qt `event.pos()` logical coordinates and the `vp_h` are in different coordinate scales, so the Y-flip (`int(vp_h) - 1 - y_qt`) produces an incorrect Y coordinate.
  3. If `event.pos()` returns unscaled logical pixels, ensure `_get_vp_size(view)` also strictly returns the viewport height in those same logical units.
  4. Ensure `event.pos()` is accurate to the viewport widget (Task 1 helps this by ensuring `obj` is actually the viewport).
- **Acceptance**: The placed 3D point exactly aligns with the mouse cursor anywhere on the screen (including top-left and bottom-right).

---

## Task 3: Complete single-owner Qt-native input migration (Remove Coin3D) `Gemini Flash`

- **Goal**: Finish removing `SoEventCallback` (Coin3D) from the input pipeline as described in the input refactor skill.
- **Files to read**: `.agents/skills/dm_qt_input_architecture/SKILL.md`, `tools/dm_base.py`, `core/input_manager.py`
- **Files to modify**: `tools/dm_base.py`, `core/input_manager.py`
- **Steps**:
  1. Ensure `DMBase` no longer installs `event_cb` on `self.view`. All input must flow strictly from `DMInputManager` calling `on_mouse_press`, `on_mouse_move`, `on_key_press`, etc.
  2. Remove any lingering Coin3D event handling logic (`SoMouseButtonEvent`, `SoLocation2Event`, `SoKeyboardEvent`) from `dm_base.py` and other tool files.
  3. Validate that modifiers (Shift, Ctrl) are ONLY read from `DMInputManager`, not from Coin3D event dictionaries.
- **Acceptance**: The tools function normally (click to place, drag to size, right-click to finish). Coin3D `SoEventCallback` is completely removed from tool logic.

