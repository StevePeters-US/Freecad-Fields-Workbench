import FreeCAD
import Part

# Reconstruct a generic sphere
sphere = Part.makeSphere(50)
face = sphere.Faces[0]

# Hit point from user's screen
hit = FreeCAD.Vector(-28.64, -25.06, 31.97)

# normalAt
# The problem might be normalAt returns normal for closest point?
try:
    n1 = face.normalAt(hit.x, hit.y, hit.z)
    print(f"Normal at {hit}: {n1}")
except Exception as e:
    print(f"normalAt failed: {e}")

# distToShape
try:
    dists = face.distToShape(Part.Vertex(hit))
    info_tuple = dists[2][0]
    u, v = info_tuple[2]
    n2 = face.Surface.normal(u, v)
    print(f"Normal via distToShape: {n2}")
    print(f"u: {u}, v: {v}")
except Exception as e:
    print(f"distToShape failed: {e}")

# manual check
try:
    print(f"Manual normalized pt: {hit.normalize()}")
except Exception as e:
    pass
