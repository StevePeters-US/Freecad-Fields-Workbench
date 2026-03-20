# Direct Modeling Workbench — Input Behavior Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

**Single right-click closes the tool.** Currently, right-clicking an in-progress tool calls
`finish()` (which commits the object and resets to idle state), and a _second_ right-click
calls `terminate()`. The desired behavior is one right-click: commit if in progress, then
always terminate.

The primary change is in `DMBase.on_button3_down` (tools/dm_base.py), which is the single
entry point for right-click across all tools. A secondary fix removes `FRepEditTool`'s
overriding `on_button3_down` so the base-class behavior applies uniformly.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `DMBase.on_button3_down` | `tools/dm_base.py:625` | Right-click handler for all tools |
| `DMBase.finish` | `tools/dm_base.py:509` | Commit current work; subclasses override |
| `DMBase.terminate` | `tools/dm_base.py:402` | Async cleanup entry point (sets `_terminated=True`, schedules `_do_terminate`) |
| `DMBase.is_in_progress` | `tools/dm_base.py:513` | Returns True if state > 0 or `_is_editing` |
| `FRepEditTool.on_button3_down` | `tools/edit_tool.py:665` | Override that must be removed |

---

## Tier 1 — Single Right-Click Close

Fixes the two-click-to-close behavior. After these tasks, one right-click always commits
work and terminates the tool.

### I-001: Change `DMBase.on_button3_down` to always terminate on single right-click

**File:** `tools/dm_base.py` — replace the entire `on_button3_down` method (lines 625–635)

**What:** Replace the current two-step logic (finish → then separately terminate) with a
single closure that calls `finish()` if in progress and then always calls `terminate()`.
`terminate()` has a `_terminated` guard, so calling it after `finish()` (which may already
call `terminate()` internally) is always safe.

**Implementation:**

```python
def on_button3_down(self, event_dict):
    if not getattr(self, '_finish_scheduled', False):
        self._finish_scheduled = True
        def _rclick_close():
            if self.is_in_progress():
                dm_logger.debug(f"{self.__class__.__name__}: RMB commit+close")
                self.finish()  # subclass commit logic (may internally terminate — safe)
            else:
                dm_logger.debug(f"{self.__class__.__name__}: RMB close (idle)")
            self.terminate()  # always close; _terminated guard prevents double-fire
        QtCore.QTimer.singleShot(0, _rclick_close)
    return True  # Consume Press
```

---

### I-002: Remove `FRepEditTool.on_button3_down` override in edit_tool.py

**File:** `tools/edit_tool.py` — delete lines 665–677 (the entire `on_button3_down` method
on `FRepEditTool`)

**What:** `FRepEditTool` has its own `on_button3_down` that bypasses the base-class fix from
I-001. The override's time-based double-fire guard (`_last_btn3_time`) is a legacy artifact
from the old dual Qt+Coin3D pipeline; the current Qt-native pipeline fires each event once.
Removing the override lets `DMBase.on_button3_down` handle right-clicks for `FRepEditTool`
with the same single-click close behavior.

**Implementation:**

Delete these lines in their entirety from `FRepEditTool`:

```python
    def on_button3_down(self, event_dict):
        # Double-fire guard: Right-click arrives via both Qt and Coin3D.
        import time
        now = time.monotonic()
        if now - getattr(self, "_last_btn3_time", 0.0) < 0.05:
            return True
        self._last_btn3_time = now

        if self.is_in_progress():
             self.finish()
        else:
             self.terminate()
        return True
```

After deletion, the next method in the class should be `def finish(self):` (currently at line 679).

**Depends on:** I-001

---

## Agent Skills

See `.agents/skills/` for project-specific knowledge:

| Skill | Purpose |
|-------|---------|
| `dm_rclick_repeat` | Single-RMB close pattern; files to read |
| `dm_qt_input_architecture` | Qt-native event pipeline; ShortcutOverride, dispatch |
| `dm_tool_refactor_pattern` | DragTimerMixin, state constants, tool lifecycle |
| `dm_input_refactor` | Current vs. target input architecture |
