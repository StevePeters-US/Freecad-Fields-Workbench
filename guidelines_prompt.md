# Prompt: Generate Project Documentation

You are maintaining three documentation files for a FreeCAD workbench project. Read the entire codebase first, then generate/update each file according to the rules below.

---

## File 1: `PROJECT_GUIDELINES.md`

**Purpose**: The single source of truth for how the project works and how to contribute.

**You MUST include these sections** (in this order):

1. **Project Description** — 1–2 paragraphs explaining what the workbench does, its core technology (SDF-based modeling), and key differentiators.

2. **Folder Structure** — A `tree`-style diagram of every directory and file, with a one-line comment explaining each. Keep it current with the actual filesystem.

3. **Workbench Goals** — Numbered list of high-level objectives (e.g., "Use SDF as core representation", "Represent final geometry as NURBS patches"). These are aspirational and stable — they rarely change.

4. **Workbench Toolbar Layout** — Tables listing every toolbar command grouped by category:
   - **Creation (Primitives)**: existing commands with their `FreeCADGui.addCommand` IDs.
   - **Future Creation Tools**: planned tools not yet implemented (mark clearly).
   - **Operations**: boolean, array, transform commands.
   - **Settings**: DM Settings dialog.

5. **DM Settings** — Table of all user-configurable settings, including: setting name, widget type, range/options, default value, and description. Reference the file that implements each.

6. **SDF Rendering Pipeline** — ASCII diagram showing the data flow from user input → SDF function → voxel grid → meshing algorithm → mesh object. List the key files involved.

7. **Code Formatting & Style** — Project-specific coding conventions:
   - Python version target
   - PEP 8 deviations
   - Import ordering (FreeCAD → PySide → project → stdlib)
   - Naming conventions
   - Docstring style
   - FreeCAD property usage

8. **Logging** — How to use `sdf_logger`, with code examples. Include a table of log modes (normal vs crash investigation) and best practices.

9. **Event Safety** — The `QTimer.singleShot(0, fn)` rule for all document mutations. List what counts as a document mutation.

10. **Terminology** — Define project-specific terms (Place/Size/Set, Preview vs Final, etc.) in tables.

11. **Dependencies** — Table of required packages.

12. **Future Work** — Grouped by theme (NURBS export, mesh↔SDF, curve extraction, rendering). Keep brief — detailed steps belong in `TODO.md`.

**Rules**:
- Read every `.py` file in the project before writing. The guidelines must reflect the actual code, not assumptions.
- Use markdown tables for structured data, code blocks for examples.
- Keep each section concise — link to source files rather than duplicating code.
- Horizontal rules (`---`) between major sections.

---

## File 2: `TODO.md`

**Purpose**: A task list where each task is self-contained enough for any LLM or developer to complete without additional context.

**Structure instructions** (include these verbatim at the top of the file):

```markdown
## How to Write a Task

Each task must be **self-contained** so an LLM or developer can complete it with
no prior context beyond the files listed. Follow this template:

- [ ] **Task Title** (Complexity: N/10)
  - **Goal**: One sentence describing the desired outcome.
  - **Files to read**: List every file the implementer must read first.
  - **Files to modify/create**: List files that will change.
  - **Steps**:
    1. First concrete step…
    2. Second step…
  - **Acceptance**: How to verify the task is done.

- Mark in-progress tasks `[/]`, completed tasks `[x]`.
- Move completed tasks to `COMPLETED.md` when a milestone is reached.
- Sort by complexity within each section (lowest first).
```

**Rules for writing tasks**:
- **Sort all tasks by complexity** (lowest number first, 1/10 → 10/10).
- **Include full context** in each task:
  - List every file the implementer must read (with line numbers for specific functions when helpful).
  - Describe what pattern to follow (e.g., "see the existing `get_show_wireframe` / `set_show_wireframe` getter/setter pair").
  - Include small code snippets showing the exact change when the task is simple.
  - Name the exact function signatures and return types expected.
- **Acceptance criteria must be testable** — describe a concrete action and expected result.
- **One task = one concern.** If a task touches 3+ unrelated files, split it.
- **Code review the codebase first.** Look for:
  - Bugs (e.g., checking for attributes that don't exist on the current class).
  - Inconsistencies (e.g., `print()` instead of logger).
  - Stubs/fallthrough branches (e.g., algorithm selector that always calls the same function).
  - Missing features referenced in the UI but not implemented.

**Complexity guide**:
| Complexity | Description | Example |
|------------|-------------|---------|
| 1–2 | Single-file find-and-replace | Replace `print()` with `sdf_logger.debug()` |
| 3–4 | Add a setting, wire a new branch | Add a checkbox to the settings dialog |
| 5–6 | Multi-file feature, moderate logic | Fix preview viewport update, implement Marching Cubes |
| 7–8 | New module, algorithm, or pipeline | SDF to NURBS conversion, Dual Contouring |
| 9–10 | Architectural change, research-heavy | Full sketch-to-SDF-to-NURBS pipeline |

---

## File 3: `COMPLETED.md`

**Purpose**: Archive of completed tasks, so the TODO stays clean.

**Format**:

```markdown
## ✅ Task Title
- **Completed**: Which file(s) implemented it.
- **Result**: One sentence describing the outcome.
```

**Rules**:
- Move tasks here from `TODO.md` once verified.
- Keep entries short — just enough to know what was done and where.
- Group by milestone or date if the list grows large.

---

## How to Use This Prompt

1. Copy this entire file into a new LLM conversation.
2. Provide the LLM with the full project codebase (or let it read the files).
3. Ask: "Generate `PROJECT_GUIDELINES.md`, `TODO.md`, and `COMPLETED.md` following the instructions in this prompt."
4. To update later: provide the existing files + any new code changes, and ask the LLM to update all three files.
