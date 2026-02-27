"""
Base classes for SDF primitive creators.
No Coin3D / pivy anywhere.

Camera/ray math uses FreeCADGui.ActiveDocument.ActiveView public API only.
"""

import FreeCAD
import FreeCADGui
from PySide import QtCore

from FCDirectModeling import sdf_logger



# ─────────────────────────────────────────────────────────────────────────────
# Camera helpers (no Coin3D)
# ─────────────────────────────────────────────────────────────────────────────

def _is_orthographic(view):
    """True if the active camera is orthographic (not perspective)."""
    try:
        cam = view.getCameraNode()
        return "Orthographic" in cam.getTypeId().getName()
    except Exception:
        return False

def _cam_pos(view):
    """Camera world position as a FreeCAD.Vector (perspective only)."""
    try:
        cam = view.getCameraNode()
        p = cam.position.getValue()
        return FreeCAD.Vector(p[0], p[1], p[2])
    except Exception:
        return FreeCAD.Vector(0,0,100)


# ─────────────────────────────────────────────────────────────────────────────
# PrimitiveCreatorBase
# ─────────────────────────────────────────────────────────────────────────────

class PrimitiveCreatorBase:
    def __init__(self):
        self._terminated = False
        self.view     = FreeCADGui.ActiveDocument.ActiveView
        if not self.view:
            return

        self.callback = self.view.addEventCallback("SoEvent", self.event_cb)

        self.start_point   = None
        self.current_point = None
        self.center        = None
        self.state         = 0

    def terminate(self):
        self._terminated = True
        try:
            if self.callback:
                self.view.removeEventCallback("SoEvent", self.callback)
                self.callback = None
        except Exception:
            pass

    # Removed _get_y_inverted in favor of inline diagnostic logic in get_point_on_plane

    def finish(self):
        pass

    # ------------------------------------------------------------------
    # Geometry helpers — use FreeCAD view API, not Coin3D directly
    # ------------------------------------------------------------------

    def get_point_on_plane(self, event_dict, plane_normal, plane_point):
        """Standard Ray-Plane Intersection using FreeCAD Raycasting."""
        try:
            x = event_dict['Position'][0]
            y = event_dict['Position'][1]
            
            # TELEMETRY: Get viewport size
            v_size = self.view.getSize()
            w, h = (v_size[0], v_size[1]) if v_size else (0, 0)
            
            # DIAGNOSTIC: Test "No Inversion" for Y (maybe events are already top-down)
            # raw_y top-down: h=0 at top
            inv_y = y # TEST: Use raw y
            
            print(f"[DEBUG] COORD_DIAG: raw_x={x}, raw_y={y}, w={w}, h={h}, used_y={inv_y}")
            
            # getPoint expects (x, y) where y=0 is TOP
            focal = self.view.getPoint(x, inv_y)

            if _is_orthographic(self.view):
                ray_origin = focal
                ray_dir    = self.view.getViewDirection()
            else:
                ray_origin = _cam_pos(self.view)
                ray_dir    = focal - ray_origin
                ray_dir.normalize()

            n = plane_normal or FreeCAD.Vector(0, 0, 1)
            o = plane_point  or FreeCAD.Vector(0, 0, 0)

            denom = ray_dir.dot(n)
            if abs(denom) < 1e-6:
                return o
            t = (o - ray_origin).dot(n) / denom
            pt = ray_origin + ray_dir * t
            
            print(f"[DEBUG] Intersection: pt={pt.x:.2f},{pt.y:.2f},{pt.z:.2f}, focal_y={focal.y:.2f}")
            return pt
        except Exception as e:
            sdf_logger.debug(f"DEBUG: get_point_on_plane error: {e}")
            FreeCAD.Console.PrintError(f"get_point_on_plane: {e}\n")
            return FreeCAD.Vector(0, 0, 0)

    def get_closest_point_on_axis(self, event_dict, axis_start, axis_dir):
        """Returns the point on the given axis closest to the cursor ray."""
        try:
            pos = event_dict["Position"]
            y_inv = self._get_y_inverted(pos[1])
            focal = self.view.getPoint(pos[0], y_inv)

            if _is_orthographic(self.view):
                ray_origin = focal
                ray_dir    = self.view.getViewDirection()
            else:
                ray_origin = _cam_pos(self.view)
                ray_dir    = focal - ray_origin
                ray_dir.normalize()

            P1, V1 = ray_origin, ray_dir
            P2, V2 = axis_start, axis_dir

            DP  = P2 - P1
            v12 = V1.dot(V2)
            v11 = V1.dot(V1)
            v22 = V2.dot(V2)
            det = v11 * v22 - v12 * v12

            if abs(det) < 1e-6:
                return P2
            dp_v1 = DP.dot(V1)
            dp_v2 = DP.dot(V2)
            u = (v12 * dp_v1 - v11 * dp_v2) / det
            return P2 + V2 * u
        except Exception:
            return axis_start

    # ------------------------------------------------------------------
    # Event loop
    # ------------------------------------------------------------------

    def event_cb(self, event_dict):
        try:
            event_type = event_dict.get("Type", "Unknown")
            # Only log non-move events to avoid spam
            if event_type != "SoLocation2Event":
                sdf_logger.debug(f"DEBUG: event_cb: {event_type}")

            if event_type == "SoMouseButtonEvent":
                if event_dict["State"] == "DOWN" and event_dict["Button"] == "BUTTON1":
                    sdf_logger.debug("DEBUG: Left click detected")
                    return self.handle_click(event_dict)
            elif event_type == "SoLocation2Event":
                self.handle_move(event_dict)
            elif event_type == "SoKeyboardEvent":
                key = str(event_dict.get("Key", "None")).upper()
                sdf_logger.debug(f"DEBUG: Key event: {key}")
                if event_dict["State"] == "DOWN" and key == "ESCAPE":
                    QtCore.QTimer.singleShot(0, self.terminate)
                    return True
            sdf_logger.debug("event_cb: Returning False")
            return False
        except Exception:
            return False

    def to_local(self, p):
        if not hasattr(self, "working_plane") or not self.working_plane:
            return p
        mat = self.working_plane.toMatrix()
        mat.invert()
        return mat.multVec(p)

    def to_global(self, p):
        if not hasattr(self, "working_plane") or not self.working_plane:
            return p
        return self.working_plane.toMatrix().multVec(p)

    def get_face_under_mouse(self, event_dict):
        pos = event_dict["Position"]
        try:
            info = self.view.getObjectInfo((pos[0], pos[1]))
            if info and "Object" in info and "Component" in info:
                 return info["Object"], info["Component"]
        except Exception:
            pass
        return None, None

    def handle_click(self, event_dict):
        pass

    def handle_move(self, event_dict):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# SDFMeshPrimitiveCreator
# ─────────────────────────────────────────────────────────────────────────────

class SDFMeshPrimitiveCreator(PrimitiveCreatorBase):
    """
    Base for creators that produce an SDF object via Mesh::FeaturePython.
    Live preview is a Mesh::Feature whose .Mesh is replaced in-place each frame.
    No Coin3D.
    """

    def __init__(self):
        super().__init__()
        self._preview_obj = None   # Mesh::Feature used for live preview
        self._pending_sdf_type = None
        self._pending_sdf_params = None
        self._preview_queued = False
        self._finished = False     # Guard for finalization

        self._last_sdf_type = None
        self._last_sdf_params = None
        self._last_placement = None
        
        self._debug_pt = None      # Store current mouse 3D for debug dot
        self._preview_cursor = None # Red dot object

    # ------------------------------------------------------------------
    # Preview — create once, replace .Mesh in-place (no recompute)
    # ------------------------------------------------------------------

    def update_sdf_preview(self, sdf_type, params, resolution=None, placement=None):
        """
        Setup the pending mesh request, but defer the exact execution 
        to avoid crashing in Coin3D event traversal.
        """
        if self._terminated:
            return

        self._pending_sdf_type = sdf_type
        self._pending_sdf_params = params
        
        if resolution is None:
            from ..sdf_object import get_preview_resolution
            resolution = get_preview_resolution()
            
        self._pending_resolution = resolution
        
        self._last_sdf_type = sdf_type
        self._last_sdf_params = params
        
        # Track 3D cursor for debugging
        if "debug_pt" in params:
            self._debug_pt = params["debug_pt"]
        
        # Track placement for finalization
        if placement:
            self._last_placement = placement
        elif hasattr(self, "working_plane"):
            self._last_placement = self.working_plane
        
        if not self._preview_queued:
            self._preview_queued = True
            QtCore.QTimer.singleShot(0, self._process_preview_queue)

    def _process_preview_queue(self):
        self._preview_queued = False
        if self._terminated:
            return

        try:
            sdf_type = self._pending_sdf_type
            params = self._pending_sdf_params
            if not sdf_type or not params:
                return

            from ..sdf_object import _SDF_BUILDERS, mesh_sdf, _to_mesh_facets
            import Mesh as MeshModule
            
            bld = _SDF_BUILDERS.get(sdf_type)
            if not bld:
                return
            
            sdf_fn, (mn, mx) = bld(params)
            verts, tris = mesh_sdf(sdf_fn, mn, mx, resolution=self._pending_resolution)

            if len(verts) == 0:
                return

            facets = _to_mesh_facets(verts, tris)
            mesh   = MeshModule.Mesh(facets)

            doc = FreeCAD.activeDocument()
            if not doc:
                return

            # Hierarchy: App::Part (SDF_Preview) -> Mesh::Feature (SDF_[Type]_Preview)
            if self._preview_obj is None or self._preview_obj not in doc.Objects:
                self._preview_obj = doc.addObject("App::Part", "SDF_Preview")
                if hasattr(self._preview_obj, "ViewObject") and self._preview_obj.ViewObject:
                    try:
                        # Use a recognizable preview color
                        self._preview_obj.ViewObject.Visibility = True
                    except Exception:
                        pass
                
                # Create child mesh
                mesh_name = f"SDF_{sdf_type.capitalize()}_Preview"
                self._preview_mesh = doc.addObject("Mesh::Feature", mesh_name)
                self._preview_obj.addObject(self._preview_mesh)
                
                if hasattr(self._preview_mesh, "ViewObject") and self._preview_mesh.ViewObject:
                    try:
                        self._preview_mesh.ViewObject.ShapeColor = (0.20, 0.60, 0.85)
                        self._preview_mesh.ViewObject.Transparency = 20
                        self._preview_mesh.ViewObject.DisplayMode = "Flat Lines"
                    except Exception:
                        pass

            # Store current SDF info on the creator so finish() can access it.
            self._preview_sdf_type = sdf_type
            self._preview_sdf_params = params

            # Assign mesh directly to the child mesh object
            self._preview_mesh.Mesh = mesh

            # Update Debug Cursor
            if self._debug_pt:
                # Log intersection to help user debug
                print(f"[DEBUG] Ray Intersection: {self._debug_pt.x:.2f}, {self._debug_pt.y:.2f}, {self._debug_pt.z:.2f}")

                if self._preview_cursor is None or self._preview_cursor not in doc.Objects:
                    self._preview_cursor = doc.addObject("Part::Feature", "SDF_DebugCursor")
                    
                    import Part
                    size = 25.0 # 50mm total spread
                    l1 = Part.LineSegment(FreeCAD.Vector(-size,0,0), FreeCAD.Vector(size,0,0)).toShape()
                    l2 = Part.LineSegment(FreeCAD.Vector(0,-size,0), FreeCAD.Vector(0,size,0)).toShape()
                    l3 = Part.LineSegment(FreeCAD.Vector(0,0,-size), FreeCAD.Vector(0,0,size)).toShape()
                    self._preview_cursor.Shape = Part.Compound([l1, l2, l3])
                    
                    if hasattr(self._preview_cursor, "ViewObject") and self._preview_cursor.ViewObject:
                        self._preview_cursor.ViewObject.ShapeColor = (1.0, 0.0, 0.0) # Red
                        self._preview_cursor.ViewObject.LineColor = (1.0, 0.0, 0.0)
                        self._preview_cursor.ViewObject.LineWidth = 12.0
                        self._preview_cursor.ViewObject.PointColor = (1.0, 0.0, 0.0)
                        self._preview_cursor.ViewObject.PointSize = 16.0
                        self._preview_cursor.ViewObject.Transparency = 0
                        self._preview_cursor.ViewObject.Selectable = False
                        
                        # Use flat emissive coloring (no lighting/shading)
                        if hasattr(self._preview_cursor.ViewObject, "LightModel"):
                            self._preview_cursor.ViewObject.LightModel = "NoLight"
                        
                        # Hide from tree view to avoid clutter
                        self._preview_cursor.ViewObject.Visibility = True
                        if hasattr(self._preview_cursor, "ShowInTree"):
                             self._preview_cursor.ShowInTree = False
                
                # Use larger lines (30mm) for high visibility
                debug_shape = Part.Compound([
                    Part.makeLine((self._debug_pt.x-15,self._debug_pt.y,self._debug_pt.z),(self._debug_pt.x+15,self._debug_pt.y,self._debug_pt.z)),
                    Part.makeLine((self._debug_pt.x,self._debug_pt.y-15,self._debug_pt.z),(self._debug_pt.x,self._debug_pt.y+15,self._debug_pt.z)),
                    Part.makeLine((self._debug_pt.x,self._debug_pt.y,self._debug_pt.z-15),(self._debug_pt.x,self._debug_pt.y,self._debug_pt.z+15))
                ])
                self._preview_cursor.Shape = debug_shape
                self._preview_cursor.Placement = FreeCAD.Placement(FreeCAD.Vector(0,0,0), FreeCAD.Rotation())

            # A plain updateGui() is sufficient to repaint. No recompute needed.
            FreeCADGui.updateGui()

        except Exception as e:
            sdf_logger.debug(f"DEBUG: _process_preview_queue error: {e}")
            pass


    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def finish(self):
        """Schedule the finalization to happen safely outside the event loop."""
        QtCore.QTimer.singleShot(0, self._do_finish)

    def _do_finish(self):
        """Standard finalization for all SDF primitives."""
        if self._finished:
            return
            
        try:
            if self._last_sdf_type and self._last_sdf_params:
                from ..sdf_object import create_sdf_object
                sdf_logger.debug(f"_do_finish: Finalizing. type={self._last_sdf_type}, params={self._last_sdf_params}, placement={self._last_placement}")
                # Create the final high-res SDFObject
                create_sdf_object(
                    self._last_sdf_type.capitalize(), 
                    self._last_sdf_type, 
                    self._last_sdf_params,
                    placement=self._last_placement
                )
            else:
                sdf_logger.debug(f"_do_finish: No SDF data to finalize (type={self._last_sdf_type})")
        except Exception as e:
            sdf_logger.error(f"_do_finish FAILED: {e}")
            import traceback
            sdf_logger.error(traceback.format_exc())

        self._finished = True
        self.terminate()

        # Close task panel if open
        try:
            import FreeCADGui
            FreeCADGui.Control.closeDialog()
        except Exception:
            pass

    def terminate(self):
        """Remove preview objects (Part container + child mesh) and unregister the event callback."""
        if self._preview_obj is not None:
            try:
                doc = FreeCAD.activeDocument()
                if doc:
                    # Remove child mesh first
                    if hasattr(self, '_preview_mesh') and self._preview_mesh is not None:
                        if self._preview_mesh in doc.Objects:
                            doc.removeObject(self._preview_mesh.Name)
                        self._preview_mesh = None
                    # Remove debug cursor
                    if self._preview_cursor is not None:
                        if self._preview_cursor in doc.Objects:
                            doc.removeObject(self._preview_cursor.Name)
                        self._preview_cursor = None
                    # Then remove the parent container
                    if self._preview_obj in doc.Objects:
                        doc.removeObject(self._preview_obj.Name)
                    doc.recompute()
            except Exception as e:
                sdf_logger.debug(f"Error removing preview objects: {e}")
            self._preview_obj = None
        super().terminate()

