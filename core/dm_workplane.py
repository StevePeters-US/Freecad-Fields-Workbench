import FreeCAD
import Part
import math
from pivy import coin
from FCDirectModeling import dm_logger

class DMWorkPlane:
    def __init__(self, obj):
        obj.Proxy = self
        
        # Add a Placement property specifically for the work plane if we need explicit tracking,
        # otherwise we can just use the standard Placement of the FeaturePython object.
        # We'll rely on the standard obj.Placement for the actual 3D orientation.
        
        # A property to store the size of the grid if we wanted to make it static,
        # but the request is for it to scale to the viewport size.
        if not hasattr(obj, "Shape"):
            obj.addProperty("Part::PropertyPartShape", "Shape", "DirectModeling", "Shape")
        
        if not hasattr(obj, "Length"):
            obj.addProperty("App::PropertyLength", "Length", "DirectModeling", "Length of the grid")
            obj.Length = 100.0
        if not hasattr(obj, "Width"):
            obj.addProperty("App::PropertyLength", "Width", "DirectModeling", "Width of the grid")
            obj.Width = 100.0
            
    def execute(self, obj):
        pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None

class ViewProviderDMWorkPlane:
    def __init__(self, vobj):
        vobj.Proxy = self
        
    def attach(self, vobj):
        self.Object = vobj.Object
        
        self.root_node = coin.SoSeparator()
        self.root_node.setName("DM_WorkPlane_Feature_Root")
        
        # Make visuals strictly unpickable
        pick_style = coin.SoPickStyle()
        pick_style.style.setValue(coin.SoPickStyle.UNPICKABLE)
        self.root_node.addChild(pick_style)
        
        # Grid visual
        # Add a bright center marker to visualize the exactly (0,0,0) point of the grid
        self.center_sep = coin.SoSeparator()
        self.center_mat = coin.SoMaterial()
        self.center_mat.diffuseColor.setValue(1.0, 0.0, 0.0) # Red
        
        self.center_coords = coin.SoCoordinate3()
        self.center_coords.point.setValues(0, 4, [(-10.0, 0, 0), (10.0, 0, 0), (0, -10.0, 0), (0, 10.0, 0)])
        self.center_lines = coin.SoLineSet()
        self.center_lines.numVertices.setValues(0, 2, [2, 2]) # Two lines, each with 2 vertices
        self.center_sep.addChild(self.center_mat)
        self.center_sep.addChild(self.center_coords)
        self.center_sep.addChild(self.center_lines)
        self.root_node.addChild(self.center_sep)

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
        
        # Initial grid geometry
        l = self.Object.Length if hasattr(self.Object, "Length") else 100.0
        w = self.Object.Width if hasattr(self.Object, "Width") else 100.0
        self._setup_grid(l, w, 10)
        
        self.transform = coin.SoTransform()
        self.root_node.insertChild(self.transform, 0)
        self.root_node.addChild(self.grid_sep)
        
        vobj.addDisplayMode(self.root_node, "Standard")
        
        # To dynamically scale with the camera
        try:
            import FreeCADGui
            # Use the view associated with the ViewObject if possible, otherwise activeView
            self.view = vobj.ViewWindow if hasattr(vobj, "ViewWindow") else FreeCADGui.activeView()
            if self.view:
                self.callback_id = self.view.addEventCallback("SoEvent", self._on_event)
        except Exception as e:
            dm_logger.error(f"DMWorkPlane ViewProvider attach error: {e}")

    def _setup_grid(self, length, width, steps):
        points = []
        half_l = length / 2.0
        half_w = width / 2.0
        step_l = length / float(steps)
        step_w = width / float(steps)
        
        # Lines parallel to X (along width)
        for i in range(steps + 1):
            y = -half_w + i * step_w
            points.append((-half_l, y, 0))
            points.append((half_l, y, 0))
            
        # Lines parallel to Y (along length)
        for i in range(steps + 1):
            x = -half_l + i * step_l
            points.append((x, -half_w, 0))
            points.append((x, half_w, 0))
            
        self.grid_coords.point.setValues(0, len(points), points)
        self.grid_lines.numVertices.setValues(0, (steps + 1) * 2, [2] * ((steps + 1) * 2))
        
        f_points = [
            (-half_l, -half_w, 0),
            (half_l, -half_w, 0),
            (half_l, half_w, 0),
            (-half_l, half_w, 0)
        ]
        self.face_coords.point.setValues(0, 4, f_points)
        self.face_set.numVertices.setValue(4)

    def _on_event(self, event_dict):
        # Update scale dynamically based on camera
        if not hasattr(self, 'view') or self.view is None:
            return False
            
        try:
            cam_node = self.view.getCameraNode()
            if not cam_node:
                return False
                
            # The object's Placement already handles world position and orientation.
            # We ONLY want to apply the dynamic scale here.
            self.transform.translation.setValue(0, 0, 0)
            self.transform.rotation.setValue(0, 0, 0, 1)

            if self.Object and hasattr(self.Object, "Placement"):
                p = self.Object.Placement
                pos = p.Base
                
                # Calculate scale based on distance to camera
                cam_pos = FreeCAD.Vector(*cam_node.position.getValue().getValue())
                dist = (cam_pos - pos).Length
                
                if hasattr(cam_node, 'height') and hasattr(cam_node.height, 'getValue'):
                    viewport_height = cam_node.height.getValue()
                    scale = viewport_height / 300.0
                else:
                    fov = cam_node.heightAngle.getValue() if hasattr(cam_node, 'heightAngle') else 0.785
                    viewport_height = 2.0 * dist * math.tan(fov / 2.0)
                    scale = viewport_height / 300.0
                    
                self.transform.scaleFactor.setValue(scale, scale, scale)
        except Exception:
            pass
        return False

    def updateData(self, fp, prop):
        if prop == "Placement":
            # Nothing to do for translation/rotation as Coin3D parent handles it
            pass
        elif prop in ["Length", "Width"]:
            l = self.Object.Length if hasattr(self.Object, "Length") else 100.0
            w = self.Object.Width if hasattr(self.Object, "Width") else 100.0
            self._setup_grid(l, w, 10)

    def getDisplayModes(self, obj):
        return ["Standard"]

    def getDefaultDisplayMode(self):
        return "Standard"

    def setDisplayMode(self, mode):
        return mode

    def __del__(self):
        try:
            if hasattr(self, 'view') and hasattr(self, 'callback_id'):
                self.view.removeEventCallback("SoEvent", self.callback_id)
        except Exception:
            pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None

def create_dm_workplane(name="DM_WorkPlane", placement=None):
    doc = FreeCAD.activeDocument()
    if not doc:
        return None
        
    obj = doc.addObject("Part::FeaturePython", name)
    DMWorkPlane(obj)
    ViewProviderDMWorkPlane(obj.ViewObject)
    
    if placement:
        obj.Placement = placement
        
    return obj
