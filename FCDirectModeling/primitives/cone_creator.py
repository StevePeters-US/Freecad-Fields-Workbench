"""
SDF Cone creator.
"""

import FreeCAD
from .base import SDFPrimitiveCreator, log_to_file
import FCDirectModeling.sdf_lib as sdf_lib


class ConeCreator(SDFPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.radius = 0.1
        self.height = 0.1

    def handle_click(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
        if self.state == 0:  # Set center
            self.center = pt
            self.sdf_trans.translation.setValue(pt.x, pt.y, pt.z)
            self.state = 1
        elif self.state == 1:  # Lock radius, start height
            self.state = 2
        elif self.state == 2:  # Finish
            self.finish()

    def handle_move(self, event_dict):
        if self.state == 1:
            pt = self.get_point_on_plane(event_dict)
            self.radius = (pt - self.center).Length
            sdf = sdf_lib.SDFCone(self.radius, self.height)
            self.update_sdf_preview(sdf)
            self.view.redraw()

        elif self.state == 2:
            axis = FreeCAD.Vector(0, 0, 1)
            pt_on_axis = self.get_closest_point_on_axis(event_dict, self.center, axis)
            diff = pt_on_axis - self.center
            self.height = diff.dot(axis)
            if abs(self.height) < 0.1:
                self.height = 0.1 if self.height >= 0 else -0.1
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
                    ("App::PropertyLength", "Height", "SDF", "Cone Height"),
                ],
                {"Radius": self.radius, "Height": abs(self.height)},
            )
            if self.height < 0:
                pl = obj.Placement
                pl.Rotation = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 180)
                obj.Placement = pl
            log_to_file("ConeCreator: Object created successfully.")
        except Exception as e:
            msg = f"ConeCreator: Error creating object: {e}"
            log_to_file(msg)
            FreeCAD.Console.PrintError(msg + "\n")
            import traceback
            traceback.print_exc()
