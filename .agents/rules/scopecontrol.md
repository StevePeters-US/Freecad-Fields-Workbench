---
trigger: model_decision
description: when the user asks to stay on task
---

# Strict Scope Control
- **No Side Effects:** Perform *only* the specific change requested. 
- **Prohibited Actions:** Do not refactor adjacent code, do not "improve" style, and do not add error handling unless explicitly asked.
- **Negative Constraint:** If a task requires modifying File A, do not touch File B under any circumstances.
- **Verification:** Before implementing, if the task is ambiguous, ask for clarification instead of guessing.