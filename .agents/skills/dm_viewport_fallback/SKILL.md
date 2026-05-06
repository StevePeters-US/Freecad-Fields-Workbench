---
name: DM Viewport Fallback Rule
description: Enforces the "No Silent Fallbacks" rule for workplanes when modeling. 
---

# DM Viewport Fallback Rule

## Core Rule: No Silent Fallbacks

This project strictly enforces that tools behavior should never depend on hidden or implicitly cached state that the user cannot see. 

When a user triggers a tool click, the tool casts a ray through the viewport. If the ray hits a face on existing geometry, it aligns the workplane to that face. If it hits an explicit visual `DMWorkPlane`, it snaps to it. 

If the ray hits **empty space**, the fall-back behavior must be completely stateless:
- **CORRECT:** Assume the camera-facing viewport plane passing through the origin (or camera focus).
- **WRONG:** Re-use the "last working plane" that visual tools used on previous clicks. This is a "silent fallback" that leaves the user confused when they start drawing and their object snaps to an invisible angled grid from a previous tool session.

## Subclassing Guidelines

When creating a new modeling tool (e.g. subclassing `PrimitiveCreatorBase` or `NURBSPrimitiveCreator`), you must:
1. NEVER declare or use a class-level variable like `_last_working_plane`.
2. Ensure that empty-space clicks correctly fall back to the viewport aligned plane natively provided by `ViewProjector.get_base_plane()`. 
3. Always trust `get_mouse_plane_pt()`'s fallback; do not attempt to cache it across tool invocations unless an explicit, user-facing persistent object (like a `DMWorkPlane`) dictates it.
