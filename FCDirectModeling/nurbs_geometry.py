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
            for p in filtered_points[:len(fit_pts)]:
                if p.handle_out is not None:
                    v = p.handle_out - p.position
                    if v.Length > 0.0001:
                        tangents.append(v)
                        has_tangents = True
                        continue
                tangents.append(FreeCAD.Vector(0,0,0))
            
            if has_tangents:
                # Different FreeCAD versions have different BSplineCurve.interpolate signatures.
                # Common: (Points, Periodic, Tolerance, Tangents)
                # Common: (Points, Periodic, Tolerance, StartTangent, EndTangent)
                
                try:
                    # Try 1: Full interpolation with tangent list
                    bs.interpolate(fit_pts, is_closed, 0.001, tangents)
                except Exception:
                    try:
                        # Try 2: End tangents only
                        if len(tangents) >= 2:
                            bs.interpolate(fit_pts, is_closed, 0.001, tangents[0], tangents[-1])
                        else:
                            bs.interpolate(fit_pts, is_closed)
                    except Exception:
                        try:
                            # Try 3: Basic
                            bs.interpolate(fit_pts, is_closed)
                        except:
                            pass
            else:
                try:
                    bs.interpolate(fit_pts, is_closed)
                except:
                    try: bs.interpolate(fit_pts)
                    except: pass
            
            # Final validation: check if B-spline is valid for meshing
            if bs.Degree == 0 and len(fit_pts) >= 2:
                 # Fallback to simple polygon if interpolation produced a degenerate curve
                 return Part.makePolygon(fit_pts).toBSpline()

            return bs
        except Exception:
            # Silent fallback to avoid log spam during drag
            try:
                if len(positions) >= 2:
                    wire = Part.makePolygon(positions)
                    return wire.toBSpline()
            except:
                pass
            return None

    def to_shape(self):
        """Returns the curve as a Part.Shape (Compound of Edge, Vertices, and Lines)."""
        shapes = []
        
        # 1. The main B-spline edge
        bs = self.bspline
        if bs:
            try:
                shapes.append(bs.toShape())
            except Exception as e:
                from FCDirectModeling import dm_logger
                dm_logger.debug(f"DEBUG: DMCurve.to_shape edge error: {e}")

        # 2. Control points as vertices (ensure markers are drawn for all pts)
        for p in self.points:
            shapes.append(Part.Vertex(p.position))
            
            # 3. Handle lines and markers for visual feedback & selection
            if p.handle_in:
                shapes.append(Part.makeLine(p.position, p.handle_in))
                shapes.append(Part.Vertex(p.handle_in)) # Marker at end of handle
            if p.handle_out:
                shapes.append(Part.makeLine(p.position, p.handle_out))
                shapes.append(Part.Vertex(p.handle_out)) # Marker at end of handle

        if not shapes:
            return Part.Shape()
        if len(shapes) == 1:
            return shapes[0]
        return Part.Compound(shapes)

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
