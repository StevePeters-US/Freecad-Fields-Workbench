
import FreeCAD
import Part
import FCDirectModeling.sdf_lib as sdf_lib
import numpy as np

def create_smooth_loft(sdf, axis='z', slices=10):
    """
    Attempt to create a lofted surface from SDF slices.
    """
    print(f"Generating {slices} slices along {axis}...")
    
    # 1. Get Contours
    # contours_from_sdf returns a list of polylines (Nx3 arrays)
    contours = sdf_lib.contours_from_sdf(sdf, resolution=64, axis=axis, slices=slices)
    
    wires = []
    
    for i, polyline in enumerate(contours):
        if len(polyline) < 3:
            continue
            
        # Create FreeCAD Points
        points = [FreeCAD.Vector(p[0], p[1], p[2]) for p in polyline]
        
        # Close the loop
        points.append(points[0])
        
        # Create B-Spline Curve (Periodic/Closed)
        try:
            # approx = Part.BSplineCurve()
            # approx.approximate(points, Parameters=..., Continuity=...) 
            # Part.BSplineCurve(points, Periodic=True) might interpret as control points or interpolate
            
            # Interpolate is usually 'Part.BSplineCurve.interpolate' or passing logic
            # Let's try creating a Polygon then converting/fitting
            
            # Simple approach: Polygon -> BSpline
            # poly = Part.makePolygon(points)
            # bspline = poly.toShape().toNurbs() # This usually just makes linear segments
            
            # Better: Direct BSpline interpolation
            bspline = Part.BSplineCurve()
            bspline.interpolate(points)
            
            if bspline.isClosed():
                 wire = Part.Wire(bspline.toShape())
                 wires.append(wire)
            else:
                 # Check if start/end match, force closed?
                 # If interpolate didn't close it, maybe explicit closing needed
                 pass
                 
        except Exception as e:
            print(f"Slice {i} failed: {e}")

    if not wires:
        print("No valid wires generated.")
        return None

    print(f"Generated {len(wires)} wires.")
    
    # 2. Loft
    try:
        # loft = Part.makeLoft(wires, True, True) # solid=True, ruled=True/False
        # Use ruled=False for smoothing
        loft = Part.makeLoft(wires, True, False)
        return loft
    except Exception as e:
        print(f"Loft failed: {e}")
        return None

def test_conversion():
    doc = FreeCAD.newDocument("NurbsTest")
    
    # Create a test SDF (Sphere? Box?)
    # Let's use a Sphere (Capsule zero length) or just the Box we have
    sdf = sdf_lib.SDFBox(20.0) # 20x20x20
    
    # Create Loft
    shape = create_smooth_loft(sdf, slices=15)
    
    if shape:
        obj = doc.addObject("Part::Feature", "LoftedSDF")
        obj.Shape = shape
        print("Loft Created Successfully")
        
    # Also test Mesh -> Shape (Faceted)
    mesh_verts, mesh_faces, _ = sdf_lib.mesh_from_sdf(sdf, resolution=32)
    if mesh_verts is not None:
        # Create Mesh Object
        import Mesh
        m = Mesh.Mesh()
        for f in mesh_faces:
             # f is indices [v1, v2, v3]
             v1 = FreeCAD.Vector(*mesh_verts[f[0]])
             v2 = FreeCAD.Vector(*mesh_verts[f[1]])
             v3 = FreeCAD.Vector(*mesh_verts[f[2]])
             m.addFacet(v1, v2, v3)
             
        # Convert to Part
        # This is the "Faceted" option
        shape_faceted = Part.Shape()
        shape_faceted.makeShapeFromMesh(m.Topology, 0.1) # Tolerance
        
        obj2 = doc.addObject("Part::Feature", "FacetedSDF")
        obj2.Shape = shape_faceted
        obj2.ViewObject.Visibility = False
        print("Faceted Shape Created")

test_conversion()
