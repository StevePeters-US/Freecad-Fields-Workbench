---
description: Fix a single task from todo_meshing.md by its ID (e.g. /fix-task M-003)
---
# Fix a Task

Fix a single task from `todo_meshing.md` by its ID (e.g. `/fix-task M-003`).

## Instructions

1. Read `todo_meshing.md` and locate the task matching the ID given as the argument.
2. Read the required agent skill(s) listed below before making any changes:
   - `.agents/skills/dm_mesher_architecture/SKILL.md` — mesher class hierarchy, output format, timer instrumentation
   - `.agents/skills/dm_logging/SKILL.md` — logging conventions
3. Read the exact file(s) listed in the task at the specified line numbers.
4. If the task has a **Depends on** field, verify those tasks are already completed
   (the required functions/methods exist in the codebase). If not, report the missing dependency.
5. Apply only the change described in the task — nothing more, nothing less.
6. Wrap any new meshing stages with `mesh_timer.start(stage)` / `mesh_timer.stop(stage)`
   using the prefix convention from the skill doc.
7. Use `dm_logger.*` for any log statements (never `print()` or `FreeCAD.Console` directly).
8. After making the change, confirm the task is done by briefly describing what was changed.
9. Do NOT mark the checkbox in `todo_meshing.md` — the user will do that.

## Rules

- Only change what the task specifies. Do not refactor surrounding code.
- If the change would break other code, report the conflict rather than making an unrelated fix.
- If the task references a function or line that does not exist, report it.
- Maintain the existing code style: NumPy vectorization over Python loops, type hints on public methods.
- All new functions must have a docstring explaining parameters and return values.
