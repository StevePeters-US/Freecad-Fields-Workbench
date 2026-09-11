# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
Translate Tool for FCFields.
Moves whole FldObjects or selected sub-elements (vertices/handles).
"""

import FreeCAD
import FreeCADGui
from PySide import QtCore
from freecad.fields.core import fld_logger
from freecad.fields.core.input.input_manager import FldInputManager
from .fld_base import FldBase

class TranslateTool(FldBase):
    def get_command_id(self):
        return "Fields_Translate"

    def __init__(self):
        super().__init__()

        # Selection tracking
        self.targets = [] # List of dicts: {"obj": obj, "type": "point"|"handle_in"|"handle_out"|"placement", "idx": int|None, "orig_world": Vector|Placement}
        self.obj_orig_points = {} # obj -> original_points_list
        self.obj_orig_h_in = {}   # obj -> original_h_in_list
        self.obj_orig_h_out = {}  # obj -> original_h_out_list
        self.obj_orig_placement = {} # obj -> original_placement
        self._trans_transaction = False
        
        sel_ex = FreeCADGui.Selection.getSelectionEx()
        if not sel_ex:
            fld_logger.debug("TranslateTool: Nothing selected.")
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
                elif hasattr(obj, "Coordinates"):
                    self.obj_orig_points[obj] = FreeCAD.Vector(obj.Coordinates)
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

                             if not found and hasattr(obj, "Coordinates"):
                                 p_w = obj.Placement.multVec(obj.Coordinates)
                                 if (v_world - p_w).Length < 0.001:
                                     self.targets.append({"obj": obj, "type": "point", "idx": 0, "orig_world": v_world})
                                     found = True
                             
                             if not found and hasattr(obj, "Position"):
                                 p_w = obj.Placement.multVec(obj.Position)
                                 if (v_world - p_w).Length < 0.001:
                                     self.targets.append({"obj": obj, "type": "point", "idx": 0, "orig_world": v_world})
                         except Exception as e:
                             fld_logger.debug(f"TranslateTool: Vertex selection parsing failed: {e}")
                    elif "Edge" in sub:
                        # For FldObjects, Edge1 is the main B-spline. Other edges are handles.
                        # Allow dragging the whole object by its main curve, but ignore handles.
                        if sub == "Edge1":
                            self.targets.append({"obj": obj, "type": "placement", "idx": None, "orig_world": FreeCAD.Placement(obj.Placement)})
                        else:
                            fld_logger.debug(f"TranslateTool: Ignoring handle Edge {sub}")
                    # FUTURE: Handle other sub-elements here
            else:
                self.targets.append({"obj": obj, "type": "placement", "idx": None, "orig_world": FreeCAD.Placement(obj.Placement)})

        if not self.targets:
            # If we had sub-elements selected but didn't find valid Fields vertices/handles,
            # don't fall back to whole-object move. This prevents accidental move when clicking sticks.
            if sub_names:
                fld_logger.debug("TranslateTool: Hit non-Vertex sub-element. ignoring.")
            else:
                fld_logger.debug("TranslateTool: No valid Fields targets.")
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
        
        self._constraint_axis = None
        self._constraint_plane = None
        self.state = 0  # Waiting for LMB press to begin drag
        fld_logger.debug(f"TranslateTool started with {len(self.targets)} targets.")
        self._is_editing = True # Mark as "in progress" for RMB finish

        from pivy import coin
        from freecad.fields.core.input.fld_snap import FldSnapGuide
        self._snap_guide_root = coin.SoAnnotation() if (coin and hasattr(coin, "SoAnnotation")) else None
        if self._snap_guide_root and self.view and hasattr(self.view, "getSceneGraph") and self.view.getSceneGraph():
            self.view.getSceneGraph().addChild(self._snap_guide_root)
        self._snap_guide = FldSnapGuide()
        if self._snap_guide_root:
            self._snap_guide.draw(self._snap_guide_root)

    def is_in_progress(self):
        """Returns True if there are active targets to move."""
        return len(self.targets) > 0 or getattr(self, "_is_editing", False)

    def handle_keyboard(self, event_dict):
        key_code = event_dict.get("Key")
        mod = event_dict.get("Modifiers", QtCore.Qt.NoModifier)
        is_shift = bool(mod & QtCore.Qt.ShiftModifier)

        if key_code == QtCore.Qt.Key_Escape:
            self.cancel()
            return True
        if key_code == QtCore.Qt.Key_X:
            if is_shift: self._constraint_plane = 'yz'; self._constraint_axis = None
            else: self._constraint_axis = 'x'; self._constraint_plane = None
            self._update_constraint_visual()
            return True
        if key_code == QtCore.Qt.Key_Y:
            if is_shift: self._constraint_plane = 'xz'; self._constraint_axis = None
            else: self._constraint_axis = 'y'; self._constraint_plane = None
            self._update_constraint_visual()
            return True
        if key_code == QtCore.Qt.Key_Z:
            if is_shift: self._constraint_plane = 'xy'; self._constraint_axis = None
            else: self._constraint_axis = 'z'; self._constraint_plane = None
            self._update_constraint_visual()
            return True
        return super().handle_keyboard(event_dict)

    def _resolve_delta(self, event_dict):
        pt = None
        if self._constraint_axis:
            ax = {'x': FreeCAD.Vector(1, 0, 0),
                  'y': FreeCAD.Vector(0, 1, 0),
                  'z': FreeCAD.Vector(0, 0, 1)}[self._constraint_axis]
            # Closest point on the axis to the mouse ray - correct at every camera
            # angle, unlike projecting onto a camera plane and dropping components.
            pt = self.projector.get_axis_point(self.start_mouse_pos, ax, event_dict)
            if pt is None:
                return None
            delta = pt - self.start_mouse_pos
            from freecad.fields.core.input import fld_snap
            if fld_snap.snap_active():
                from freecad.fields.core.fld_settings import get_snap_grid_step
                d = fld_snap.snap_length(delta.dot(ax), get_snap_grid_step())
                delta = FreeCAD.Vector(ax) * d
                if getattr(self, "_snap_guide", None):
                    self._snap_guide.show_axis(self.start_mouse_pos, ax, self.view)
            elif getattr(self, "_snap_guide", None):
                self._snap_guide.hide()
            return delta

        normal = self.view.getViewDirection()
        u_hint = None
        if self._constraint_plane:
            normal = {'xy': FreeCAD.Vector(0, 0, 1),
                      'yz': FreeCAD.Vector(1, 0, 0),
                      'xz': FreeCAD.Vector(0, 1, 0)}[self._constraint_plane]
            u_hint = {'xy': FreeCAD.Vector(1, 0, 0),
                      'yz': FreeCAD.Vector(0, 1, 0),
                      'xz': FreeCAD.Vector(1, 0, 0)}[self._constraint_plane]
        pt = self.projector.get_mouse_world_pos(
            event_dict, normal, self.center_w, place_on_geometry=False)
        if pt is None:
            return None
        delta = pt - self.start_mouse_pos
        from freecad.fields.core.input import fld_snap
        if fld_snap.snap_active():
            from freecad.fields.core.fld_settings import get_snap_grid_step
            delta = fld_snap.snap_vector_to_grid(delta, get_snap_grid_step())
            if getattr(self, "_snap_guide", None):
                self._snap_guide.show_plane(self.start_mouse_pos, normal, self.view, u_hint=u_hint)
        elif getattr(self, "_snap_guide", None):
            self._snap_guide.hide()
        return delta

    def handle_move(self, event_dict):
        if self.state != 1:
            return
        delta = self._resolve_delta(event_dict)
        if delta is None:
            return
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

                    elif hasattr(obj, "Coordinates"):
                        obj.Coordinates = local
                        if hasattr(obj, "Position"):
                            obj.Position = local
                    elif hasattr(obj, "Position"):
                        obj.Position = local
                elif t_type == "handle_in":
                    h = list(obj.HandleIn); h[idx] = local; obj.HandleIn = h
                elif t_type == "handle_out":
                    h = list(obj.HandleOut); h[idx] = local; obj.HandleOut = h
            dirty.add(obj)
        
        for obj in dirty:
            obj.touch()
        if dirty:
            doc = next(iter(dirty)).Document
            QtCore.QTimer.singleShot(0, lambda d=doc, objs=list(dirty): d.recompute(objs))

    def on_button1_down(self, event_dict):
        cam_dir = self.view.getViewDirection()
        pt = self.projector.get_mouse_world_pos(event_dict, cam_dir, self.center_w, place_on_geometry=False)
        if pt is not None:
            self.start_mouse_pos = pt
            self.state = 1
            doc = None
            if self.targets:
                doc = getattr(self.targets[0].get("obj", None), "Document", None)
            if not doc:
                doc = FreeCAD.ActiveDocument
            if doc and hasattr(doc, "openTransaction"):
                doc.openTransaction("Translate")
                self._trans_transaction = True
            self._start_drag_timer()
        return True

    def on_button1_up(self, _event_dict):
        self._stop_drag_timer()
        if getattr(self, "_snap_guide", None):
            self._snap_guide.hide()
        if self.state == 1:
            self.finish()
        return True


    def _drag_update(self):
        # Self-terminate if LMB was released (handles cases where on_button1_up is intercepted)
        if not FldInputManager.get_instance()._left_mouse_down:
            self._stop_drag_timer()
            if getattr(self, "_snap_guide", None):
                self._snap_guide.hide()
            self.state = 0
            return
        if self.state != 1:
            self._stop_drag_timer()
            if getattr(self, "_snap_guide", None):
                self._snap_guide.hide()
            return
        event_dict = {"Position": FldInputManager.get_instance()._last_qt_pos}
        delta = self._resolve_delta(event_dict)
        if delta is None:
            return
        self.apply_delta(delta)
        self.view.redraw()

    def restore_original(self):
        restored = []
        for obj, p in self.obj_orig_placement.items():
            obj.Placement = p
            if obj in self.obj_orig_points:
                if hasattr(obj, "Points"):
                    obj.Points = list(self.obj_orig_points[obj])
                    obj.HandleIn = list(self.obj_orig_h_in[obj])
                    obj.HandleOut = list(self.obj_orig_h_out[obj])
                elif hasattr(obj, "Coordinates"):
                    obj.Coordinates = FreeCAD.Vector(self.obj_orig_points[obj])
                    if hasattr(obj, "Position"):
                        obj.Position = FreeCAD.Vector(self.obj_orig_points[obj])
                elif hasattr(obj, "Position"):
                    obj.Position = FreeCAD.Vector(self.obj_orig_points[obj])
            obj.touch()
            restored.append(obj)
        def _deferred_recompute(objs=restored):
            for obj in objs:
                obj.Document.recompute()
        QtCore.QTimer.singleShot(0, _deferred_recompute)

    def cancel(self):
        self._stop_drag_timer()
        if getattr(self, "_snap_guide", None):
            self._snap_guide.undraw()
            self._snap_guide = None
        if getattr(self, "_snap_guide_root", None):
            if self.view and hasattr(self.view, "getSceneGraph") and self.view.getSceneGraph():
                try:
                    self.view.getSceneGraph().removeChild(self._snap_guide_root)
                except Exception as e:
                    fld_logger.debug(f"TranslateTool.cancel: {e}")
            self._snap_guide_root = None
        if getattr(self, "_trans_transaction", False):
            doc = None
            if self.targets:
                doc = getattr(self.targets[0].get("obj", None), "Document", None)
            if not doc:
                doc = FreeCAD.ActiveDocument
            if doc and hasattr(doc, "abortTransaction"):
                doc.abortTransaction()
            self._trans_transaction = False
        super().cancel()

    def finish(self):
        self._stop_drag_timer()
        if getattr(self, "_snap_guide", None):
            self._snap_guide.undraw()
            self._snap_guide = None
        if getattr(self, "_snap_guide_root", None):
            if self.view and hasattr(self.view, "getSceneGraph") and self.view.getSceneGraph():
                try:
                    self.view.getSceneGraph().removeChild(self._snap_guide_root)
                except Exception as e:
                    fld_logger.debug(f"TranslateTool.finish: {e}")
            self._snap_guide_root = None
        if getattr(self, "_trans_transaction", False):
            doc = None
            if self.targets:
                doc = getattr(self.targets[0].get("obj", None), "Document", None)
            if not doc:
                doc = FreeCAD.ActiveDocument
            if doc and hasattr(doc, "commitTransaction"):
                doc.commitTransaction()
            self._trans_transaction = False
        fld_logger.debug("Translation accepted.")
        self.targets = []
        self._is_editing = False
        self.reset_state() # Returns to idle

    def _do_terminate(self):
        if getattr(self, "_snap_guide", None):
            try:
                self._snap_guide.undraw()
            except Exception as e:
                fld_logger.debug(f"TranslateTool._do_terminate: Failed to undraw snap guide: {e}")
            self._snap_guide = None
        if getattr(self, "_snap_guide_root", None):
            try:
                if self.view and hasattr(self.view, "getSceneGraph") and self.view.getSceneGraph():
                    self.view.getSceneGraph().removeChild(self._snap_guide_root)
            except Exception as e:
                fld_logger.debug(f"TranslateTool._do_terminate: Failed to remove snap guide root: {e}")
            self._snap_guide_root = None
        super()._do_terminate()

    def get_context_menu(self, event_dict=None):
        return super().get_context_menu(event_dict)
