"""
Run this as a FreeCAD Macro to test ray casting
Position mouse over sphere before running.
"""
import FreeCAD
import FreeCADGui
import Part

view = FreeCADGui.ActiveDocument.ActiveView
doc = FreeCAD.ActiveDocument

# Get cursor position
cursor = view.getCursorPos()
print(f"getCursorPos (Qt): {cursor}")

# Try getPoint (working plane intersection)
gp = view.getPoint(*cursor)
print(f"getPoint: {gp}")

# Get the sphere object
sphere_obj = doc.getObject("Sphere")
if sphere_obj:
    # Try casting a ray from camera through cursor into shape
    # Camera ray: origin and direction
    cam_node = view.getCameraNode()
    cam_pos = FreeCAD.Vector(*cam_node.position.getValue().getValue())
    
    # The direction from cam to mouse point
    ray_dir = (gp - cam_pos)
    ray_dir.normalize()
    
    print(f"Camera pos: {cam_pos}")
    print(f"Ray direction: {ray_dir}")
    
    # Cast ray against shape
    # Build a long line from camera through the point
    far = cam_pos + ray_dir * 5000
    ray_shape = Part.makeLine(tuple(cam_pos), tuple(far))
    
    try:
        inter = sphere_obj.Shape.section(ray_shape)
        print(f"Intersection vertices: {inter.Vertexes}")
        for v in inter.Vertexes:
            print(f"  Intersection point: {v.Point}")
    except Exception as e:
        print(f"section() error: {e}")
    
    # Also try distToShape with the ray
    try:
        dists = sphere_obj.Shape.distToShape(Part.Vertex(tuple(gp)))
        print(f"\ndistToShape against whole shape:")
        if dists:
            print(f"  pt on sphere: {dists[1][0][0]}")
    except Exception as e:
        print(f"distToShape error: {e}")
