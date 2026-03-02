import FreeCAD
import Part
import FreeCADGui
from pivy import coin
import math
import traceback
from . import dm_logger

class WorkPlaneManager:
    """
    Manages the dynamic work plane detection and its visual representation in the 3D viewport.
    """
    def __init__(self, view):
        self.view = view
        self.root_node = coin.SoSeparator()
        self.root_node.setName("DM_WorkPlane_Root")
        
        # Make visuals strictly unpickable
        pick_style = coin.SoPickStyle()
        pick_style.style.setValue(coin.SoPickStyle.UNPICKABLE)
        self.root_node.addChild(pick_style)
        
        # Grid visual
        self.grid_sep = coin.SoSeparator()
        
        # Material for grid and plane
        self.plane_mat = coin.SoMaterial()
        self.plane_mat.diffuseColor.setValue(0.2, 0.6, 0.9)
        self.plane_mat.transparency.setValue(0.7)
        self.grid_sep.addChild(self.plane_mat)
        
        # Grid Coord and LineSet
        self.grid_coords = coin.SoCoordinate3()
        self.grid_lines = coin.SoLineSet()
        self.grid_sep.addChild(self.grid_coords)
        self.grid_sep.addChild(self.grid_lines)
        
        # Semi-transparent rectangle
        self.face_coords = coin.SoCoordinate3()
        self.face_set = coin.SoFaceSet()
        self.grid_sep.addChild(self.face_coords)
        self.grid_sep.addChild(self.face_set)
        
        # Initial grid geometry (100x100 grid centered at origin)
        self._setup_grid(100, 10)
        
        self.transform = coin.SoTransform()
        self.root_node.insertChild(self.transform, 0)
        self.root_node.addChild(self.grid_sep)
        
        self.is_visible = False
        self.current_placement = FreeCAD.Placement()

    def _setup_grid(self, size, steps):
        """Sets up the grid lines and the background rectangle."""
        points = []
        half = size / 2.0
        step_val = size / float(steps)
        
        # Lines parallel to Y axis
        for i in range(steps + 1):
            x = -half + i * step_val
            points.append((x, -half, 0))
            points.append((x, half, 0))
            
        # Lines parallel to X axis
        for i in range(steps + 1):
            y = -half + i * step_val
            points.append((-half, y, 0))
            points.append((half, y, 0))
            
        self.grid_coords.point.setValues(0, len(points), points)
        self.grid_lines.numVertices.setValues(0, (steps + 1) * 2, [2] * ((steps + 1) * 2))
        
        # Background rectangle
        f_points = [
            (-half, -half, 0),
            (half, -half, 0),
            (half, half, 0),
            (-half, half, 0)
        ]
        self.face_coords.point.setValues(0, 4, f_points)
        self.face_set.numVertices.setValue(4)

    def show(self):
        if not self.is_visible:
            sg = self.view.getSceneGraph()
            sg.addChild(self.root_node)
            self.is_visible = True

    def hide(self):
        if self.is_visible:
            sg = self.view.getSceneGraph()
            sg.removeChild(self.root_node)
            self.is_visible = False

    def update(self, event_dict):
        """Raycasts and updates the work plane transform."""
        pos = event_dict.get("Position")
        if not pos:
            dm_logger.debug("WorkPlaneManager: No position in event_dict")
            return
            
        try:
            info = self.view.getObjectInfo((pos[0], pos[1]))
            if info and "Object" in info and "Component" in info:
                obj_name = info["Object"]
                doc = FreeCAD.ActiveDocument
                if not doc:
                    dm_logger.debug("WorkPlaneManager: No active document")
                    return
                obj = doc.getObject(obj_name)
                if not obj:
                    dm_logger.debug(f"WorkPlaneManager: Could not find object {obj_name}")
                    return

                subname = info["Component"]
                dm_logger.debug(f"WorkPlaneManager: Hovered over {obj_name}.{subname}")
                if "Face" in subname:
                    face = obj.Shape.getElement(subname)
                    mouse_pt = self.view.getPoint(pos[0], pos[1])
                    
                    dm_logger.debug(f"WorkPlaneManager: Found face, raycasting hit point...")
                    # More robust way to get UV: distToShape
                    # proj = face.projectPoint(mouse_pt) <- Missing in some FC versions
                    # Use Part.Vertex because distToShape requires a Shape, not a Point (geometry)
                    dists = face.distToShape(Part.Vertex(mouse_pt))
                    if dists and len(dists) > 0:
                        # dists[0] is the distance
                        # dists[1] is a list of sub-component info for face
                        # dists[2] is a list of sub-component info for vertex
                        # For face-vertex distance, dists[0][1] usually contains the point and UV
                        info_param = dists[0][1]
                        u, v = None, None
                        target_pt = None
                        
                        if isinstance(info_param, (list, tuple)) and len(info_param) >= 2:
                            target_pt = info_param[0]
                            # UV is usually in the second element of the info tuple
                            if len(info_param) >= 2 and isinstance(info_param[1], (list, tuple)):
                                params = info_param[1]
                                if len(params) >= 2:
                                    u, v = params[0], params[1]
                                    
                        if u is None or target_pt is None:
                            dm_logger.debug(f"WorkPlaneManager: Could not parse UV or Point from {info_param}")
                            return
                            
                            normal = face.Surface.normal(u, v)
                            dm_logger.debug(f"WorkPlaneManager: Face normal {normal}")
                        
                            z_axis = FreeCAD.Vector(normal)
                            z_axis.normalize()
                            global_z = FreeCAD.Vector(0, 0, 1)
                            if abs(z_axis.dot(global_z)) > 1 - 1e-6:
                                dm_logger.debug("WorkPlaneManager: Normal is parallel to Z")
                                x_axis = FreeCAD.Vector(1, 0, 0)
                            else:
                                dm_logger.debug("WorkPlaneManager: Normal is skewed, projecting X")
                                x_axis = global_z.cross(z_axis)
                                x_axis.normalize()
                                
                            y_axis = z_axis.cross(x_axis)
                            y_axis.normalize()
                            
                            m = FreeCAD.Matrix(
                                x_axis.x, y_axis.x, z_axis.x, 0.0,
                                x_axis.y, y_axis.y, z_axis.y, 0.0,
                                x_axis.z, y_axis.z, z_axis.z, 0.0,
                                0.0,      0.0,      0.0,      1.0
                            )
                            
                            # Snapped color (Greenish)
                            self.plane_mat.diffuseColor.setValue(0.2, 0.8, 0.4)
                            self.plane_mat.transparency.setValue(0.6)
                            
                            self.current_placement = FreeCAD.Placement(target_pt, FreeCAD.Rotation(m))
                            # Reset scale when attached to face
                            self.transform.scaleFactor.setValue(1.0, 1.0, 1.0)
                            self._update_transform()
                            self.show()
                            return
                    else:
                        dm_logger.debug("WorkPlaneManager: distToShape returned invalid dists")
        except Exception as e:
            dm_logger.error(f"WorkPlaneManager.update error: {e}")
            dm_logger.exception("WorkPlaneManager.update exception trace")
            
        # Default color (Blueish)
        self.plane_mat.diffuseColor.setValue(0.2, 0.6, 0.9)
        self.plane_mat.transparency.setValue(0.8)
        
        mouse_pt = self.view.getPoint(pos[0], pos[1])
        if mouse_pt:
            # Face camera and scale
            cam_node = self.view.getCameraNode()
            if cam_node:
                try:
                    vd = self.view.getViewDirection()
                    ud = self.view.getUpDirection()
                    z_axis = FreeCAD.Vector(-vd[0], -vd[1], -vd[2])
                    z_axis.normalize()
                    y_axis = FreeCAD.Vector(ud[0], ud[1], ud[2])
                    y_axis.normalize()
                    x_axis = y_axis.cross(z_axis)
                    x_axis.normalize()
                    y_axis = z_axis.cross(x_axis)
                    
                    m = FreeCAD.Matrix(
                        x_axis.x, y_axis.x, z_axis.x, 0.0,
                        x_axis.y, y_axis.y, z_axis.y, 0.0,
                        x_axis.z, y_axis.z, z_axis.z, 0.0,
                        0.0,      0.0,      0.0,      1.0
                    )
                    rot = FreeCAD.Rotation(m)
                except Exception as e:
                    dm_logger.error(f"WorkPlaneManager rotation fallback: {e}")
                    rot = FreeCAD.Rotation()
                
                self.current_placement = FreeCAD.Placement(mouse_pt, rot)
                
                # Dynamic scaling
                try:
                    cam_pos = FreeCAD.Vector(*cam_node.position.getValue().getValue())
                    dist = (cam_pos - mouse_pt).Length
                    
                    if hasattr(cam_node, 'height') and hasattr(cam_node.height, 'getValue'):
                        viewport_height = cam_node.height.getValue()
                        scale = viewport_height / 50.0
                    else:
                        fov = cam_node.heightAngle.getValue() if hasattr(cam_node, 'heightAngle') else 0.785
                        viewport_height = 2.0 * dist * math.tan(fov / 2.0)
                        scale = viewport_height / 50.0
                except Exception as e:
                    dm_logger.error(f"WorkPlaneManager scaling fallback: {e}")
                    scale = 1.0
                
                self.transform.scaleFactor.setValue(scale, scale, scale)
            else:
                self.current_placement = FreeCAD.Placement(mouse_pt, FreeCAD.Rotation())
                self.transform.scaleFactor.setValue(1.0, 1.0, 1.0)
                
            self._update_transform()
            self.show()

    def _update_transform(self):
        p = self.current_placement
        pos = p.Base
        rot = p.Rotation.Q
        
        self.transform.translation.setValue(pos.x, pos.y, pos.z)
        self.transform.rotation.setValue(rot[0], rot[1], rot[2], rot[3])

    def get_placement(self):
        return self.current_placement
