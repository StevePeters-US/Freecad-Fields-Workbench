import FreeCAD
import Part
import math
from pivy import coin
from . import dm_logger

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
        if not hasattr(obj, "GridSpacing"):
            obj.addProperty("App::PropertyLength", "GridSpacing", "DirectModeling", "Spacing between grid lines")
            obj.GridSpacing = 10.0
            
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
        l = self.Object.Length.Value if hasattr(self.Object.Length, "Value") else float(getattr(self.Object, "Length", 100.0))
        w = self.Object.Width.Value if hasattr(self.Object.Width, "Value") else float(getattr(self.Object, "Width", 100.0))
        s = self.Object.GridSpacing.Value if hasattr(self.Object, "GridSpacing") and hasattr(self.Object.GridSpacing, "Value") else float(getattr(self.Object, "GridSpacing", 10.0))
        self._setup_grid(l, w, s)
        
        self.transform = coin.SoTransform()
        self.root_node.insertChild(self.transform, 0)
        self.root_node.addChild(self.grid_sep)
        
        vobj.addDisplayMode(self.root_node, "Standard")
        
        # No dynamic scale event callback needed anymore.

    def _setup_grid(self, length, width, spacing):
        points = []
        half_l = length / 2.0
        half_w = width / 2.0
        
        # Avoid zero or negative spacing
        if spacing <= 0:
            spacing = 10.0
            
        steps_l = int(length / spacing)
        steps_w = int(width / spacing)
        
        # Center the grid lines nicely
        # Lines parallel to X (along width)
        y_starts = [0]
        for i in range(1, int(half_w / spacing) + 1):
            y_starts.append(i * spacing)
            y_starts.append(-i * spacing)
            
        for y in y_starts:
            points.append((-half_l, y, 0))
            points.append((half_l, y, 0))
            
        # Lines parallel to Y (along length)
        x_starts = [0]
        for i in range(1, int(half_l / spacing) + 1):
            x_starts.append(i * spacing)
            x_starts.append(-i * spacing)
            
        for x in x_starts:
            points.append((x, -half_w, 0))
            points.append((x, half_w, 0))
            
        self.grid_coords.point.setNum(len(points))
        if len(points) > 0:
            self.grid_coords.point.setValues(0, len(points), points)
            
        num_lines = len(points) // 2
        self.grid_lines.numVertices.setNum(num_lines)
        if num_lines > 0:
            self.grid_lines.numVertices.setValues(0, num_lines, [2] * num_lines)
        
        f_points = [
            (-half_l, -half_w, 0),
            (half_l, -half_w, 0),
            (half_l, half_w, 0),
            (-half_l, half_w, 0)
        ]
        self.face_coords.point.setValues(0, 4, f_points)
        self.face_set.numVertices.setValue(4)



    def updateData(self, fp, prop):
        if prop == "Placement":
            # Nothing to do for translation/rotation as Coin3D parent handles it
            pass
        elif prop in ["Length", "Width", "GridSpacing"]:
            l = self.Object.Length.Value if hasattr(self.Object.Length, "Value") else float(getattr(self.Object, "Length", 100.0))
            w = self.Object.Width.Value if hasattr(self.Object.Width, "Value") else float(getattr(self.Object, "Width", 100.0))
            s = self.Object.GridSpacing.Value if hasattr(self.Object.GridSpacing, "Value") else float(getattr(self.Object, "GridSpacing", 10.0))
            self._setup_grid(l, w, s)

    def getDisplayModes(self, obj):
        return ["Standard"]

    def getDefaultDisplayMode(self):
        return "Standard"

    def setDisplayMode(self, mode):
        return mode

    def __del__(self):
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
