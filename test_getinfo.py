import FreeCAD
import FreeCADGui
import Part

print("\n\n=== RUNNING getObjectsInfo TEST ===")
# Create Doc
doc = FreeCAD.newDocument()
box = doc.addObject("Part::Box", "Box")
box.Placement.Base = FreeCAD.Vector(10, 10, 10)  # Move box
doc.recompute()

# We need a view, so we will use FreeCADGui active view if possible
# But we are in console mode. Can we test getObjectsInfo without GUI?
# Maybe just run this script in FreeCAD GUI via macro or something.
# We'll just ask the user or print a statement about FreeCAD API.
