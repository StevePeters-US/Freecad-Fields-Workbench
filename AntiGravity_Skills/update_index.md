---
name: Project Index Updater
description: Updates the INDEX.md file at repo root to ensure file mappings, classes, and skills are accurate.
---

# Project Index Updater

This skill ensures that `INDEX.md` remains a reliable source of truth for agents, reducing the need for expensive discovery tool calls.

## Instructions

When you add, rename, or delete a file, class, or skill, you MUST update `INDEX.md`.

### 1. Update File Map
If you added/moved/deleted a file in `core/`, `tools/`, or `commands/`, update the **File Map** section. 
- Use the `Role` column to briefly describe what the file is for.

### 2. Update Class → File Index
If you added/renamed/deleted a class, update the **Class → File Index** section.
- You can find all classes in a file using: `grep "^class " path/to/file.py`

### 3. Update Skill Dispatch Table
If you created a new skill in `.agents/skills/`, add it to the **Skill Dispatch Table**.
- Provide a clear "If you are about to..." trigger description.

### 4. Update Known Bugs
If you discovered a new bug that is non-trivial or likely to be encountered by other agents, add it to the **Known Bugs** table with a one-liner fix hint.

### 5. Update Development Patterns
If project-wide conventions change (e.g., new logging rules, new event handling requirements), update the **Development Patterns** section.

---

## Verification
After updating, verify that all links or paths in the tables are valid and that the class mappings are correct.
