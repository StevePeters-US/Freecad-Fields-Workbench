# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Read [AGENTS.md](AGENTS.md) for full project architecture, conventions, known bugs, and agent skills.

## Claude-Specific Context

`memory/MEMORY.md` is auto-loaded into every Claude Code session. It contains session history, confirmed bug details, renderer optimization notes, and the planned file list for upcoming features. Treat it as the authoritative record of what has been decided or discovered across sessions.

When you learn something new that should persist — a confirmed bug, a validated pattern, an architectural decision — update `memory/MEMORY.md` rather than leaving it in conversation context only.
