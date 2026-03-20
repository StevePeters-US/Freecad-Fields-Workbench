---
name: DM Todo List Format
description: Reference for writing todo_*.md task lists for this project. Required reading before creating a new todo file or adding tasks to an existing one. Covers document structure, task format, dependency ordering, and when to create companion agent skills.
---

# DM Todo List Format

Task lists in this project are structured markdown documents consumed by both human developers
and AI agents (Gemini Flash). Every task must be atomic, self-contained, and include enough
context that the agent never needs to guess what file to edit or what the code should look like.

**Existing examples to read as reference:**
- `todo_meshing.md` — mesh optimization pipeline
- `todo_rendering.md` — GPU ray marching renderer

---

## File Naming

```
todo_{topic}.md
```

One file per coherent feature area. Do not mix unrelated subsystems in one file.

---

## Document Structure

```markdown
# Direct Modeling Workbench — {Topic} Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

{2–4 paragraph explanation of why this work is needed, what the current state is,
and what the goal state looks like after all tasks are done.}

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `SymbolName` | `path/to/file.py:line` | One-line description |
...

---

## Tier 1 — {Name} ({Do First})

{One-sentence description of what this tier accomplishes.}

### {PREFIX}-001: {Task title}
...

### {PREFIX}-002: {Task title}
...

---

## Tier 2 — {Name}

...

---

## Agent Skills

See `.agents/skills/` for project-specific knowledge:

| Skill | Purpose |
|-------|---------|
| `skill_name` | What the agent needs it for |

See `.agents/workflows/` for executable workflows:

| Workflow | Purpose |
|----------|---------|
| `/fix-task` | Fix a single task by ID |
```

---

## Task Format

Each task follows this exact template:

```markdown
### {PREFIX}-{NNN}: {Short imperative title}

**File:** `path/to/file.py` — description of where in the file (line N or "after ClassName.method()")

**What:** One or two sentences describing what this task adds or changes.

**Implementation:**
{Python/GLSL/etc. code block showing exactly what to write, OR a numbered algorithm
when the approach is less obvious than the code. Include enough context that the
agent can paste the code without reading the file first.}

**Depends on:** {PREFIX}-NNN, {PREFIX}-NNN   ← omit this line if no dependencies
```

### Checklist Syntax

Completed tasks are marked with `[x]` in the heading:
```markdown
### [x] M-001: Task that is already done
```

Pending tasks have no prefix (not `[ ]` — just the bare heading).

---

## Task Writing Rules

### 1. Exact file + location references
Always specify:
- The file path relative to the repo root
- Where in the file to insert: line number OR `"after ClassName.method_name() (line N)"`
- Never say "add somewhere in file X" — be precise

Good: `**File:** core/sdf/sdf_field.py — append after line 72 (end of curvature_grid)`
Bad:  `**File:** core/sdf/sdf_field.py`

### 2. Show the code
Every task must include the full code the agent should write, formatted as a code block.
Do not describe the code in prose and expect the agent to derive it.

- For new methods: show the complete method body including the `def` line and docstring
- For algorithm tasks (where multiple correct implementations exist): show the algorithm
  as numbered steps, plus the function signature and any non-obvious data structure
- For GLSL: show the full GLSL snippet, not just a description

### 3. One change per task
Each task touches one logical unit: one method, one class, one file section.
If a task naturally spans two files (e.g. adding a preference accessor + wiring it in),
split it into two tasks with an explicit dependency.

Exception: companion additions that have no value in isolation (e.g. getter + setter pair)
may be combined into one task.

### 4. Dependencies
List dependencies explicitly. An agent will check that all dependencies exist before
starting a task. If the dependency chain is wrong, the agent will stop and report it.

Only list *direct* dependencies — not transitive ones. If C depends on B which depends on A,
only write `Depends on: B` for C.

### 5. Implementation vs. algorithm
Use a code block when there is one clearly correct implementation.
Use a numbered algorithm list when there are multiple valid implementations but you want
to constrain the approach (e.g. for performance or API compatibility reasons).

---

## Tier Structure

Group tasks into tiers by execution order. Agents work top-to-bottom; tier N must be
complete before tier N+1 can start.

**Tier naming conventions:**

| Tier name pattern | Meaning |
|-------------------|---------|
| "Tier 1 — Infrastructure (Do First)" | Primitives used by everything else |
| "Tier 2 — {Core Feature}" | The main deliverable |
| "Tier 3 — {Extension}" | Non-critical improvements |
| "Tier N — {Optimization}" | Performance / quality polish |

Tier intro line (after the heading): one sentence explaining what the tier accomplishes
and what unblocks when it's done.

---

## Task ID Prefix Convention

Pick a 1–2 letter prefix per todo file. Use consistent numbering with zero-padding to 3 digits.

| File | Prefix | Example |
|------|--------|---------|
| `todo_meshing.md` | `M` | `M-001` |
| `todo_rendering.md` | `R` | `R-001` |

When adding tasks to an existing file, continue the sequence (do not reuse IDs of deleted tasks).

---

## Agent Skills — When to Create Them

Create a companion skill file in `.agents/skills/{skill_name}/SKILL.md` when:

1. **Multiple tasks share non-obvious context** — e.g. all meshing tasks need to know
   the output format `(flat_verts, flat_idx)`. Put that in a skill rather than repeating
   it in every task.

2. **The API being used has tricky behavior** — e.g. Coin3D uniform nodes, pivy idioms,
   FreeCAD parameter paths. A skill prevents the agent from guessing at the API.

3. **A new interface is being established** — e.g. `to_glsl()` return format. The skill
   defines the contract so every implementer is consistent.

Do NOT create a skill for:
- Context that only one task needs (put it inline in that task)
- Information already covered by an existing skill (update the existing one)
- General Python or numpy knowledge

### Skill file format

```markdown
---
name: Human-readable skill name
description: One sentence. Used by agents to decide whether to load this skill.
---

# Skill Title

{Concise reference. Not a tutorial. Agents need facts, not explanations.}

## Section

{API signatures, code examples, gotchas}

...

## Files to Read Before Editing

1. `path/to/file.py` — why
...
```

---

## Key APIs Table

The Key APIs table at the top of every todo file maps symbol names to file locations so
an agent can jump directly to the right line without a codebase search.

**What to include:**
- Every function/class the tasks modify or call that is not standard Python
- Every preference accessor the tasks depend on
- The output format of any data pipeline the tasks extend

**Format:** `path/to/file.py:line` where `line` is the line the class or function definition
starts. Keep this table updated when line numbers shift due to earlier insertions.

---

## Background Section

The background should answer:
1. **Current state**: what exists now, what its limitations are
2. **Goal state**: what the world looks like after all tasks complete
3. **Approach**: the high-level algorithm or architecture used (one paragraph, not a tutorial)

Avoid: implementation details that belong in individual tasks, motivational language,
time estimates.

---

## Full Example Task (good)

```markdown
### R-002: Implement `SdfSphereField.to_glsl()`

**File:** `core/sdf/sdf/sphere.py` — append to `SdfSphereField` after `bounding_box()` (line 28)

**What:** Return GLSL for `length(p - center) - radius`.

```python
def to_glsl(self, node_id: str) -> dict:
    uid = f"sphere_{node_id.replace('-', '_')}"
    functions = (
        f"float sdf_{uid}(vec3 p) {{\n"
        f"    return length(p - u_{uid}_center) - u_{uid}_radius;\n"
        f"}}\n"
    )
    uniforms = (
        f"uniform vec3  u_{uid}_center;\n"
        f"uniform float u_{uid}_radius;\n"
    )
    return {
        "functions": functions,
        "uniforms":  uniforms,
        "call":      f"sdf_{uid}(p)",
        "params": {
            f"u_{uid}_center": (self.center.x, self.center.y, self.center.z),
            f"u_{uid}_radius": float(self.radius),
        },
    }
\```

**Depends on:** R-001
```

---

## Full Example Task (bad — do not write tasks like this)

```markdown
### R-002: Add GLSL support to sphere

**File:** `core/sdf/sdf/sphere.py`

**What:** Add a `to_glsl` method that returns the sphere SDF in GLSL format
with uniforms for the center and radius.

**Depends on:** R-001
```

Problems: no line reference, no code shown, agent must derive the entire implementation.
