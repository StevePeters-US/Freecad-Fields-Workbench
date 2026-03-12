---
description: Fix a single task from todo_meshing.md by its ID (e.g. /fix-task M-003)
---
# Fix a Task

Fix a single task from any `todo_*.md` file by its ID (e.g. `/fix-task M-003`, `/fix-task X-001`).

## Instructions

1. Identify the correct todo file from the task ID prefix:
   - `M-*` → `todo_meshing.md`
   - `R-*` → `todo_rendering.md`
   - `S-*` → `todo_sdf.md`
   - `X-*` → `todo_refactor.md`
2. Read the todo file and locate the task matching the ID given as the argument.
3. Read the Agent Skills listed at the bottom of that todo file before making any changes.
   Always also read:
   - `.agents/skills/dm_logging/SKILL.md` — logging conventions
4. Read the exact file(s) listed in the task at the specified line numbers.
5. If the task has a **Depends on** field, verify those tasks are already completed
   (the required functions/methods exist in the codebase). If not, report the missing dependency.
6. Apply only the change described in the task — nothing more, nothing less.
7. Use `dm_logger.*` for any log statements (never `print()` or `FreeCAD.Console` directly).
8. After making the change, confirm the task is done by briefly describing what was changed.
9. Do NOT mark the checkbox in the todo file — the user will do that.

## Rules

- Only change what the task specifies. Do not refactor surrounding code.
- If the change would break other code, report the conflict rather than making an unrelated fix.
- If the task references a function or line that does not exist, report it.
- Maintain the existing code style: NumPy vectorization over Python loops, type hints on public methods.
- All new functions must have a docstring explaining parameters and return values.
