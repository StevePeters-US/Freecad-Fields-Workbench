import FreeCAD
import Part

sphere = Part.makeSphere(50)
face = sphere.Faces[0]
hit = FreeCAD.Vector(-28.64, -25.06, 31.97)

print("Hit point:", hit)
print("Manual normalized:", hit.normalize())

try:
    u, v = face.Surface.parameter(hit)
    n = face.Surface.normal(u, v)
    print(f"Surface.parameter normal: {n} (u={u}, v={v})")
except Exception as e:
    print(f"parameter failed: {e}")

try:
    dists = face.distToShape(Part.Vertex(hit))
    info_tuple = dists[2][0]
    u, v = info_tuple[2]
    n2 = face.Surface.normal(u, v)
    print(f"distToShape normal: {n2} (u={u}, v={v})")
except Exception as e:
    print(f"distToShape failed: {e}")

