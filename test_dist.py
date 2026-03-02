import FreeCAD
import Part

sphere = Part.makeSphere(50)
face = sphere.Faces[0]
hit = FreeCAD.Vector(-28.0, -25.0, 31.0)
v = Part.Vertex(hit)
dists = face.distToShape(v)

print(f"distToShape output length: {len(dists)}")
for i, item in enumerate(dists):
    print(f"[{i}]: {item}")
    
# Layout: (distance, [(pt1, pt2)], [('Face', 0, (u, v), 'Vertex', 0, None)])
p1, p2 = dists[1][0]
print(f"Original hit pt: {hit}")
print(f"Pt1 (from dists[1][0][0]): {p1}")
print(f"Pt2 (from dists[1][0][1]): {p2}")

# So if Pt2 is the point on the sphere, then project_local_pt should be Pt2
