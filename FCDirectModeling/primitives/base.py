"""
Base classes for SDF primitive creators.
"""

import FreeCAD
import FreeCADGui
from pivy import coin
from PySide import QtCore
import Part

# Preview target variables removed since native BRep does not need point clouds
def log_to_file(msg):
    FreeCAD.Console.PrintLog(f"[SDF] {msg}\n")


class PrimitiveCreatorBase:
    def __init__(self):
        log_to_file(f"PrimitiveCreatorBase: Init {self.__class__.__name__}")
        self.view = FreeCADGui.ActiveDocument.ActiveView
        self.callback = self.view.addEventCallback("SoEvent", self.event_cb)

        self.start_point = None
        self.current_point = None
        self.center = None
        self.state = 0

        self.sg = coin.SoSeparator()
        self.sg.ref()

        # Guide Material
        self.material = coin.SoMaterial()
        self.material.diffuseColor.setValue(0.2, 0.6, 0.8)
        self.material.transparency.setValue(0.5)
        self.sg.addChild(self.material)

        # Shape Nodes (subclasses may add to preview_sep)
        self.preview_sep = coin.SoSeparator()
        self.sg.addChild(self.preview_sep)

        self.view.getSceneGraph().addChild(self.sg)

    def terminate(self):
        log_to_file("PrimitiveCreatorBase: Terminating...")
        try:
            if self.callback:
                self.view.removeEventCallback("SoEvent", self.callback)
                self.callback = None
            if self.sg:
                self.view.getSceneGraph().removeChild(self.sg)
                self.sg = None
            log_to_file("PrimitiveCreatorBase: Terminated successfully.")
        except Exception as e:
            log_to_file(f"PrimitiveCreatorBase: Error terminating: {e}")
            FreeCAD.Console.PrintError(f"PrimitiveCreatorBase: Error terminating: {e}\n")
            import traceback
            traceback.print_exc()

    def get_point_on_plane(self, event_dict):
        try:
            pos = event_dict["Position"]
            point_on_focal_plane = self.view.getPoint(pos[0], pos[1])
            view_dir = self.view.getViewDirection()
            cam = self.view.getCameraNode()

            if cam.getTypeId() == coin.SoOrthographicCamera.getClassTypeId():
                ray_origin = point_on_focal_plane
                ray_dir = view_dir
            else:
                cam_pos_sb = cam.position.getValue()
                ray_origin = FreeCAD.Vector(cam_pos_sb[0], cam_pos_sb[1], cam_pos_sb[2])
                ray_dir = point_on_focal_plane - ray_origin
                ray_dir.normalize()

            plane_normal = FreeCAD.Vector(0, 0, 1)
            plane_point = FreeCAD.Vector(0, 0, 0)

            denom = ray_dir.dot(plane_normal)
            if abs(denom) < 1e-6:
                return plane_point

            t = (plane_point - ray_origin).dot(plane_normal) / denom
            return ray_origin + ray_dir * t
        except Exception as e:
            FreeCAD.Console.PrintError(f"get_point_on_plane: Error: {e}\n")
            return FreeCAD.Vector(0, 0, 0)

    def get_closest_point_on_axis(self, event_dict, axis_start, axis_dir):
        """Returns the point on the given axis closest to the cursor ray."""
        try:
            pos = event_dict["Position"]
            point_on_focal_plane = self.view.getPoint(pos[0], pos[1])
            cam = self.view.getCameraNode()

            if cam.getTypeId() == coin.SoOrthographicCamera.getClassTypeId():
                ray_origin = point_on_focal_plane
                ray_dir = self.view.getViewDirection()
            else:
                cam_pos_sb = cam.position.getValue()
                ray_origin = FreeCAD.Vector(cam_pos_sb[0], cam_pos_sb[1], cam_pos_sb[2])
                ray_dir = point_on_focal_plane - ray_origin
                ray_dir.normalize()

            P1, V1 = ray_origin, ray_dir
            P2, V2 = axis_start, axis_dir

            DP = P2 - P1
            v12 = V1.dot(V2)
            v11 = V1.dot(V1)
            v22 = V2.dot(V2)
            det = v11 * v22 - v12 * v12

            if abs(det) < 1e-6:
                return P2

            dp_v1 = DP.dot(V1)
            dp_v2 = DP.dot(V2)
            u = (v12 * dp_v1 - v11 * dp_v2) / det
            return P2 + V2 * u
        except Exception as e:
            FreeCAD.Console.PrintError(f"get_closest_point_on_axis Error: {e}\n")
            return axis_start

    def event_cb(self, event_dict):
        try:
            event_type = event_dict["Type"]
            if event_type == "SoMouseButtonEvent":
                if event_dict["State"] == "DOWN" and event_dict["Button"] == "BUTTON1":
                    self.handle_click(event_dict)
            elif event_type == "SoLocation2Event":
                self.handle_move(event_dict)
            elif event_type == "SoKeyboardEvent":
                if event_dict["State"] == "DOWN" and str(event_dict["Key"]).upper() == "ESCAPE":
                    QtCore.QTimer.singleShot(0, self.terminate)
            return False
        except Exception as e:
            FreeCAD.Console.PrintError(f"PrimitiveCreatorBase: Event Callback Error: {e}\n")
            import traceback
            traceback.print_exc()
            return False

    def handle_click(self, event_dict):
        pass

    def handle_move(self, event_dict):
        pass


class BRepPrimitiveCreator(PrimitiveCreatorBase):
    """Base for creators that produce a persistent BRep Part object."""

    def __init__(self):
        super().__init__()
        self.obj = None
        
        # We restore the Coin3D edge nodes for interactive wireframe previews
        self.edge_mat = coin.SoMaterial()
        self.edge_mat.diffuseColor.setValue(0.0, 1.0, 0.0) # Bright green
        self.preview_sep.addChild(self.edge_mat)

        self.edge_coords = coin.SoCoordinate3()
        self.preview_sep.addChild(self.edge_coords)

        self.edge_lines = coin.SoIndexedLineSet()
        self.preview_sep.addChild(self.edge_lines)

    def finish(self):
        # Subclasses should handle their specific creation and finalization
        self.obj = None # Ensure we don't delete on terminate
        QtCore.QTimer.singleShot(0, self.terminate)

    def terminate(self):
        # If we cancel during creation, self.obj is deleted
        if hasattr(self, 'obj') and self.obj:
            try:
                name = self.obj.Name
                FreeCAD.activeDocument().removeObject(name)
                FreeCAD.activeDocument().recompute()
            except Exception as e:
                log_to_file(f"Error removing temporary object {name}: {e}")
        super().terminate()

    def create_generic_part(self, type_name, name, properties_values):
        """
        Generic helper to create a Part document object and set properties.
        :param type_name: e.g. "Part::Sphere"
        :param name: e.g. "Sphere"
        :param properties_values: dict of {Name: Value}
        """
        doc = FreeCAD.activeDocument()
        if not doc:
            doc = FreeCAD.newDocument()

        obj = doc.addObject(type_name, name)

        for prop_name, val in properties_values.items():
            if hasattr(obj, prop_name):
                setattr(obj, prop_name, val)

        doc.recompute()
        FreeCAD.Console.PrintMessage(f"{name} created successfully.\n")
        return obj

    def update_preview(self, shape, placement=None):
        """
        Updates the Coin3D preview with a standard BRep shape.
        :param shape: A FreeCAD Part.Shape object
        :param placement: Optional FreeCAD.Placement to apply
        """
        try:
            if placement:
                shape = shape.copy()
                shape.Placement = placement
                
            # Extract and Draw Edges
            edges = shape.Edges
            edge_verts = []
            edge_indices = []
            current_idx = 0
            
            for e in edges:
                # Discretize edge
                pts = e.discretize(Deflection=0.05)
                if not pts: continue
                
                # Add points
                edge_verts.extend([v for v in pts])
                
                # Add indices for this line strip
                num_pts = len(pts)
                indices = list(range(current_idx, current_idx + num_pts))
                indices.append(-1)
                edge_indices.extend(indices)
                
                current_idx += num_pts
                
            self.edge_coords.point.setValues(0, len(edge_verts), edge_verts)
            self.edge_lines.coordIndex.setValues(0, len(edge_indices), edge_indices)
            
            self.view.redraw()
            
        except Exception as e:
            FreeCAD.Console.PrintError(f"Preview Tessellation Failed: {e}\n")
