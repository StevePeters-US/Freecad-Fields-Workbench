import FreeCAD
import FreeCADGui
import math
from PySide import QtCore
from core import dm_logger
from core.dm_object import create_dm_object, get_meshing_type, get_meshing_resolution
from core.frep_mesher import mesh_timer
from tools.dm_base import DMBase

# Ensure we import the right storage classes. For now, defaulting to MarchingCubes
from core.frep.sdf.box import SdfBoxField
from core.frep.sdf.sphere import SdfSphereField
from core.frep.sdf.cylinder import SdfCylinderField

# Resolution for interactive preview (lower = faster updates)
_PREVIEW_RES = 10
# Resolution for final committed mesh (higher = more detail)
def _get_final_res():
    return int(get_meshing_resolution())


class PrimitiveCreatorBase(DMBase):
    """Base class for F-Rep primitive creator tools with live mesh preview."""
    def __init__(self):
        super().__init__()
        self._preview_obj = None     # Live FreeCAD object for preview
        self._update_pending = False  # Throttle rapid updates
        # Reset the shared timer so preview calls for this tool session are isolated
        mesh_timer.reset()
        # Primitive creation must snap to workplane, not arbitrary geometry surfaces.
        # Individual instances shadow the class variable to avoid changing global state.
        self.place_on_geometry = False

    def _get_preview_field(self):
        """Subclasses return the current field based on click state + current_point."""
        return None

    def _get_final_field(self):
        """Subclasses return the final field when placement is committed."""
        return None

    def _get_final_points(self):
        """Subclasses return the corner/boundary points for the committed object."""
        return None

    def update_preview(self):
        """Called on every mouse move by DMBase.handle_move. Updates the live mesh."""
        field = self._get_preview_field()
        if field is None:
            return

        if self._preview_obj is None:
            # Create the preview object for the first time
            # Use last part of class name without 'Creator' suffix
            name = type(self).__name__.replace("Creator", "")
            self._preview_obj = create_dm_object(name=name, shape_type="frep")

        # Throttle: only queue one update per frame
        if not self._update_pending:
            self._update_pending = True
            QtCore.QTimer.singleShot(0, lambda: self._apply_preview_field(field))

    def _apply_preview_field(self, field):
        self._update_pending = False
        if self._preview_obj is None or not self._preview_obj.Document:
            return
        try:
            proxy = self._preview_obj.Proxy
            if proxy is None:
                return
            proxy.FRepField = field
            self._preview_obj.touch()
            # Only recompute this one object for speed
            self._preview_obj.Document.recompute([self._preview_obj])
        except Exception as e:
            dm_logger.debug(f"PrimitiveCreatorBase preview update error: {e}")

    def _finalize_object(self, name):
        """Commit the preview object as the final result, upgrading its mesh resolution."""
        field = self._get_final_field()
        points = self._get_final_points()
        if field is None:
            return
        QtCore.QTimer.singleShot(0, lambda: self.__do_commit(name, field, points))
        self.finish()

    def __do_commit(self, name, field, points):
        obj = self._preview_obj
        if obj is None or not obj.Document:
            # fallback: create fresh
            obj = create_dm_object(name=name, shape_type="frep")

        # Rename to final name
        try:
            obj.Label = name
        except Exception:
            pass

        # Add points for editing
        if points is not None:
            try:
                if not hasattr(obj, "Points"):
                    obj.addProperty("App::PropertyVectorList", "Points", "FRep", "Control Points")
                obj.Points = points
            except Exception:
                pass

        # Upgrade resolution for final mesh
        primitive_name = type(self).__name__.replace("Creator", "")
        proxy = obj.Proxy
        proxy.FRepField = field
        
        # Set per-object properties
        if hasattr(obj, "MeshingResolution"):
            obj.MeshingResolution = float(_get_final_res())
        
        obj.touch()
        obj.Document.recompute([obj])
        # Print accumulated timer summary now that the tool is accepted
        mesh_timer.summary(f"{primitive_name} preview ({_PREVIEW_RES} res) + final ({int(getattr(obj, 'MeshingResolution', _get_final_res()))} res)")
        self._preview_obj = None  # Severed; the object is now the user's



    def _create_frep_object(self, name, field, points=None):
        """Helper to create the FreeCAD object and assign the field (for 1-shot creation)."""
        obj = create_dm_object(name=name, shape_type="frep")
        obj.Proxy.FRepField = field
        if points is not None:
            if not hasattr(obj, "Points"):
                obj.addProperty("App::PropertyVectorList", "Points", "FRep", "Control Points")
            obj.Points = points
        obj.touch()
        return obj


class BoxCreator(PrimitiveCreatorBase):
    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None
        self._height_drag_screen_y = None
        self._height_drag_base = None
        # Pre-load the active workplane so preview is correct before the 1st click
        visible_wps = self.get_visible_workplanes()
        if visible_wps:
            self.working_plane = visible_wps[0].getGlobalPlacement() if hasattr(visible_wps[0], "getGlobalPlacement") else visible_wps[0].Placement
        dm_logger.info("Box Tool: Click 1st corner")

    def on_button1_down(self, event_dict):
        pos = self.get_mouse_plane_pt(event_dict)
        if pos is None:
            return True

        if self.state == 0:
            # 1st click - anchor the tool
            self.points.append(pos)
            self.state = 1
            
            # Lock working_plane from the active workplane for consistent transforms
            if not getattr(self, "working_plane", None):
                visible_wps = self.get_visible_workplanes()
                if visible_wps:
                    self.working_plane = visible_wps[0].getGlobalPlacement() if hasattr(visible_wps[0], "getGlobalPlacement") else visible_wps[0].Placement
            
            dm_logger.info("Box Tool: Click 2nd corner")
            
        elif self.state == 1:
            # 2nd click - determines base size (x/y)
            self.points.append(pos)
            self.state = 2
            
            # Record screen position for height drag
            pos2d = event_dict.get("Position", (0, 0))
            self._height_drag_start_pos = (pos2d[0], pos2d[1])
            self._height_drag_base = pos
            
            dm_logger.info("Box Tool: Click height")
            
        elif self.state == 2:
            # 3rd click - determines height (z). Finalize shape.
            self.points.append(self.current_point)
            
            # Transition to a finalized state or finish tool
            self.state = 3 
            self._finalize_object("Box")
            # Note: Right click implicitly finishes the tool as handled by DMBase

        return True

    def on_move_state_1(self, event_dict):
        self.current_point = self.get_mouse_plane_pt(event_dict)

    def on_move_state_2(self, event_dict):
        """Height drag: move current_point along workplane normal."""
        if not hasattr(self, "_height_drag_screen_y") or self._height_drag_base is None:
            return
            
        wp = getattr(self, "working_plane", None)
        if wp is not None:
            normal = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        else:
            normal = FreeCAD.Vector(0, 0, 1)

        pos = event_dict.get("Position")
        if pos:
            try:
                # Provide x,y coordinates to get the view ray
                ray_p, ray_d = self.projector._get_view_ray(pos[0], pos[1])
                if ray_p and ray_d:
                    # UX-friendly 2D Pixel Projection Math:
                    p2 = self._height_drag_base
                    # 1. Get 3D camera vectors to define screen space
                    vd = self.view.getViewDirection()
                    # ViewDirection points INTO the screen
                    forward = FreeCAD.Vector(vd[0], vd[1], vd[2])
                    forward.normalize()
                    
                    ud = self.view.getUpDirection()
                    v_up = FreeCAD.Vector(ud[0], ud[1], ud[2])
                    v_up.normalize()
                    
                    v_right = v_up.cross(forward)
                    v_right.normalize()
                    # Re-derive up to ensure orthogonality
                    v_up = forward.cross(v_right)
                    v_up.normalize()
                    
                    # 2. Project 3D Normal into 2D screen coordinates (Right, Up)
                    # Note: We want to know how the Normal looks on screen
                    nx = normal.dot(v_right)
                    ny = normal.dot(v_up)
                    normal_2d = QtCore.QPointF(nx, ny)
                    n2d_len = math.sqrt(nx*nx + ny*ny)
                    
                    if n2d_len > 1e-4:
                        # 3. Calculate 2D mouse movement in screen space
                        # Initial mouse position was recorded in on_button1_down
                        curr_x, curr_y = pos[0], pos[1]
                        # Use cached initial position if available or event_dict if first time
                        if not hasattr(self, "_height_drag_start_pos"):
                            self._height_drag_start_pos = (curr_x, curr_y)
                            
                        dx = curr_x - self._height_drag_start_pos[0]
                        dy = self._height_drag_start_pos[1] - curr_y # Flip Y so + is UP
                        
                        # 4. Project mouse movement onto the 2D Normal axis
                        # Dot product gives pixels moved along the normal
                        proj_pixels = (dx * nx + dy * ny) / n2d_len
                        
                        # 5. Convert pixels to mm using the view's perspective/scale
                        # A simple way: find the 3D distance between two points 100px apart on the view plane at depth p2
                        try:
                            cam = self.view.getCameraNode()
                            dist_to_cam = (p2 - ray_p).Length
                            if hasattr(cam, "heightAngle"): # Perspective
                                fov = cam.heightAngle.getValue()
                                # viewport height in mm at this depth
                                vw_h_mm = 2.0 * dist_to_cam * math.tan(fov / 2.0)
                            else: # Ortho
                                vw_h_mm = cam.height.getValue()
                                
                            # Convert viewport height to px
                            viewer = self.view.getViewer()
                            vw_h_px = 1000.0
                            if hasattr(viewer, "getSize"):
                                sz = viewer.getSize()
                                vw_h_px = float(sz.height() if hasattr(sz, "height") else sz[1])
                                
                            mm_per_px = vw_h_mm / vw_h_px
                        except:
                            mm_per_px = 0.5 # fallback
                            
                        # Height is the pixel movement component scaled by mm/px, 
                        # boosted by 1/n2d_len to account for foreshortening (visual projection)
                        t2 = (proj_pixels * mm_per_px) / max(n2d_len, 0.1)
                        
                        self.current_point = p2 + normal * t2
                        return
            except Exception as e:
                dm_logger.debug(f"Height drag 2D projection failed: {e}")

        # Final Fallback to simple screen-Y delta if pixel projection completely fails
        curr_y = event_dict["Position"][1]
        start_y = getattr(self, "_height_drag_start_pos", (0, curr_y))[1]
        delta_px = curr_y - start_y
        height_mm = -delta_px / 4.0
        self.current_point = self._height_drag_base + normal * height_mm

    def _get_preview_field(self):
        if not self.points or self.current_point is None:
            return None
        if len(self.points) == 1:
            # Footprint preview: hold local Z constant at first point
            loc_p1 = self.to_local(self.points[0])
            loc_cur = self.to_local(self.current_point)
            # clamp local Z so footprint stays flat on the workplane
            loc_p2 = FreeCAD.Vector(loc_cur.x, loc_cur.y, loc_p1.z)
            p2 = self.to_global(loc_p2)
            return self._make_field(self.points[0], p2, self.points[0])
        elif len(self.points) == 2:
            return self._make_field(self.points[0], self.points[1], self.current_point)
        return None

    def _get_final_field(self):
        if len(self.points) == 3:
            return self._make_field(*self.points)
        return None

    def _make_field(self, p1, p2, p3):
        wp = getattr(self, "working_plane", None)
        
        loc_p1 = self.to_local(p1)
        loc_p2 = self.to_local(p2)
        loc_p3 = self.to_local(p3)
        
        size_x = abs(loc_p2.x - loc_p1.x)
        size_y = abs(loc_p2.y - loc_p1.y)
        # Height = distance of 3rd point along local Z from the base plane (local Z midpoint of p1 and p3)
        size_z = abs(loc_p3.z - loc_p1.z)
        if size_x < 0.01 or size_y < 0.01:
            return None
            
        cx = (loc_p1.x + loc_p2.x) / 2.0
        cy = (loc_p1.y + loc_p2.y) / 2.0
        cz = (loc_p1.z + loc_p3.z) / 2.0
        
        return SdfBoxField(FreeCAD.Vector(cx, cy, cz), FreeCAD.Vector(size_x, size_y, max(size_z, 0.01)), placement=wp)

    def _get_final_points(self):
        if len(self.points) < 3:
            return None
        wp = getattr(self, "working_plane", None)

        loc_p1 = self.to_local(self.points[0])
        loc_p2 = self.to_local(self.points[1])
        loc_p3 = self.to_local(self.points[2])
        
        size_x = abs(loc_p2.x - loc_p1.x)
        size_y = abs(loc_p2.y - loc_p1.y)
        size_z = abs(loc_p3.z - loc_p1.z)
        cx = (loc_p1.x + loc_p2.x) / 2.0
        cy = (loc_p1.y + loc_p2.y) / 2.0
        cz = (loc_p1.z + loc_p3.z) / 2.0
        
        pts_local = [
            FreeCAD.Vector(cx - size_x/2, cy - size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy - size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy + size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx - size_x/2, cy + size_y/2, cz - size_z/2),
            FreeCAD.Vector(cx - size_x/2, cy - size_y/2, cz + size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy - size_y/2, cz + size_z/2),
            FreeCAD.Vector(cx + size_x/2, cy + size_y/2, cz + size_z/2),
            FreeCAD.Vector(cx - size_x/2, cy + size_y/2, cz + size_z/2),
        ]
        return [self.to_global(pt) for pt in pts_local]


class SphereCreator(PrimitiveCreatorBase):
    def __init__(self):
        super().__init__()
        self.center = None
        self.current_point = None
        dm_logger.info("Sphere Tool: Click center")

    def on_button1_down(self, event_dict):
        pos = self.get_mouse_world_pos(event_dict)
        if pos is None:
            return True

        if self.state == 0:
            self.center = pos
            self.state = 1
            dm_logger.info("Sphere Tool: Click radius")
        elif self.state == 1:
            self.current_point = pos
            self.state = 2
            self._finalize_object("Sphere")

        return True

    def on_move_state_1(self, event_dict):
        self.current_point = self.get_mouse_world_pos(event_dict)

    def _get_preview_field(self):
        if self.center is None or self.current_point is None:
            return None
        radius = (self.current_point - self.center).Length
        if radius < 0.01:
            return None
        return SdfSphereField(self.center, radius)

    def _get_final_field(self):
        return self._get_preview_field()

    def _get_final_points(self):
        if self.center is None or self.current_point is None:
            return None
        radius = (self.current_point - self.center).Length
        return [self.center, self.center + FreeCAD.Vector(radius, 0, 0)]


class CylinderCreator(PrimitiveCreatorBase):
    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None
        dm_logger.info("Cylinder Tool: Click base center")

    def on_button1_down(self, event_dict):
        pos = self.get_mouse_world_pos(event_dict)
        if pos is None:
            return True

        if self.state == 0:
            self.points.append(pos)
            self.state = 1
            dm_logger.info("Cylinder Tool: Click radius")
        elif self.state == 1:
            self.points.append(pos)
            self.state = 2
            dm_logger.info("Cylinder Tool: Click height")
        elif self.state == 2:
            self.points.append(pos)
            self.current_point = pos
            self.state = 3
            self._finalize_object("Cylinder")

        return True

    def on_move_state_1(self, event_dict):
        self.current_point = self.get_mouse_world_pos(event_dict)

    def on_move_state_2(self, event_dict):
        self.current_point = self.get_mouse_world_pos(event_dict)

    def _get_preview_field(self):
        if not self.points or self.current_point is None:
            return None
        c_base = self.points[0]
        n, _ = self.get_base_plane()
        if self.state == 1:
            radius = (self.current_point - c_base).Length
            height = 1.0  # minimal placeholder
        else:
            radius = (self.points[1] - c_base).Length
            height = (self.current_point - c_base).dot(n)
        if radius < 0.01:
            return None
        center = c_base + n * (height / 2.0)
        return SdfCylinderField(center, n, radius, max(abs(height), 0.01))

    def _get_final_field(self):
        if len(self.points) < 3:
            return None
        c_base, p_rad, p_height = self.points
        n, _ = self.get_base_plane()
        radius = (p_rad - c_base).Length
        height = (p_height - c_base).dot(n)
        center = c_base + n * (height / 2.0)
        return SdfCylinderField(center, n, radius, abs(height))

    def _get_final_points(self):
        if len(self.points) < 3:
            return None
        return list(self.points)
