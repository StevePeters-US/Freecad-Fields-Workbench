"""
SDF Torus creator.
"""

import FreeCAD
from .base import SDFPrimitiveCreator, log_to_file
import FCDirectModeling.sdf_lib as sdf_lib
from FCDirectModeling.primitives.torus import SDFTorus


class TorusCreator(SDFPrimitiveCreator):
    def __init__(self):
        super().__init__()
        self.R = 1.0  # Major radius
        self.r = 0.5  # Minor radius

    def handle_click(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
        if self.state == 0:  # Set center
            self.center = pt
            self.sdf_trans.translation.setValue(pt.x, pt.y, pt.z)
            self.state = 1
        elif self.state == 1:  # Lock major radius
            self.state = 2
        elif self.state == 2:  # Finish
            self.finish()

    def handle_move(self, event_dict):
        pt = self.get_point_on_plane(event_dict)
        if self.state == 1:
            self.R = max(0.1, (pt - self.center).Length)
            sdf = SDFTorus(self.R, self.r)
            self.update_sdf_preview(sdf)
            self.view.redraw()

        elif self.state == 2:
            dist = (pt - self.center).Length
            self.r = max(0.01, abs(dist - self.R))
            sdf = SDFTorus(self.R, self.r)
            self.update_sdf_preview(sdf)
            self.view.redraw()

    def create_object(self):
        log_to_file("TorusCreator: Creating object...")
        try:
            self.create_generic_sdf(
                "SDF_Torus",
                [
                    ("App::PropertyLength", "MajorRadius", "SDF", "Major Radius"),
                    ("App::PropertyLength", "MinorRadius", "SDF", "Minor Radius"),
                ],
                {"MajorRadius": self.R, "MinorRadius": self.r},
            )
            log_to_file("TorusCreator: Object created successfully.")
        except Exception as e:
            msg = f"TorusCreator: Error creating object: {e}"
            log_to_file(msg)
            FreeCAD.Console.PrintError(msg + "\n")
            import traceback
            traceback.print_exc()
