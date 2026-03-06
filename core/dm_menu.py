import FreeCADGui
from PySide import QtCore, QtGui
from core import dm_logger

class DMMenuManager:
    """Handles dynamic context menus and UI interactions."""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = DMMenuManager()
        return cls._instance

    def __init__(self):
        self._menu_open = False
        self._active_menu = None
        self._ignore_hotkeys = False

    def is_menu_active(self):
        return self._menu_open

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
        # Briefly ignore hotkeys after menu closes to prevent immediate re-trigger
        self._ignore_hotkeys = True
        QtCore.QTimer.singleShot(150, lambda: setattr(self, '_ignore_hotkeys', False))

    def trigger_dynamic_menu(self, items):
        if not items or self._menu_open:
            return False
            
        try:
            self._active_menu = QtGui.QMenu()
            self._build_dynamic_menu(self._active_menu, items)
            self._active_menu.aboutToHide.connect(self._on_menu_hide)
            self._menu_open = True
            
            # Use singleShot to exec so we don't block the caller immediately
            QtCore.QTimer.singleShot(0, lambda: self._active_menu.exec_(QtGui.QCursor.pos()))
            return True
        except Exception as e:
            dm_logger.error(f"Error triggering dynamic menu: {e}")
            self._menu_open = False
            return False

    def show_context_menu(self):
        """Shows the default Direct Modeling context menu."""
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
            self.trigger_dynamic_menu(items)
        except Exception as e:
            dm_logger.error(f"Error showing context menu: {e}")
