---
name: README Folder Structure Updater
description: Updates the Folder Structure section of the README.md to accurately reflect the current project repository.
---

# README Folder Structure Updater

This skill ensures that the "Folder Structure" section of the project's `README.md` is kept up-to-date with the actual directories and files in the repository.

## Instructions

When asked to update the folder structure in the README, follow these steps:

1. **Analyze Current Structure**:
   - Use the necessary file system tools (e.g., `list_dir`) to explore the project directory, focusing on the root folder and primary subdirectories (like `core`, `tools`, `commands`, etc.).
   - Identify any new modules, files, or removed old files.

2. **Format the Structure Output**:
   - Format the gathered directory structure into a clear ASCII or Markdown tree.
   - Example formatting:
     ```
     Project-Root/
     ├── core/                          # Base logic and backend systems
     │   ├── example.py                 # Core processing file
     ├── tools/                         # User-facing interactive tools
     │   └── tool_base.py               # Abstract class for all tools
     └── README.md                      # Project documentation
     ```
   - Make sure to keep or add helpful inline comments (`# ...`) for the most important files or new additions so a developer knows what they do at a glance.

3. **Update README.md**:
   - Locate the "Folder Structure" section in `README.md`.
   - Replace the outdated ASCII/Markdown tree with the newly generated, accurate one.
   - Do not alter other sections of the README unless explicitly asked.
