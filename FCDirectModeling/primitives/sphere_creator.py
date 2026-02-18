"""
SDF Sphere creator.
"""

import FreeCAD
from .base import SDFPrimitiveCreator, log_to_file
import FCDirectModeling.sdf_lib as sdf_lib


class SphereCreator(SDFPrimitiveCreator):
    def __init__(self):
        super().__init__()
        log_to_file("SphereCreator: Initializing...")
        self.radius = 1.0

    def handle_click(self, event_dict):
        log_to_file(f"SphereCreator: Click! State={self.state}")
        pt = self.get_point_on_plane(event_dict)

        if self.state == 0:
            self.center = pt
            self.sdf_trans.translation.setValue(pt.x, pt.y, pt.z)
            self.state = 1
        elif self.state == 1:
            self.handle_move(event_dict)
            log_to_file("SphereCreator: Finishing interaction...")
            self.finish()
            return True

    def handle_move(self, event_dict):
        if self.state == 1:
            pt = self.get_point_on_plane(event_dict)
            self.radius = (pt - self.center).Length
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
