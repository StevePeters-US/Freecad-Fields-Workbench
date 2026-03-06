import FreeCAD
import FreeCADGui
import math
from core import dm_logger

class ViewProjector:
    """Handles projection of 2D screen coordinates to 3D world space."""

    def __init__(self, view):
        self.view = view

    def _is_orthographic(self):
        """True if the active camera is orthographic (not perspective)."""
        try:
            cam = self.view.getCameraNode()
            return "Orthographic" in cam.getTypeId().getName()
        except Exception as e:
            dm_logger.debug(f"Camera orthographic check failed: {e}")
            return False

    def _cam_pos(self):
        """Camera world position as a FreeCAD.Vector (perspective only)."""
        try:
            cam = self.view.getCameraNode()
            p = cam.position.getValue()
            return FreeCAD.Vector(p[0], p[1], p[2])
        except Exception as e:
            dm_logger.debug(f"Camera position acquisition failed: {e}")
            return FreeCAD.Vector(0,0,100)

    def _get_view_ray(self, x, y):
        """Acquire a (Base, Direction) ray from the view/viewer."""
        try:
            # Direct getRay (FC 0.20+)
            if hasattr(self.view, "getRay"):
                ray = self.view.getRay(x, y)
                if ray:
                    if isinstance(ray, dict) and "base" in ray and "dir" in ray:
                        return (FreeCAD.Vector(ray["base"]), FreeCAD.Vector(ray["dir"]))
                    elif isinstance(ray, tuple) and len(ray) >= 2:
                        return (FreeCAD.Vector(ray[0]), FreeCAD.Vector(ray[1]))

            # Coin3D viewer fallback
            viewer = self.view.getViewer()
            h = 1000
            if hasattr(viewer, "getGlxSize"):
                h = viewer.getGlxSize()[1]
            elif hasattr(viewer, "getSize"):
                sz = viewer.getSize()
                h = sz.height() if hasattr(sz, "height") else sz[1]

            def try_ray(cur_y):
                if hasattr(viewer, "getRay"):
                    r = viewer.getRay(int(x), int(cur_y))
                    if r:
                        if isinstance(r, dict) and "base" in r and "dir" in r:
                            return (FreeCAD.Vector(r["base"]), FreeCAD.Vector(r["dir"]))
                        elif isinstance(r, tuple) and len(r) >= 2:
                            return (FreeCAD.Vector(r[0]), FreeCAD.Vector(r[1]))
                return None

            res = try_ray(y) or try_ray(h - y)
            if res:
                return res
        except Exception as e:
            dm_logger.debug(f"Ray acquisition failed: {e}")
            
        # Synthesize fallback using pure camera math
        try:
            cam = self.view.getCameraNode()
            if not cam:
                return None, None
                
            pos = cam.position.getValue()
            rot = cam.orientation.getValue()
            
            # FreeCAD's getViewer().getGlxSize() returns the pure viewport dimensions
            viewer = self.view.getViewer()
            w = 1000.0
            h = 1000.0
            if hasattr(viewer, "getGlxSize"):
                sz = viewer.getGlxSize()
                w = float(sz[0])
                h = float(sz[1])
            elif hasattr(viewer, "getSize"):
                sz = viewer.getSize()
                w = float(sz.width() if hasattr(sz, "width") else sz[0])
                h = float(sz.height() if hasattr(sz, "height") else sz[1])
                
            # Get aspect ratio
            aspect = w / h
            
            # Get camera orientation as vectors
            # rotation matrix derived from quaternion
            quat_tuple = rot.getValue()
            qx, qy, qz, qw = quat_tuple[0], quat_tuple[1], quat_tuple[2], quat_tuple[3]
            
            # Forward vector (Z-axis in freecad camera space usually points backwards, so we invert)
            fx = 2.0 * (qx*qz + qw*qy)
            fy = 2.0 * (qy*qz - qw*qx)
            fz = 1.0 - 2.0 * (qx*qx + qy*qy)
            forward = FreeCAD.Vector(-fx, -fy, -fz)
            
            # Up vector (Y-axis)
            ux = 2.0 * (qx*qy - qw*qz)
            uy = 1.0 - 2.0 * (qx*qx + qz*qz)
            uz = 2.0 * (qy*qz + qw*qx)
            up = FreeCAD.Vector(ux, uy, uz)
            
            # Right vector (X-axis)
            rx = 1.0 - 2.0 * (qy*qy + qz*qz)
            ry = 2.0 * (qx*qy + qw*qz)
            rz = 2.0 * (qx*qz - qw*qy)
            right = FreeCAD.Vector(rx, ry, rz)
            
            if hasattr(cam, "heightAngle"):
                # Perspective
                ha = cam.heightAngle.getValue()
                ndc_x = (x / w) * 2.0 - 1.0
                ndc_y = 1.0 - (y / h) * 2.0
                
                # View plane dimensions
                plane_h = math.tan(ha / 2.0)
                plane_w = plane_h * aspect
                
                ray_d = forward + right * (ndc_x * plane_w) + up * (ndc_y * plane_h)
                ray_d.normalize()
                
                ray_p = FreeCAD.Vector(*pos)
                return ray_p, ray_d
                
            elif hasattr(cam, "height"):
                # Orthographic
                height = cam.height.getValue()
                width = height * aspect
                
                ndc_x = (x / w) * 2.0 - 1.0
                ndc_y = 1.0 - (y / h) * 2.0
                
                ray_p = FreeCAD.Vector(*pos) + right * (ndc_x * width / 2.0) + up * (ndc_y * height / 2.0)
                ray_d = forward
                ray_d.normalize()
                return ray_p, ray_d
                
        except Exception as e:
            dm_logger.debug(f"Pure math fallback ray synthesis failed: {e}")

        return None, None

    def _intersect_ray_plane(self, ray_p, ray_d, plane_normal, plane_point):
        """Standard Ray-Plane intersection. Returns Vector or None."""
        try:
            denom = ray_d.dot(plane_normal)
            if abs(denom) > 1e-6:
                t = (plane_point - ray_p).dot(plane_normal) / denom
                return ray_p + ray_d * t
        except Exception as e:
            dm_logger.debug(f"Ray-plane intersection failed: {e}")
        return None

    def _get_geometry_point(self, event_dict):
        pos = event_dict.get("Position")
        if not pos: 
            return None
        try:
            infos = []
            if hasattr(self.view, "getObjectsInfo"):
                infos = self.view.getObjectsInfo((int(pos[0]), int(pos[1])))
            else:
                single_info = self.view.getObjectInfo((int(pos[0]), int(pos[1])))
                infos = [single_info] if single_info else []
                
            if not infos:
                infos = []
                
            for info in infos:
                if not info or "Object" not in info or "Component" not in info:
                    continue
                # Skip preview objects by name or proxy type
                obj_name = info["Object"]
                doc = FreeCAD.ActiveDocument
                obj = doc.getObject(obj_name) if doc else None
                if not obj or not obj.Shape:
                    continue
                    
                subname = info["Component"]
                if "Face" in subname:
                    if obj.Shape.isNull():
                        if 'x' in info and 'y' in info and 'z' in info:
                            world_pt = FreeCAD.Vector(info['x'], info['y'], info['z'])
                            return world_pt
                        continue

                    face = obj.Shape.getElement(subname)
                    import Part
                    ray_p, ray_d = self._get_view_ray(pos[0], pos[1])
                    if ray_p and ray_d:
                        ray_d.normalize()
                        gpl = obj.getGlobalPlacement() if hasattr(obj, "getGlobalPlacement") else obj.Placement
                        gpl_inv = gpl.inverse()
                        
                        local_near = gpl_inv.multVec(ray_p + ray_d * 1)
                        local_far  = gpl_inv.multVec(ray_p + ray_d * 100000)
                        
                        ray_wire = Part.makeLine(tuple(local_near), tuple(local_far))
                        inter = face.section(ray_wire)
                        if inter.Vertexes:
                            best_pt = min(inter.Vertexes, key=lambda v: (v.Point - local_near).Length).Point
                            world_pt = gpl.multVec(best_pt)
                            return world_pt
                    else:
                        # If we can't get a ray, use the exact 3D hit point provided by FreeCAD
                        if 'x' in info and 'y' in info and 'z' in info:
                            world_pt = FreeCAD.Vector(info['x'], info['y'], info['z'])
                            return world_pt
                            
            # Fallback
            fb = self.view.getPoint(pos[0], pos[1])
            return fb
        except Exception as e:
            dm_logger.debug(f"_get_geometry_point failed: {e}")
            return self.view.getPoint(pos[0], pos[1])

    def get_mouse_world_pos(self, event_dict, plane_normal=None, plane_point=None, place_on_geometry=False):
        """
        Unified mouse-to-world position. 
        If plane_normal and plane_point are provided, intersects with that plane.
        Otherwise, returns the depth-buffered point on geometry.
        """
        if not self.view:
            return None

        pos = event_dict.get("Position")
        if pos is None:
            pos = (0, 0)
        x, y = pos[0], pos[1]
        
        # 0. Intercept if place on geometry is active, bypassing plane intersection
        if place_on_geometry:
            geom_pt = self._get_geometry_point(event_dict)
            if geom_pt:
                return geom_pt
                
        # 1. Plane Intersection
        if plane_normal is not None and plane_point is not None:
            ray_p, ray_d = self._get_view_ray(x, y)
            if ray_p and ray_d:
                pt = self._intersect_ray_plane(ray_p, ray_d, plane_normal, plane_point)
                if pt:
                    return pt

        # 2. Fallback: Depth-buffered point on surface
        try:
            return self.view.getPoint(x, y)
        except Exception as e:
            dm_logger.error(f"getPoint failed: {e}")
            return None

    def get_visible_workplanes(self):
        """Returns a list of all visible DMWorkPlane objects in the document."""
        doc = FreeCAD.ActiveDocument
        if not doc:
            return []
            
        planes = []
        for obj in doc.Objects:
            if hasattr(obj, "Proxy") and obj.Proxy.__class__.__name__ == "DMWorkPlane":
                if hasattr(obj, "Visibility") and obj.Visibility:
                    planes.append(obj)
        return planes

    def get_base_plane(self, wp_obj=None):
        """Returns (normal, origin) for a given workplane, or the default viewport plane."""
        if wp_obj and hasattr(wp_obj, "Placement"):
            wp_placement = wp_obj.Placement
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

    def get_mouse_plane_pt(self, event_dict, place_on_geometry=False, working_plane=None):
        """Intersection of mouse ray with the closest visible working plane."""
        # 0. Check if place on geometry is explicitly enabled
        if place_on_geometry:
            pt = self.get_mouse_world_pos(event_dict, place_on_geometry=True)
            if pt is not None:
                return pt

        # 1. If we have a working_plane already established for this tool session, stick to it.
        # This prevents the plane from jumping mid-operation (like drawing a box)
        if working_plane:
            n = working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
            o = working_plane.Base
            return self.get_mouse_world_pos(event_dict, n, o, place_on_geometry=False)

        # 2. Get the actual 3D point the mouse is hovering over in the scene
        pos = event_dict.get("Position", (0, 0))
        x, y = pos[0], pos[1]
        
        if not self.view:
            return FreeCAD.Vector(0,0,0)
            
        scene_pt = None
        try:
            scene_pt = self.view.getPoint(x, y)
        except Exception as e:
            dm_logger.debug(f"get_mouse_plane_pt: getPoint failed: {e}")

        # 3. If we don't have a valid scene point, we can't reliably synthesize a ray. 
        # Fall back to default plane.
        if scene_pt is None:
            n, o = self.get_base_plane(None)
            return self.get_mouse_world_pos(event_dict, n, o)

        # 4. Synthesize the ray from camera through scene_pt
        try:
            cam = self.view.getCameraNode()
            if not cam or not hasattr(cam, "position"):
                raise ValueError("No camera")
            cam_vec = cam.position.getValue()
            if hasattr(cam_vec, "getValue"):
                cam_pos_tuple = cam_vec.getValue()
            else:
                cam_pos_tuple = (cam_vec[0], cam_vec[1], cam_vec[2])
            
            ray_p = FreeCAD.Vector(*cam_pos_tuple)
            ray_d = scene_pt - ray_p
            ray_d.normalize()
            
            # 5. Intersect ray with ALL visible workplanes, pick the closest one
            visible_wps = self.get_visible_workplanes()
            closest_t = float('inf')
            closest_pt = None
            closest_wp = None
            
            for wp in visible_wps:
                n, o = self.get_base_plane(wp)
                denom = ray_d.dot(n)
                if abs(denom) > 1e-6:
                    t = (o - ray_p).dot(n) / denom
                    if t > 0 and t < closest_t:
                        # Check if intersection point is within the bounds of the workplane visual
                        pt_candidate = ray_p + ray_d * t
                        
                        # Calculate bounds check (basic rectangle check)
                        # We bring the point into the workplane's local coordinate system
                        if hasattr(wp, "Placement"):
                            wp_inv_plac = wp.Placement.inverse()
                            local_pt = wp_inv_plac.multVec(pt_candidate)
                            
                            w = wp.Width if hasattr(wp, "Width") else 100.0
                            l = wp.Length if hasattr(wp, "Length") else 100.0
                            
                            if abs(local_pt.x) <= l/2.0 and abs(local_pt.y) <= w/2.0:
                                closest_t = t
                                closest_pt = pt_candidate
                                closest_wp = wp
            
            if closest_pt is not None:
                # If we hit a workplane and we don't already have one locked, set it
                # The tool handles this by updating its local working_plane
                # For now, just Select it if not selected (Optional UX logic)
                return closest_pt, closest_wp
                
        except Exception as e:
            dm_logger.debug(f"Auto-workplane raycast failed: {e}")

        # 6. Fallback: just return the getPoint directly, or intersect default plane
        n, o = self.get_base_plane(None)
        return self.get_mouse_world_pos(event_dict, n, o), None
