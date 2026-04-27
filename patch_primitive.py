import re

with open("tools/primitive_tool.py", "r") as f:
    content = f.read()

# 1. Init vars
content = content.replace("self._edit_is_rotating = False",
                          "self._edit_is_rotating = False\n        self._center_handle = None\n        self._rot_handle = None\n        self._rot_line = None")

# 2. Reset state cleanup
cleanup_code = """        if getattr(self, "_center_handle", None):
            self._center_handle.undraw()
            self._center_handle = None
        if getattr(self, "_rot_handle", None):
            self._rot_handle.undraw()
            self._rot_handle = None
        if getattr(self, "_rot_line", None):
            self._rot_line.undraw()
            self._rot_line = None"""
content = content.replace("self.dm_points.clear()", cleanup_code + "\n        self.dm_points.clear()")

# 3. Add transform handles method
add_handles_code = """    def _add_transform_handles(self):
        if not getattr(self, "_is_editing", False) or not self.working_plane:
            return
        r = self._compute_handle_radius()
        if not getattr(self, "_center_handle", None):
            self._center_handle = DMPoint(self.working_plane.Base)
            self._center_handle.draw_point(self.points_root, radius=r*1.5, color=(0.8, 0.8, 0.2))
        else:
            self._center_handle.position = self.working_plane.Base
            self._center_handle.update_draw(radius=r*1.5)
        offset = self.working_plane.Rotation.multVec(FreeCAD.Vector(self._compute_default_size() * 0.4, 0, 0))
        rot_pos = self.working_plane.Base + offset
        if not getattr(self, "_rot_handle", None):
            self._rot_handle = DMPoint(rot_pos)
            self._rot_handle.draw_point(self.points_root, radius=r*0.8, color=(0.2, 0.8, 0.8))
        else:
            self._rot_handle.position = rot_pos
            self._rot_handle.update_draw(radius=r*0.8)
        line_pts = [self.working_plane.Base, rot_pos]
        if not getattr(self, "_rot_line", None):
            self._rot_line = DMLineSet(self.points_root, color=(0.2, 0.8, 0.8), width=2.0)
        self._rot_line.update_lines(line_pts)

    def _update_handle_positions"""
content = content.replace("    def _update_handle_positions", add_handles_code)

# 4. update_handle_positions and edit_object call
content = content.replace("self.dm_points[i].update_draw(radius=r)", "self.dm_points[i].update_draw(radius=r)\n        self._add_transform_handles()")
content = content.replace("self.points = [self.working_plane.multVec(pt) for pt in obj.Points]", "self.points = [self.working_plane.multVec(pt) for pt in obj.Points]\n        self._add_transform_handles()")

# 5. _edit_hover
hover_replace = """        pts = [dm_pt.position for dm_pt in self.dm_points]
        if getattr(self, "_center_handle", None): pts.append(self._center_handle.position)
        if getattr(self, "_rot_handle", None): pts.append(self._rot_handle.position)
        idx, _ = self._hit_test_perp(ray_p, ray_d, pts)"""
content = content.replace("pts = [dm_pt.position for dm_pt in self.dm_points]\n        idx, _ = self._hit_test_perp(ray_p, ray_d, pts)", hover_replace)

# 6. _edit_on_mouse_press
press_old = """        idx, _ = self._hit_test_perp(ray_p, ray_d, self.points)
        if idx is not None:
            self._dragging_idx = idx
            self._drag_plane_n = FreeCAD.Vector(-self.view.getViewDirection())
            self._drag_plane_o = self.points[idx]
            self._edit_is_rotating = (event_dict.get("Modifiers") == QtCore.Qt.ShiftModifier)
            if self._edit_is_rotating:
                # Pivot is center of all points
                self._edit_pivot = sum(self.points, FreeCAD.Vector()) / len(self.points)
                v = self.points[idx] - self._edit_pivot
                self._edit_last_angle = math.atan2(v.y, v.x)
            self._start_drag_timer()
            return True
        return False"""

press_new = """        # Test special handles
        special_pts = []
        if getattr(self, "_center_handle", None): special_pts.append(self._center_handle.position)
        if getattr(self, "_rot_handle", None): special_pts.append(self._rot_handle.position)
        idx, _ = self._hit_test_perp(ray_p, ray_d, special_pts)
        if idx is not None:
            self._dragging_idx = 'center' if idx == 0 else 'rot'
            if self._dragging_idx == 'center':
                self._drag_plane_n = FreeCAD.Vector(-self.view.getViewDirection())
                self._drag_plane_o = self._center_handle.position
            else:
                self._drag_plane_n = self.working_plane.Rotation.multVec(FreeCAD.Vector(0,0,1)) if self.working_plane else FreeCAD.Vector(0,0,1)
                self._drag_plane_o = self._rot_handle.position
                self._edit_pivot = self.working_plane.Base if self.working_plane else self._center_handle.position
                v = self._rot_handle.position - self._edit_pivot
                self._edit_last_angle = math.atan2(v.y, v.x)
            self._start_drag_timer()
            return True

        # Test regular points
        idx, _ = self._hit_test_perp(ray_p, ray_d, self.points)
        if idx is not None:
            self._dragging_idx = idx
            self._drag_plane_n = FreeCAD.Vector(-self.view.getViewDirection())
            self._drag_plane_o = self.points[idx]
            self._edit_is_rotating = (event_dict.get("Modifiers") == QtCore.Qt.ShiftModifier)
            if self._edit_is_rotating:
                self._edit_pivot = sum(self.points, FreeCAD.Vector()) / len(self.points)
                v = self.points[idx] - self._edit_pivot
                self._edit_last_angle = math.atan2(v.y, v.x)
            self._start_drag_timer()
            return True
        return False"""
content = content.replace(press_old, press_new)

# 7. _drag_update in PrimitiveCreatorBase
drag_old = """        if new_pt:
            if is_ctrl:"""

drag_new = """        if new_pt:
            if self._dragging_idx == 'center':
                delta = new_pt - self._center_handle.position
                self.points = [p + delta for p in self.points]
                if self.working_plane:
                    self.working_plane.Base += delta
            elif self._dragging_idx == 'rot':
                pivot = self._edit_pivot
                v = new_pt - pivot
                angle = math.atan2(v.y, v.x)
                da = angle - self._edit_last_angle
                rot = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), math.degrees(da))
                self.points = [pivot + rot.multVec(p - pivot) for p in self.points]
                if self.working_plane:
                    self.working_plane.Rotation = self.working_plane.Rotation.multiply(rot)
                self._edit_last_angle = angle
            elif is_ctrl:"""
content = content.replace(drag_old, drag_new)

# 8. BoxCreator and CylinderCreator _drag_update delegation
for subclass in ["BoxCreator", "CylinderCreator"]:
    content = re.sub(
        r"(def _drag_update\(self\):\n\s+if self\._drag_check_lmb_released\(\):\n\s+return\n\s+if self\._dragging_idx is None:\n\s+return\n)",
        r"\1\n        if self._dragging_idx in ('center', 'rot'):\n            super()._drag_update()\n            return\n",
        content
    )

with open("tools/primitive_tool.py", "w") as f:
    f.write(content)

print("Patched primitive_tool.py")
