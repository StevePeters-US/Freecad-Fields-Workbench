import FreeCAD
from PySide import QtCore, QtGui
from core import dm_logger
from tools.dm_base import DMBase

class DMInputManager(QtCore.QObject):
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = DMInputManager()
        return cls._instance

    def __init__(self):
        super().__init__()
        self._middle_mouse_down = False
        self._shift_down = False

    def eventFilter(self, obj, event):
        try:
            # Track modifier keys and middle mouse for navigation visibility
            if event.type() == QtCore.QEvent.KeyPress:
                if event.key() == QtCore.Qt.Key_Shift:
                    self._shift_down = True
            elif event.type() == QtCore.QEvent.KeyRelease:
                if event.key() == QtCore.Qt.Key_Shift:
                    self._shift_down = False
                    
            if event.type() in [QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease]:
                if event.button() == QtCore.Qt.MiddleButton:
                    self._middle_mouse_down = (event.type() == QtCore.QEvent.MouseButtonPress)

            # Suppress FreeCAD context menu globally in the workbench
            if event.type() == QtCore.QEvent.ContextMenu:
                dm_logger.debug("DMInputManager: Suppressing ContextMenu")
                return True

            # Handle Right-Click tool finishing, but NOT if navigating (Middle mouse or Shift down)
            if event.type() in [QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease]:
                if event.button() == QtCore.Qt.RightButton:
                    # Ignore if navigating (FreeCAD native Rotate)
                    if self._middle_mouse_down or self._shift_down:
                        return False # Let FreeCAD handle it
                        
                    if DMBase.active_tool:
                        # If just a normal right click, finish the tool
                        if event.type() == QtCore.QEvent.MouseButtonPress:
                            dm_logger.debug("DMInputManager: Right-click finish tool")
                            DMBase.active_tool.finish()
                        return True # Block FreeCAD from moving the camera or opening a menu
        except Exception as e:
            dm_logger.error(f"DMInputManager eventFilter error: {e}")

        return False

    def initialize(self):
        try:
            dm_logger.debug("DMInputManager: Installing event filter...")
            QtGui.QApplication.instance().installEventFilter(self)
        except Exception as e:
            dm_logger.error(f"DMInputManager initialization error: {e}")

    def restore(self):
        try:
            dm_logger.debug("DMInputManager: Removing event filter...")
            QtGui.QApplication.instance().removeEventFilter(self)
        except Exception as e:
            dm_logger.error(f"DMInputManager restore error: {e}")
