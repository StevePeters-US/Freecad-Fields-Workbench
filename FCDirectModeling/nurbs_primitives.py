"""
FCDirectModeling/nurbs_primitives.py

NURBS primitive builders for Direct Modeling.
Each function constructs geometry explicitly from NURBS surfaces (Part.BSplineSurface).
"""

import FreeCAD as App
import Part
import math

def build_box(length, width, height):
    """Build a Box from (0,0,0) to (L,W,H) allowing negative dimensions."""
    if abs(length) < 0.001 or abs(width) < 0.001:
        return Part.Shape()
    
    # Create the base rectangle in XY plane
    p1 = App.Vector(0, 0, 0)
    p2 = App.Vector(length, 0, 0)
    p3 = App.Vector(length, width, 0)
    p4 = App.Vector(0, width, 0)
    wire = Part.makePolygon([p1, p2, p3, p4, p1])
    face = Part.Face(wire)
    
    if abs(height) < 0.001:
        return face.toNurbs()
        
    # Extrude along Z (supports negative height)
    box = face.extrude(App.Vector(0, 0, height))
    return box.toNurbs()

def build_sphere(radius):
    """Build a Sphere centered at (0,0,0) from a NURBS surface."""
    R = abs(radius)
    if R < 0.001:
        return Part.Shape()
    sphere = Part.makeSphere(R)
    return sphere.toNurbs()

def build_cone(radius, height):
    """Build a Cone with base at origin (0,0,0)."""
    R, H = abs(radius), abs(height)
    if R < 0.001:
        return Part.Shape()
        
    if H < 0.001:
        # Return base circle as face
        face = Part.makeCircle(R, App.Vector(0,0,0), App.Vector(0,0,1))
        return face.toNurbs()

    # radius1=radius, radius2=0 (apex)
    cone = Part.makeCone(R, 0, H)
    return cone.toNurbs()

def build_torus(major_r, minor_r):
    """Build a Torus centered at (0,0,0) from a NURBS surface."""
    R, r = abs(major_r), abs(minor_r)
    if R < 0.001 or r < 0.001:
        return Part.Shape()
    
    # Ensure R > r for a standard torus
    if R < r:
         R, r = r, R
         
    torus = Part.makeTorus(R, r)
    return torus.toNurbs()

def build_curve(points, periodic=False):
    """Build a NURBS curve (BSpline) passing through a list of points.
    If the first and last points are the same, it creates a closed face.
    """
    if len(points) < 2:
        return Part.Shape()
    
    try:
        # Check if closed (first and last points are very close)
        is_closed = False
        if len(points) >= 3:
            d = (points[0] - points[-1]).Length
            if d < 0.001:
                is_closed = True
                periodic = True # Ensure BSpline is periodic if points are same

        # Create a BSpline using points as fit points (interpolate)
        bspline = Part.BSplineCurve()
        
        # If is_closed, we should probably remove the redundant last point 
        # for bspline.interpolate(..., periodic=True)
        fit_points = list(points)
        if is_closed and len(fit_points) > 3:
            fit_points.pop() # Remove the overlap point for periodic interpolation
            
        bspline.interpolate(fit_points, periodic)
        shape = bspline.toShape()
        
        if is_closed:
            # Try to build a face if it's closed
            try:
                wire = Part.Wire(shape.Edges)
                face = Part.Face(wire)
                return face.toNurbs()
            except Exception:
                return shape
        
        return shape
    except Exception:
        from FCDirectModeling import dm_logger
        dm_logger.exception("build_curve failed")
        # Fallback to simple polygon/wire
        return Part.makePolygon(points)
