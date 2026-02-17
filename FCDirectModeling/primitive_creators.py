
import FreeCAD
import FreeCADGui
import Part
from pivy import coin
from PySide import QtCore, QtGui
import FCDirectModeling.sdf_renderer as sdf_renderer
import FCDirectModeling.sdf_lib as sdf_lib
import FCDirectModeling.sdf_utils as sdf_utils
import numpy as np
import math

import os

def log_to_file(msg):
    with open("/tmp/fc_debug.log", "a") as f:
        f.write(msg + "\n")
        f.flush()

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
        
        
        # Guide Material (for non-SDF or wireframe guides)
        self.material = coin.SoMaterial()
        self.material.diffuseColor.setValue(0.2, 0.6, 0.8)
        self.material.transparency.setValue(0.5)
        self.sg.addChild(self.material)
        
        # Shape Nodes (To be added by subclasses)
        self.preview_sep = coin.SoSeparator()
        self.sg.addChild(self.preview_sep)
        
        # SDF Preview Nodes
        self.sdf_sep = coin.SoSeparator()
        self.sg.addChild(self.sdf_sep)
        
        self.sdf_mat = coin.SoMaterial()
        self.sdf_mat.diffuseColor.setValue(0.7, 0.7, 0.7) # Light Gray
        self.sdf_sep.addChild(self.sdf_mat)
        
        self.sdf_trans = coin.SoTransform()
        self.sdf_sep.addChild(self.sdf_trans)
        
        self.sdf_coords = coin.SoCoordinate3()
        self.sdf_sep.addChild(self.sdf_coords)
        
        self.sdf_norm = coin.SoNormal()
        self.sdf_sep.addChild(self.sdf_norm)
        
        self.sdf_faces = coin.SoIndexedFaceSet()
        self.sdf_sep.addChild(self.sdf_faces)
        
        self.view.getSceneGraph().addChild(self.sg)
        
    def terminate(self):
        log_to_file("PrimitiveCreatorBase: Terminating...")
        try:
            if self.callback:
                self.view.removeEventCallback("SoEvent", self.callback)
                self.callback = None
            if self.sg:
                self.view.getSceneGraph().removeChild(self.sg)
                # self.sg.unref() # Possible double-free crash?
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
            FreeCAD.Console.PrintMessage(f"get_point_on_plane: Position={pos}\n")
            
            point_on_focal_plane = self.view.getPoint(pos[0], pos[1])
            FreeCAD.Console.PrintMessage(f"get_point_on_plane: point_on_focal_plane={point_on_focal_plane}\n")
            
            view_dir = self.view.getViewDirection()
            FreeCAD.Console.PrintMessage(f"get_point_on_plane: view_dir={view_dir}\n")
            
            cam = self.view.getCameraNode()
            FreeCAD.Console.PrintMessage(f"get_point_on_plane: cam={cam.getTypeId().getName()}\n")
            
            if cam.getTypeId() == coin.SoOrthographicCamera.getClassTypeId():
                ray_origin = point_on_focal_plane
                ray_dir = view_dir
            else:
                cam_pos_sb = cam.position.getValue()
                ray_origin = FreeCAD.Vector(cam_pos_sb[0], cam_pos_sb[1], cam_pos_sb[2])
                ray_dir = point_on_focal_plane - ray_origin
                ray_dir.normalize()
                
            # Plane Z=0
            plane_normal = FreeCAD.Vector(0,0,1)
            plane_point = FreeCAD.Vector(0,0,0)
            
            denom = ray_dir.dot(plane_normal)
            if abs(denom) < 1e-6: return plane_point
            
            t = (plane_point - ray_origin).dot(plane_normal) / denom
            pt = ray_origin + ray_dir * t
            FreeCAD.Console.PrintMessage(f"get_point_on_plane: Result={pt}\n")
            return pt
        except Exception as e:
            FreeCAD.Console.PrintError(f"get_point_on_plane: Error: {e}\n")
            return FreeCAD.Vector(0,0,0)

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

    def handle_click(self, event_dict): pass
    def handle_move(self, event_dict): pass

    def update_sdf_preview(self, sdf_obj):
        """
        Updates the SDF preview mesh from the given SDF object.
        """
        try:
            # Generate mesh (Low Res for speed)
            verts, faces, normals = sdf_lib.mesh_from_sdf(sdf_obj, resolution=16, margin=0.1)
            
            if verts is not None and len(verts) > 0:
                self.sdf_coords.point.setValues(0, len(verts), verts)
                if normals is not None:
                    self.sdf_norm.vector.setValues(0, len(normals), normals)
                
                # Faces
                # Flatten faces (M, 3) -> list with -1 separator
                flat_faces = []
                for f in faces:
                    flat_faces.extend([f[0], f[1], f[2], -1])
                
                self.sdf_faces.coordIndex.setValues(0, len(flat_faces), flat_faces)
            else:
                self.sdf_coords.point.setNum(0)
                self.sdf_faces.coordIndex.setNum(0)

        except Exception as e:
            FreeCAD.Console.PrintError(f"SDF Preview Error: {e}\n")
            self.sdf_faces.coordIndex.setNum(0)


    def get_closest_point_on_axis(self, event_dict, axis_start, axis_dir):
        """
        Finds the point on the given axis that is closest to the ray cast from the cursor.
        :param axis_start: Vector, start of the axis (e.g. center of cone)
        :param axis_dir: Vector, direction of the axis (normalized)
        """
        try:
            # Ray from camera
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
            
            # Intersection of two lines (approximate)
            # L1: P1 + t * V1 (Ray)
            # L2: P2 + u * V2 (Axis)
            # Find u.
            
            P1 = ray_origin
            V1 = ray_dir
            P2 = axis_start
            V2 = axis_dir
            
            # Vector between starts
            DP = P2 - P1
            
            # Dot products
            v12 = V1.dot(V2)
            v11 = V1.dot(V1) # 1.0
            v22 = V2.dot(V2) # 1.0
            
            # Determinant
            det = v11 * v22 - v12 * v12
            
            # If parallel?
            if abs(det) < 1e-6:
                return P2 # Default to start
            
            dp_v1 = DP.dot(V1)
            dp_v2 = DP.dot(V2)
            
            # u = (v12 * dp_v1 - v11 * dp_v2) / det
            u = (v12 * dp_v1 - v11 * dp_v2) / det
            
            # Point on axis
            return P2 + V2 * u
            
        except Exception as e:
            FreeCAD.Console.PrintError(f"get_closest_point_on_axis Error: {e}\n")
            return axis_start

class SDFPrimitiveCreator(PrimitiveCreatorBase):
    def finish(self):
        """
        Called when interaction is complete.
        Creates object and schedules safe termination.
        """
        self.create_object()
        # Defer termination to avoid crashing Coin3D while handling event
        QtCore.QTimer.singleShot(0, self.terminate)

    def create_generic_sdf(self, type_name, properties_list, properties_values):
        """
        Generic helper to create SDF object.
        :param type_name: e.g. "SDF_Sphere"
        :param properties_list: list of tuples (Type, Name, Group, Tooltip)
        :param properties_values: dict of {Name: Value}
        :return: created object
        """
        doc = FreeCAD.activeDocument()
        if not doc: doc = FreeCAD.newDocument()
        
        # Use Factory
        obj = sdf_utils.SDFObjectFactory.create_sdf_object(doc, type_name, sdf_renderer.SDFBoxFeature)
        
        # Add Properties
        for prop_def in properties_list:
            # prop_def: (Type, Name, Group, Tooltip)
            obj.addProperty(prop_def[0], prop_def[1], prop_def[2], prop_def[3])
            
        # Common Properties
        sdf_utils.SDFObjectFactory.add_common_properties(obj)
        
        # Placement
        obj.Placement.Base = self.center
        
        # ViewProvider
        sdf_utils.SDFObjectFactory.setup_view_provider(obj)
        
        # Set Values
        for prop_name, val in properties_values.items():
            setattr(obj, prop_name, val)
            
        doc.recompute()
        FreeCAD.Console.PrintMessage(f"{type_name} created successfully.\n")
        return obj

class SphereCreator(SDFPrimitiveCreator):
    def __init__(self):
        super().__init__()
        log_to_file("SphereCreator: Initializing...")
        self.radius = 1.0
        log_to_file("SphereCreator: Scene graph created.")
        
    def handle_click(self, event_dict):
        log_to_file(f"SphereCreator: Click! State={self.state}")
        pt = self.get_point_on_plane(event_dict)
        
        if self.state == 0:
            self.center = pt
            # Move SDF Preview to center
            # self.sdf_trans is available from Base
            self.sdf_trans.translation.setValue(pt.x, pt.y, pt.z)
            self.state = 1
        elif self.state == 1:
            # Finish
            self.handle_move(event_dict) # Final update
            
            log_to_file(f"{self.__class__.__name__}: Finishing interaction...")
            self.finish()
            return True
            
    def handle_move(self, event_dict):
        if self.state == 1:
            pt = self.get_point_on_plane(event_dict)
            self.radius = (pt - self.center).Length
            
            # Update SDF
            # Create temp SDF object
            sdf = sdf_lib.SDFSphere(self.radius)
            self.update_sdf_preview(sdf)
            self.view.redraw()
            
    def create_object(self):
        log_to_file("SphereCreator: Creating object...")
        try:
            self.create_generic_sdf(
                "SDF_Sphere",
                [("App::PropertyLength", "Radius", "SDF", "Sphere Radius")],
                {"Radius": self.radius}
            )
            log_to_file("SphereCreator: Object created successfully.")
        except Exception as e:
            msg = f"SphereCreator: Error creating object: {e}"
            log_to_file(msg)
            FreeCAD.Console.PrintError(msg + "\n")
            import traceback
            traceback.print_exc()


class ConeCreator(SDFPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.radius = 0.1 # Default internal
        self.height = 0.1
        
    def handle_click(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
        if self.state == 0: # Center
            self.center = pt
            self.sdf_trans.translation.setValue(pt.x, pt.y, pt.z)
            self.state = 1
        elif self.state == 1: # Radius
            self.state = 2
            # No drag start Y needed for 3D logic
        elif self.state == 2: # Height
            self.finish()
            
    def handle_move(self, event_dict):
        if self.state == 1:
            pt = self.get_point_on_plane(event_dict)
            self.radius = (pt - self.center).Length
            
            # Update SDF
            sdf = sdf_lib.SDFCone(self.radius, self.height)
            self.update_sdf_preview(sdf)
            self.view.redraw()
            
        elif self.state == 2:
            # 3D Height Logic
            # Axis is Z
            axis = FreeCAD.Vector(0,0,1)
            pt_on_axis = self.get_closest_point_on_axis(event_dict, self.center, axis)
            
            # Height is distance from center along axis
            # Signed distance: projected vector
            diff = pt_on_axis - self.center
            self.height = diff.dot(axis)
            
            if abs(self.height) < 0.1: self.height = 0.1 if self.height >= 0 else -0.1
            
            # Update SDF
            sdf = sdf_lib.SDFCone(self.radius, self.height)
            self.update_sdf_preview(sdf)
            self.view.redraw()

    def create_object(self):
        log_to_file("ConeCreator: Creating object...")
        try:
            obj = self.create_generic_sdf(
                "SDF_Cone",
                [
                    ("App::PropertyLength", "Radius", "SDF", "Cone Radius"),
                    ("App::PropertyLength", "Height", "SDF", "Cone Height")
                ],
                {
                    "Radius": self.radius,
                    "Height": abs(self.height)
                }
            )
            
            # If height was negative (Down), user expects Cone pointing Down.
            # SDFCone is Z-up (0 to H).
            # We need to rotate the Object by 180 degrees around X or Y to flip Z.
            # Base (0) stays at 0. Tip (H) becomes (-H).
            if self.height < 0:
                # Apply rotation to Placement
                # Get current placement (has Base/Center)
                pl = obj.Placement
                # Rotate 180 around X
                rot = FreeCAD.Rotation(FreeCAD.Vector(1,0,0), 180)
                pl.Rotation = rot
                obj.Placement = pl
                
            log_to_file("ConeCreator: Object created successfully.")
        except Exception as e:
            msg = f"ConeCreator: Error creating object: {e}"
            log_to_file(msg)
            FreeCAD.Console.PrintError(msg + "\n")
            import traceback
            traceback.print_exc()


class TorusCreator(SDFPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.R = 1.0 # Major
        self.r = 0.5 # Minor
        
    def handle_click(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
        if self.state == 0: # Center
            self.center = pt
            self.sdf_trans.translation.setValue(pt.x, pt.y, pt.z)
            self.state = 1
        elif self.state == 1: # Major Radius
            self.state = 2
        elif self.state == 2: # Minor Radius
            self.finish()

    def handle_move(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
        if self.state == 1:
            self.R = (pt - self.center).Length
            if self.R < 0.1: self.R = 0.1
            
            # Update SDF
            sdf = sdf_lib.SDFTorus(self.R, self.r)
            self.update_sdf_preview(sdf)
            self.view.redraw()
            
        elif self.state == 2:
            # Distance from Major Ring?
            # Or just distance from center minus R?
            dist = (pt - self.center).Length
            self.r = abs(dist - self.R)
            if self.r < 0.01: self.r = 0.01
            
            # Update SDF
            sdf = sdf_lib.SDFTorus(self.R, self.r)
            self.update_sdf_preview(sdf)
            self.view.redraw()

    def create_object(self):
        self.create_generic_sdf(
            "SDF_Torus",
            [
                ("App::PropertyLength", "MajorRadius", "SDF", "Major Radius"),
                ("App::PropertyLength", "MinorRadius", "SDF", "Minor Radius")
            ],
            {"MajorRadius": self.R, "MinorRadius": self.r}
        )
