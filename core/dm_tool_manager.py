from core import dm_logger


class DMSelectionObserver:
    """Blocks FreeCAD object selection while a DM tool is active."""

    def addSelection(self, docName, objName, subName, pnt):
        if not DMToolManager.get_instance().has_active_tool():
            return
        try:
            import FreeCAD, FreeCADGui
            doc = FreeCAD.getDocument(docName)
            if doc:
                obj = doc.getObject(objName)
                if obj:
                    FreeCADGui.Selection.removeSelection(obj)
        except Exception:
            pass


class DMToolManager:
    """Singleton tracking the currently active DM tool.

    Lives in core/ so input_manager.py and other core modules can import it
    without creating a circular dependency on tools/.
    """
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = DMToolManager()
        return cls._instance

    def __init__(self):
        self._active_tool = None

    def set_active_tool(self, tool):
        self._active_tool = tool

    def get_active_tool(self):
        return self._active_tool

    def has_active_tool(self):
        return self._active_tool is not None
