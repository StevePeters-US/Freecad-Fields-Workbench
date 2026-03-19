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
            from pivy import coin
            cam = self.view.getCameraNode()
            return cam.getTypeId() == coin.SoOrthographicCamera.getClassTypeId()
        except Exception as e:
            dm_logger.debug(f"Camera orthographic check failed: {e}")
            return True  # Default to True (most FreeCAD modeling uses orthographic)

    def _get_vp_height(self):
        """Get viewport pixel height via multiple fallbacks."""
        try:
            viewer = self.view.getViewer()
            for method in ("getGlxSize", "getSize"):
                if hasattr(viewer, method):
                    try:
                        sz = getattr(viewer, method)()
                        h = float(sz[1] if isinstance(sz, (list, tuple)) else sz.height())
                        if h > 0:
                            return h
                    except Exception:
                        continue
        except Exception:
            pass
        try:
            h = float(self.view.height())
            if h > 0:
                return h
        except Exception:
            pass
        return None

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
        # Use clean standardized "Position" key for the event_dict
        return DMInputManager.get_instance().get_ray(self.view, {"Position": (x, y)})

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

    def _get_geometry_point(self, event_dict, skip_names=None):
        pos = DMInputManager.get_instance().get_mouse_pos(event_dict)
        x, y = int(pos[0]), int(pos[1])
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
                    except Exception:
                        continue # Stale subname (e.g. Face1 on empty/different shape)

                    import Part
                    ray_p, ray_d = self._get_view_ray(pos[0], pos[1])
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
                        except Exception:
                            continue
                            
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

    def get_mouse_plane_pt(self, event_dict, place_on_geometry=False, working_plane=None, skip_objects=None):
        """
        Returns the closest hit to the camera.

        Priority order:
          1. Bounds-checked visible workplane (closest to camera, within its grid)
          2. Geometry surface (if place_on_geometry=True)
          3. working_plane as infinite fallback (keeps the tool on its plane when the
             cursor moves outside all workplane bounds — e.g. mid-curve draw)
          4. Camera-facing plane

        skip_objects: optional list of FreeCAD objects to exclude from both
                      workplane and geometry hit tests.
        """
        if not self.view:
            return FreeCAD.Vector(0,0,0)

        # Small depth bias (in mm) to prioritize workplanes over geometry at the same depth.
        # This prevents "random" snapping to underlying faces when a workplane is active.
        WP_BIAS = 1e-3 
        EPSILON = 1e-4 # Bounds tolerance

        skip_names = {obj.Name for obj in skip_objects} if skip_objects else set()

        try:
            ray_p, ray_d = DMInputManager.get_instance().get_ray(self.view, event_dict)
            if not ray_p:
                raise ValueError("no ray")

            # Camera position for depth comparison (handles orthographic too).
            try:
                cam_vals = self.view.getCameraNode().position.getValue()
                cam_pos = FreeCAD.Vector(cam_vals[0], cam_vals[1], cam_vals[2])
            except Exception:
                cam_pos = ray_p

            # 1. Bounds-checked workplane hit.
            wp_t = float('inf')
            wp_pt = None
            wp_hit = None
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
                except Exception:
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
            geom_pt = None
            geom_t = float('inf')
            if place_on_geometry:
                geom_pt = self._get_geometry_point(event_dict, skip_names=skip_names)
                if geom_pt is not None:
                    geom_t = (geom_pt - cam_pos).dot(ray_d)

            # 2b. SDF surface hit (always tested — not gated by place_on_geometry).
            sdf_pt = None
            sdf_t = float('inf')
            sdf_result = self.get_sdf_hit(event_dict, skip_objects=skip_objects)
            if sdf_result is not None:
                sdf_pt, _sdf_n, _sdf_obj = sdf_result
                sdf_t = (sdf_pt - cam_pos).dot(ray_d)

            # 3. Infinite working_plane hit test.
            fallback_t = float('inf')
            fallback_pt = None
            if working_plane:
                if hasattr(working_plane, "getGlobalPlacement"):
                    wp_p = working_plane.getGlobalPlacement()
                elif hasattr(working_plane, "Placement"):
                    wp_p = working_plane.Placement
                else:
                    wp_p = working_plane
                
                n_fb = wp_p.Rotation.multVec(FreeCAD.Vector(0,0,1))
                o_fb = wp_p.Base
                denom = ray_d.dot(n_fb)
                if abs(denom) > 1e-6:
                    t_fb = (o_fb - ray_p).dot(n_fb) / denom
                    pt_fb = ray_p + ray_d * t_fb
                    fb_dist = (pt_fb - cam_pos).dot(ray_d)
                    if fb_dist > 0:
                        # Apply bias to the fallback plane too
                        fallback_t = fb_dist - WP_BIAS
                        fallback_pt = pt_fb

            # 4. Rotation hint for synthesized placements
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

            # 5. Pick closest of all candidates.
            candidates = []
            if wp_pt is not None:
                candidates.append((wp_t, (wp_pt, wp_hit)))
            if sdf_pt is not None:
                _pt, world_n, _obj = sdf_result
                candidates.append((sdf_t, (sdf_pt, synthesize_placement(sdf_pt, world_n))))
            if geom_pt is not None:
                info = self.get_geometry_info(event_dict, skip_objects=skip_objects)
                if info:
                    world_hit, world_n, _obj, _sub = info
                    candidates.append((geom_t, (world_hit, synthesize_placement(world_hit, world_n))))
                else:
                    candidates.append((geom_t, (geom_pt, None)))
            if fallback_pt is not None:
                candidates.append((fallback_t, (fallback_pt, None)))
            
            if candidates:
                candidates.sort(key=lambda x: x[0])
                return candidates[0][1]

        except Exception as e:
            dm_logger.debug(f"get_mouse_plane_pt failed: {e}")

        # 5. Camera-facing plane.
        n_cam, o_cam = self.get_base_plane(None)
        pt_cam = self.get_mouse_world_pos(event_dict, n_cam, o_cam)
        rot_cam = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), n_cam)
        return pt_cam, FreeCAD.Placement(pt_cam if pt_cam else FreeCAD.Vector(0,0,0), rot_cam)

    def get_geometry_info(self, event_dict, skip_objects=None):
        """
        Robustly returns (point, normal, obj, subname) for the surface under mouse.
        Uses Part.section for accurate hits and distToShape for normals.
        """
        if not self.view: return None
        pos = DMInputManager.get_instance().get_mouse_pos(event_dict)
        x, y = int(pos[0]), int(pos[1])
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
                
                doc = FreeCAD.ActiveDocument
                obj = doc.getObject(obj_name) if doc else None
                if not obj or not hasattr(obj, "Shape") or obj.Shape.isNull(): continue
                
                subname = info["Component"]
                if "Face" not in subname: continue
                try:
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
                except Exception:
                    continue
                    
            return None
        except Exception as e:
            dm_logger.debug(f"get_geometry_info failed: {e}")
            return None

    def get_sdf_hit(self, event_dict, skip_objects=None):
        """
        Ray-march against all visible F-Rep SDF objects in the scene.
        Returns (hit_point, hit_normal, obj) for the closest hit, or None.
        """
        if not self.view:
            return None
        doc = FreeCAD.ActiveDocument
        if not doc:
            return None
        skip_names = {o.Name for o in skip_objects} if skip_objects else set()
        try:
            # Use the clean, standardized Qt-native ray from DMInputManager.
            rp, rd = DMInputManager.get_instance().get_ray(self.view, event_dict)
            if not rp or not rd:
                return None

            rd_n = FreeCAD.Vector(rd)
            rd_n.normalize()

            # Collect visible SDF objects.
            sdf_objs = []
            for obj in doc.Objects:
                if obj.Name in skip_names:
                    continue
                field = getattr(getattr(obj, "Proxy", None), "SdfField", None)
                if field is None:
                    field = getattr(getattr(obj, "Proxy", None), "FRepField", None)
                
                if field is None:
                    continue

                try:
                    if not obj.ViewObject.Visibility:
                        continue
                except Exception:
                    pass
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

            return None
        except Exception as e:
            dm_logger.debug(f"get_sdf_hit failed: {e}")
            return None
