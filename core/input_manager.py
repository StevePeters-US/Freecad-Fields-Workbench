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
        self._menu_open = False

    def _is_menu_active(self):
        return getattr(self, '_menu_open', False)

    def eventFilter(self, obj, event):
        try:
            # Drop auto-repeat events to prevent multiple menus
            if getattr(event, "isAutoRepeat", lambda: False)():
                if event.type() in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease, QtCore.QEvent.ShortcutOverride):
                    return True

            # 1. ALWAYS consume ShortcutOverride for our hotkeys so FreeCAD doesn't steal them.
            if event.type() == QtCore.QEvent.ShortcutOverride:
                key = event.key()
                text = event.text().lower() if hasattr(event, "text") else ""
                if key in (QtCore.Qt.Key_S, QtCore.Qt.Key_D) or text in ('s', 'd'):
                    event.accept()
                    return True

            # 2. Allow active menu to be toggled closed strictly on KeyPress
            if self._is_menu_active():
                if event.type() == QtCore.QEvent.KeyPress:
                    key = event.key()
                    text = event.text().lower() if hasattr(event, "text") else ""
                    if key in (QtCore.Qt.Key_S, QtCore.Qt.Key_D) or text in ('s', 'd'):
                        if getattr(self, '_ignore_hotkeys', False):
                            return True
                        try:
                            if hasattr(self, '_active_menu') and self._active_menu:
                                self._active_menu.close()
                        except Exception:
                            pass
                        return True
                return False

            # Track modifier keys and middle mouse for navigation visibility
            if event.type() == QtCore.QEvent.KeyPress:
                if event.key() == QtCore.Qt.Key_Shift:
                    self._shift_down = True
                elif event.key() == QtCore.Qt.Key_Control:
                    if DMBase.active_tool and hasattr(DMBase.active_tool, 'toggle_snapping'):
                        DMBase.active_tool.toggle_snapping()
                        return True
            elif event.type() == QtCore.QEvent.KeyRelease:
                if event.key() == QtCore.Qt.Key_Shift:
                    self._shift_down = False

            # 3. Handle opening our menus strictly on KeyPress
            if event.type() == QtCore.QEvent.KeyPress:
                key = event.key()
                text = event.text().lower() if hasattr(event, "text") else ""
                
                if key in (QtCore.Qt.Key_S, QtCore.Qt.Key_D) or text in ('s', 'd'):
                    if getattr(self, '_ignore_hotkeys', False):
                        return True
                        
                    if key == QtCore.Qt.Key_S or text == 's':
                        if DMBase.active_tool and hasattr(DMBase.active_tool, 'get_snapping_menu'):
                            if self._trigger_dynamic_menu(DMBase.active_tool.get_snapping_menu()):
                                return True
                        return False
                        
                    if key == QtCore.Qt.Key_D or text == 'd':
                        if DMBase.active_tool:
                            items = []
                            if hasattr(DMBase.active_tool, 'get_context_menu'):
                                items = DMBase.active_tool.get_context_menu()
                            elif hasattr(DMBase.active_tool, 'on_tool_menu'):
                                if DMBase.active_tool.on_tool_menu():
                                    return True
                            
                            if items and self._trigger_dynamic_menu(items):
                                return True
                        
                        self.show_context_menu()
                        return True
                    
            if event.type() in [QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease]:
                is_press = (event.type() == QtCore.QEvent.MouseButtonPress)
                if event.button() == QtCore.Qt.MiddleButton:
                    self._middle_mouse_down = is_press

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
                if len(item) == 2:
                    name, action = item
                    if isinstance(action, list):
                        submenu = menu.addMenu(name)
                        self._build_dynamic_menu(submenu, action)
                    else:
                        menu.addAction(name, action)
                elif len(item) == 3:
                    name, action, is_checked = item
                    act = menu.addAction(name)
                    act.setCheckable(True)
                    act.setChecked(is_checked)
                    act.toggled.connect(action)


    def _on_menu_hide(self):
        self._menu_open = False
        self._active_menu = None
        
        self._ignore_hotkeys = True
        QtCore.QTimer.singleShot(150, lambda: setattr(self, '_ignore_hotkeys', False))

    def _trigger_dynamic_menu(self, items):
        if not items:
            return False
            
        try:
            if getattr(self, '_menu_open', False):
                return False
                
            self._active_menu = QtGui.QMenu()
            self._build_dynamic_menu(self._active_menu, items)
            self._active_menu.aboutToHide.connect(self._on_menu_hide)
            self._menu_open = True
            
            self._ignore_hotkeys = True
            QtCore.QTimer.singleShot(150, lambda: setattr(self, '_ignore_hotkeys', False))
            
            QtCore.QTimer.singleShot(0, lambda: self._active_menu.exec_(QtGui.QCursor.pos()))
            return True
        except Exception as e:
            dm_logger.error(f"Error triggering dynamic menu: {e}")
            self._menu_open = False
            return False

    def show_context_menu(self):
        try:
            def cmd(c):
                return lambda checked=False, command=c: FreeCADGui.runCommand(command)
                
            items = [
                ("WorkPlane", cmd('DM_WorkPlane')),
                "-",
                ("Create Point", cmd('DM_CreatePoint')),
                ("Create Curve", cmd('DM_CreateCurve')),
                ("Fill Curve", cmd('DM_FillCurve')),
                "-",
                ("Edit Object", cmd('DM_EditObject')),
                ("Translate", cmd('DM_Translate')),
                ("Open Sketcher", cmd('DM_OpenSketcher')),
                "-",
                ("Fuse", cmd('DM_Fuse')),
                ("Cut", cmd('DM_Cut')),
                ("Common", cmd('DM_Common')),
                "-",
                ("Settings", cmd('DM_Settings'))
            ]
            self._trigger_dynamic_menu(items)
        except Exception as e:
            dm_logger.error(f"Error showing context menu: {e}")

    def initialize(self):
        try:
            if not getattr(self, "_is_initialized", False):
                dm_logger.debug("DMInputManager: Installing event filter...")
                QtGui.QApplication.instance().installEventFilter(self)
                self._is_initialized = True
        except Exception as e:
            dm_logger.error(f"DMInputManager initialization error: {e}")

    def restore(self):
        try:
            if getattr(self, "_is_initialized", False):
                dm_logger.debug("DMInputManager: Removing event filter...")
                QtGui.QApplication.instance().removeEventFilter(self)
                self._is_initialized = False
        except Exception as e:
            dm_logger.error(f"DMInputManager restore error: {e}")
