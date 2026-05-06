---
name: TODO Task Completed
description: Moves a completed task from TODO.md to COMPLETED.md after user verification.
---

# TODO Task Completed

This skill is used when a task from `TODO.md` is finished and the user has verified its completion.

## Instructions

1. **Remove from TODO**: Delete the entire task block from `TODO.md`.
2. **Format for COMPLETED**: Reformat the task for `COMPLETED.md` using the following style:
   ```markdown
   ### ✅ [Task Title]
   - **Completed**: [A brief summary of what was implemented or fixed]
   ```
3. **Insert at the Top**: Open `COMPLETED.md` and insert the newly formatted task at the **top** of the most relevant section (e.g., the current month's section or a new section if appropriate). This ensures that the completed tasks are sorted with the most recent ones first.
