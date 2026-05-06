---
description: Test and fix box tool workplane respect
---
# Testing and Fixing Box Tool Workplane

1. Open FreeCAD with the Direct Modeling workbench.
2. Create a new document and add a Work Plane. 
3. Rotate and angle the Work Plane relative to the global axes.
4. Select the Work Plane so it is the active one.
5. Activate the Box primitive tool.
6. Click and draw a box on the angled Work Plane.
7. Verify that the box aligns to the Work Plane's coordinates (both placement and extrusion direction).
8. If the box still aligns to the global axes, inspect and modify `BoxCreator` in `tools/primitive_tool.py` to correctly multiply its dimensions and placement by the Work Plane's transformation matrix.
9. Rerun steps 1-7 to confirm the fix.
