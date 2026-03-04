import FreeCAD
import FreeCADGui
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
                elif event.key() == QtCore.Qt.Key_D:
                    # If we have an active tool, let it handle the menu first
                    if DMBase.active_tool:
                        items = []
                        if hasattr(DMBase.active_tool, 'get_context_menu'):
                            items = DMBase.active_tool.get_context_menu()
                        elif hasattr(DMBase.active_tool, 'on_tool_menu'):
                            # Legacy fallback
                            if DMBase.active_tool.on_tool_menu():
                                return True
                        
                        if items:
                            menu = QtGui.QMenu()
                            self._build_dynamic_menu(menu, items)
                            menu.exec_(QtGui.QCursor.pos())
                            return True
                    
                    self.show_context_menu()
                    return True
            elif event.type() == QtCore.QEvent.KeyRelease:
                if event.key() == QtCore.Qt.Key_Shift:
                    self._shift_down = False
                    
            if event.type() in [QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease]:
                if event.button() == QtCore.Qt.MiddleButton:
                    self._middle_mouse_down = (event.type() == QtCore.QEvent.MouseButtonPress)

            # Suppress FreeCAD context menu globally in the workbench ONLY if tool active
            if event.type() == QtCore.QEvent.ContextMenu:
                if DMBase.active_tool:
                    dm_logger.debug("DMInputManager: Suppressing ContextMenu")
                    return True
                return False

            # Handle Right-Click
            if event.type() in [QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease]:
                if event.button() == QtCore.Qt.RightButton:
                    # Ignore if navigating (FreeCAD native Rotate)
                    if self._middle_mouse_down or self._shift_down:
                        return False # Let FreeCAD handle it
                        
                    if DMBase.active_tool:
                        # Right click accepts (finishes) the tool
                        if event.type() == QtCore.QEvent.MouseButtonPress:
                            dm_logger.debug("DMInputManager: Right-click finish tool")
                            DMBase.active_tool.finish()
                        return True # Block FreeCAD
                        
                    # If no tool is active, behave normally
                    return False
        except Exception as e:
            dm_logger.error(f"DMInputManager eventFilter error: {e}")

        return False

    def _build_dynamic_menu(self, menu, items):
        for item in items:
            if item == "-":
                menu.addSeparator()
            elif isinstance(item, tuple):
                name, action = item
                if isinstance(action, list):
                    submenu = menu.addMenu(name)
                    self._build_dynamic_menu(submenu, action)
                else:
                    menu.addAction(name, action)

    def show_context_menu(self):
        try:
            menu = QtGui.QMenu()
            
            def add_action(name, command_name):
                action = menu.addAction(name)
                action.triggered.connect(lambda checked=False, cmd=command_name: FreeCADGui.runCommand(cmd))
            
            add_action("WorkPlane", 'DM_WorkPlane')
            menu.addSeparator()
            add_action("Create Point", 'DM_CreatePoint')
            add_action("Create Curve", 'DM_CreateCurve')
            add_action("Fill Curve", 'DM_FillCurve')
            menu.addSeparator()
            add_action("Edit Object", 'DM_EditObject')
            add_action("Translate", 'DM_Translate')
            add_action("Open Sketcher", 'DM_OpenSketcher')
            menu.addSeparator()
            add_action("Fuse", 'DM_Fuse')
            add_action("Cut", 'DM_Cut')
            add_action("Common", 'DM_Common')
            menu.addSeparator()
            add_action("Settings", 'DM_Settings')
            
            menu.exec_(QtGui.QCursor.pos())
        except Exception as e:
            dm_logger.error(f"Error showing context menu: {e}")

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
