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
        sdf_logger.debug(f"DEBUG: PrimitiveCreatorBase.__init__ for {self.__class__.__name__}")
        
        sdf_logger.debug("DEBUG: Accessing ActiveView...")
        self.view     = FreeCADGui.ActiveDocument.ActiveView
        if not self.view:
            sdf_logger.debug("DEBUG: No ActiveView found!")
            return

        sdf_logger.debug("DEBUG: Adding event callback...")
        # Check if view is valid before calling addEventCallback
        sdf_logger.debug(f"DEBUG: View Type: {type(self.view)}")
        self.callback = self.view.addEventCallback("SoEvent", self.event_cb)
        sdf_logger.debug("DEBUG: Callback added")

        self.start_point   = None
        self.current_point = None
        self.center        = None
        self.state         = 0

    def terminate(self):
        self._terminated = True
        sdf_logger.debug("PrimitiveCreatorBase: Terminating...")
        try:
            if self.callback:
                self.view.removeEventCallback("SoEvent", self.callback)
                self.callback = None
            sdf_logger.debug("PrimitiveCreatorBase: Terminated successfully.")
        except Exception as e:
            sdf_logger.debug(f"PrimitiveCreatorBase: Error terminating: {e}")
            FreeCAD.Console.PrintError(f"PrimitiveCreatorBase: Error terminating: {e}\n")

    def finish(self):
        pass

    # ------------------------------------------------------------------
    # Geometry helpers — use FreeCAD view API, not Coin3D directly
    # ------------------------------------------------------------------

    def get_point_on_plane(self, event_dict, plane_normal=None, plane_point=None):
        """Project cursor onto a world-space plane. Defaults to Z=0."""
        try:
            pos = event_dict["Position"]
            focal = self.view.getPoint(pos[0], pos[1])

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
            return pt
        except Exception as e:
            sdf_logger.debug(f"DEBUG: get_point_on_plane error: {e}")
            FreeCAD.Console.PrintError(f"get_point_on_plane: {e}\n")
            return FreeCAD.Vector(0, 0, 0)

    def get_closest_point_on_axis(self, event_dict, axis_start, axis_dir):
        """Returns the point on the given axis closest to the cursor ray."""
        try:
            pos = event_dict["Position"]
            focal = self.view.getPoint(pos[0], pos[1])

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
        except Exception as e:
            FreeCAD.Console.PrintError(f"get_closest_point_on_axis: {e}\n")
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
        except Exception as e:
            sdf_logger.debug(f"DEBUG: event_cb error: {e}")
            FreeCAD.Console.PrintError(f"PrimitiveCreatorBase: Event Callback Error: {e}\n")
            return False

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

        # Store last known valid state for finalization
        self._last_sdf_type = None
        self._last_sdf_params = None
        self._last_placement = None

    # ------------------------------------------------------------------
    # Preview — create once, replace .Mesh in-place (no recompute)
    # ------------------------------------------------------------------

    def update_sdf_preview(self, sdf_type, params, resolution=None):
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
        
        # Track for finalization
        self._last_sdf_type = sdf_type
        self._last_sdf_params = params
        if hasattr(self, "working_plane"):
            self._last_placement = self.working_plane
        
        if not self._preview_queued:
            self._preview_queued = True
            sdf_logger.debug("DEBUG: Scheduling deferred SDF preview generation...")
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

            FreeCAD.Console.PrintMessage(f"DEBUG: Processing queued SDF preview {sdf_type} at res {self._pending_resolution}\n")
            import Mesh as MeshModule
            import FreeCADGui
            from ..sdf_object import (_SDF_BUILDERS, mesh_sdf, _to_mesh_facets, get_show_wireframe)

            bld = _SDF_BUILDERS.get(sdf_type)
            if bld is None:
                FreeCAD.Console.PrintError(f"SDFMeshPrimitiveCreator: unknown sdf_type '{sdf_type}'\n")
                return

            FreeCAD.Console.PrintMessage("DEBUG: Building SDF function...\n")
            sdf_fn, (mn, mx) = bld(params)
            FreeCAD.Console.PrintMessage(f"DEBUG: Meshing SDF with bounds {mn} to {mx}...\n")
            verts, tris = mesh_sdf(sdf_fn, mn, mx, resolution=self._pending_resolution)
            FreeCAD.Console.PrintMessage(f"DEBUG: Mesh result: {len(verts)} verts, {len(tris)} tris\n")

            if len(verts) == 0:
                FreeCAD.Console.PrintMessage("DEBUG: Empty mesh result\n")
                return

            FreeCAD.Console.PrintMessage(f"DEBUG: Creating mesh facets for {len(tris)} triangles...\n")
            facets = _to_mesh_facets(verts, tris)
            mesh   = MeshModule.Mesh(facets)

            doc = FreeCAD.activeDocument()
            if not doc:
                return

            # Use a plain Mesh::Feature (NOT FeaturePython) for the preview.
            # A plain Feature has no proxy / no execute() cycle, so doc.recompute()
            # will NEVER wipe the .Mesh we assign here.  We just set .Mesh and
            # call updateGui() — the viewport refreshes immediately.
            if self._preview_obj is None or self._preview_obj not in doc.Objects:
                FreeCAD.Console.PrintMessage(f"DEBUG: Creating plain Mesh::Feature preview in {doc.Name}...\n")
                self._preview_obj = doc.addObject("Mesh::Feature", "SDF_Preview")
                if hasattr(self._preview_obj, "ViewObject") and self._preview_obj.ViewObject:
                    try:
                        self._preview_obj.ViewObject.ShapeColor = (0.20, 0.60, 0.85)
                        self._preview_obj.ViewObject.Transparency = 20
                        # Always use Flat Lines so mesh edges are visible during preview
                        self._preview_obj.ViewObject.DisplayMode = "Flat Lines"
                    except Exception:
                        pass
                FreeCAD.Console.PrintMessage("DEBUG: Preview object created\n")

            # Store current SDF info on the creator so finish() can access it.
            self._preview_sdf_type = sdf_type
            self._preview_sdf_params = params

            # Assign mesh directly — no recompute needed or wanted.
            FreeCAD.Console.PrintMessage("DEBUG: Assigning .Mesh to plain Feature...\n")
            self._preview_obj.Mesh = mesh

            # A plain updateGui() is sufficient to repaint. No recompute needed.
            FreeCADGui.updateGui()
            FreeCAD.Console.PrintMessage("DEBUG: .Mesh assigned and viewport updated\n")

        except Exception as e:
            FreeCAD.Console.PrintError(f"SDF Preview Update Failed: {e}\n")


    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def finish(self):
        """Schedule the finalization to happen safely outside the event loop."""
        sdf_logger.debug("DEBUG: Deferring finish() to Qt event loop...")
        QtCore.QTimer.singleShot(0, self._do_finish)

    def _do_finish(self):
        """Standard finalization for all SDF primitives."""
        if self._finished:
            return
            
        sdf_logger.debug(f"SDFMeshPrimitiveCreator: Finishing {self._last_sdf_type}...")
        try:
            FreeCAD.Console.PrintMessage(f"DEBUG: _do_finish: type={self._last_sdf_type} params={self._last_sdf_params}\n")
            FreeCAD.Console.PrintMessage(f"DEBUG: _do_finish: placement={self._last_placement}\n")
            if self._last_sdf_type and self._last_sdf_params:
                from ..sdf_object import create_sdf_object
                # Create the final high-res SDFObject
                create_sdf_object(
                    self._last_sdf_type.capitalize(), 
                    self._last_sdf_type, 
                    self._last_sdf_params,
                    placement=self._last_placement
                )
        except Exception as e:
            FreeCAD.Console.PrintError(f"Finalization Error: {e}\n")

        self._finished = True
        self.terminate()

        # Close task panel if open
        try:
            import FreeCADGui
            FreeCADGui.Control.closeDialog()
        except Exception:
            pass

    def terminate(self):
        """Remove preview object and unregister the event callback."""
        if self._preview_obj is not None:
            try:
                doc = FreeCAD.activeDocument()
                if doc and self._preview_obj in doc.Objects:
                    doc.removeObject(self._preview_obj.Name)
                    doc.recompute()
            except Exception as e:
                sdf_logger.debug(f"Error removing preview object: {e}")
            self._preview_obj = None
        super().terminate()
