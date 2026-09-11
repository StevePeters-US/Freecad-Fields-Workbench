# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/creators/cylinder_creator.py

Interactive SDF cylinder and torus primitive creator tools.
"""
import FreeCAD
from freecad.fields.core.sdf.sdf.cylinder import SdfCylinderField
from freecad.fields.tools.fld_base import ToolState
from freecad.fields.tools.primitive_creator_base import (
    PrimitiveCreatorBase, MIN_PRIMITIVE_DIM_TYPED_MM
)
import math


class CylinderCreator(PrimitiveCreatorBase):

    CREATION_STEPS = [ToolState.PLACE_ANCHOR, ToolState.DRAG_XY, ToolState.DRAG_Z]

    def get_command_id(self):
        return "Fields_CreateCylinder"

    def get_sdf_type(self):
        return "cylinder"

    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None
        self._height_drag_base = None

    def edit_object(self, obj):
        super().edit_object(obj)
        pts = list(getattr(self, "points", []))
        if len(pts) >= 3:
            self.current_point = pts[2]
            self.points = pts
        elif len(pts) >= 2:
            self.current_point = pts[1]
            self.points = pts
        self.state = ToolState.IDLE

        self._draw_point_handles()

    def _first_point(self):
        """The base circle, not the base centre.

        points[0] is the centre of the base -- offset from the solid's middle
        only along Z, so on a squat cylinder "First Point" and "Center" sit
        almost on top of each other. points[1] is the point on the bounding
        circle the user dragged the radius out to.
        """
        if len(self.points) >= 2 and self.points[1] is not None:
            return FreeCAD.Vector(self.points[1])
        return super()._first_point()

    def _sync_edit_points(self):
        for i in range(len(self.fld_points)):
            if i < len(self.points):
                self.points[i] = self.fld_points[i].position
            elif i == len(self.points):
                self.points.append(self.fld_points[i].position)

    def _point_axis_override(self, idx):
        if idx == 2 and self.working_plane:
            # Height handle can only move along the cylinder's local Z.
            return self.working_plane.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        return None

    # ------------------------------------------------------------------
    # Creation stage hooks
    # ------------------------------------------------------------------

    def _on_stage_accept(self, state, pos):
        if state == ToolState.PLACE_ANCHOR:
            self.points = [pos, pos]
            self._field_placement = self._get_placement()
        elif state == ToolState.DRAG_XY:
            if len(self.points) >= 2:
                self.points[1] = pos
            self._height_drag_base = self.points[0]
            self.current_point = self.points[0]
            if len(self.points) < 3:
                self.points.append(self.points[0])
        elif state == ToolState.DRAG_Z:
            if len(self.points) >= 3:
                self.points[2] = pos
            self.current_point = pos

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_XY:
            if len(self.points) >= 2:
                self.points[1] = pos
        elif state == ToolState.DRAG_Z:
            if len(self.points) >= 3:
                self.points[2] = pos
            self.current_point = pos

    def _cylinder_field_from_points(self):
        if len(self.points) < 3:
            return None
        loc_base = self.to_local(self.points[0])
        loc_rad  = self.to_local(self.points[1])
        loc_h    = self.to_local(self.points[2])
        radius = math.sqrt((loc_rad.x - loc_base.x)**2 + (loc_rad.y - loc_base.y)**2)
        height = loc_h.z - loc_base.z
        if radius < MIN_PRIMITIVE_DIM_TYPED_MM:
            return None
        if abs(height) < MIN_PRIMITIVE_DIM_TYPED_MM:
            height = MIN_PRIMITIVE_DIM_TYPED_MM if height >= 0 else -MIN_PRIMITIVE_DIM_TYPED_MM
        fp = self._get_placement()
        return SdfCylinderField(loc_base, FreeCAD.Vector(0, 0, 1), radius, height, placement=fp)

    def _get_preview_field(self):
        if len(self.points) < 2:
            return None
        # During DRAG_XY, only 2 points set: show a flat disk preview
        if len(self.points) == 2 or self.points[0] == self.points[2]:
            loc_base = self.to_local(self.points[0])
            loc_rad  = self.to_local(self.points[1])
            radius = math.sqrt((loc_rad.x - loc_base.x)**2 + (loc_rad.y - loc_base.y)**2)
            if radius < MIN_PRIMITIVE_DIM_TYPED_MM:
                return None
            fp = self._get_placement()
            return SdfCylinderField(loc_base, FreeCAD.Vector(0, 0, 1), radius, 1.0, placement=fp)
        return self._cylinder_field_from_points()

    def _get_edit_preview_field(self):
        return self._cylinder_field_from_points()

    def _get_final_field(self):
        return self._cylinder_field_from_points()

    def get_parameters(self):
        if len(self.points) < 3: return {}
        loc_base = self.to_local(self.points[0])
        loc_rad = self.to_local(self.points[1])
        loc_h = self.to_local(self.points[2])
        radius = math.sqrt((loc_rad.x - loc_base.x)**2 + (loc_rad.y - loc_base.y)**2)
        height = loc_h.z - loc_base.z
        return {"Radius": radius, "Height": height}

    def _apply_parameters(self, params):
        if len(self.points) < 3:
            return False
        r = max(float(params.get("Radius", 1.0)), MIN_PRIMITIVE_DIM_TYPED_MM)
        h = float(params.get("Height", 1.0))
        if abs(h) < MIN_PRIMITIVE_DIM_TYPED_MM:
            h = MIN_PRIMITIVE_DIM_TYPED_MM if h >= 0 else -MIN_PRIMITIVE_DIM_TYPED_MM
        loc_base = self.to_local(self.points[0])
        self.points[1] = self.to_global(loc_base + FreeCAD.Vector(r, 0, 0))
        self.points[2] = self.to_global(loc_base + FreeCAD.Vector(0, 0, h))
        return True




