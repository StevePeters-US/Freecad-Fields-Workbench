from core import dm_logger


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
