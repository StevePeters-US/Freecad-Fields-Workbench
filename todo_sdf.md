# Direct Modeling Workbench — SDF Renaming Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

The project is moving away from the "SDF" nomenclature towards "SDF" across the codebase.
This involves replacing string references in source files, markdown docs, and agent skills.
The references strictly map as follows (case-sensitive replacements):
- `Sdf` -> `Sdf`
- `SDF` -> `SDF`
- `sdf` -> `sdf`
- `SDF` -> `SDF`

---

## Tier 1 — Automating Replacements (Do First)

These tasks cover running a script or making iterative changes across the `.py` and `.md` files to update content strings.

### S-001: Automate bulk content replacement

**File:** (Create a script at `/tmp/rename_sdf.py` temporarily)

**What:** Write a script to replace Sdf occurrences globally in `.py` and `.md` files under the current directory. Follow the strict replacement map described in the Background. Run this script using `python3 /tmp/rename_sdf.py`. After doing that, discard the script.

**Implementation:**
```python
import os

def replace_in_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        new_content = content.replace('SDF', 'SDF')
        new_content = new_content.replace('Sdf', 'Sdf')
        new_content = new_content.replace('sdf', 'sdf')
        new_content = new_content.replace('SDF', 'SDF')

        if new_content != content:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
    except Exception:
        pass

for root, dirs, files in os.walk('.'):
    for f in files:
        if f.endswith('.py') or f.endswith('.md'):
            replace_in_file(os.path.join(root, f))
```

---

## Tier 2 — File and Folder Renaming

Any remaining references hidden in file names or folder paths must be renamed.

### S-002: Rename related files and folders

**File:** Bash script or `run_command` usage

**What:** Rename the following files and directories containing "sdf".

**Implementation:**
```bash
# Agent Skills
mv .agents/skills/dm_sdf_boolean .agents/skills/dm_sdf_boolean
mv AntiGravity_Skills/sdf_field_implementation.md AntiGravity_Skills/sdf_field_implementation.md
```

**Depends on:** S-001
