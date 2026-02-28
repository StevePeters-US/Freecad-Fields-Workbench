import FreeCAD
import Part

class DMPoint:
    """3D point with optional control handles for NURBS curves."""
    def __init__(self, position, handle_in=None, handle_out=None, weight=1.0):
        if isinstance(position, DMPoint):
            self.position = FreeCAD.Vector(position.position)
            self.handle_in = FreeCAD.Vector(position.handle_in) if position.handle_in else None
            self.handle_out = FreeCAD.Vector(position.handle_out) if position.handle_out else None
            self.weight = position.weight
        else:
            self.position = FreeCAD.Vector(position)
            self.handle_in = FreeCAD.Vector(handle_in) if handle_in is not None else None
            self.handle_out = FreeCAD.Vector(handle_out) if handle_out is not None else None
            self.weight = weight

    def to_vector(self):
        """Return the position as a FreeCAD.Vector."""
        return FreeCAD.Vector(self.position)

    def is_sharp(self):
        """Returns True if the point has no handles (G0 continuity)."""
        return self.handle_in is None and self.handle_out is None

    def __repr__(self):
        return f"DMPoint({self.position.x:.2f}, {self.position.y:.2f}, {self.position.z:.2f})"

class DMCurve:
    """
    NURBS curve wrapper for Part.BSplineCurve.
    Can be initialized with a sequence of DMPoint/Vector objects or a Part.BSplineCurve.
    """
    def __init__(self, points=None, bspline=None, degree=3):
        self._bspline = bspline
        self.points = []
        if points:
            for p in points:
                if isinstance(p, DMPoint):
                    self.points.append(p)
                else:
                    self.points.append(DMPoint(p))
        self.degree = degree
        self.metadata = {}

    @property
    def bspline(self):
        """The underlying Part.BSplineCurve."""
        if self._bspline is None and self.points:
            self._bspline = self._build_from_points()
        return self._bspline

    def _build_from_points(self):
        """Construct a BSplineCurve from the points list."""
        if len(self.points) < 2:
            return None

        # Filter out consecutive duplicate points (crashes OCCT interpolate/build)
        filtered_points = []
        for p in self.points:
            if not filtered_points or (p.position - filtered_points[-1].position).Length > 0.0001:
                filtered_points.append(p)
        
        if len(filtered_points) < 2:
            return None

        positions = [p.to_vector() for p in filtered_points]
        is_closed = self.is_closed
        
        try:
            # For periodic interpolation, OCCT expects the last point NOT to be a repeat of the first
            fit_pts = list(positions)
            is_closed = self.is_closed
            if is_closed and len(fit_pts) > 2:
                if (fit_pts[0] - fit_pts[-1]).Length < 0.001:
                    fit_pts.pop()
            
            bs = Part.BSplineCurve()
            
            # Prepare tangents if any points have them
            tangents = []
            has_tangents = False
            for p in filtered_points:
                if p.handle_out:
                    t = p.handle_out - p.position
                    tangents.append(t)
                    has_tangents = True
                else:
                    tangents.append(FreeCAD.Vector(0,0,0))
            
            if has_tangents and not is_closed:
                # OCCT interpolate can take tangents: (points, periodic, tolerance, tangents)
                # But some versions expect a specific format. Let's stick to points for now 
                # unless we have a guaranteed working signature.
                # Actually, try points only if it fails with tangents.
                try:
                    bs.interpolate(fit_pts, is_closed)
                except:
                    bs.interpolate(fit_pts)
            else:
                bs.interpolate(fit_pts, is_closed)
            return bs
        except Exception as e:
            from FCDirectModeling import dm_logger
            dm_logger.debug(f"DEBUG: DMCurve interpolate fallback (points={len(positions)}, closed={is_closed}): {e}")
            # Fallback: create a BSpline from the polygon wire
            try:
                wire = Part.makePolygon(positions)
                if hasattr(wire, "toBSpline"):
                    return wire.toBSpline()
                # If toBSpline is missing, try creating a B-spline from points manually
                return Part.BSplineCurve(positions, is_closed)
            except Exception as e2:
                dm_logger.debug(f"DEBUG: DMCurve ultimate fallback failed: {e2}")
                return None

    def to_shape(self):
        """Returns the curve as a Part.Shape (Edge)."""
        bs = self.bspline
        if bs:
            try:
                return bs.toShape()
            except Exception as e:
                from FCDirectModeling import dm_logger
                dm_logger.debug(f"DEBUG: DMCurve.to_shape error: {e}")
        return Part.Shape()

    @property
    def is_closed(self):
        """Returns True if the start and end points are the same."""
        if len(self.points) < 2:
            if self._bspline:
                return self._bspline.isClosed()
            return False
        return (self.points[0].position - self.points[-1].position).Length < 0.001

    def __repr__(self):
        pts_str = f"{len(self.points)} pts" if self.points else "no pts"
        closed_str = " (closed)" if self.is_closed else ""
        return f"DMCurve({pts_str}){closed_str}"

class DMPatch:
    """NURBS surface from a control point grid."""
    def __init__(self, control_grid, u_degree=1, v_degree=1):
        self.control_grid = control_grid  # List[List[DMPoint]] — rows x cols
        self.u_degree = u_degree
        self.v_degree = v_degree

    def to_bspline_surface(self):
        """Returns a Part.BSplineSurface by interpolating the control grid."""
        if not self.control_grid or len(self.control_grid) < 2 or len(self.control_grid[0]) < 2:
            return None
            
        points_grid = []
        for row in self.control_grid:
            points_grid.append([p.to_vector() if hasattr(p, "to_vector") else FreeCAD.Vector(p) for p in row])
            
        try:
            bs = Part.BSplineSurface()
            bs.interpolate(points_grid)
            return bs
        except Exception as e:
            from FCDirectModeling import dm_logger
            dm_logger.debug(f"DEBUG: DMPatch interpolate error: {e}")
            return None

    def to_shape(self):
        """Returns the patch as a Part.Shape (Face)."""
        bs = self.to_bspline_surface()
        if bs:
            try:
                return bs.toShape()
            except Exception:
                pass
        return Part.Shape()
