# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/objects/fld_view_provider.py

FldViewProvider — the Gui-level ViewProvider for Fields document
objects: Coin3D display modes, handle/overlay rendering, viewport event
handling. See core/objects/fld_object_proxy.py for the paired App-level
FldObjectProxy.
"""

import FreeCAD
import FreeCADGui
try:
    from pivy import coin
except ImportError:
    coin = None

from freecad.fields.core import fld_logger
from freecad.fields.core.objects.fld_object import apply_near_clip_override, get_line_width, get_point_size
from freecad.fields.core.render.field_appearance import DEFAULT_ADDITIVE_COLOR


class FldViewProvider:
    """ViewProvider for Fields objects. Shows an orange part icon."""
    def __init__(self, vobj):
        # setup_view MUST run before vobj.Proxy = self.
        # Assigning vobj.Proxy triggers attach() synchronously in FreeCAD,
        # which creates the FldRenderer. If setup_view ran after, it would
        # overwrite renderer=None and destroy the renderer reference.
        self.setup_view(vobj)
        vobj.Proxy = self

    def setup_view(self, vobj):
        vobj.PointColor = DEFAULT_ADDITIVE_COLOR
        vobj.LineColor = DEFAULT_ADDITIVE_COLOR
        vobj.LineWidth = get_line_width()
        vobj.PointSize = get_point_size()
        
        shape_type = getattr(vobj.Object, "ShapeType", None)
        if shape_type == "point":
            vobj.PointSize = 0.0
            vobj.LineWidth = 0.0
        elif shape_type == "surface":
            vobj.PointSize = 0.0
            vobj.LineWidth = 0.0
            # Same reason as "sdf" below: this runs before attach() or any recompute, so
            # Shape is empty and "Shaded" is not in the enumeration yet. The attempt could
            # only ever fail, and it logged
            #   'Shaded' is not part of the enumeration in Fields#FldSurface…
            # on every fill-curve, which reads like a fault. create_fld_object() sets it for
            # "surface" as well as "sdf" once the recompute has produced a mesh.
        elif shape_type == "sdf":
            vobj.PointSize = 0.0
            vobj.LineWidth = 0.0
            # No attempt to set DisplayMode here: this runs before attach() or
            # any recompute, so Shape is still empty and "Shaded" isn't yet a
            # valid mode. create_fld_object() retries this after recompute,
            # once Shape may have a real preview mesh.
        else:
            # Same timing problem as the two branches above, but curves DO want a
            # display mode and nothing downstream sets one for them: create_fld_object
            # only retries for shape_type in ("sdf", "surface") (fld_object.py:116).
            # So the assignment stays and is guarded instead of dropped -- it becomes a
            # no-op while Shape is empty, rather than logging a failure on every curve,
            # and it still takes effect if the enumeration is already populated.
            try:
                modes = vobj.getEnumerationsOfProperty("DisplayMode") or []
                if "Flat Lines" in modes:
                    vobj.DisplayMode = "Flat Lines"
            except Exception as e:
                from freecad.fields.core import fld_logger
                fld_logger.debug(f"Failed to set DisplayMode to Flat Lines: {e}")

        # FldRenderer handles all Coin3D overlays (meshes, handles, etc)
        self.renderer = None
        self._strategy = None

    def attach(self, vobj):
        from freecad.fields.core import fld_logger
        self.Object = vobj.Object

        # The one place SelectionColor comes into existence. attach() runs on
        # creation AND on document restore, so every Fields object has the property
        # -- including one saved before it existed -- and FieldAppearance can
        # read it straight, with no default of its own and no second source for
        # the colour.
        if not hasattr(vobj, "SelectionColor"):
            from freecad.fields.core.render.field_appearance import DEFAULT_SELECTION_COLOR
            vobj.addProperty("App::PropertyColor", "SelectionColor", "Fields",
                             "Colour of this object's selection outline")
            vobj.SelectionColor = DEFAULT_SELECTION_COLOR

        if coin:
            from freecad.fields.core.render.fld_renderer import FldRenderer, SdfRendererStrategy, NURBSRendererStrategy
            self.renderer = FldRenderer(vobj)

            st = getattr(self.Object, "ShapeType", None)
            self._strategy = SdfRendererStrategy() if st == "sdf" else NURBSRendererStrategy()
            self._strategy.setup(self.renderer, vobj)
            
            # Keep label for backward compatibility with other methods
            if hasattr(self._strategy, "label"):
                self._scene_rm_label = self._strategy.label

            if hasattr(self.renderer, "_setup_camera_sensor"):
                self.renderer._setup_camera_sensor()

    def on_prefs_changed(self):
        """Update Coin3D styles and visibility based on global preferences."""
        if self.renderer:
            self.renderer.on_prefs_changed(self.Object)
            
        if hasattr(self, "_scene_rm_label"):
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
            FldSceneVoxelRenderer.get_instance().on_prefs_changed()

        # Generic FreeCAD ViewObject properties
        try:
            vobj = self.Object.ViewObject
            lw = get_line_width() # Get current line width
            vobj.LineWidth = lw
            vobj.PointSize = get_point_size()
        except Exception as e:
            from freecad.fields.core import fld_logger
            fld_logger.debug(f"on_prefs_changed line width/point size update failed: {e}")

    def updateData(self, fp, prop):
        from freecad.fields.core import fld_logger
        
        if prop in ["ShowWireframe", "Group"]:
            self.on_prefs_changed()
        
        if prop == "DisplayMode":
            self._strategy.set_display_mode(self.renderer, fp.ViewObject.DisplayMode)
            
        self._strategy.update(self.renderer, fp, prop)
        
        if not prop:
            self.on_prefs_changed()
            # Also ensure visibility is correct
            vobj = fp.ViewObject
            if self.renderer:
                self.renderer.update_visibility(vobj.Visibility)

    def onChanged(self, vobj, prop):
        """Called when a property of the ViewObject changes (e.g. Visibility)."""
        if prop == "Visibility":
            if self.renderer:
                self.renderer.update_visibility(vobj.Visibility)
            if hasattr(self, "_scene_rm_label"):
                from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                sr = FldSceneVoxelRenderer.get_instance()
                sr.set_field_visible(self._scene_rm_label, vobj.Visibility)
        elif prop in ("ShapeAppearance", "SelectionColor"):
            # SelectionColor is ours and arrives under its own name. The body
            # colour does not: "ShapeAppearance", not "ShapeColor" --
            # FreeCAD 1.1 keeps ShapeColor and
            # ShapeMaterial as live aliases you can still read and assign, but the
            # change it NOTIFIES is always the underlying ShapeAppearance material
            # array -- setting ViewObject.ShapeColor produces exactly one
            # onChanged, with prop == "ShapeAppearance". Instrumenting the live
            # proxy on 2026-08-28 recorded that and nothing else, so a branch on
            # ("ShapeColor", "ShapeMaterial") never runs and the user's colour
            # edit never leaves the property editor.
            #
            # A redraw alone would render the colour captured at
            # compile_visible() time (sdf_field_registry.py:166), not the one just
            # edited. refresh_appearance() re-resolves the live scene fields and
            # redraws; it deliberately does not rebuild, because recompiling GLSL
            # for a colour change would cost a shader compile per editor tick.
            try:
                from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                FldSceneVoxelRenderer.get_instance().refresh_appearance()
            except Exception as e:
                from freecad.fields.core import fld_logger
                fld_logger.debug(f"onChanged {prop} refresh failed: {e}")

        # Re-apply near clip override (FreeCAD navigation resets camera params)
        apply_near_clip_override()

    def onDelete(self, vobj, subelements):
        """Called when the object is about to be deleted."""
        try:
            from freecad.fields.core import fld_logger
            obj = getattr(self, "Object", None)
            if obj is None and vobj is not None:
                obj = getattr(vobj, "Object", None)

            # The scene field is NOT released here. This hook only fires for a tree
            # delete, so it missed `doc.removeObject` (the modifier stack panel's
            # delete button) and document close entirely -- MS-010. Releasing it is
            # `_FldDocumentObserver` in fld_scene_voxel_renderer.py, which sees all
            # three paths; keeping a copy here would just be a second mechanism that
            # covers less.

            if self.renderer and hasattr(self.renderer, "_cleanup_camera_sensor"):
                self.renderer._cleanup_camera_sensor()

            # CN-001: Clean up control nodes if this object has ControlNodes

            if obj and hasattr(obj, "ControlNodes") and obj.ControlNodes:
                nodes_to_delete = []
                for node in obj.ControlNodes:
                    if node is None:
                        continue
                    in_list = getattr(node, "InList", []) or []
                    # Delete a node only when the curve being deleted is its last referrer
                    other_referrers = [ref for ref in in_list if ref != obj]
                    if not other_referrers:
                        node_name = getattr(node, "Name", None)
                        if node_name:
                            nodes_to_delete.append(node_name)

                # CN-002: sweep the orphans HERE, synchronously, so their removal
                # lands in the SAME undo transaction as the curve. This used to be a
                # QTimer.singleShot(0, ...), which fired after that transaction had
                # closed -- and `Document::removeObject` with no transaction active
                # `delete`s the object outright instead of handing ownership to the
                # undo stack. The closed transaction still held a copy of the curve
                # whose ControlNodes pointed at the freed node, so undoing the delete
                # replayed that link list over a dangling pointer and killed FreeCAD:
                #     App::PropertyLinkList::setValues
                #     App::PropertyLinkList::Paste
                #     App::TransactionObject::applyChn
                #     App::Transaction::apply
                #     App::Document::undo
                # Inside the transaction the node is owned by it, not freed, and one
                # undo restores curve and nodes together.
                if nodes_to_delete and hasattr(obj, "Document") and obj.Document:
                    d = obj.Document
                    for name in nodes_to_delete:
                        try:
                            if d.getObject(name) is not None:
                                d.removeObject(name)
                        except Exception as ex:
                            fld_logger.debug(f"FldViewProvider.onDelete: failed to remove node {name}: {ex}")
        except Exception as e:
            from freecad.fields.core import fld_logger
            fld_logger.debug(f"FldObject.onDelete error: {e}")
        return True

        

    def getIcon(self):
        # Orange stairstep icon (Part)
        return """
            /* XPM */
            static char * orange_part_xpm[] = {
            "16 16 3 1",
            " 	c None",
            ".	c #FFA500",
            "+	c #000000",
            "                ",
            "  ++++++        ",
            "  +....++       ",
            "  +.....+       ",
            "  +..+..+       ",
            "  +..+..+       ",
            "  +..++++++     ",
            "  +..+....++    ",
            "  +..+.....+    ",
            "  +..+..+..+    ",
            "  ++++..++++++  ",
            "     +..+....++ ",
            "     +..+.....+ ",
            "     +..+..+..+ ",
            "     ++++++++++ ",
            "                "};
            """

    def claimChildren(self):
        # self.Object is set in attach(), but the tree view can repaint between
        # `vobj.Proxy = self` in __init__ and attach() completing -- creating many
        # objects in a loop makes that window wide enough to hit. Reported as
        # AttributeError: 'FldViewProvider' object has no attribute 'Object'.
        # There are no children to claim before attach() anyway.
        obj = getattr(self, "Object", None)
        if obj is None:
            return []
        children = []
        if hasattr(obj, "ControlNodes") and obj.ControlNodes:
            children.extend([node for node in obj.ControlNodes if node is not None])
        if hasattr(obj, "BoundaryCurves") and obj.BoundaryCurves:
            children.extend([curve for curve in obj.BoundaryCurves if curve is not None])
        for child in obj.OutList:
            if child not in children:
                children.append(child)
        return children

    def __getstate__(self):
        return {}

    def __setstate__(self, state):
        pass

    def doubleClicked(self, vobj):
        import FreeCADGui
        from freecad.fields.core import fld_logger
        
        obj = vobj.Object
        
        # Ensure the object is selected
        sel = FreeCADGui.Selection.getSelection()
        if not sel or sel[0] != obj:
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(obj)
            
        fld_logger.info(f"FldViewProvider: double-clicked {obj.Label}")
        
        # One dispatch table, in tools/edit_tool.py. This used to be a second
        # copy that branched on the same ShapeType to the same tools but handled
        # a different subset -- 'point' only ever worked because the else branch
        # fell through to the router below.
        from freecad.fields.tools import edit_tool
        edit_tool.activate()
            
        # Return True to indicate the double-click was handled,
        # preventing FreeCAD from opening the default transform tool.
        return True

    def setEdit(self, vobj, mode=0):
        # Called when FreeCAD tries to put the object into Edit mode
        # We redirect this to our custom edit_tool instead of FreeCAD's default task panels
        self.doubleClicked(vobj)
        return True

    def unsetEdit(self, vobj, mode=0):
        # Called when leaving edit mode. We just return True.
        return True



# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

