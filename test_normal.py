import FreeCAD
import Part
import math

# Create a sphere (radius 10)
sphere = Part.makeSphere(10)

# Pick a point on the spherical surface (e.g. angle pi/4)
x = 10 * math.cos(math.pi/4)
y = 10 * math.sin(math.pi/4)
z = 0
pt = FreeCAD.Vector(x, y, z)

face = sphere.Faces[0] # Spherical face
print(f"Point on sphere: {pt}")

# 1. Parameter method
try:
    u, v = face.Surface.parameter(pt)
    n1 = face.Surface.normal(u, v)
    print(f"Method 1 (parameter): Normal: {n1}")
except Exception as e:
    print(f"Method 1 failed: {e}")

# 2. distToShape method
try:
    dists = face.distToShape(Part.Vertex(pt))
    print(f"Method 2 (distToShape) output structure:")
    print(dists)
except Exception as e:
    print(f"Method 2 failed: {e}")

# 3. normalAt method (taking u, v)
try:
    n3 = face.normalAt(u, v)
    print(f"Method 3 (normalAt u,v): Normal: {n3}")
except Exception as e:
    print(f"Method 3 (u,v) failed: {e}")

# 4. normalAt method (taking x, y, z)
try:
    n4 = face.normalAt(pt.x, pt.y, pt.z)
    print(f"Method 4 (normalAt x,y,z): Normal: {n4}")
except Exception as e:
    print(f"Method 4 (x,y,z) failed: {e}")
