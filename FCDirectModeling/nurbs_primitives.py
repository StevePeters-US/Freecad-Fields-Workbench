"""
FCDirectModeling/nurbs_primitives.py

NURBS curve builders for Direct Modeling.
Each function constructs geometry explicitly from NURBS surfaces (Part.BSplineSurface).
"""

import FreeCAD as App
import Part
import math

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
