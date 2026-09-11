# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
Scale Tool for FCFields.
Scales whole FldObjects or selected sub-elements (vertices/handles) around a pivot.
Uniform only: warns if axis is locked and scales uniformly anyway.
"""

import math
import FreeCAD
import FreeCADGui
from PySide import QtCore
from freecad.fields.core import fld_logger
from freecad.fields.core.input.input_manager import FldInputManager
from .translate_tool import TranslateTool


class ScaleTool(TranslateTool):
    def get_command_id(self):
        return "Fields_Scale"

    def __init__(self):
        super().__init__()
        if not getattr(self, "targets", None):
            return
        self.start_dist = 1.0

    def handle_keyboard(self, event_dict):
        key_code = event_dict.get("Key")

        if key_code == QtCore.Qt.Key_Escape:
            self.cancel()
            return True
        if key_code in (QtCore.Qt.Key_X, QtCore.Qt.Key_Y, QtCore.Qt.Key_Z):
            fld_logger.warn("Non-uniform scale of SDF objects breaks distance field properties; scaling uniformly.")
            return True
        return super().handle_keyboard(event_dict)

    def on_button1_down(self, event_dict):
        cam_dir = self.view.getViewDirection()
        pt = self.projector.get_mouse_world_pos(event_dict, cam_dir, self.center_w, place_on_geometry=False)
        if pt is not None:
            self.start_mouse_pos = pt
            self.state = 1
            im = FldInputManager.get_instance()
            cur = im.get_mouse_pos(event_dict)
            pivot_px = self._get_pivot_px(cur)
            self.start_dist = max(math.hypot(cur[0] - pivot_px[0], cur[1] - pivot_px[1]), 1.0)

            doc = None
            if self.targets:
                doc = getattr(self.targets[0].get("obj", None), "Document", None)
            if not doc:
                doc = FreeCAD.ActiveDocument
            if doc and hasattr(doc, "openTransaction"):
                doc.openTransaction("Scale")
                self._trans_transaction = True
            self._start_drag_timer()
        return True

    def _get_pivot_px(self, fallback_px):
        if hasattr(self, "view") and self.view and hasattr(self.view, "getPointOnScreen"):
            try:
                px = self.view.getPointOnScreen(self.center_w)
                if px is not None:
                    return px
            except Exception:
                pass
        return fallback_px

    def _resolve_factor(self, event_dict):
        im = FldInputManager.get_instance()
        cur = im.get_mouse_pos(event_dict)
        pivot_px = self._get_pivot_px(cur)
        cur_dist = math.hypot(cur[0] - pivot_px[0], cur[1] - pivot_px[1])
        factor = cur_dist / max(self.start_dist, 1.0)
        from freecad.fields.core.input import fld_snap
        from freecad.fields.core.fld_settings import get_snap_during_direct_drag
        if get_snap_during_direct_drag() and fld_snap.snap_active():
            factor = fld_snap.quantize(factor, 0.1)

        try:
            mw = FreeCADGui.getMainWindow()
            if mw:
                sb = mw.statusBar()
                if sb:
                    sb.showMessage(f"Scale: {factor:.2f}×", 1000)
        except Exception:
            pass

        return factor

    def handle_move(self, event_dict):
        if self.state != 1:
            return
        factor = self._resolve_factor(event_dict)
        self.apply_scale(factor)
        self.view.redraw()

    def _drag_update(self):
        if not FldInputManager.get_instance()._left_mouse_down:
            self._stop_drag_timer()
            self.state = 0
            return
        if self.state != 1:
            self._stop_drag_timer()
            return
        event_dict = {"Position": FldInputManager.get_instance()._last_qt_pos}
        factor = self._resolve_factor(event_dict)
        self.apply_scale(factor)
        self.view.redraw()

    def apply_scale(self, factor):
        pivot = self.center_w
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

        # Apply uniform scale
        for t in self.targets:
            obj, t_type, idx, orig_w = t["obj"], t["type"], t["idx"], t["orig_world"]
            pl = self.obj_orig_placement[obj]
            inv_pl = pl.inverse()

            if t_type == "placement":
                new_p = FreeCAD.Placement(orig_w)
                new_p.Base = pivot + (orig_w.Base - pivot) * factor
                obj.Placement = new_p
            else:
                p_new_world = pivot + (orig_w - pivot) * factor
                local = inv_pl.multVec(p_new_world)

                if t_type == "point":
                    if hasattr(obj, "Points"):
                        pts = list(obj.Points)
                        pts[idx] = local
                        obj.Points = pts

                        is_closed = hasattr(obj, "Closed") and obj.Closed
                        is_fused = is_closed and len(pts) > 2 and (self.obj_orig_points[obj][0] - self.obj_orig_points[obj][-1]).Length < 0.05
                        if is_fused:
                            if idx == 0: pts[-1] = local; obj.Points = pts
                            elif idx == len(pts)-1: pts[0] = local; obj.Points = pts

                        if hasattr(obj, "HandleIn") and idx < len(obj.HandleIn):
                            h_w = pl.multVec(self.obj_orig_h_in[obj][idx])
                            h_new_world = pivot + (h_w - pivot) * factor
                            h_in = list(obj.HandleIn); h_in[idx] = inv_pl.multVec(h_new_world); obj.HandleIn = h_in
                            if is_fused:
                                if idx == 0 and len(h_in) >= len(pts): h_in[-1] = h_in[0]; obj.HandleIn = h_in
                                elif idx == len(pts)-1 and len(h_in) >= len(pts): h_in[0] = h_in[-1]; obj.HandleIn = h_in

                        if hasattr(obj, "HandleOut") and idx < len(obj.HandleOut):
                            h_w = pl.multVec(self.obj_orig_h_out[obj][idx])
                            h_new_world = pivot + (h_w - pivot) * factor
                            h_out = list(obj.HandleOut); h_out[idx] = inv_pl.multVec(h_new_world); obj.HandleOut = h_out
                            if is_fused:
                                if idx == 0 and len(h_out) >= len(pts): h_out[-1] = h_out[0]; obj.HandleOut = h_out
                                elif idx == len(pts)-1 and len(h_out) >= len(pts): h_out[0] = h_out[-1]; obj.HandleOut = h_out

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
