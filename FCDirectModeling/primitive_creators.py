
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
                     self.terminate()
            return False
        except Exception as e:
            FreeCAD.Console.PrintError(f"PrimitiveCreatorBase: Event Callback Error: {e}\n")
            import traceback
            traceback.print_exc()
            return False

    def handle_click(self, event_dict): pass
    def handle_move(self, event_dict): pass


class SphereCreator(PrimitiveCreatorBase):
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
            self.create_object()
            
            # Defer termination to avoid crashing Coin3D while handling event
            QtCore.QTimer.singleShot(0, self.terminate)
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
            doc = FreeCAD.activeDocument()
            if not doc: doc = FreeCAD.newDocument()
            
            log_to_file("SphereCreator: Adding Object...")
            log_to_file("SphereCreator: Adding Object...")
            # Use Factory
            obj = sdf_utils.SDFObjectFactory.create_sdf_object(doc, "SDF_Sphere", sdf_renderer.SDFBoxFeature)
            
            log_to_file("SphereCreator: Adding Properties...")
            obj.addProperty("App::PropertyLength", "Radius", "SDF", "Sphere Radius")
            
            # Common Properties
            sdf_utils.SDFObjectFactory.add_common_properties(obj)
            
            log_to_file("SphereCreator: Setting Placement...")
            obj.Placement.Base = self.center
            
            log_to_file("SphereCreator: Attaching ViewProvider...")
            sdf_utils.SDFObjectFactory.setup_view_provider(obj)
            
            # Trigger update by setting main property LAST
            obj.Radius = self.radius

            log_to_file("SphereCreator: Recomputing...")
            doc.recompute()
            log_to_file("SphereCreator: Object created successfully.")
            FreeCAD.Console.PrintMessage("SphereCreator: Object created successfully.\n")
        except Exception as e:
            msg = f"SphereCreator: Error creating object: {e}"
            log_to_file(msg)
            FreeCAD.Console.PrintError(msg + "\n")
            import traceback
            traceback.print_exc()

class ConeCreator(PrimitiveCreatorBase):
    def __init__(self):
        super().__init__()
        self.radius = 1.0
        self.height = 1.0
        
        self.cone = coin.SoCone()
        self.trans = coin.SoTranslation()
        self.rot = coin.SoRotation() # To align standard Cone (Y-up?) to FreeCAD (Z-up?)
        # Coin SoCone is Y-aligned. FreeCAD Cone is Z-aligned.
        # We need to rotate -90 deg around X.
        self.rot.rotation.setValue(coin.SbVec3f(1,0,0), -1.5708)
        
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
            self.drag_start_y = event_dict["Position"][1]
        elif self.state == 2: # Height
            self.create_object()
            self.terminate()
            
    def handle_move(self, event_dict):
        if self.state == 1:
            pt = self.get_point_on_plane(event_dict)
            self.radius = (pt - self.center).Length
            self.cone.bottomRadius.setValue(self.radius)
            self.view.redraw()
        elif self.state == 2:
            dy = event_dict["Position"][1] - self.drag_start_y
            self.height = dy / 5.0 # Sensitivity
            if abs(self.height) < 0.1: self.height = 0.1
            
            self.cone.height.setValue(abs(self.height))
            
            # Adjust offset so base remains at center
            # Coin Cone is centered. So we move it up by h/2 (in local Y)
            # h positive or negative? Coin height is positive.
            # If we want negative height (down), we rotate?
            # Let's keep it simple: Grows Up.
            
            # Shift Coin Cone (local Y) by h/2 so bottom is at 0
            offset = abs(self.height) / 2.0
            if self.height < 0:
                # If dragging down, we want base at 0, tip at -H
                # Centered cone at -H/2
                offset = -abs(self.height) / 2.0
                
            self.mid_trans.translation.setValue(0, offset, 0) # Local Y is Global Z due to rot
            self.view.redraw()

    def create_object(self):
        doc = FreeCAD.activeDocument()
        if not doc: doc = FreeCAD.newDocument()
        
        # Use Factory
        obj = sdf_utils.SDFObjectFactory.create_sdf_object(doc, "SDF_Cone", sdf_renderer.SDFBoxFeature)
        
        obj.addProperty("App::PropertyLength", "Radius", "SDF", "Base Radius")
        obj.addProperty("App::PropertyLength", "Height", "SDF", "Cone Height")
        
        # Common
        sdf_utils.SDFObjectFactory.add_common_properties(obj)

        obj.Placement.Base = self.center
        
        sdf_utils.SDFObjectFactory.setup_view_provider(obj)
            
        # Trigger Update
        obj.Radius = self.radius
        obj.Height = self.height

        doc.recompute()

class TorusCreator(PrimitiveCreatorBase):
    def __init__(self):
        super().__init__()
        self.R = 1.0 # Major
        self.r = 0.5 # Minor
        
        # FreeCAD Torus is Z-axis aligned (Ring in XY)
        # Coin SoTube? No, SoTorus?
        # Coin SoTorus: No standard node? 
        # Coin has no SoTorus! It's not in standard Inventor.
        # We might need to use a Mesh or multiple shapes.
        # Or just use a Circle (for ring) and a wireframe?
        
        # Workaround: Use a simple ring (Circle) to visualize Major, and maybe a Sphere for Minor thickness?
        # Or just Lines.
        
        self.line_coords = coin.SoCoordinate3()
        self.line_set = coin.SoLineSet()
        self.preview_sep.addChild(self.line_coords)
        self.preview_sep.addChild(self.line_set)
        
    def update_viz(self):
        # Draw 2 circles? 
        # Major ring
        num_seg = 64
        pts = []
        for i in range(num_seg + 1):
            a = 2.0 * math.pi * i / num_seg
            x = self.center.x + self.R * math.cos(a)
            y = self.center.y + self.R * math.sin(a)
            pts.append([x, y, self.center.z])
            
        # Offset rings for thickness?
        # Inner ring (R-r)
        for i in range(num_seg + 1):
            a = 2.0 * math.pi * i / num_seg
            x = self.center.x + (self.R - self.r) * math.cos(a)
            y = self.center.y + (self.R - self.r) * math.sin(a)
            pts.append([x, y, self.center.z])
            
        # Outer ring (R+r)
        for i in range(num_seg + 1):
            a = 2.0 * math.pi * i / num_seg
            x = self.center.x + (self.R + self.r) * math.cos(a)
            y = self.center.y + (self.R + self.r) * math.sin(a)
            pts.append([x, y, self.center.z])

        self.line_coords.point.setValues(0, len(pts), pts)
        
        # Segments
        # 0..64
        # 65..129
        # 130..194
        nums = [num_seg+1, num_seg+1, num_seg+1]
        self.line_set.numVertices.setValues(0, 3, nums)
        
    def handle_click(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
        if self.state == 0:
            self.center = pt
            self.state = 1
        elif self.state == 1: # Major Radius
            self.state = 2
            self.start_r_pt = pt
        elif self.state == 2: # Minor Radius
            self.create_object()
            self.terminate()

    def handle_move(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
        if self.state == 1:
            self.R = (pt - self.center).Length
            self.update_viz()
            self.view.redraw()
        elif self.state == 2:
            # Distance from ring? Or just distance from click point?
            dist = (pt - self.start_r_pt).Length
            self.r = dist
            self.update_viz()
            self.view.redraw()
            
    def create_object(self):
        doc = FreeCAD.activeDocument()
        if not doc: doc = FreeCAD.newDocument()
        
        # Use Factory
        obj = sdf_utils.SDFObjectFactory.create_sdf_object(doc, "SDF_Torus", sdf_renderer.SDFBoxFeature)

        obj.addProperty("App::PropertyLength", "MajorRadius", "SDF", "Major Radius (R)")
        obj.addProperty("App::PropertyLength", "MinorRadius", "SDF", "Minor Radius (r)")
        
        # Common
        sdf_utils.SDFObjectFactory.add_common_properties(obj)

        obj.Placement.Base = self.center
        
        sdf_utils.SDFObjectFactory.setup_view_provider(obj)
        
        # Trigger Update
        obj.MajorRadius = self.R
        obj.MinorRadius = self.r
        
        doc.recompute()
