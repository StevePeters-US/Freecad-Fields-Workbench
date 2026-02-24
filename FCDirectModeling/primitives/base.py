"""
Base classes for SDF primitive creators.
"""

import FreeCAD
import FreeCADGui
from pivy import coin
from PySide import QtCore
import FCDirectModeling.sdf_renderer as sdf_renderer
import FCDirectModeling.sdf_utils as sdf_utils

# Preview point count target (~100 points per shape during drag)
PREVIEW_RESOLUTION = sdf_utils.DEFAULT_RESOLUTION
PREVIEW_SAMPLES = 4
PREVIEW_ITERATIONS = 4


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

        # SDF Point Cloud Preview Nodes
        self.sdf_sep = coin.SoSeparator()
        self.sg.addChild(self.sdf_sep)

        self.sdf_mat = coin.SoMaterial()
        self.sdf_mat.diffuseColor.setValue(0.7, 0.7, 0.7)
        self.sdf_sep.addChild(self.sdf_mat)

        self.sdf_trans = coin.SoTransform()
        self.sdf_sep.addChild(self.sdf_trans)

        self.sdf_style = coin.SoDrawStyle()
        self.sdf_style.pointSize.setValue(4.0)
        self.sdf_sep.addChild(self.sdf_style)

        self.sdf_coords = coin.SoCoordinate3()
        self.sdf_sep.addChild(self.sdf_coords)

        self.sdf_points = coin.SoPointSet()
        self.sdf_sep.addChild(self.sdf_points)

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

    def update_sdf_preview(self, sdf_obj):
        """Updates the SDF preview as traced mathematical edges."""
        try:
            # Lightweight analytical edge tracing for live preview
            pts = sdf_obj.trace_edges(num_seeds=50, variance_threshold=0.2)
            
            if pts is not None and len(pts) > 0:
                self.sdf_coords.point.setValues(0, len(pts), pts)
                self.sdf_points.numPoints.setValue(len(pts))
            else:
                self.sdf_coords.point.setNum(0)
                self.sdf_points.numPoints.setValue(0)
        except Exception as e:
            FreeCAD.Console.PrintError(f"SDF Preview Error: {e}\n")
            self.sdf_coords.point.setNum(0)
            self.sdf_points.numPoints.setValue(0)


class SDFPrimitiveCreator(PrimitiveCreatorBase):
    """Base for creators that produce a persistent SDF document object."""

    def finish(self):
        self.create_object()
        QtCore.QTimer.singleShot(0, self.terminate)

    def create_generic_sdf(self, type_name, properties_list, properties_values):
        """
        Generic helper to create an SDF document object.
        :param type_name: e.g. "SDF_Sphere"
        :param properties_list: list of (Type, Name, Group, Tooltip) tuples
        :param properties_values: dict of {Name: Value}
        """
        doc = FreeCAD.activeDocument()
        if not doc:
            doc = FreeCAD.newDocument()

        obj = sdf_utils.SDFObjectFactory.create_sdf_object(doc, type_name, sdf_renderer.SDFBoxFeature)

        for prop_def in properties_list:
            obj.addProperty(prop_def[0], prop_def[1], prop_def[2], prop_def[3])

        sdf_utils.SDFObjectFactory.add_common_properties(obj)
        obj.Placement.Base = self.center
        sdf_utils.SDFObjectFactory.setup_view_provider(obj)

        for prop_name, val in properties_values.items():
            setattr(obj, prop_name, val)

        doc.recompute()
        FreeCAD.Console.PrintMessage(f"{type_name} created successfully.\n")
        return obj
