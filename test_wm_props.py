import FreeCAD

doc = FreeCAD.newDocument()
from FCDirectModeling.dm_workplane import create_dm_workplane
wp = create_dm_workplane("TestWP")

print("WP Properties:", wp.PropertiesList)
print("Base:", wp.Placement.Base)
print("Rotation:", wp.Placement.Rotation.Q)
