
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
        
        # Guide Material
        self.material = coin.SoMaterial()
        self.material.diffuseColor.setValue(0.2, 0.6, 0.8)
        self.material.transparency.setValue(0.5)
        self.sg.addChild(self.material)
        
        # Shape Nodes (To be added by subclasses)
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
        
        # Coin3D Sphere
        self.sphere = coin.SoSphere()
        self.sphere.radius.setValue(0) # Start invisible/zero
        self.trans = coin.SoTranslation()
        
        self.preview_sep.addChild(self.trans)
        self.preview_sep.addChild(self.sphere)
        log_to_file("SphereCreator: Scene graph created.")
        
    def handle_click(self, event_dict):
        log_to_file(f"SphereCreator: Click! State={self.state}")
        pt = self.get_point_on_plane(event_dict)
        
        if self.state == 0:
            self.center = pt
            self.trans.translation.setValue(pt.x, pt.y, pt.z)
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
            self.sphere.radius.setValue(self.radius)
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
        
        self.cone = coin.SoCone()
        self.cone.bottomRadius.setValue(0) # Start invisible
        self.cone.height.setValue(0)
        
        self.trans = coin.SoTranslation()
        
        self.rot = coin.SoRotation() # To align standard Cone (Y-up?) to FreeCAD (Z-up?)
        # Coin SoCone is Y-aligned. FreeCAD Cone is Z-aligned.
        # We need to rotate +90 deg around X (Y -> Z).
        # Previous -90 (Y -> -Z) caused upside-down cone.
        self.rot.rotation.setValue(coin.SbVec3f(1,0,0), 1.5708)
        
        # Center of Coin Cone is at height/2.
        # FreeCAD Cone Base is at 0.
        # So we need to shift Coin Cone up by height/2 in its Y-axis (which is Z after rotation)
        # Or just handle translation.
        
        self.preview_sep.addChild(self.trans)
        self.preview_sep.addChild(self.rot)
        self.mid_trans = coin.SoTranslation() # For centering offset
        self.preview_sep.addChild(self.mid_trans)
        self.preview_sep.addChild(self.cone)
        
    def handle_click(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
        if self.state == 0: # Center
            self.center = pt
            self.trans.translation.setValue(pt.x, pt.y, pt.z)
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
            self.cone.bottomRadius.setValue(self.radius)
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
            
            self.cone.height.setValue(abs(self.height))
            
            # Offset logic to keep base fixed at center
            offset = abs(self.height) / 2.0
            if self.height < 0:
                offset = -abs(self.height) / 2.0
                
            self.mid_trans.translation.setValue(0, offset, 0) 
            self.view.redraw()

    def create_object(self):
        self.create_generic_sdf(
            "SDF_Cone",
            [
                ("App::PropertyLength", "Radius", "SDF", "Base Radius"),
                ("App::PropertyLength", "Height", "SDF", "Cone Height")
            ],
            {"Radius": self.radius, "Height": self.height}
        )

class TorusCreator(SDFPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.R = 1.0 # Major
        self.r = 0.5 # Minor
        
        # FreeCAD Torus is Z-axis aligned (Ring in XY)
        # Coin SoTube assumes Y axis? No, SoTorus doesn't exist.
        # Let's use a SoGroup with a custom implementation or approximation?
        # For preview, maybe just a Circle (SoLineSet) and another Circle?
        # Or just use SoSphere for center and rely on final object.
        # Actually, let doesn't matter much for crash.
        # Let's assume we want a Ring.
        # For now, let's just use Two Concentric Circles using SoLineSet for preview (XY plane)?
        # Or just a placeholder SoSphere.
        # Let's keep it simple: A Sphere for center, and maybe a Circle.
        
        self.trans = coin.SoTranslation()
        self.preview_sep.addChild(self.trans)
        
        # Visual guide: Major Radius Ring
        # We can construct a simple circle using Coordinate3 and LineSet
        self.major_circle_sep = coin.SoSeparator()
        self.major_coords = coin.SoCoordinate3()
        self.major_lines = coin.SoLineSet()
        self.major_circle_sep.addChild(self.major_coords)
        self.major_circle_sep.addChild(self.major_lines)
        self.preview_sep.addChild(self.major_circle_sep)
        
        # Minor Radius circle (rotated 90 deg)? 
        # For preview, just showing the Major Ring size is usually enough.
        
    def _update_circle(self, radius):
        # Generate points for a circle in XY plane
        num_pts = 64
        pts = []
        for i in range(num_pts + 1):
            angle = 2.0 * math.pi * i / num_pts
            x = radius * math.cos(angle)
            y = radius * math.sin(angle)
            pts.append([x, y, 0.0])
            
        self.major_coords.point.setValues(pts)
        self.major_lines.numVertices.setValue(num_pts + 1)
        
    def handle_click(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
        if self.state == 0: # Center
            self.center = pt
            self.trans.translation.setValue(pt.x, pt.y, pt.z)
            self.state = 1
        elif self.state == 1: # Major Radius
            self.state = 2
            self.drag_start_pt = pt
        elif self.state == 2: # Minor Radius
            self.finish()

    def handle_move(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
        if self.state == 1:
            self.R = (pt - self.center).Length
            if self.R < 0.1: self.R = 0.1
            self._update_circle(self.R)
            self.view.redraw()
        elif self.state == 2:
            # Distance from Major Ring?
            # Or just distance from center minus R?
            dist = (pt - self.center).Length
            self.r = abs(dist - self.R)
            if self.r < 0.01: self.r = 0.01
            # Redraw? We don't have minor visual.
            FreeCAD.Console.PrintMessage(f"Torus Minor R: {self.r}\r")

    def create_object(self):
        self.create_generic_sdf(
            "SDF_Torus",
            [
                ("App::PropertyLength", "MajorRadius", "SDF", "Major Radius"),
                ("App::PropertyLength", "MinorRadius", "SDF", "Minor Radius")
            ],
            {"MajorRadius": self.R, "MinorRadius": self.r}
        )
