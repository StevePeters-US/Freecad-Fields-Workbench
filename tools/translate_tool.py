"""
Translate Tool for FCDirectModeling.
Moves whole DMObjects or selected sub-elements (vertices/handles).
"""

import FreeCAD
import FreeCADGui
from PySide import QtCore
from core import dm_logger
from .dm_base import DMBase

class TranslateTool(DMBase):
    def __init__(self):
        super().__init__()
        self.state = 1 # Start in dragging mode
        
        # Selection tracking
        self.targets = [] # List of dicts: {"obj": obj, "type": "point"|"handle_in"|"handle_out"|"placement", "idx": int|None, "orig_world": Vector|Placement}
        self.obj_orig_points = {} # obj -> original_points_list
        self.obj_orig_h_in = {}   # obj -> original_h_in_list
        self.obj_orig_h_out = {}  # obj -> original_h_out_list
        self.obj_orig_placement = {} # obj -> original_placement
        
        sel_ex = FreeCADGui.Selection.getSelectionEx()
        if not sel_ex:
            dm_logger.debug("TranslateTool: Nothing selected.")
            self.terminate()
            return

        for s in sel_ex:
            obj = s.Object
            if not hasattr(obj, "ShapeType"):
                continue
            
            # 1. Capture full original state for any object touched
            if obj not in self.obj_orig_placement:
                self.obj_orig_placement[obj] = FreeCAD.Placement(obj.Placement)
                if hasattr(obj, "Points"):
                    self.obj_orig_points[obj] = list(obj.Points)
                    self.obj_orig_h_in[obj] = list(obj.HandleIn) if hasattr(obj, "HandleIn") else []
                    self.obj_orig_h_out[obj] = list(obj.HandleOut) if hasattr(obj, "HandleOut") else []
                elif hasattr(obj, "Position"):
                    self.obj_orig_points[obj] = FreeCAD.Vector(obj.Position)

            # 2. Identify specific sub-elements (Vertices only for points/handles)
            sub_names = s.SubElementNames
            if sub_names:
                for sub in sub_names:
                    if "Vertex" in sub:
                         try:
                             idx = int(sub.replace("Vertex", "")) - 1
                             v_world = obj.Shape.Vertexes[idx].Point
                             
                             found = False
                             # Check Main Points
                             if hasattr(obj, "Points"):
                                 for i, p in enumerate(obj.Points):
                                     p_w = obj.Placement.multVec(p)
                                     if (v_world - p_w).Length < 0.001:
                                         self.targets.append({"obj": obj, "type": "point", "idx": i, "orig_world": v_world})
                                         found = True; break
                             
                             # Check Handles
                             if not found and hasattr(obj, "HandleIn"):
                                 for i, p in enumerate(obj.HandleIn):
                                     if p is None: continue
                                     p_w = obj.Placement.multVec(p)
                                     if (v_world - p_w).Length < 0.001:
                                         self.targets.append({"obj": obj, "type": "handle_in", "idx": i, "orig_world": v_world})
                                         found = True; break
                             
                             if not found and hasattr(obj, "HandleOut"):
                                 for i, p in enumerate(obj.HandleOut):
                                     if p is None: continue
                                     p_w = obj.Placement.multVec(p)
                                     if (v_world - p_w).Length < 0.001:
                                         self.targets.append({"obj": obj, "type": "handle_out", "idx": i, "orig_world": v_world})
                                         found = True; break
                                         
                             if not found and hasattr(obj, "Position"):
                                 p_w = obj.Placement.multVec(obj.Position)
                                 if (v_world - p_w).Length < 0.001:
                                     self.targets.append({"obj": obj, "type": "point", "idx": 0, "orig_world": v_world})
                         except Exception as e:
                             dm_logger.debug(f"TranslateTool: Vertex selection parsing failed: {e}")
                    elif "Edge" in sub:
                        # For DMObjects, Edge1 is the main B-spline. Other edges are handles.
                        # Allow dragging the whole object by its main curve, but ignore handles.
                        if sub == "Edge1":
                            self.targets.append({"obj": obj, "type": "placement", "idx": None, "orig_world": FreeCAD.Placement(obj.Placement)})
                        else:
                            dm_logger.debug(f"TranslateTool: Ignoring handle Edge {sub}")
                    # FUTURE: Handle other sub-elements here
            else:
                self.targets.append({"obj": obj, "type": "placement", "idx": None, "orig_world": FreeCAD.Placement(obj.Placement)})

        if not self.targets:
            # If we had sub-elements selected but didn't find valid DM vertices/handles,
            # don't fall back to whole-object move. This prevents accidental move when clicking sticks.
            if sub_names:
                dm_logger.debug("TranslateTool: Hit non-Vertex sub-element. ignoring.")
            else:
                dm_logger.debug("TranslateTool: No valid DM targets.")
            self.terminate()
            return

        # Snap center of targets to mouse
        self.center_w = FreeCAD.Vector(0,0,0)
        for t in self.targets:
            val = t["orig_world"]
            if isinstance(val, FreeCAD.Placement): self.center_w += val.Base
            else: self.center_w += val
        self.center_w /= len(self.targets)
        self.start_mouse_pos = self.center_w
        
        self.constraint_axis = None 
        self.constraint_plane = None
        dm_logger.debug(f"TranslateTool started with {len(self.targets)} targets.")

    def handle_keyboard(self, event_dict):
        key = str(event_dict.get("Key", "None")).upper()
        mod = event_dict.get("Mod", "None")
        is_shift = "SHIFT" in mod

        if key == "ESCAPE":
            self.cancel()
            return True
        if key in ["X", "Y", "Z"]:
            axis = key.lower()
            if is_shift:
                planes = {'x': 'yz', 'y': 'xz', 'z': 'xy'}
                self.constraint_plane = planes[axis]
                self.constraint_axis = None
            else:
                self.constraint_axis = axis
                self.constraint_plane = None
            return True
        return super().handle_keyboard(event_dict)

    def handle_move(self, event_dict):
        cam_dir = self.view.getViewDirection()
        pt = self.get_mouse_world_pos(event_dict, cam_dir, self.center_w)
        if pt is None: return

        delta = pt - self.start_mouse_pos
        if self.constraint_axis == 'x': delta = FreeCAD.Vector(delta.x, 0, 0)
        elif self.constraint_axis == 'y': delta = FreeCAD.Vector(0, delta.y, 0)
        elif self.constraint_axis == 'z': delta = FreeCAD.Vector(0, 0, delta.z)
        if self.constraint_plane == 'xy': delta.z = 0
        elif self.constraint_plane == 'yz': delta.x = 0
        elif self.constraint_plane == 'xz': delta.y = 0

        self.apply_delta(delta)
        self.view.redraw()

    def apply_delta(self, delta):
        dirty = set()
        
        # Reset to originals
        for obj in self.obj_orig_placement:
            obj.Placement = FreeCAD.Placement(self.obj_orig_placement[obj])
            if obj in self.obj_orig_points:
                if hasattr(obj, "Points"):
                    obj.Points = list(self.obj_orig_points[obj])
                    obj.HandleIn = list(self.obj_orig_h_in[obj])
                    obj.HandleOut = list(self.obj_orig_h_out[obj])
                elif hasattr(obj, "Position"):
                    obj.Position = FreeCAD.Vector(self.obj_orig_points[obj])

        # Apply delta
        for t in self.targets:
            obj, t_type, idx, orig_w = t["obj"], t["type"], t["idx"], t["orig_world"]
            
            if t_type == "placement":
                new_p = FreeCAD.Placement(orig_w)
                new_p.move(delta)
                obj.Placement = new_p
            else:
                new_w = orig_w + delta
                local = obj.Placement.inverse().multVec(new_w)
                
                if t_type == "point":
                    if hasattr(obj, "Points"):
                        pts = list(obj.Points); pts[idx] = local; obj.Points = pts
                        
                        # PERIODIC SYNC: If closed, sync start/end points if they overlap
                        is_closed = hasattr(obj, "Closed") and obj.Closed
                        is_fused = is_closed and len(pts) > 2 and (self.obj_orig_points[obj][0] - self.obj_orig_points[obj][-1]).Length < 0.05
                        
                        if is_fused:
                            if idx == 0: pts[-1] = local; obj.Points = pts
                            elif idx == len(pts)-1: pts[0] = local; obj.Points = pts

                        # HANDLES FOLLOW POINTS: move corresponding hi/ho
                        if hasattr(obj, "HandleIn") and idx < len(obj.HandleIn):
                            h_w = obj.Placement.multVec(self.obj_orig_h_in[obj][idx]) + delta
                            h_in = list(obj.HandleIn); h_in[idx] = obj.Placement.inverse().multVec(h_w); obj.HandleIn = h_in
                            # Sync handles if periodic and fused
                            if is_fused:
                                if idx == 0 and len(h_in) >= len(pts):
                                    h_in[-1] = h_in[0]; obj.HandleIn = h_in
                                elif idx == len(pts)-1 and len(h_in) >= len(pts):
                                    h_in[0] = h_in[-1]; obj.HandleIn = h_in

                        if hasattr(obj, "HandleOut") and idx < len(obj.HandleOut):
                            h_w = obj.Placement.multVec(self.obj_orig_h_out[obj][idx]) + delta
                            h_out = list(obj.HandleOut); h_out[idx] = obj.Placement.inverse().multVec(h_w); obj.HandleOut = h_out
                            # Sync handles if periodic and fused
                            if is_fused:
                                if idx == 0 and len(h_out) >= len(pts):
                                    h_out[-1] = h_out[0]; obj.HandleOut = h_out
                                elif idx == len(pts)-1 and len(h_out) >= len(pts):
                                    h_out[0] = h_out[-1]; obj.HandleOut = h_out

                    elif hasattr(obj, "Position"):
                        obj.Position = local
                elif t_type == "handle_in":
                    h = list(obj.HandleIn); h[idx] = local; obj.HandleIn = h
                elif t_type == "handle_out":
                    h = list(obj.HandleOut); h[idx] = local; obj.HandleOut = h
            dirty.add(obj)
        
        for obj in dirty:
            obj.touch(); obj.Document.recompute()

    def handle_click(self, event_dict):
        btn = event_dict.get("Button")
        if btn == "BUTTON1": self.finish(); return True
        elif btn == "BUTTON3": self.cancel(); return True
        return False

    def cancel(self):
        for obj, p in self.obj_orig_placement.items():
            obj.Placement = p
            if obj in self.obj_orig_points:
                if hasattr(obj, "Points"):
                    obj.Points = list(self.obj_orig_points[obj])
                    obj.HandleIn = list(self.obj_orig_h_in[obj])
                    obj.HandleOut = list(self.obj_orig_h_out[obj])
                elif hasattr(obj, "Position"):
                    obj.Position = FreeCAD.Vector(self.obj_orig_points[obj])
            obj.touch(); obj.Document.recompute()
        self.terminate()

    def finish(self):
        self.terminate()
