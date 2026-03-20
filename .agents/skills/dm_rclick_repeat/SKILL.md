---
name: DM Right-Click Close
description: Pattern for single-RMB tool close. Required reading before editing on_button3_down or any tool's finish() method.
---

# DM Right-Click Close

## Design Contract

| Action | Behavior |
|--------|----------|
| Right-click (any state) | Commit in-progress work (if any), then always terminate |
| Enter key | Commit in-progress work; tool may stay alive (subclass decides) |
| ESC key | Terminate without committing |

## Single-RMB Close Pattern

`DMBase.on_button3_down` (tools/dm_base.py) is the sole entry point for right-click on all
tools. It uses a deferred closure so `finish()` and `terminate()` run outside the Qt event
dispatch stack:

```python
def on_button3_down(self, event_dict):
    if not getattr(self, '_finish_scheduled', False):
        self._finish_scheduled = True
        def _rclick_close():
            if self.is_in_progress():
                self.finish()      # subclass commit (may internally call terminate — safe)
            self.terminate()       # always close; _terminated guard prevents double-fire
        QtCore.QTimer.singleShot(0, _rclick_close)
    return True
```

## `_terminated` Guard

`terminate()` sets `self._terminated = True` immediately and returns early if already set.
Calling `terminate()` after `finish()` (which may itself call `terminate()`) is always safe.

## Tool Overrides

Do **not** add an `on_button3_down` override to a tool unless it has unique commit semantics
that cannot be expressed by overriding `finish()`. The base-class implementation applies to
all tools. If a tool previously had an override (e.g. `FRepEditTool`), remove it.

## finish() vs terminate()

| Method | Commits | Stays alive |
|--------|---------|-------------|
| `finish()` | Yes | Depends on tool (subclass may call `reset_state()` to stay alive, or `terminate()`) |
| `terminate()` | No | No |

The RMB close pattern calls both: `finish()` commits, then `terminate()` ensures the tool
always exits regardless of what `finish()` did internally.

## Files to Read Before Editing

1. `tools/dm_base.py` — `on_button3_down` (line 625), `terminate` (line 402), `is_in_progress` (line 513)
2. `tools/edit_tool.py` — `FRepEditTool.on_button3_down` (line 665, to be removed per I-002)
3. `tools/primitive_tool.py` — `SdfPrimitiveCreator.finish` (line 157); calls `reset_state()` not `terminate()`
