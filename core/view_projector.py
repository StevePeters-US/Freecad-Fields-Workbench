import FreeCAD
import FreeCADGui
import Part
import math
from core import dm_logger
from core.input_manager import DMInputManager

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
        """Delegate ray acquisition to DMInputManager."""
        # Note: x,y are ignored here because DMInputManager.get_ray uses event_dict or last_qt_pos.
        # We pass a synthetic event_dict to use the specific x,y if needed, but usually 
        # it's better to just let the manager handle it.
        return DMInputManager.get_instance().get_ray(self.view, {"QtPosition": (x, y)})

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
        pos = DMInputManager.get_instance().get_mouse_pos(event_dict)
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
                    # Prefer FreeCAD's own pick coordinates (already in world space,
                    # always on the visible front surface). Only fall back to manual
                    # ray-section when they are absent.
                    if 'x' in info and 'y' in info and 'z' in info:
                        return FreeCAD.Vector(info['x'], info['y'], info['z'])

                    if obj.Shape.isNull():
                        continue

                    # Fallback: ray-section in LOCAL object space (handles any Placement)
                    face = obj.Shape.getElement(subname)
                    import Part
                    ray_p, ray_d = self._get_view_ray(pos[0], pos[1])
                    if ray_p and ray_d:
                        ray_d.normalize()
                        gpl = obj.getGlobalPlacement() if hasattr(obj, "getGlobalPlacement") else obj.Placement
                        gpl_inv = gpl.inverse()
                        local_near = gpl_inv.multVec(ray_p + ray_d * 1.0)
                        local_far  = gpl_inv.multVec(ray_p + ray_d * 100000.0)
                        ray_wire = Part.makeLine(tuple(local_near), tuple(local_far))
                        inter = face.section(ray_wire)
                        if inter.Vertexes:
                            best_local = min(inter.Vertexes, key=lambda v: (v.Point - local_near).Length).Point
                            return gpl.multVec(best_local)
                            
            return None
        except Exception as e:
            dm_logger.debug(f"_get_geometry_point failed: {e}")
            return None

    def get_mouse_world_pos(self, event_dict, plane_normal=None, plane_point=None, place_on_geometry=False):
        if not self.view:
            return None

        pos = DMInputManager.get_instance().get_mouse_pos(event_dict)
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

        return None

    def get_visible_workplanes(self):
        """Returns a list of all visible DMWorkPlane objects in the document."""
        doc = FreeCAD.ActiveDocument
        if not doc:
            return []
            
        planes = []
        for obj in doc.Objects:
            if hasattr(obj, "Proxy") and obj.Proxy.__class__.__name__ == "DMWorkPlane":
                try:
                    visible = obj.ViewObject.Visibility if hasattr(obj, "ViewObject") else obj.Visibility
                except Exception:
                    visible = True  # assume visible if we can't check
                if visible:
                    planes.append(obj)
        dm_logger.debug_throttled("gvwp", f"get_visible_workplanes: returning {len(planes)} planes")
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
        """Returns the closest hit to the camera: locked plane > workplane/geometry > camera plane."""
        # 1. Locked working plane — never leave it mid-operation.
        if working_plane:
            n = working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1))
            o = working_plane.Base
            return self.get_mouse_world_pos(event_dict, n, o, place_on_geometry=False)

        if not self.view:
            return FreeCAD.Vector(0,0,0)

        try:
            ray_p, ray_d = DMInputManager.get_instance().get_ray(self.view, event_dict)
            if not ray_p:
                raise ValueError("no ray")

            # 2. Find the closest workplane hit (within its visual bounds).
            # Use camera-relative depth for "in front of camera" check — ray_p may be
            # the focal-plane point for orthographic cameras, not the camera itself.
            try:
                cam_vals = self.view.getCameraNode().position.getValue()
                cam_pos = FreeCAD.Vector(cam_vals[0], cam_vals[1], cam_vals[2])
            except Exception:
                cam_pos = ray_p

            wp_t = float('inf')
            wp_pt = None
            wp_hit = None
            for wp in self.get_visible_workplanes():
                n, o = self.get_base_plane(wp)
                denom = ray_d.dot(n)
                if abs(denom) < 1e-6:
                    continue
                t = (o - ray_p).dot(n) / denom
                pt_candidate = ray_p + ray_d * t
                # "in front of camera" check in camera space (handles both ortho and perspective)
                if (pt_candidate - cam_pos).dot(ray_d) <= 0:
                    continue
                wp_inv = wp.Placement.inverse()
                local_pt = wp_inv.multVec(pt_candidate)
                try:
                    l = float(wp.Length)
                    w = float(wp.Width)
                except Exception:
                    l, w = 100.0, 100.0
                if abs(local_pt.x) > l / 2.0 or abs(local_pt.y) > w / 2.0:
                    continue
                cam_t = (pt_candidate - cam_pos).dot(ray_d)
                if cam_t < wp_t:
                    wp_t = cam_t
                    wp_pt = pt_candidate
                    wp_hit = wp
            dm_logger.debug_throttled("wp_hit", f"  wp_hit={wp_hit.Name if wp_hit else None} cam_t={wp_t:.2f}")

            # 3. Find geometry hit depth (if enabled). Use camera-relative depth.
            geom_pt = None
            geom_t = float('inf')
            if place_on_geometry:
                geom_pt = self._get_geometry_point(event_dict)
                if geom_pt is not None:
                    geom_t = (geom_pt - cam_pos).dot(ray_d)

            dm_logger.debug_throttled("gmpp", f"get_mouse_plane_pt: wp_t={wp_t:.2f} geom_t={geom_t:.2f} wp_hit={wp_hit is not None} geom_pt={geom_pt is not None}")

            # 4. Return whichever is closer to the camera.
            if wp_pt is not None and wp_t <= geom_t:
                return wp_pt, wp_hit
            if geom_pt is not None:
                return geom_pt

        except Exception as e:
            dm_logger.debug(f"get_mouse_plane_pt failed: {e}")

        # 5. Fallback: camera-facing plane.
        n, o = self.get_base_plane(None)
        return self.get_mouse_world_pos(event_dict, n, o), None

    def get_geometry_info(self, event_dict, skip_objects=None):
        """
        Robustly returns (point, normal, obj, subname) for the surface under mouse.
        Uses Part.section for accurate hits and distToShape for normals.
        """
        if not self.view: return None
        pos = DMInputManager.get_instance().get_mouse_pos(event_dict)
        skip_names = [obj.Name for obj in skip_objects] if skip_objects else []

        try:
            # 1. Get objects under pixel
            if hasattr(self.view, "getObjectsInfo"):
                infos = self.view.getObjectsInfo((int(pos[0]), int(pos[1]))) or []
            else:
                s_info = self.view.getObjectInfo((int(pos[0]), int(pos[1])))
                infos = [s_info] if s_info else []

            for info in infos:
                if not info or "Object" not in info or "Component" not in info: continue
                obj_name = info["Object"]
                if obj_name in skip_names: continue
                
                doc = FreeCAD.ActiveDocument
                obj = doc.getObject(obj_name) if doc else None
                if not obj or not hasattr(obj, "Shape") or obj.Shape.isNull(): continue
                
                subname = info["Component"]
                if "Face" not in subname: continue
                face = obj.Shape.getElement(subname)

                gpl = obj.getGlobalPlacement() if hasattr(obj, "getGlobalPlacement") else obj.Placement
                gpl_inv = gpl.inverse()

                # 2. Hit position — prefer FreeCAD's pick coordinates (world space,
                # always on the visible front surface). Fall back to ray-section.
                if 'x' in info and 'y' in info and 'z' in info:
                    world_hit = FreeCAD.Vector(info['x'], info['y'], info['z'])
                    local_hit = gpl_inv.multVec(world_hit)
                else:
                    ray_p, ray_d = DMInputManager.get_instance().get_ray(self.view, event_dict)
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
                    except: pass
                
                if local_n:
                    if face.Orientation == "Reversed": local_n.multiply(-1.0)
                    world_n = gpl.Rotation.multVec(local_n); world_n.normalize()
                    return world_hit, world_n, obj, subname
                    
            return None
        except Exception as e:
            dm_logger.debug(f"get_geometry_info failed: {e}")
            return None
