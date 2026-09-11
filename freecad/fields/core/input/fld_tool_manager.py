# SPDX-License-Identifier: CC-BY-NC-SA-4.0
from freecad.fields.core import fld_logger


class FldSelectionManager:
    """Handles SDF object selection on left-click when no tool is active."""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = FldSelectionManager()
        return cls._instance

    def __init__(self):
        self._last_selection_time = 0.0

    def try_sdf_selection(self, qt_pos):
        """Attempt to select an SDF object at the given Qt screen position.

        Args:
            qt_pos: tuple (x, y) in Qt coordinates (Y=0 at top)

        Returns:
            True if an SDF object was selected, False otherwise.
        """
        import time
        now = time.monotonic()
        if now - self._last_selection_time < 0.1:  # 100ms debounce
            return False
        self._last_selection_time = now

        try:
            import FreeCAD, FreeCADGui
            view = FreeCADGui.ActiveDocument.ActiveView if FreeCADGui.ActiveDocument else None
            if not view:
                return False

            doc = getattr(FreeCAD, "ActiveDocument", None)
            sdf_obj = None

            # ONE picker, chosen by whether the g-buffer exists -- not two stacked.
            # The surface-id readback IS the pick when the voxel renderer is drawing:
            # a `None` from it means the click missed, and re-running the CPU sphere
            # trace to disagree with the pixel the user actually clicked would both
            # cost a full march per click and reintroduce the older, coarser answer
            # this task replaced. The CPU projector remains the picker only where
            # there is no g-buffer to read (no renderer, no live GL context).
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
            from freecad.fields.core.sdf.gpu_field_eval import has_active_context
            renderer = FldSceneVoxelRenderer.get_instance()
            use_gpu_pick = (doc is not None and renderer is not None
                            and has_active_context()
                            and getattr(getattr(renderer, "_gbuf_fbo", None), "id", 0))

            if use_gpu_pick:
                # qt_pos is LOGICAL Qt pixels; the g-buffer pick_surface_at reads
                # is allocated at the NATIVE device resolution, and compares
                # against renderer._vp_size, which is native. On any display with
                # fractional scaling those spaces differ -- measured 909x446
                # logical against a 1268x622 g-buffer (ratio 1.396) -- so passing
                # the logical coordinate straight through reads the wrong texel
                # and the pick misses or selects the wrong body. This is the same
                # conversion FldInputManager.get_gl_pos already does for the CPU
                # picker; the y-flip stays inside pick_surface_at, which owns it.
                from freecad.fields.core.input.input_manager import FldInputManager
                ratio = FldInputManager.get_instance().device_pixel_ratio()
                sid = renderer.pick_surface_at(int(round(qt_pos[0] * ratio)),
                                               int(round(qt_pos[1] * ratio)))
                if sid is not None and sid != 65535:
                    from freecad.fields.core.objects.fld_surface_id import find_object_by_surface_id
                    sdf_obj = find_object_by_surface_id(doc, sid)
            else:
                from freecad.fields.core.input.view_projector import ViewProjector
                proj = ViewProjector(view)
                sdf_result = proj.get_sdf_hit({"Position": qt_pos})
                if sdf_result:
                    _, _, sdf_obj = sdf_result

            if sdf_obj:
                from freecad.fields.core.input.input_manager import FldInputManager
                input_mgr = FldInputManager.get_instance()
                is_multi = input_mgr.is_shift_down() or input_mgr.is_ctrl_down()

                if is_multi:
                    is_already_selected = False
                    for sel_obj in FreeCADGui.Selection.getSelection():
                        if sel_obj.Name == sdf_obj.Name and sel_obj.Document.Name == sdf_obj.Document.Name:
                            is_already_selected = True
                            break
                    if is_already_selected:
                        FreeCADGui.Selection.removeSelection(sdf_obj)
                    else:
                        FreeCADGui.Selection.addSelection(sdf_obj)
                else:
                    FreeCADGui.Selection.clearSelection()
                    FreeCADGui.Selection.addSelection(sdf_obj)
                return True
        except Exception as e:
            fld_logger.debug(f"SDF selection failed: {e}")
        return False


class FldSelectionObserver:
    """Blocks FreeCAD object selection while a Fields tool is active and handles redraws."""

    def addSelection(self, docName, objName, subName, pnt):
        if FldToolManager.get_instance().has_active_tool():
            try:
                import FreeCAD, FreeCADGui
                doc = FreeCAD.getDocument(docName)
                if doc:
                    obj = doc.getObject(objName)
                    if obj:
                        FreeCADGui.Selection.removeSelection(obj)
            except Exception as e:
                fld_logger.warn(f"FldSelectionObserver.addSelection: selection removal failed: {e}")
        else:
            self._request_redraw()

    def removeSelection(self, docName, objName, subName):
        if not FldToolManager.get_instance().has_active_tool():
            self._request_redraw()

    def clearSelection(self, docName):
        if not FldToolManager.get_instance().has_active_tool():
            self._request_redraw()

    def _request_redraw(self):
        try:
            import FreeCADGui
            view = FreeCADGui.ActiveDocument.ActiveView
            if view:
                view.redraw()
        except Exception as e:
            fld_logger.debug(f"FldSelectionObserver._request_redraw: redraw failed: {e}")


class FldToolManager:
    """Singleton tracking the currently active Fields tool.

    Lives in core/ so input_manager.py and other core modules can import it
    without creating a circular dependency on tools/.
    """
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = FldToolManager()
        return cls._instance

    def __init__(self):
        self._active_tool = None

    def set_active_tool(self, tool):
        self._active_tool = tool

    def get_active_tool(self):
        return self._active_tool

    def has_active_tool(self):
        return self._active_tool is not None

    # Attributes a tool may hold its working object in, in the order a command
    # should prefer them: the modifier tools use _target_obj, FldBase creators
    # _active_obj, the work plane tool _editing_obj.
    _TARGET_ATTRS = ("_target_obj", "_active_obj", "_editing_obj")

    def get_active_tool_object(self):
        """The document object the active tool is working on, or None."""
        tool = self._active_tool
        if tool is None:
            return None
        for attr in self._TARGET_ATTRS:
            obj = getattr(tool, attr, None)
            if obj is not None:
                return obj
        return None


def resolve_command_target():
    """The object a selection-driven command should act on.

    The current selection when there is one, otherwise whatever the active tool is
    editing. The fallback is not a convenience: FreeCADGui.Selection is *always*
    empty while a Fields tool runs, because FldSelectionObserver.addSelection removes
    every selection -- programmatic ones included -- for as long as
    has_active_tool() is true. A command invoked from inside an edit session
    therefore sees nothing selected and tells the user to select something, with
    the object in question sitting right there under the tool.
    """
    import FreeCADGui
    sel = FreeCADGui.Selection.getSelection()
    if sel:
        return sel[0]
    return FldToolManager.get_instance().get_active_tool_object()
