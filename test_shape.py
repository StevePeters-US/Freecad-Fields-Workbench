import FreeCAD, Part
doc = FreeCAD.newDocument()
box = doc.addObject("Part::Box", "Box")
box.Placement.Base = FreeCAD.Vector(10, 0, 0)
doc.recompute()
print("Box Placement:", box.Placement.Base)
print("Shape Placement:", box.Shape.Placement.Base)
print("Vertex 0:", box.Shape.Vertexes[0].Point)

line = Part.makeLine(FreeCAD.Vector(0,0,0), FreeCAD.Vector(1,1,1))
fp = doc.addObject("Part::Feature", "Line")
fp.Shape = line
fp.Placement.Base = FreeCAD.Vector(10, 0, 0)
doc.recompute()
print("Line Placement:", fp.Placement.Base)
print("Line Shape Placement:", fp.Shape.Placement.Base)
print("Line Edge Vertex 0:", fp.Shape.Edges[0].Vertexes[0].Point)
print("Line Edge Curve Start:", fp.Shape.Edges[0].Curve.StartPoint)
