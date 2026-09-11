# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/creators/sphere_creator.py

Interactive SDF sphere primitive creator tool.
"""
import FreeCAD
from freecad.fields.core.sdf.sdf.sphere import SdfSphereField
from freecad.fields.tools.fld_base import ToolState
from freecad.fields.tools.primitive_creator_base import (
    PrimitiveCreatorBase, MIN_PRIMITIVE_DIM_TYPED_MM
)


class SphereCreator(PrimitiveCreatorBase):

    CREATION_STEPS = [ToolState.PLACE_ANCHOR, ToolState.DRAG_XY]

    def get_command_id(self):
        return "Fields_CreateSphere"

    def get_sdf_type(self):
        return "sphere"

    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None

    def edit_object(self, obj):
        super().edit_object(obj) # loads self.points from obj.Points
        if not self.points or len(self.points) < 2:
            # Reconstruct fallback if Points property is empty or incomplete
            field = getattr(obj.Proxy, "SdfField", None)
            if field and hasattr(field, "center") and hasattr(field, "radius"):
                loc_c = field.center
                r = field.radius
            else:
                loc_c = FreeCAD.Vector(0, 0, 0)
                r = self._compute_default_size() * 0.5
            self.points = [self.to_global(loc_c), self.to_global(loc_c + FreeCAD.Vector(r, 0, 0))]
        
        self.state = ToolState.IDLE

        self._draw_point_handles()

    def _first_point(self):
        """The rim, not the centre.

        A sphere is drawn centre-first, so points[0] is its middle and "First
        Point" would be indistinguishable from "Center". points[1] is the point
        on the bounding circle the user dragged the radius out to, which is what
        the owner asked the mode to reference.
        """
        if len(self.points) >= 2 and self.points[1] is not None:
            return FreeCAD.Vector(self.points[1])
        return super()._first_point()

    def _sync_edit_points(self):
        for i in range(len(self.fld_points)):
            if i < len(self.points):
                self.points[i] = self.fld_points[i].position

    # ------------------------------------------------------------------
    # Creation stage hooks
    # ------------------------------------------------------------------

    def _on_stage_accept(self, state, pos):
        if state == ToolState.PLACE_ANCHOR:
            s = self._compute_default_size() * 0.5
            loc_p = self.to_local(pos)
            self.points = [pos, self.to_global(loc_p + FreeCAD.Vector(s, 0, 0))]
            self._anchor_pt = pos
        elif state == ToolState.DRAG_XY:
            if len(self.points) >= 2:
                self.points[1] = pos

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_XY:
            if len(self.points) >= 2:
                self.points[1] = pos

    def _sphere_field_from_points(self):
        if len(self.points) < 2:
            return None
        loc_c = self.to_local(self.points[0])
        loc_r = self.to_local(self.points[1])
        radius = (loc_r - loc_c).Length
        if radius < MIN_PRIMITIVE_DIM_TYPED_MM:
            return None
        return SdfSphereField(loc_c, radius, placement=self._get_placement())

    def _get_preview_field(self):
        return self._sphere_field_from_points()

    def _get_edit_preview_field(self):
        return self._sphere_field_from_points()

    def _get_final_field(self):
        return self._sphere_field_from_points()

    def _get_final_points(self):
        if len(self.points) < 2:
            return None
        return [self.to_local(p) for p in self.points]

    def get_parameters(self):
        if len(self.points) < 2: return {}
        loc_c = self.to_local(self.points[0])
        loc_r = self.to_local(self.points[1])
        return {"Radius": (loc_r - loc_c).Length}

    def _apply_parameters(self, params):
        if len(self.points) < 2:
            return False
        r = max(float(params.get("Radius", 1.0)), MIN_PRIMITIVE_DIM_TYPED_MM)
        loc_c = self.to_local(self.points[0])
        self.points[1] = self.to_global(loc_c + FreeCAD.Vector(r, 0, 0))
        return True


