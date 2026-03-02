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
        """Returns True if the point has no handles or handles are at the position (G0)."""
        if self.handle_in is None and self.handle_out is None:
            return True
        # Check distance to avoid "near-zero" handle issues
        dist_in = (self.handle_in - self.position).Length if self.handle_in else 0
        dist_out = (self.handle_out - self.position).Length if self.handle_out else 0
        return dist_in < 1e-4 and dist_out < 1e-4

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

        # 1. Faster path: If no points have handles, use native OCCT interpolation
        has_handles = any(not p.is_sharp() for p in filtered_points)
        
        if not has_handles and len(fit_pts) >= 2:
            try:
                # Try most compatible way: empty constructor + interpolate
                # If interpolate() takes keyword 'PeriodicFlag', use it, else positional
                bs = Part.BSplineCurve()
                try:
                    bs.interpolate(fit_pts, PeriodicFlag=is_closed)
                except TypeError:
                    bs.interpolate(fit_pts, is_closed)
                self._bspline = bs
                return bs
            except Exception as e:
                from . import dm_logger
                dm_logger.debug(f"DEBUG: Native interpolate failed: {e}. Falling back to manual pole building.")
        
        # 2. Handle path: Build Bezier poles for each cubic segment (preserved for explicit tangent control)
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
            else:
                mults = [4] + [3] * (num_segments - 1) + [4]

            # Try the complex constructor (FreeCAD 0.21+)
            try:
                bs = Part.BSplineCurve(poles, weights, knots, mults, is_closed, degree)
            except Exception:
                # Fallback to buildFromPolesMultsKnots method (more compatible)
                bs = Part.BSplineCurve()
                if hasattr(bs, "buildFromPolesMultsKnots"):
                    bs.buildFromPolesMultsKnots(poles, mults, knots, is_closed, degree, weights)
                else:
                    # Last resort: simple constructor + interpolate (might lose tangent info)
                    try:
                        bs = Part.BSplineCurve(poles, is_closed, degree, True) # Some old versions use 4th arg for interp
                    except:
                        # Fallback to pure interpolation on fit_pts
                        bs.interpolate(fit_pts, is_closed)
            
            self._bspline = bs
            return bs
        except Exception as e:
            from . import dm_logger
            dm_logger.debug(f"DEBUG: DMCurve construction failed: {e}")
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
        """Returns the curve as a Part.Shape (Edge). Control points move to Coin3D overlay."""
        bs = self.bspline
        if bs:
            try:
                # Return only the edge. Markers/handles are now handled by DMViewProvider overlay.
                return Part.Edge(bs)
            except Exception as e:
                from . import dm_logger
                dm_logger.debug(f"DEBUG: DMCurve.to_shape edge error: {e}")

        # Fallback if no BSpline could be built (e.g. 1 point)
        if len(self.points) == 1:
            return Part.Vertex(self.points[0].position)
            
        return Part.Shape()

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


