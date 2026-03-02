"""
Run this as a FreeCAD Macro: Macro -> Macros -> test_hitinfo.py
Hover the mouse over the sphere, then run this macro to print the getObjectsInfo dict.
"""
import FreeCAD
import FreeCADGui

view = FreeCADGui.ActiveDocument.ActiveView
cursor = view.getCursorPos()

print(f"getCursorPos(): {cursor}")

# Try both cursor positions
infos = view.getObjectsInfo(cursor)
print(f"\ngetObjectsInfo(getCursorPos()) returned:")
if infos:
    for info in infos:
        for k, v in info.items():
            print(f"  {k}: {v}")
else:
    print("  None")
