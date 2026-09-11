# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/creators/torus_creator.py

Interactive SDF torus primitive creator tool.
"""
import FreeCAD
from freecad.fields.core.sdf.sdf.torus import SdfTorusField
from freecad.fields.tools.fld_base import ToolState
from freecad.fields.tools.primitive_creator_base import (
    PrimitiveCreatorBase, MIN_PRIMITIVE_DIM_MM, MIN_PRIMITIVE_DIM_TYPED_MM
)
import math


class TorusCreator(PrimitiveCreatorBase):
    """3-click torus creation: anchor → major radius → tube radius."""

    CREATION_STEPS = [ToolState.PLACE_ANCHOR, ToolState.DRAG_XY, ToolState.CUSTOM_1]

    def get_command_id(self):
        return "Fields_CreateTorus"

    def get_sdf_type(self):
        return "torus"

    def __init__(self):
        super().__init__()
        self.points = []
        self.current_point = None

    def edit_object(self, obj):
        super().edit_object(obj)
        if not self.points:
            field = getattr(obj.Proxy, "SdfField", None)
            if field:
                loc_c = field.center
                R = field.major_radius
                r = field.tube_radius
                # Anchor stored at the bottom of the torus (center minus tube_r in Z)
                # so _torus_field_from_points can re-apply the offset consistently
                loc_anchor = FreeCAD.Vector(loc_c.x, loc_c.y, loc_c.z - r)
                self.points = [
                    self.to_global(loc_anchor),
                    self.to_global(loc_anchor + FreeCAD.Vector(R, 0, 0)),
                    self.to_global(loc_anchor + FreeCAD.Vector(R + r, 0, 0)),
                ]
        self.state = ToolState.IDLE
        self._draw_point_handles()

    def _sync_edit_points(self):
        for i in range(len(self.fld_points)):
            if i < len(self.points):
                self.points[i] = self.fld_points[i].position

    # ------------------------------------------------------------------
    # Creation stage hooks
    # ------------------------------------------------------------------

    def _on_stage_accept(self, state, pos):
        if state == ToolState.PLACE_ANCHOR:
            self.points = [pos, pos, pos]
            self._field_placement = self._get_placement()
        elif state == ToolState.DRAG_XY:
            if len(self.points) >= 2:
                self.points[1] = pos
            # Start CUSTOM_1 from the same plane; keep tube point at ring edge
            self.points[2] = pos
        elif state == ToolState.CUSTOM_1:
            if len(self.points) >= 3:
                self.points[2] = pos

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_XY:
            if len(self.points) >= 2:
                self.points[1] = pos
                self.points[2] = pos
        elif state == ToolState.CUSTOM_1:
            if len(self.points) >= 3:
                self.points[2] = pos

    # ------------------------------------------------------------------
    # Field builders
    # ------------------------------------------------------------------

    def _torus_field_from_points(self):
        if len(self.points) < 3:
            return None
        loc_c = self.to_local(self.points[0])
        loc_r = self.to_local(self.points[1])
        loc_t = self.to_local(self.points[2])
        major_r = math.sqrt((loc_r.x - loc_c.x) ** 2 + (loc_r.y - loc_c.y) ** 2)
        if major_r < MIN_PRIMITIVE_DIM_MM:
            return None
        dist_t = math.sqrt((loc_t.x - loc_c.x) ** 2 + (loc_t.y - loc_c.y) ** 2)
        tube_r = max(abs(dist_t - major_r), MIN_PRIMITIVE_DIM_MM)
        # Raise center by tube_r so the torus rests on the workplane rather than clipping through it
        loc_c = FreeCAD.Vector(loc_c.x, loc_c.y, loc_c.z + tube_r)
        fp = self._get_placement()
        return SdfTorusField(loc_c, major_r, tube_r, placement=fp)

    def _get_preview_field(self):
        if len(self.points) < 2:
            return None
        loc_c = self.to_local(self.points[0])
        loc_r = self.to_local(self.points[1])
        major_r = math.sqrt((loc_r.x - loc_c.x) ** 2 + (loc_r.y - loc_c.y) ** 2)
        if major_r < MIN_PRIMITIVE_DIM_MM:
            return None
        # During DRAG_XY only 2 unique points: show a thin ring as preview
        if len(self.points) < 3 or self.points[1] == self.points[2]:
            tube_r = self.preview_minor_radius(major_r)
            fp = self._get_placement()
            return SdfTorusField(loc_c, major_r, tube_r, placement=fp)
        return self._torus_field_from_points()

    def _get_edit_preview_field(self):
        return self._torus_field_from_points()

    def _get_final_field(self):
        return self._torus_field_from_points()

    def get_parameters(self):
        if len(self.points) < 3: return {}
        loc_c = self.to_local(self.points[0])
        loc_r = self.to_local(self.points[1])
        loc_t = self.to_local(self.points[2])
        major_r = math.sqrt((loc_r.x - loc_c.x)**2 + (loc_r.y - loc_c.y)**2)
        dist_t = math.sqrt((loc_t.x - loc_c.x)**2 + (loc_t.y - loc_c.y)**2)
        tube_r = abs(dist_t - major_r)
        return {"Major Radius": major_r, "Tube Radius": tube_r}

    def _apply_parameters(self, params):
        if len(self.points) < 3:
            return False
        R = max(float(params.get("Major Radius", 10.0)), MIN_PRIMITIVE_DIM_TYPED_MM)
        r = max(float(params.get("Tube Radius", 2.0)), MIN_PRIMITIVE_DIM_TYPED_MM)
        loc_c = self.to_local(self.points[0])
        self.points[1] = self.to_global(loc_c + FreeCAD.Vector(R, 0, 0))
        self.points[2] = self.to_global(loc_c + FreeCAD.Vector(R + r, 0, 0))
        return True
