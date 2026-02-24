import FreeCAD
import Part
from FCDirectModeling.dm_part import create_dm_part

doc = FreeCAD.newDocument()
box = Part.makeBox(10, 10, 10)

dm_obj = create_dm_part("MyBox")
dm_obj.Shape = box

print("Successfully created DM Part with box shape.")
