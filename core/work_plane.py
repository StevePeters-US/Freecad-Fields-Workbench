import FreeCAD
import Part
import FreeCADGui
from pivy import coin
import math
import traceback
from . import dm_logger
from core.input_manager import DMInputManager

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
        """Raycasts and updates the work plane transform using centralized logic."""
        from core.view_projector import ViewProjector
        projector = ViewProjector(self.view)
        
        # 1. Try to snap to surface geometry
        geo = projector.get_geometry_info(event_dict)
        if geo:
            world_hit, world_n, obj, subname = geo
            # Snapped color (Greenish)
            self.plane_mat.diffuseColor.setValue(0.2, 0.8, 0.4)
            self.plane_mat.transparency.setValue(0.6)
            
            # Basis vectors from normal
            z_axis = world_n
            global_z = FreeCAD.Vector(0, 0, 1)
            x_axis = global_z.cross(z_axis) if abs(z_axis.dot(global_z)) < 0.99 else FreeCAD.Vector(1, 0, 0)
            x_axis.normalize()
            y_axis = z_axis.cross(x_axis); y_axis.normalize()
            
            m = FreeCAD.Matrix(
                x_axis.x, y_axis.x, z_axis.x, world_hit.x,
                x_axis.y, y_axis.y, z_axis.y, world_hit.y,
                x_axis.z, y_axis.z, z_axis.z, world_hit.z,
                0.0,      0.0,      0.0,      1.0
            )
            self.current_placement = FreeCAD.Placement(m)
            self.transform.scaleFactor.setValue(1.0, 1.0, 1.0)
            self._update_transform()
            self.show()
            return

        # 2. Fallback: Camera-Facing plane at scene focus depth
        self.plane_mat.diffuseColor.setValue(0.2, 0.6, 0.9)
        self.plane_mat.transparency.setValue(0.8)
        
        pos_2d = DMInputManager.get_instance().get_mouse_pos(event_dict)
        mouse_pt = None
        try: mouse_pt = self.view.getPoint(pos_2d[0], pos_2d[1])
        except: pass
        
        if not mouse_pt:
            focus = self.view.getFocus() if hasattr(self.view, "getFocus") else FreeCAD.Vector(0,0,0)
            mouse_pt = focus

        self.current_placement = DMInputManager.get_instance().get_view_transform(self.view, mouse_pt)
        
        # Dynamic scaling
        try:
            cam_node = self.view.getCameraNode()
            if cam_node:
                cam_pos = FreeCAD.Vector(*cam_node.position.getValue().getValue())
                dist = (cam_pos - mouse_pt).Length
                if hasattr(cam_node, 'height'): scale = cam_node.height.getValue() / 50.0
                else: 
                    fov = cam_node.heightAngle.getValue() if hasattr(cam_node, 'heightAngle') else 0.785
                    scale = (2.0 * dist * math.tan(fov / 2.0)) / 50.0
                self.transform.scaleFactor.setValue(scale, scale, scale)
        except: pass
        
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
