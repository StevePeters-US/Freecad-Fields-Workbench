"""
FCDirectModeling/nurbs_primitives.py

NURBS primitive builders for Direct Modeling.
Each function constructs geometry explicitly from NURBS surfaces (Part.BSplineSurface).
"""

import FreeCAD as App
import Part
import math

def build_box(length, width, height):
    """Build a Box starting at (0,0,0)."""
    if abs(length) < 0.001 or abs(width) < 0.001:
        return Part.Shape()
    
    if abs(height) < 0.001:
        # Return a planar face for preview
        face = Part.makePlane(length, width, App.Vector(0,0,0), App.Vector(0,0,1))
        return face.toNurbs()
        
    box = Part.makeBox(length, width, height)
    nurbs_box = box.toNurbs()
    if not nurbs_box or nurbs_box.isNull():
        from FCDirectModeling import dm_logger
        dm_logger.error(f"build_box: toNurbs() returned NULL for {length}x{width}x{height}")
    return nurbs_box

def build_sphere(radius):
    """Build a Sphere centered at (0,0,0) from a NURBS surface."""
    if abs(radius) < 0.001:
        return Part.Shape()
    sphere = Part.makeSphere(radius)
    return sphere.toNurbs()

def build_cone(radius, height):
    """Build a Cone with base at origin (0,0,0)."""
    if abs(radius) < 0.001:
        return Part.Shape()
        
    if abs(height) < 0.001:
        # Return base circle as face
        face = Part.makeCircle(radius, App.Vector(0,0,0), App.Vector(0,0,1))
        return face.toNurbs()

    # radius1=radius, radius2=0 (apex)
    cone = Part.makeCone(radius, 0, height)
    return cone.toNurbs()

def build_torus(major_r, minor_r):
    """Build a Torus centered at (0,0,0) from a NURBS surface."""
    if abs(major_r) < 0.001 or abs(minor_r) < 0.001:
        return Part.Shape()
    torus = Part.makeTorus(major_r, minor_r)
    return torus.toNurbs()
