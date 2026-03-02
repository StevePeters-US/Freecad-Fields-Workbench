import FreeCAD
import FreeCADGui

# Create a sphere
doc = FreeCAD.newDocument()
import Part
sphere = doc.addObject("Part::Sphere", "Sphere")

# Gui init requires an active view
dir_view = dir(FreeCADGui.ActiveDocument.ActiveView) if FreeCADGui.ActiveDocument else []
print("View methods:", [m for m in dir_view if "Object" in m or "pick" in m.lower()])
