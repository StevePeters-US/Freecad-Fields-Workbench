"""
dm_commands/command_open_sketcher.py

Provides a toolbar button that switches to the Sketcher workbench, optionally
creating a new sketch on the active face or on the XY plane if nothing is selected.
"""

import FreeCAD
import FreeCADGui
from core import dm_logger
from PySide import QtCore


class SketchWatcher:
    """Monitors a sketch edit session and returns to the DM workbench when finished."""
    def __init__(self, sketch_name, workbench_to_return):
        self.sketch_name = sketch_name
        self.workbench_to_return = workbench_to_return
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.check_edit)
        self.timer.start(1000) # Poll every second
        dm_logger.debug(f"SketchWatcher: Started for {sketch_name}")

    def check_edit(self):
        try:
            # Check if document still exists
            doc = FreeCAD.ActiveDocument
            if not doc:
                self.stop()
                return
                
            # Check if object still exists
            obj = doc.getObject(self.sketch_name)
            if not obj:
                self.stop()
                return

            # Check if we are still in edit mode
            gui_doc = FreeCADGui.ActiveDocument
            if not gui_doc:
                self.stop()
                return
                
            edit_obj = gui_doc.getInEdit()
            
            # If no object is in edit mode, or a DIFFERENT object is in edit mode,
            # we assume the Sketcher session for OUR sketch is over.
            if not edit_obj or edit_obj.Object.Name != self.sketch_name:
                dm_logger.info(f"SketchWatcher: Sketch {self.sketch_name} edit finished. Returning to {self.workbench_to_return}")
                FreeCADGui.activateWorkbench(self.workbench_to_return)
                self.stop()
        except Exception as e:
            dm_logger.debug(f"SketchWatcher error: {e}")
            self.stop()

    def stop(self):
        if self.timer:
            self.timer.stop()
            self.timer = None
        # Clean up reference from command class
        if self in DMOpenSketcherCommand.watchers:
            DMOpenSketcherCommand.watchers.remove(self)


class DMOpenSketcherCommand:
    """Switch to the Sketcher workbench and optionally start a new sketch."""
    watchers = []

    def GetResources(self):
        return {
            "Pixmap":  "SketcherWorkbench",
            "MenuText": "Open Sketcher",
            "ToolTip":  "Switch to Sketcher workbench to draw profiles for SDF extrusions",
            "Accel":    "S, K",
        }

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return
            
        view = FreeCADGui.activeView()
        if not view:
            return

        dm_logger.info("DM_OpenSketcher: Detecting placement...")
        
        placement = FreeCAD.Placement()
        target_wp = None
        
        # 1. Detect selected DMWorkPlane
        sel = FreeCADGui.Selection.getSelection()
        for obj in sel:
            # Check for name specifically as class comparison might fail across reloads
            if hasattr(obj, "Proxy") and obj.Proxy.__class__.__name__ == "DMWorkPlane":
                target_wp = obj
                break
        
        if target_wp:
            dm_logger.info(f"DM_OpenSketcher: Using selected workplane: {target_wp.Label}")
            placement = target_wp.Placement
        else:
            # 2. Align to viewport
            dm_logger.info("DM_OpenSketcher: No workplane selected, aligning to viewport.")
            try:
                # View direction is camera -> scene
                vd = view.getViewDirection()
                ud = view.getUpDirection()
                
                # Sketch Z faces camera
                z_axis = -vd
                z_axis.normalize()
                
                # Sketch Y is viewport UP
                y_axis = ud
                y_axis.normalize()
                
                # Sketch X completes the set
                x_axis = y_axis.cross(z_axis)
                x_axis.normalize()
                
                # Re-cross Y to ensure perfect orthogonality
                y_axis = z_axis.cross(x_axis)
                
                m = FreeCAD.Matrix(
                    x_axis.x, y_axis.x, z_axis.x, 0.0,
                    x_axis.y, y_axis.y, z_axis.y, 0.0,
                    x_axis.z, y_axis.z, z_axis.z, 0.0,
                    0.0,      0.0,      0.0,      1.0
                )
                
                # Use current focus point (or view.getPoint under mouse if we wanted to be fancy)
                sz = view.getSize()
                w = sz.width() if hasattr(sz, "width") else sz[0]
                h = sz.height() if hasattr(sz, "height") else sz[1]
                target_pt = view.getPoint(w // 2, h // 2)
                if not target_pt:
                    target_pt = view.getFocus()
                
                placement = FreeCAD.Placement(target_pt, FreeCAD.Rotation(m))
            except Exception as e:
                dm_logger.debug(f"DM_OpenSketcher: Viewport alignment fallback failed: {e}")
                placement = FreeCAD.Placement()

        # 3. Create Sketch
        try:
            sketch = doc.addObject("Sketcher::SketchObject", "Sketch")
            sketch.Placement = placement
            doc.recompute()
            
            # Switch workbench
            FreeCADGui.activateWorkbench("SketcherWorkbench")
            
            # Open for editing
            # Note: ActiveDocument here is FreeCADGui.ActiveDocument
            FreeCADGui.ActiveDocument.setEdit(sketch.Name)
            
            # Start watcher to return to DM workbench
            watcher = SketchWatcher(sketch.Name, "DirectModelingWorkbench")
            DMOpenSketcherCommand.watchers.append(watcher)
            
            dm_logger.info(f"DM_OpenSketcher: Created sketch '{sketch.Label}' at {placement.Base}")
            
        except Exception as e:
            FreeCAD.Console.PrintError(f"DM_OpenSketcher: Failed to create/open Sketcher: {e}\n")
            dm_logger.exception("DM_OpenSketcher failure")


FreeCADGui.addCommand("DM_OpenSketcher", DMOpenSketcherCommand())
