---
name: README Generator
description: Generates or updates the project README.md with an overall snapshot of the project, its folder structure, and an overview of the math.
---

# README Generator

This skill is used to generate or drastically update a project's `README.md` file. The goal is to present a high-level, human-readable snapshot of what the project is, how it is organized, and the underlying mathematical principles it relies on. 

## Instructions

When asked to update or generate the README, follow these steps to create a comprehensive yet concise document:

1. **Overall Project Snapshot**:
   - Begin with a clear, engaging title and a strong 1-2 paragraph introduction summarizing the project's purpose and main features. 
   - Ensure you read existing documentation or the `TODO.md` to capture the current state and overarching goal or philosophy of the software.

2. **Folder Structure Overview**:
   - Create a clean ASCII or Markdown visual representation of the repository's directory structure.
   - Annotate the most important folders and files so a new developer understands where key logic resides (e.g., `core/` for base classes, `tools/` for user interactions).

3. **Mathematical Overview**:
   - Provide a section detailing any core mathematical concepts the project relies heavily on (e.g., Raycasting, SDFs (Signed Distance Fields), NURBS evaluation, matrix transformations).
   - Explain these concepts briefly in plain English, using code snippets or pseudo-formulas only if they aid understanding. 
   - The goal is not to prove theories, but to give incoming developers enough context to read the code without being completely lost.

4. **Human-Readable Presentation**:
   - Keep the tone professional but welcoming. 
   - Use Markdown headers, bullet points, and code blocks effectively to break up walls of text. 
   - Ensure the final output fits seamlessly into `README.md`.
