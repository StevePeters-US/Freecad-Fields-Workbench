---
name: DM Logging
description: Guidelines for logging in the Direct Modeling workbench. Required reading before adding log statements to any DM module.
---

# DM Logging Skill

All logging in the Direct Modeling workbench goes through `core/dm_logger.py`.
Never call `FreeCAD.Console.Print*` directly — use the `dm_logger` module.

---

## API Reference

```python
from core import dm_logger

dm_logger.log(msg)          # FreeCAD.Console.PrintLog — only visible in Report View with "Log" enabled
dm_logger.info(msg)         # FreeCAD.Console.PrintMessage — always visible
dm_logger.debug(msg)        # FreeCAD.Console.PrintMessage with [DEBUG] prefix
dm_logger.warn(msg)         # FreeCAD.Console.PrintWarning — yellow in Report View
dm_logger.error(msg)        # FreeCAD.Console.PrintError — red in Report View
dm_logger.exception(header) # Captures current traceback, logs as ERROR
dm_logger.debug_throttled(key, msg, interval=0.5)  # Rate-limited debug (e.g. mouse move handlers)
```

All functions also write to `~/.FreeCAD/DirectModeling.log` when crash logging
is enabled (`EnableCrashLog` preference, default `True`). `ERROR` level always
writes to the file regardless of the preference.

---

## Log Categories

### 1. Verbose / Performance Logs (Persistent)

- **Purpose:** Give the user insight into performance and internals.
- **Convention:** Gate behind a preference flag (e.g. `get_perf_profiler_enabled()`).
- **Example:** The `MeshTimer.summary()` in `dm_mesher.py` only emits `[PERF]` logs
  when profiling is enabled.
- Use `dm_logger.info()` for these — they should be visible in the Report View
  but only when the user has opted in.

### 2. Transient / Progress Logs (Disposable)

- **Purpose:** Status updates during active operations (dragging, meshing preview).
- **Convention:** Use `dm_logger.debug_throttled()` to avoid flooding the console.
- Rate-limit to ≥ 0.5s intervals for mouse-driven updates.
- These should not persist after the operation completes.

### 3. Error Logs (Always Active)

- **Purpose:** Capture failures for debugging.
- **Convention:** Always use `dm_logger.error()` or `dm_logger.exception()`.
- Always write to the log file (even if crash logging is disabled).
- Include enough context to reproduce: function name, key parameters, traceback.

---

## Best Practices

- **Do not use `print()`.** It goes to stdout which may not be visible in FreeCAD.
- **Do not use `FreeCAD.Console.Print*` directly.** Use `dm_logger.*` for consistent formatting and file logging.
- **Prefix context in messages** when the source isn't obvious:
  `dm_logger.warn(f"[AdaptiveMC] Cell count exceeds limit: {n_cells}")`
- **Multi-line messages are supported.** Pass a string with `\n` and each line
  will be formatted and logged individually.
- **Never log inside tight loops** (per-cell, per-vertex). Use aggregated summaries
  (like `MeshTimer`) or `debug_throttled()` instead.

---

## File

`core/dm_logger.py` — 92 lines, no dependencies beyond `FreeCAD`, `os`, `time`, `traceback`.
