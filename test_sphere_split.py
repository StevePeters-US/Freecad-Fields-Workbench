import FreeCAD
import Part

R = 10.0

s_list = []
outer_faces = []

# u from 0 to 360 in steps of 90:
for u in [0, 90, 180, 270]:
    for v_pairs in [ (0, 90), (-90, 0) ]:
        wedge = Part.makeSphere(R, FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), v_pairs[0], v_pairs[1], 90)
        
        rot = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), u)
        pl = FreeCAD.Placement(FreeCAD.Vector(0,0,0), rot)
        wedge.transformShape(pl.toMatrix())
        
        s_list.append(wedge)

        for f in wedge.Faces:
            if "Sphere" in str(type(f.Surface)):
                outer_faces.append(f)

print("Outer faces:", len(outer_faces))
shell = Part.Shell(outer_faces)
solid = Part.Solid(shell)
print("Solid from shell isValid:", solid.isValid(), "volume:", solid.Volume, "faces:", len(solid.Faces))
