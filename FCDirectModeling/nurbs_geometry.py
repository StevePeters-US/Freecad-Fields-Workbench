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
    def __init__(self, points=None, bspline=None, degree=3, is_closed=False):
        self._bspline = bspline
        self.points = []
        self._is_closed = is_closed
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
        
        # Periodic logic: OCCT expects N points for a periodic curve of degree k.
        # If the user provided N+1 points where P0 == PN, remove the last one.
        fit_pts = list(positions)
        if is_closed and len(fit_pts) > 2:
            if (fit_pts[0] - fit_pts[-1]).Length < 0.005:
                fit_pts.pop()
        
        n = len(fit_pts)
        filtered_points = filtered_points[:n]
        # 1. Helper for auto-tangents (Catmull-Rom)
        def get_auto_tan(idx):
            prev = fit_pts[(idx - 1) % n] if (is_closed or idx > 0) else (fit_pts[1] - (fit_pts[1]-fit_pts[0]))
            nxt = fit_pts[(idx + 1) % n] if (is_closed or idx < n-1) else (fit_pts[-1] + (fit_pts[-1]-fit_pts[-2]))
            
            # Use chord to keep tangent magnitude proportional
            chord = (fit_pts[(idx+1)%n] - fit_pts[idx%n]).Length if (is_closed or idx < n-1) else (fit_pts[idx] - fit_pts[idx-1]).Length
            return (nxt - prev).normalize() * (chord / 3.0)

        # 2. Build Bezier poles for each cubic segment
        poles = []
        num_segments = n if is_closed else n - 1
        for i in range(num_segments):
            p1 = fit_pts[i]
            p2 = fit_pts[(i + 1) % n]
            
            p1_obj = filtered_points[i]
            p2_obj = filtered_points[(i + 1) % n]
            
            # Bezier pole v1 (leaving p1)
            if p1_obj.handle_out and (p1_obj.handle_out - p1).Length > 1e-4:
                v1 = p1_obj.handle_out
            else:
                v1 = p1 + get_auto_tan(i)
                
            # Bezier pole v2 (entering p2)
            if p2_obj.handle_in and (p2_obj.handle_in - p2).Length > 1e-4:
                v2 = p2_obj.handle_in
            else:
                v2 = p2 - get_auto_tan((i + 1) % n)
            
            poles.extend([p1, v1, v2])
        
        if not is_closed:
            poles.append(fit_pts[-1])

        try:
            degree = 3
            weights = [1.0] * len(poles)
            knots = [float(j) for j in range(num_segments + 1)]
            
            if is_closed:
                mults = [3] * (num_segments + 1)
                bs = Part.BSplineCurve(poles, weights, knots, mults, True, degree)
            else:
                mults = [4] + [3] * (num_segments - 1) + [4]
                bs = Part.BSplineCurve(poles, weights, knots, mults, False, degree)
            
            self._bspline = bs
            return bs
        except Exception as e:
            from FCDirectModeling import dm_logger
            dm_logger.debug(f"DEBUG: Part.BSplineCurve constructor failed: {e}. Falling back to default interpolate.")
            try:
                bs = Part.BSplineCurve()
                bs.interpolate(fit_pts, is_closed)
                self._bspline = bs
                return bs
            except:
                return None
            
            # Final validation
            if bs.Degree == 0 and len(fit_pts) >= 2:
                return Part.makePolygon(fit_pts).toBSpline()

            return bs
        except Exception as e:
            # Silent fallback during drag
            if len(fit_pts) >= 2:
                try:
                    return Part.makePolygon(fit_pts).toBSpline()
                except: pass
            return None

    def to_shape(self):
        """Returns the curve as a Part.Shape (Compound of Edge, Vertices, and Lines)."""
        shapes = []
        
        # 1. The main B-spline edge
        bs = self.bspline
        if bs:
            try:
                # Use Part.Edge(bs) for standard NURBS segment creation
                shapes.append(Part.Edge(bs))
            except Exception as e:
                from FCDirectModeling import dm_logger
                dm_logger.debug(f"DEBUG: DMCurve.to_shape edge error: {e}")

        # 2. Control points as vertices (ensure markers are drawn for all pts)
        for p in self.points:
            shapes.append(Part.Vertex(p.position))
            
            # 3. Handle lines and markers for visual feedback & selection
            # Guard against coincident points which crash Part.makeLine
            if p.handle_in and (p.handle_in - p.position).Length > 1e-4:
                try:
                    shapes.append(Part.makeLine(p.position, p.handle_in))
                    shapes.append(Part.Vertex(p.handle_in))
                except: pass
            if p.handle_out and (p.handle_out - p.position).Length > 1e-4:
                try:
                    shapes.append(Part.makeLine(p.position, p.handle_out))
                    shapes.append(Part.Vertex(p.handle_out))
                except: pass

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
            return self._is_closed
        return self._is_closed

    def value(self, t):
        """Returns the point at normalized parameter t [0, 1]."""
        bs = self.bspline
        if not bs:
            return FreeCAD.Vector(0,0,0)
        # Map [0, 1] to knot range
        u = bs.FirstParameter + t * (bs.LastParameter - bs.FirstParameter)
        return bs.value(u)

    def __repr__(self):
        pts_str = f"{len(self.points)} pts" if self.points else "no pts"
        closed_str = " (closed)" if self.is_closed else ""
        return f"DMCurve({pts_str}){closed_str}"

class DMSurface:
    """NURBS surface from a control point grid."""
    def __init__(self, control_grid, u_degree=3, v_degree=3):
        self.control_grid = control_grid  # List[List[FreeCAD.Vector or DMPoint]]
        self.u_degree = u_degree
        self.v_degree = v_degree

    @classmethod
    def from_boundaries(cls, c1, c2, d1, d2, res=8):
        """
        Create a DMSurface using a bilinear Coons patch from 4 boundary curves.
        c1, c2: V-boundaries at v=0 and v=1 (U directions)
        d1, d2: U-boundaries at u=0 and u=1 (V directions)
        """
        grid = []
        for j in range(res):
            v = j / (res - 1)
            row = []
            for i in range(res):
                u = i / (res - 1)
                
                # Boundaries
                p1 = c1.value(u) # S(u, 0)
                p2 = c2.value(u) # S(u, 1)
                q1 = d1.value(v) # S(0, v)
                q2 = d2.value(v) # S(1, v)
                
                # Corners
                s00 = c1.value(0)
                s10 = c1.value(1)
                s01 = c2.value(0)
                s11 = c2.value(1)
                
                # Bilinear blend formula
                sc = p1 * (1 - v) + p2 * v
                sd = q1 * (1 - u) + q2 * u
                scd = s00 * (1 - u) * (1 - v) + s10 * u * (1 - v) + s01 * (1 - u) * v + s11 * u * v
                
                pos = sc + sd - scd
                row.append(pos)
            grid.append(row)
        return cls(grid)

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
            dm_logger.debug(f"DEBUG: DMSurface interpolate error: {e}")
            return None

    def to_shape(self):
        """Returns the surface as a Part.Shape (Face)."""
        bs = self.to_bspline_surface()
        if bs:
            try:
                return bs.toShape()
            except Exception:
                pass
        return Part.Shape()
