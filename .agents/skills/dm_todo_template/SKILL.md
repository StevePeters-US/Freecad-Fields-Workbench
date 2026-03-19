---
name: DM Gemini Flash Todo Template
description: Reference template for creating atomized task lists for the Gemini Flash AI model. Required reading when creating or updating todo_*.md files.
---

# DM Gemini Flash Todo Template

This skill defines the standardized format for task lists intended for the Gemini Flash model. These lists prioritize clarity, atomicity, and self-contained instructions.

## Template Format

All task lists should follow this structure. Use `[Topic]` to describe the functional area.

```markdown
# todo_{topic}.md — [Topic]

Read `[Relevant Skill Path]` before starting any task.

---

## Task 1: [Task Name] `Gemini Flash`

- **Goal**: [Brief description of what this task aims to achieve]
- **Files to read**: `[List of files and relevant line numbers]`
- **Files to modify**: `[List of files to be changed]`
- **Steps**:
  1. [Step 1 description. Be specific about code changes if known.]
  2. [Step 2 description. Include code snippets where helpful.]
  3. [Step 3 description.]
- **Acceptance**: [Clear criteria for how to test and verify the task is complete and working correctly]

---

## Task 2: [Task Name] `Gemini Flash`

- **Goal**: [Brief description of what this task aims to achieve]
- **Files to read**: `[List of files and relevant line numbers]`
- **Files to modify**: `[List of files to be changed]`
- **Steps**:
  1. [Step 1 description]
  2. [Step 2 description]
- **Acceptance**: [Clear criteria for testing]
```

## Rules for Task Creation

1. **Atomicity**: Each task should be a single, logical change.
2. **Context**: Provide line numbers or specific function/method names for all file references.
3. **Explicit Steps**: Break down the implementation into clear, numbered steps.
4. **Acceptance Criteria**: Define exactly how to verify the change works.
5. **Tagging**: Always include the `Gemini Flash` tag in the task header.
