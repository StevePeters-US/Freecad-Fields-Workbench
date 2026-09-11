# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
import Part
import math
from freecad.fields.core import fld_logger
from freecad.fields.core.input.input_manager import FldInputManager

class ViewProjector:
    """Handles projection of 2D screen coordinates to 3D world space."""

    def __init__(self, view):
        self.view = view

    def get_ray(self, event_dict=None):
        """Centralized ray generation from screen coordinates."""
        im = FldInputManager.get_instance()
        view = self.view
        if not view: return None, None
        pos = im.get_mouse_pos(event_dict)
        x, y_qt = pos[0], pos[1]

        x_phys, y_phys = im.get_gl_pos_phys(view, event_dict)
        if x_phys is None: return None, None

        try:
            # 1. Try FreeCAD's native getRay (0.20+)
            if hasattr(view, "getRay"):
                ray = view.getRay(x_phys, y_phys)
                if ray:
                    if isinstance(ray, dict):
                        b = FreeCAD.Vector(ray["base"])
                        d = FreeCAD.Vector(ray["dir"])
                        d.normalize()
                        return (b, d)
                    elif isinstance(ray, tuple):
                        b = FreeCAD.Vector(ray[0])
                        d = FreeCAD.Vector(ray[1])
                        d.normalize()
                        return (b, d)

            # 2. Support view.getPoint fallback (used by WorkPlaneManager)
            # We synthesize a ray direction from camera to focus point if getPoint is used
            scene_pt = None
            try: scene_pt = view.getPoint(x_phys, y_phys)
            except Exception as e:
                fld_logger.debug(f"view.getPoint fallback failed: {e}")

            cam = view.getCameraNode()
            if not cam: return None, None

            p = cam.position.getValue()
            ray_p = FreeCAD.Vector(p[0], p[1], p[2])

            if scene_pt:
                if hasattr(cam, "height"):
                    # Orthographic: all rays are parallel to the view direction.
                    # scene_pt is already the correct lateral position; use it as
                    # the ray origin so the plane intersection is exact.
                    vd = view.getViewDirection()
                    ray_d = FreeCAD.Vector(vd[0], vd[1], vd[2])
                    ray_d.normalize()
                    return scene_pt, ray_d
                else:
                    # Perspective: ray goes from camera through scene_pt.
                    ray_d = scene_pt - ray_p
                    ray_d.normalize()
                    return ray_p, ray_d

            # 3. Pure Math Fallback (Directly from Camera)
            rot = cam.orientation.getValue()
            vp_sz = im._get_vp_size(view)
            w, h = vp_sz if vp_sz else (1000.0, 1000.0)

            aspect = w / h
            quat = rot.getValue()
            qx, qy, qz, qw = quat[0], quat[1], quat[2], quat[3]

            # Forward vector
            fx, fy, fz = 2.0*(qx*qz + qw*qy), 2.0*(qy*qz - qw*qx), 1.0 - 2.0*(qx*qx + qy*qy)
            forward = FreeCAD.Vector(-fx, -fy, -fz)
            # Up vector
            ux, uy, uz = 2.0*(qx*qy - qw*qz), 1.0 - 2.0*(qx*qx + qz*qz), 2.0*(qy*qz + qw*qx)
            up = FreeCAD.Vector(ux, uy, uz)
            # Right vector
            rx, ry, rz = 1.0 - 2.0*(qy*qy + qz*qz), 2.0*(qx*qy + qw*qz), 2.0*(qx*qz - qw*qy)
            right = FreeCAD.Vector(rx, ry, rz)

            if hasattr(cam, "heightAngle"): # Perspective
                ha = cam.heightAngle.getValue()
                # Path 3 NDC formula expects y-from-top (Qt convention), use y_qt not flipped y
                ndc_x, ndc_y = (x/w)*2.0 - 1.0, 1.0 - (y_qt/h)*2.0
                plane_h = math.tan(ha/2.0); plane_w = plane_h * aspect
                ray_d = forward + right*(ndc_x*plane_w) + up*(ndc_y*plane_h)
                ray_d.normalize()
                return ray_p, ray_d
            elif hasattr(cam, "height"): # Ortho
                height = cam.height.getValue(); width = height * aspect
                # Path 3 NDC formula expects y-from-top (Qt convention), use y_qt not flipped y
                ndc_x, ndc_y = (x/w)*2.0 - 1.0, 1.0 - (y_qt/h)*2.0
                ray_p_ortho = ray_p + right*(ndc_x*width/2.0) + up*(ndc_y*height/2.0)
                forward.normalize()  # modifies in-place; returns None - do not use return value
                return ray_p_ortho, forward

        except Exception as e:
            fld_logger.debug(f"ViewProjector.get_ray failed: {e}")
        return None, None

    def project_to_screen(self, world_pt):
        """World point -> (x, y_qt) logical pixel coordinates, or None.

        The algebraic inverse of get_ray()'s "Pure Math Fallback" branch (same
        camera position/orientation quaternion -> forward/up/right, and the
        same perspective heightAngle / ortho height + viewport-size math, run
        forward instead of backward). Do NOT use view.getPointOnScreen() here
        -- it returns coordinates outside the viewport in this FreeCAD build
        (see feedback_verify_by_clicking memory / OG-015).

        Returns None for a point behind the camera in perspective mode, where
        screen position is undefined.
        """
        im = FldInputManager.get_instance()
        view = self.view
        if not view:
            return None
        try:
            cam = view.getCameraNode()
            if not cam:
                return None
            p = cam.position.getValue()
            cam_pos = FreeCAD.Vector(p[0], p[1], p[2])

            vp_sz = im._get_vp_size(view)
            w, h = vp_sz if vp_sz else (1000.0, 1000.0)
            aspect = w / h

            rot = cam.orientation.getValue()
            quat = rot.getValue()
            qx, qy, qz, qw = quat[0], quat[1], quat[2], quat[3]

            fx, fy, fz = 2.0*(qx*qz + qw*qy), 2.0*(qy*qz - qw*qx), 1.0 - 2.0*(qx*qx + qy*qy)
            forward = FreeCAD.Vector(-fx, -fy, -fz)
            ux, uy, uz = 2.0*(qx*qy - qw*qz), 1.0 - 2.0*(qx*qx + qz*qz), 2.0*(qy*qz + qw*qx)
            up = FreeCAD.Vector(ux, uy, uz)
            rx, ry, rz = 1.0 - 2.0*(qy*qy + qz*qz), 2.0*(qx*qy + qw*qz), 2.0*(qx*qz - qw*qy)
            right = FreeCAD.Vector(rx, ry, rz)

            wp = FreeCAD.Vector(world_pt)

            if hasattr(cam, "heightAngle"):  # Perspective
                d = wp - cam_pos
                fwd_comp = d.dot(forward)
                if fwd_comp <= 1e-9:
                    return None  # behind the camera
                d_scaled = d * (1.0 / fwd_comp)
                ha = cam.heightAngle.getValue()
                plane_h = math.tan(ha / 2.0)
                plane_w = plane_h * aspect
                ndc_x = d_scaled.dot(right) / plane_w
                ndc_y = d_scaled.dot(up) / plane_h
            elif hasattr(cam, "height"):  # Ortho
                v = wp - cam_pos
                height = cam.height.getValue()
                width = height * aspect
                ndc_x = 2.0 * v.dot(right) / width
                ndc_y = 2.0 * v.dot(up) / height
            else:
                return None

            x = (ndc_x + 1.0) * 0.5 * w
            y_qt = (1.0 - ndc_y) * 0.5 * h
            return (x, y_qt)
        except Exception as e:
            fld_logger.debug(f"ViewProjector.project_to_screen failed: {e}")
        return None

    def px_per_world(self, ref_pt):
        """Return screen pixels per world unit at ref_pt in the active view."""
        im = FldInputManager.get_instance()
        view = self.view
        if not view:
            return 10.0
        cam = view.getCameraNode()
        if not cam:
            return 10.0

        vp_sz = im._get_vp_size(view)
        vp_h = vp_sz[1] if vp_sz else 1000.0

        try:
            if hasattr(cam, "height") and hasattr(cam.height, "getValue"):
                v = cam.height.getValue()
                if isinstance(v, (int, float)):
                    half_world_h = float(v) / 2.0
                else:
                    half_world_h = 100.0
            elif hasattr(cam, "heightAngle") and hasattr(cam.heightAngle, "getValue"):
                v = cam.heightAngle.getValue()
                if isinstance(v, (int, float)):
                    cam_p_vals = cam.position.getValue()
                    cam_pos = FreeCAD.Vector(float(cam_p_vals[0]), float(cam_p_vals[1]), float(cam_p_vals[2]))
                    depth = (FreeCAD.Vector(ref_pt) - cam_pos).Length
                    fov = float(v)
                    half_world_h = depth * math.tan(fov / 2.0)
                else:
                    half_world_h = 100.0
            else:
                half_world_h = 100.0
        except Exception:
            half_world_h = 100.0

        try:
            val = float(half_world_h)
        except Exception:
            val = 100.0

        return (vp_h / 2.0) / max(val, 1e-6)

    def get_projected_point(self, base_point_3d, normal_3d, event_dict):
        """
        Calculates the 3D point along (base_point_3d + t*normal_3d) that corresponds
        to the current mouse position.  Uses a screen-space projection so it is 1:1
        with mouse movement in both perspective and orthographic views.

        Algorithm:
          1. Derive camera right/up vectors from the camera's orientation quaternion.
          2. Project the 3D normal into screen-space (right, up) components.
          3. Compute pixels-per-world-unit scale from camera FOV/height.
          4. Measure mouse delta since drag start along the projected normal direction.
          5. Convert pixel delta → world-space offset along normal → return new point.
        """
        im = FldInputManager.get_instance()
        view = self.view
        if not view: return base_point_3d

        try:
            normal_3d_copy = FreeCAD.Vector(normal_3d)
            n_len = normal_3d_copy.Length
            if n_len < 1e-10:
                return base_point_3d
            normal_3d_copy.normalize()

            # --- Step 1: Camera basis vectors from orientation quaternion ---
            cam = view.getCameraNode()
            if not cam:
                return base_point_3d

            rot = cam.orientation.getValue()
            qx, qy, qz, qw = rot.getValue()
            # Right (X screen axis) and Up (Y screen axis) in world space
            ux = 2*(qx*qy - qw*qz); uy = 1 - 2*(qx*qx + qz*qz); uz = 2*(qy*qz + qw*qx)
            rx = 1 - 2*(qy*qy + qz*qz); ry = 2*(qx*qy + qw*qz); rz = 2*(qx*qz - qw*qy)
            cam_right = FreeCAD.Vector(rx, ry, rz)
            cam_up    = FreeCAD.Vector(ux, uy, uz)

            # --- Step 2: Project the world normal into screen-space ---
            # scr_nx = how much the normal points in the screen-right direction
            # scr_ny = how much it points in the screen-up direction (positive = up)
            scr_nx = normal_3d_copy.dot(cam_right)
            scr_ny = normal_3d_copy.dot(cam_up)
            # Qt Y is positive-DOWN, so negate the up component for pixel space
            scr_ny_px = -scr_ny   # screen-up world → screen-top Qt pixel direction

            scr_len_ndc = math.sqrt(scr_nx*scr_nx + scr_ny*scr_ny)
            if scr_len_ndc < 1e-6:
                return base_point_3d  # Normal points straight at camera – degenerate

            # --- Step 3: Pixels-per-world-unit scale ---
            px_per_world = self.px_per_world(base_point_3d)

            # scr_len in pixels for 1 world unit along normal
            scr_len_px = scr_len_ndc * px_per_world

            # --- Step 4: Mouse pixel delta since drag start ---
            curr_pos = im.get_mouse_pos(event_dict)
            drag_start = event_dict.get("DragStart2D") if event_dict else None
            if drag_start is None:
                drag_start = curr_pos   # No anchor → zero delta (safe fallback)

            dx = curr_pos[0] - drag_start[0]          # positive = mouse moved right
            dy = curr_pos[1] - drag_start[1]          # positive = mouse moved down (Qt)

            # Project pixel delta onto screen-space normal direction
            # (scr_nx in right direction, scr_ny_px in down direction)
            scr_len_unit = math.sqrt(scr_nx*scr_nx + scr_ny_px*scr_ny_px)
            if scr_len_unit < 1e-9:
                return base_point_3d
            pixel_delta = (dx * scr_nx + dy * scr_ny_px) / scr_len_unit

            # --- Step 5: Convert to world offset ---
            world_delta = pixel_delta / max(scr_len_px, 1e-6)

            return base_point_3d + normal_3d_copy * world_delta

        except Exception as e:
            fld_logger.debug(f"ViewProjector.get_projected_point failed: {e}")
        return base_point_3d

    def get_axis_point(self, base, normal, event_dict):
        """
        Returns the point on the axis (base + t * normal) that is closest to
        the mouse ray.  This is the geometrically correct way to drag out a
        height: the result literally sits where the cursor points in 3D space,
        independent of camera angle or zoom level.

        Uses the standard closest-point-of-two-skew-lines formula:
            w = ray_origin - base
            b = ray_dir · normal
            s = (w·normal - b*(w·ray_dir)) / (1 - b²)
            result = base + s * normal
        """
        ray_p, ray_d = self.get_ray(event_dict)
        if ray_p is None or ray_d is None:
            return base
        try:
            n = FreeCAD.Vector(normal)
            n.normalize()

            w = ray_p - base          # vector from axis origin to ray origin
            b = ray_d.dot(n)          # cos(angle) between ray dir and axis
            denom = 1.0 - b * b       # sin²(angle); zero when parallel

            if abs(denom) < 1e-8:
                # Axis is pointing straight at the camera - no depth info.
                return base

            e = w.dot(n)
            d = w.dot(ray_d)
            s = (e - b * d) / denom   # signed distance along axis

            return base + n * s

        except Exception as ex:
            fld_logger.debug(f"ViewProjector.get_axis_point failed: {ex}")
            return base

    def _get_view_ray(self, x, y):
        """Delegate ray acquisition to FldInputManager."""
        # Use clean standardized "Position" key for the event_dict
        return self.get_ray({"Position": (x, y)})

    def _intersect_ray_plane(self, ray_p, ray_d, plane_normal, plane_point):
        """Standard Ray-Plane intersection. Returns Vector or None."""
        try:
            denom = ray_d.dot(plane_normal)
            if abs(denom) > 1e-6:
                t = (plane_point - ray_p).dot(plane_normal) / denom
                return ray_p + ray_d * t
        except Exception as e:
            fld_logger.debug(f"Ray-plane intersection failed: {e}")
        return None

    def _get_geometry_point(self, event_dict, skip_names=None):
        x, y = FldInputManager.get_instance().get_gl_pos_phys(self.view, event_dict)
        if x is None: return None
        try:
            infos = []
            if hasattr(self.view, "getObjectsInfo"):
                infos = self.view.getObjectsInfo((x, y))
            else:
                single_info = self.view.getObjectInfo((x, y))
                infos = [single_info] if single_info else []

            if not infos:
                infos = []

            for info in infos:
                if not info or "Object" not in info or "Component" not in info:
                    continue
                obj_name = info["Object"]
                if skip_names and obj_name in skip_names:
                    continue
                doc = FreeCAD.ActiveDocument
                obj = doc.getObject(obj_name) if doc else None
                if not obj or not obj.Shape:
                    continue
                    
                subname = info["Component"]
                if "Face" in subname:
                    # Prefer FreeCAD's own pick coordinates (already in world space,
                    # always on the visible front surface). Only fall back to manual
                    # ray-section when they are absent.
                    if 'x' in info and 'y' in info and 'z' in info:
                        return FreeCAD.Vector(info['x'], info['y'], info['z'])

                    if obj.Shape.isNull():
                        continue

                    # Fallback: ray-section in LOCAL object space (handles any Placement)
                    try:
                        face = obj.Shape.getElement(subname)
                    except Exception as e:
                        fld_logger.debug(f"ViewProjector._get_geometry_point: getElement failed for {subname}: {e}")
                        continue # Stale subname (e.g. Face1 on empty/different shape)

                    import Part
                    ray_p, ray_d = self._get_view_ray(x, y)
                    if ray_p and ray_d:
                        ray_d.normalize()
                        gpl = obj.getGlobalPlacement() if hasattr(obj, "getGlobalPlacement") else obj.Placement
                        gpl_inv = gpl.inverse()
                        local_near = gpl_inv.multVec(ray_p + ray_d * 1.0)
                        local_far  = gpl_inv.multVec(ray_p + ray_d * 100000.0)
                        ray_wire = Part.makeLine(tuple(local_near), tuple(local_far))
                        try:
                            inter = face.section(ray_wire)
                            if inter.Vertexes:
                                best_local = min(inter.Vertexes, key=lambda v: (v.Point - local_near).Length).Point
                                return gpl.multVec(best_local)
                        except Exception as e:
                            fld_logger.debug(f"ViewProjector._get_geometry_point: ray_wire section failed: {e}")
                            continue
                            
            return None
        except Exception as e:
            fld_logger.debug(f"_get_geometry_point failed: {e}")
            return None

    def get_surface_hit(self, event_dict, skip_names=None):
        """The point where the cursor ray meets real geometry -- an SDF surface or a
        B-Rep face -- or None when it meets nothing.

        This is the "did I hit anything" question, and `get_mouse_world_pos` cannot
        answer it: even with place_on_geometry=True it falls through to a plane
        through the origin (below), so it always returns a point. A caller that
        needs a miss to stay a miss -- a sculpt brush deciding whether to stamp --
        must ask here instead.
        """
        sdf_hit = self.get_sdf_hit(event_dict)
        if sdf_hit:
            return sdf_hit[0]
        return self._get_geometry_point(event_dict, skip_names=skip_names)

    def get_mouse_world_pos(self, event_dict, plane_normal=None, plane_point=None, place_on_geometry=False):
        if not self.view:
            return None

        pos = FldInputManager.get_instance().get_mouse_pos(event_dict)
        x, y = pos[0], pos[1]
        
        # 0. Intercept if place on geometry is active, bypassing plane intersection
        if place_on_geometry:
            geom_pt = self._get_geometry_point(event_dict)
            if geom_pt:
                return geom_pt
            sdf_hit = self.get_sdf_hit(event_dict)
            if sdf_hit:
                return sdf_hit[0]
                
        # 1. Plane Intersection
        if plane_normal is not None and plane_point is not None:
            ray_p, ray_d = self._get_view_ray(x, y)
            if ray_p and ray_d:
                pt = self._intersect_ray_plane(ray_p, ray_d, plane_normal, plane_point)
                if pt:
                    return pt

        # 2. Viewport fallback plane if no specific plane was specified
        ray_p, ray_d = self._get_view_ray(x, y)
        if ray_p and ray_d:
            cam_dir = self.get_camera_direction()
            if cam_dir:
                pt = self._intersect_ray_plane(ray_p, ray_d, cam_dir, FreeCAD.Vector(0, 0, 0))
                if pt:
                    return pt

        return None

    def get_camera_direction(self):
        """Returns the camera viewing direction as a normalized FreeCAD.Vector, or (0, 0, -1)."""
        if self.view and hasattr(self.view, "getViewDirection"):
            try:
                vd = FreeCAD.Vector(self.view.getViewDirection())
                vd.normalize()
                return vd
            except Exception as e:
                fld_logger.debug(f"ViewProjector.get_camera_direction failed: {e}")
        return FreeCAD.Vector(0, 0, -1)

    def get_visible_workplanes(self):
        """Returns a list of all visible FldWorkPlane objects in the document."""
        planes = []
        try:
            doc = FreeCAD.ActiveDocument
            if self.view and hasattr(self.view, "getDocument"):
                gui_doc = self.view.getDocument()
                if gui_doc and hasattr(gui_doc, "Document"):
                    doc = gui_doc.Document

            if not doc:
                return []
            for obj in doc.Objects:
                if hasattr(obj, "Proxy") and getattr(obj.Proxy, "is_fld_workplane", False):
                    try:
                        visible = obj.ViewObject.Visibility if hasattr(obj, "ViewObject") else obj.Visibility
                    except Exception as e:
                        fld_logger.debug(f"ViewProjector.get_visible_workplanes: Visibility check failed for {obj.Name}: {e}")
                        visible = True  # assume visible if we can't check
                    if visible:
                        planes.append(obj)
        except Exception as e:
            fld_logger.debug(f"ViewProjector.get_visible_workplanes: failed to enumerate workplanes: {e}")
        return planes

    def get_base_plane(self, wp_obj=None):
        """Returns (normal, origin) for a given workplane, or the default viewport plane."""
        wp_placement = None
        if wp_obj:
             # Prefer getGlobalPlacement to handle nested objects correctly
             if hasattr(wp_obj, "getGlobalPlacement"):
                 wp_placement = wp_obj.getGlobalPlacement()
             elif hasattr(wp_obj, "Placement"):
                 wp_placement = wp_obj.Placement
             elif hasattr(wp_obj, "Base") and hasattr(wp_obj, "Rotation"):
                 wp_placement = wp_obj
        
        if wp_placement:
            n = wp_placement.Rotation.multVec(FreeCAD.Vector(0,0,1))
            o = wp_placement.Base
            return n, o

        # Fallback to camera facing
        if not self.view:
            return FreeCAD.Vector(0,0,1), FreeCAD.Vector(0,0,0)
            
        cam_node = self.view.getCameraNode()
        cam_pos = FreeCAD.Vector(*cam_node.position.getValue().getValue()) if cam_node else FreeCAD.Vector(0,0,100)
        
        vd = self.view.getViewDirection()
        n = FreeCAD.Vector(-vd[0], -vd[1], -vd[2])
        n.normalize()
        
        if hasattr(self.view, "getFocus"):
            return n, self.view.getFocus()
        return n, FreeCAD.Vector(0,0,0)

    def get_mouse_plane_pt(self, event_dict, place_on_geometry=False, working_plane=None, skip_objects=None, debug=False):
        """
        Calculates a 3D world position from a 2D viewport click, prioritizing:
        1. Visible workplanes (biased closer to camera).
        2. Geometry surfaces (NURBS/B-Rep).
        3. SDF surfaces.
        4. Infinite extension of the provided 'working_plane'.
        5. Viewport-aligned plane at camera focus.
        """
        if not self.view:
            return FreeCAD.Vector(0,0,0)

        # Small depth bias (in mm) to prioritize workplanes over geometry at the same depth.
        # This prevents "random" snapping to underlying faces when a workplane is active.
        WP_BIAS = 1.0 # 1 mm bias for robustness
        EPSILON = 1e-4 # Bounds tolerance

        skip_names = {obj.Name for obj in skip_objects} if skip_objects else set()

        # Pre-declare variables for the exception/fallback paths
        ray_p = None
        ray_d = None
        cam_pos = None
        wp_t = float('inf')
        wp_pt = None
        wp_hit = None
        geom_pt = None
        geom_t = float('inf')
        sdf_pt = None
        sdf_t = float('inf')
        sdf_result = None

        # Rotation hint for synthesized placements
        def synthesize_placement(pt, normal):
            # Ensure normal faces toward viewer
            vd = self.view.getViewDirection() if self.view else (0, 0, -1)
            view_dir = FreeCAD.Vector(vd[0], vd[1], vd[2])
            if normal.dot(view_dir) > 0:
                normal = normal.negative()
            
            # Standard global Z gravity
            z_axis = normal
            global_z = FreeCAD.Vector(0, 0, 1)
            x_axis = global_z.cross(z_axis) if abs(z_axis.dot(global_z)) < 0.99 else FreeCAD.Vector(1, 0, 0)
            x_axis.normalize()
            y_axis = z_axis.cross(x_axis)
            y_axis.normalize()
            
            m = FreeCAD.Matrix(
                x_axis.x, y_axis.x, z_axis.x, pt.x,
                x_axis.y, y_axis.y, z_axis.y, pt.y,
                x_axis.z, y_axis.z, z_axis.z, pt.z,
                0, 0, 0, 1
            )
            return FreeCAD.Placement(m)

        try:
            ray_p, ray_d = self.get_ray(event_dict)
            if not ray_p:
                raise ValueError("no ray")

            # Camera position for depth comparison (handles orthographic too).
            try:
                cam_vals = self.view.getCameraNode().position.getValue()
                cam_pos = FreeCAD.Vector(cam_vals[0], cam_vals[1], cam_vals[2])
            except Exception as e:
                fld_logger.debug(f"ViewProjector.get_mouse_plane_pt: cam_pos acquisition failed: {e}")
                cam_pos = ray_p

            # 1. Bounds-checked workplane hit.
            for wp in self.get_visible_workplanes():
                if wp.Name in skip_names:
                    continue
                n, o = self.get_base_plane(wp)
                denom = ray_d.dot(n)
                if abs(denom) < 1e-6:
                    continue
                t = (o - ray_p).dot(n) / denom
                pt_candidate = ray_p + ray_d * t
                if (pt_candidate - cam_pos).dot(ray_d) <= 0:
                    continue
                
                # Critical: Use GLOBAL placement for boundary check
                gpl = wp.getGlobalPlacement() if hasattr(wp, "getGlobalPlacement") else wp.Placement
                wp_inv = gpl.inverse()
                local_pt = wp_inv.multVec(pt_candidate)
                try:
                    l = float(wp.Length)
                    w = float(wp.Width)
                except Exception as e:
                    fld_logger.debug(f"ViewProjector.get_mouse_plane_pt: wp Length/Width fallback: {e}")
                    l, w = 100.0, 100.0
                if abs(local_pt.x) > (l / 2.0 + EPSILON) or abs(local_pt.y) > (w / 2.0 + EPSILON):
                    continue
                cam_t = (pt_candidate - cam_pos).dot(ray_d)
                # Apply bias so overlapping workplanes win over geometry
                biased_t = cam_t - WP_BIAS
                if biased_t < wp_t:
                    wp_t = biased_t
                    wp_pt = pt_candidate
                    wp_hit = wp

            # 2. Geometry hit (if enabled).
            if place_on_geometry:
                geom_pt = self._get_geometry_point(event_dict, skip_names=skip_names)
                if geom_pt is not None:
                    geom_t = (geom_pt - cam_pos).dot(ray_d)

            # 2b. SDF surface hit (always tested - not gated by place_on_geometry).
            sdf_result = self.get_sdf_hit(event_dict, skip_objects=skip_objects)
            if sdf_result is not None:
                sdf_pt, _sdf_n, _sdf_obj = sdf_result
                sdf_t = (sdf_pt - cam_pos).dot(ray_d)

        except Exception as e:
            from freecad.fields.core import fld_logger
            fld_logger.debug(f"get_mouse_plane_pt failed hit-test: {e}")

        # 4. Pick closest of all candidates.
        # Format: (t, point, hit_result, description)
        candidates = []
        if wp_pt is not None:
            candidates.append((wp_t, wp_pt, wp_hit, f"WorkPlane:{wp_hit.Label if hasattr(wp_hit, 'Label') else wp_hit.Name}"))
        if sdf_pt is not None:
            _pt, world_n, sdf_obj = sdf_result
            name = sdf_obj.Label if hasattr(sdf_obj, 'Label') else sdf_obj.Name
            candidates.append((sdf_t, sdf_pt, synthesize_placement(sdf_pt, world_n), f"SDF:{name}"))
        if geom_pt is not None:
            info = self.get_geometry_info(event_dict, skip_objects=skip_objects)
            if info:
                world_hit, world_n, obj, _sub = info
                name = obj.Label if hasattr(obj, 'Label') else obj.Name
                candidates.append((geom_t, world_hit, synthesize_placement(world_hit, world_n), f"Geometry:{name}"))
            else:
                candidates.append((geom_t, geom_pt, None, "Geometry:Unknown"))
        
        if candidates:
            candidates.sort(key=lambda x: x[0])
            best_t, best_pt, best_hit, best_desc = candidates[0]
            
            if debug:
                log_msg = f"get_mouse_plane_pt candidates: {best_desc} (t={best_t:.4f})"
                if len(candidates) > 1:
                    log_msg += " Other: " + ", ".join([f"{c[3]} (t={c[0]:.4f})" for c in candidates[1:]])
                # from freecad.fields.core import fld_logger
                # fld_logger.info(log_msg)
            
            return best_pt, best_hit, best_desc

        # 4. Infinite working_plane hit test (Fallback if no real hits found).
        if working_plane:
            if hasattr(working_plane, "getGlobalPlacement"):
                wp_p = working_plane.getGlobalPlacement()
            elif hasattr(working_plane, "Placement"):
                wp_p = working_plane.Placement
            else:
                wp_p = working_plane # Matrix or Placement
            
            n_fb = wp_p.Rotation.multVec(FreeCAD.Vector(0,0,1))
            o_fb = wp_p.Base
            
            if ray_d is not None:
                denom = ray_d.dot(n_fb)
                if abs(denom) > 1e-6:
                    t_fb = (o_fb - ray_p).dot(n_fb) / denom
                    fallback_pt = ray_p + ray_d * t_fb
                    fallback_t = (fallback_pt - cam_pos).dot(ray_d) if cam_pos else 1.0
                    
                    if fallback_t > 0:
                        description = "Fallback:WorkingPlane"
                        return fallback_pt, working_plane, description

        # 5. Final Fallback: Camera-facing plane.
        n_cam, o_cam = self.get_base_plane(None)
        pt_cam = self.get_mouse_world_pos(event_dict, n_cam, o_cam)

        # Orient the fallback plane to be square to the screen -- same basis
        # construction as FldInputManager.get_view_transform() (CR-034): Z = view
        # normal (towards viewer), Y = view up, X = screen right (Up x Z). No
        # circular-import obstacle: this module already imports FldInputManager
        # at the top, and input_manager only reaches back into ViewProjector via
        # deferred imports inside methods.
        placement = FldInputManager.get_instance().get_view_transform(
            self.view, pt_cam if pt_cam else FreeCAD.Vector(0, 0, 0))
        return pt_cam, placement, "Fallback:CameraFacing"

    def get_geometry_info(self, event_dict, skip_objects=None):
        """
        Robustly returns (point, normal, obj, subname) for the surface under mouse.
        Uses Part.section for accurate hits and distToShape for normals.
        """
        if not self.view: return None
        x, y = FldInputManager.get_instance().get_gl_pos_phys(self.view, event_dict)
        if x is None: return None
        skip_names = [obj.Name for obj in skip_objects] if skip_objects else []

        try:
            # 1. Get objects under pixel
            if hasattr(self.view, "getObjectsInfo"):
                infos = self.view.getObjectsInfo((x, y)) or []
            else:
                s_info = self.view.getObjectInfo((x, y))
                infos = [s_info] if s_info else []

            for info in infos:
                if not info or "Object" not in info or "Component" not in info: continue
                obj_name = info["Object"]
                if obj_name in skip_names: continue
                
                doc = self.view.getDocument().Document if (self.view and hasattr(self.view, "getDocument") and self.view.getDocument()) else FreeCAD.ActiveDocument
                obj = doc.getObject(obj_name) if doc else None
                if not obj or not hasattr(obj, "Shape") or obj.Shape.isNull(): continue
                
                subname = info["Component"]
                if "Face" not in subname: continue
                try:
                    face = obj.Shape.getElement(subname)

                    gpl = obj.getGlobalPlacement() if hasattr(obj, "getGlobalPlacement") else obj.Placement
                    gpl_inv = gpl.inverse()

                    # 2. Hit position - prefer FreeCAD's pick coordinates (world space,
                    # always on the visible front surface). Fall back to ray-section.
                    world_hit = None
                    if 'Point' in info:
                        world_hit = info['Point']
                    elif 'x' in info and 'y' in info and 'z' in info:
                        world_hit = FreeCAD.Vector(info['x'], info['y'], info['z'])
                    
                    if world_hit is not None:
                        local_hit = gpl_inv.multVec(world_hit)
                    else:
                        ray_p, ray_d = self.get_ray(event_dict)
                        if not ray_p: continue
                        local_near = gpl_inv.multVec(ray_p + ray_d * 1.0)
                        local_far  = gpl_inv.multVec(ray_p + ray_d * 100000.0)
                        ray_wire = Part.makeLine(tuple(local_near), tuple(local_far))
                        inter = face.section(ray_wire)
                        if not inter.Vertexes: continue
                        local_hit = min(inter.Vertexes, key=lambda v: (v.Point - local_near).Length).Point
                        world_hit = gpl.multVec(local_hit)
                    
                    # 3. Calculate Normal
                    dists = face.distToShape(Part.Vertex(local_hit))
                    local_n = None
                    if dists and len(dists) >= 3 and len(dists[2]) > 0:
                        info_tuple = dists[2][0]
                        if len(info_tuple) >= 3 and isinstance(info_tuple[2], (tuple, list)) and len(info_tuple[2]) == 2:
                            u, v = info_tuple[2]
                            local_n = face.Surface.normal(u, v)
                    
                    if not local_n: # Fallback parameter pick
                        try:
                            u, v = face.Surface.parameter(local_hit); local_n = face.Surface.normal(u, v)
                        except Exception as e:
                            fld_logger.debug(f"face normal parameter fallback failed: {e}")
                    
                    if local_n:
                        if face.Orientation == "Reversed": local_n.multiply(-1.0)
                        world_n = gpl.Rotation.multVec(local_n); world_n.normalize()
                        return world_hit, world_n, obj, subname
                except Exception as e:
                    fld_logger.debug(f"ViewProjector.get_geometry_info: face processing failed for {subname}: {e}")
                    continue
                    
            return None
        except Exception as e:
            fld_logger.debug(f"get_geometry_info failed: {e}")
            return None

    def get_sdf_hit(self, event_dict, skip_objects=None):
        """
        Ray-march against all visible SDF SDF objects in the scene.
        Returns (hit_point, hit_normal, obj) for the closest hit, or None.
        """
        if not self.view:
            return None
        doc = FreeCAD.ActiveDocument
        if not doc:
            return None
        skip_names = {o.Name for o in skip_objects} if skip_objects else set()
        try:
            # Use the clean, standardized Qt-native ray from FldInputManager.
            rp, rd = self.get_ray(event_dict)
            if not rp or not rd:
                return None

            rd_n = FreeCAD.Vector(rd)
            rd_n.normalize()

            # Collect visible SDF objects.
            sdf_objs = []
            for obj in doc.Objects:
                if obj.Name in skip_names:
                    continue
                proxy = getattr(obj, "Proxy", None)
                if proxy is None:
                    continue
                field = getattr(proxy, "SdfField", None)
                if field is None and hasattr(proxy, "get_sdf_field"):
                    try:
                        field = proxy.get_sdf_field(obj)
                    except Exception as e:
                        fld_logger.debug(f"ViewProjector.get_sdf_hit: get_sdf_field failed for {obj.Name}: {e}")
                        field = None
                if field is None:
                    continue

                try:
                    if hasattr(obj, "ViewObject") and obj.ViewObject and not obj.ViewObject.Visibility:
                        continue
                except Exception as e:
                    fld_logger.debug(f"ViewProjector.get_sdf_hit: ViewObject.Visibility check failed for {obj.Name}: {e}")
                sdf_objs.append((obj, field))

            # Ray-march and find closest hit.
            best_t = float('inf')
            best = None
            for obj, field in sdf_objs:
                result = field.ray_march(rp, rd_n)
                if result:
                    hit_pt, hit_normal = result
                    t = (hit_pt - rp).Length
                    if t < best_t:
                        best_t = t
                        best = (hit_pt, hit_normal, obj)
            return best
        except Exception as e:
            fld_logger.debug(f"get_sdf_hit failed: {e}")
            return None
