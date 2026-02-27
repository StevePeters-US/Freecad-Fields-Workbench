import FreeCAD
import Part
import FreeCADGui
from pivy import coin
import math
import traceback
from FCDirectModeling import dm_logger

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
            return
            
        try:
            info = self.view.getObjectInfo((pos[0], pos[1]))
            if info and "Object" in info and "Component" in info:
                obj_name = info["Object"]
                doc = FreeCAD.ActiveDocument
                if not doc:
                    return
                obj = doc.getObject(obj_name)
                if not obj:
                    return

                subname = info["Component"]
                if "Face" in subname:
                    face = obj.Shape.getElement(subname)
                    mouse_pt = self.view.getPoint(pos[0], pos[1])
                    
                    # More robust way to get UV: distToShape
                    # proj = face.projectPoint(mouse_pt) <- Missing in some FC versions
                    # Use Part.Vertex because distToShape requires a Shape, not a Point (geometry)
                    dists = face.distToShape(Part.Vertex(mouse_pt))
                    if dists and len(dists) >= 3:
                        dist, pts, params = dists
                        if pts:
                            target_pt = pts[0][0]
                            # params[0] can be (u, v) or (subname, (u, v)) depending on FC version/case
                            info = params[0]
                            if len(info) >= 2 and isinstance(info[0], (int, float, float)):
                                u, v = info[0], info[1]
                            elif len(info) >= 2 and isinstance(info[1], (list, tuple)):
                                u, v = info[1][0], info[1][1]
                            else:
                                # Fallback or skip if we can't find UV
                                return
                            normal = face.Surface.normal(u, v)
                        
                        # Snapped color (Greenish)
                        self.plane_mat.diffuseColor.setValue(0.2, 0.8, 0.4)
                        self.plane_mat.transparency.setValue(0.6)
                        
                        self.current_placement = FreeCAD.Placement(target_pt, FreeCAD.Rotation(FreeCAD.Vector(0,0,1), normal))
                        self._update_transform()
                        self.show()
                        return
        except Exception:
            dm_logger.exception("WorkPlaneManager.update error")
            
        # Default color (Blueish)
        self.plane_mat.diffuseColor.setValue(0.2, 0.6, 0.9)
        self.plane_mat.transparency.setValue(0.8)
        
        mouse_pt = self.view.getPoint(pos[0], pos[1])
        if mouse_pt:
            # Force Z=0 for default XY plane at origin
            mouse_pt.z = 0 
            self.current_placement = FreeCAD.Placement(mouse_pt, FreeCAD.Rotation())
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
