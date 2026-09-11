# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui, QtWidgets
import math
from freecad.fields.core import fld_logger


def _is_view3d_gl_widget(widget):
    """True when `widget` is the GL surface belonging to a 3D view.

    Not every QOpenGLWidget in the window is a viewport -- FreeCAD 1.1 parents a
    small one straight to the main window -- and adopting the wrong one as "the
    viewport" would measure every tool click against it.
    """
    try:
        parent = widget.parent()
        for _ in range(4):
            if parent is None:
                return False
            cls = parent.metaObject().className() if hasattr(parent, "metaObject") else ""
            if "View3DInventor" in cls:
                return True
            parent = parent.parent()
    except Exception:
        return False
    return False


def _qt_alive(widget):
    """True if `widget`'s C++ side still exists.

    Closing a document destroys the 3D view widget while this manager -- a
    singleton holding a global event filter -- keeps the Python wrapper. Every
    later event then raised `RuntimeError: Internal C++ object (QWidget)
    already deleted` inside the ancestry and DPI blocks below, one DEBUG line
    each, ~150 per close. Asking first is the fix; catching it afterwards only
    made the noise quieter.

    `isWidgetType()` is the probe rather than `shiboken.isValid` because
    shiboken is a Qt-binding internal, not a declared dependency of this addon,
    and this runs on every event: it is a QObject method implemented in C++
    returning a plain bool, so it reaches the deleted object without allocating.
    """
    if widget is None:
        return False
    try:
        widget.isWidgetType()
    except RuntimeError:
        return False
    return True


def _resolve_edit_tool_for(obj):
    """Return a zero-arg callable that opens the right edit tool for `obj`, or
    None if nothing edits this object type.

    The one dispatch shared by the double-click, Tab and 'E' handlers (CR-033).
    Read all three before merging: they had independently drifted to cover
    different subsets --  only the Tab handler had a `"surface"` branch
    (`SdfFaceEditTool`), so double-click and 'E' silently did nothing for a
    selected surface patch; only the double-click and 'E' handlers checked
    `is_fld_workplane` before falling into the SDF-proxy branches, so Tab could
    never reopen a workplane. This is the union of what the three already had
    between them, not a new policy -- picking any one trigger's narrower
    behavior as "the" definition would have silently taken a working case away
    from the other two.
    """
    from freecad.fields.core.objects.fld_object_proxy import FldObjectProxy
    from freecad.fields.core.objects.fld_modifier_proxy_base import FldModifierProxyBase

    proxy = getattr(obj, "Proxy", None)
    if getattr(proxy, "is_fld_workplane", False):
        def _open():
            from freecad.fields.tools.work_plane_tool import WorkPlaneCreator
            WorkPlaneCreator()
        return _open

    if isinstance(proxy, FldObjectProxy):
        st = getattr(obj, "ShapeType", "")
        if st in ("curve", "point"):
            def _open():
                from freecad.fields.tools.nurbs_edit_tool import NurbsEditTool
                NurbsEditTool().activate()
            return _open
        elif st == "surface":
            def _open():
                from freecad.fields.tools.sdf_face_tool import SdfFaceEditTool
                SdfFaceEditTool().edit_object(obj)
            return _open
        elif st == "sdf":
            def _open():
                from freecad.fields.tools.sdf_edit_tool import SdfEditTool
                SdfEditTool().activate()
            return _open
    elif isinstance(proxy, FldModifierProxyBase):
        def _open():
            from freecad.fields.tools.sdf_edit_tool import SdfEditTool
            SdfEditTool().activate()
        return _open

    return None


class FldInputManager(QtCore.QObject):
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = FldInputManager()
        return cls._instance


    def __init__(self):
        super().__init__()
        self._left_mouse_down = False
        self._middle_mouse_down = False
        self._right_mouse_down = False
        self._shift_down = False
        self._control_down = False
        self._last_qt_pos = (0, 0)
        self._device_pixel_ratio = 1.0
        self._is_initialized = False
        self._sel_observer = None
        self._sdf_selected_on_press = False
        self._cached_viewport = None

    def _is_menu_active(self):
        from freecad.fields.core.input.fld_menu import FldMenuManager
        return FldMenuManager.get_instance().is_menu_active()

    def eventFilter(self, obj, event):
        from freecad.fields.core.input.fld_tool_manager import FldToolManager
        try:
            # Drop auto-repeat events to prevent multiple menus
            if getattr(event, "isAutoRepeat", lambda: False)():
                if event.type() in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease, QtCore.QEvent.ShortcutOverride):
                    return True

            # [Event Owner: Qt Event Filter] Coordinate tracking & Viewport detection
            # We must be extremely careful here. eventFilter is called on EVERY event.
            # Avoid expensive FreeCADGui calls on mouse move.
            
            # Key events are always processed for state tracking
            is_key_event = event.type() in [QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease, QtCore.QEvent.ShortcutOverride]
            is_mouse_event = event.type() in [QtCore.QEvent.MouseMove, QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease, QtCore.QEvent.MouseButtonDblClick]
            is_context_event = event.type() == QtCore.QEvent.ContextMenu

            if not is_key_event and not is_mouse_event and not is_context_event:
                return False

            # [Event Owner: Qt Event Filter] Modifier & Button state tracking (GLOBAL)
            # We track these BEFORE any 'return False' to ensure drag/modifier state is always correct,
            # even if the mouse leaves the viewport or the press started on a decoration.
            if is_key_event:
                if event.key() == QtCore.Qt.Key_Shift:
                    self._shift_down = (event.type() == QtCore.QEvent.KeyPress)
                elif event.key() == QtCore.Qt.Key_Control:
                    self._control_down = (event.type() == QtCore.QEvent.KeyPress)
                # Ensure we refresh the viewport cache if context might have changed
                self._cached_viewport = None

            if event.type() in [QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease]:
                is_press = (event.type() == QtCore.QEvent.MouseButtonPress)
                if event.button() == QtCore.Qt.LeftButton:
                    self._left_mouse_down = is_press
                elif event.button() == QtCore.Qt.MiddleButton:
                    self._middle_mouse_down = is_press
                elif event.button() == QtCore.Qt.RightButton:
                    self._right_mouse_down = is_press

            # If the user is currently typing in an input widget (QLineEdit, QTextEdit, etc.),
            # do not intercept key events or overrides so they reach the widget cleanly.
            if is_key_event or event.type() == QtCore.QEvent.ShortcutOverride:
                focus_w = QtWidgets.QApplication.focusWidget()
                if isinstance(focus_w, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit, QtWidgets.QAbstractSpinBox)) or \
                   isinstance(obj, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit, QtWidgets.QAbstractSpinBox)):
                    return False
                
            # --- Viewport Detection ---
            # We cache the viewport widget to avoid expensive FreeCADGui calls.
            # The cache outlives the widget: closing a document deletes the view
            # while this singleton keeps the wrapper, so check before trusting it.
            if self._cached_viewport is not None and not _qt_alive(self._cached_viewport):
                self._cached_viewport = None
            if self._cached_viewport is None:
                try:
                    # Method 1: Active View
                    av = FreeCADGui.activeView()
                    if av and hasattr(av, "getWidget"):
                        # Get the main view widget
                        self._cached_viewport = av.getWidget()
                except Exception as e:
                    fld_logger.debug(f"InputManager.eventFilter: activeView viewport detection failed: {e}")

            # Method 2: adopt the GL widget the events are actually arriving from --
            # on EVERY event, not only while the cache is empty. FreeCAD is
            # multi-view, and this used to run only inside the `is None` branch
            # above, under a condition ("View3D" in the cached widget's class name)
            # that can never hold once a QOpenGLWidget has been cached. So the
            # first 3D view to deliver an event owned the cache for the rest of the
            # session: open a second document and every click in its view was
            # measured against the first view's widget, mapFromGlobal handing the
            # tools coordinates in the wrong view's space (negative, off-widget) --
            # so tool clicks did nothing at all. Measured live 2026-09-07: pointer
            # at the centre of a 738x370 view arrived as (-97, 442).
            try:
                obj_cls = obj.metaObject().className() if hasattr(obj, "metaObject") else ""
                if ("OpenGL" in obj_cls or "Quarter" in obj_cls) and obj != self._cached_viewport:
                    # Only a viewer's own GL surface. FreeCAD parents unrelated
                    # QOpenGLWidgets to the main window (a 100x30 one is there in
                    # 1.1); adopting one would measure every click against it.
                    if _is_view3d_gl_widget(obj):
                        self._cached_viewport = obj
            except Exception as e:
                fld_logger.debug(f"InputManager.eventFilter: GL viewport detection via metaObject failed: {e}")

            # Is this event for the viewport or one of its child GL widgets?
            is_viewport_event = False
            if self._cached_viewport:
                if obj == self._cached_viewport:
                    is_viewport_event = True
                elif isinstance(obj, QtGui.QWindow):
                    # Some Qt6 viewport surfaces (e.g. the OpenGL view) deliver events
                    # from a QWindow rather than a QWidget. QWidget.isAncestorOf() only
                    # accepts QWidgets, so walk the QWindow parent chain instead and
                    # compare against the viewport's own native window handle.
                    try:
                        vp_handle = self._cached_viewport.windowHandle()
                        w = obj
                        while w is not None:
                            if w is vp_handle:
                                is_viewport_event = True
                                break
                            w = w.parent()
                    except Exception as e:
                        fld_logger.debug(f"InputManager.eventFilter: QWindow ancestry check failed: {e}")
                elif hasattr(self._cached_viewport, "isAncestorOf"):
                    try:
                        is_viewport_event = self._cached_viewport.isAncestorOf(obj)
                    except Exception as e:
                        fld_logger.debug(f"InputManager.eventFilter: isAncestorOf check failed: {e}")

            # Standardize coordinates relative to viewport & Track DPI
            if is_mouse_event:
                try:
                    # Store DPI ratio from viewport specifically
                    ratio = 1.0
                    target = self._cached_viewport if self._cached_viewport else obj
                    if hasattr(target, "devicePixelRatioF"):
                        ratio = float(target.devicePixelRatioF())
                    elif hasattr(target, "devicePixelRatio"):
                        ratio = float(target.devicePixelRatio())
                    self._device_pixel_ratio = ratio

                    # Correct way to store coordinates: always relative to the cached viewport.
                    global_pos = obj.mapToGlobal(event.pos()) if hasattr(obj, "mapToGlobal") else event.pos()
                    if self._cached_viewport and hasattr(self._cached_viewport, "mapFromGlobal"):
                        local_pos = self._cached_viewport.mapFromGlobal(global_pos)
                    else:
                        local_pos = event.pos()
                    self._last_qt_pos = (int(local_pos.x()), int(local_pos.y()))
                except Exception as e:
                    fld_logger.debug(f"InputManager.eventFilter: DPI ratio/coordinate tracking failed: {e}")

            # [Event Owner: Qt Event Filter] Right-click → Fields context menu.
            # QEvent.ContextMenu is not reliably delivered for the 3D viewport
            # (see todo_input.md), so the menu is driven directly from the RMB
            # release intercepted here. The QEvent.ContextMenu branches below
            # are swallow-guards only, so platforms that DO deliver it don't
            # pop the menu a second time.
            if (event.type() == QtCore.QEvent.MouseButtonRelease
                    and event.button() == QtCore.Qt.RightButton
                    # Qt delivers mouse events twice: first to the QWidgetWindow
                    # (window level), then to the child widget under the cursor.
                    # The viewport check only matches the widget-level delivery,
                    # so an unmatched window-level event must pass through
                    # untouched — consuming it here would stop Qt from ever
                    # re-delivering the release to the viewport widget.
                    and not (isinstance(obj, QtGui.QWindow) and not is_viewport_event)):
                active_tool = FldToolManager.get_instance().get_active_tool()
                _obj_cls = obj.metaObject().className() if hasattr(obj, "metaObject") else type(obj).__name__
                fld_logger.debug(
                    f"InputManager: RMB release — tool={type(active_tool).__name__ if active_tool else None}, "
                    f"viewport={is_viewport_event}, obj={_obj_cls}, pos={self._last_qt_pos}")
                if active_tool is not None:
                    if is_viewport_event:
                        active_tool.on_context_menu({
                            "Button": event.button(),
                            "Modifiers": event.modifiers(),
                            "Position": self._last_qt_pos,
                        })
                    # Always consume: suppress FreeCAD's context menu while a
                    # tool is active (matching press was consumed by the tool).
                    return True
                if is_viewport_event:
                    if not self._is_menu_active():
                        from freecad.fields.core.input.fld_menu import FldMenuManager
                        fld_logger.debug("InputManager: RMB release (no tool) -> show_context_menu")
                        FldMenuManager.get_instance().show_context_menu()
                    return True  # Consume so FreeCAD's viewport popup doesn't also open

            global_action = None
            if event.type() in (QtCore.QEvent.KeyPress, QtCore.QEvent.ShortcutOverride):
                from freecad.fields.core.input import fld_keymap
                evt_dict = {
                    "Key": event.key(),
                    "Modifiers": event.modifiers(),
                    "Text": event.text() if hasattr(event, "text") else ""
                }
                global_action = fld_keymap.action_for(evt_dict, [fld_keymap.CTX_GLOBAL])

            # --- Viewport Toggle (Ctrl+Space) ---
            if event.type() == QtCore.QEvent.KeyPress and is_viewport_event:
                if global_action == "global.maximize_view":
                    try:
                        mw = FreeCADGui.getMainWindow()
                        mdi = mw.findChild(QtWidgets.QMdiArea)
                        if mdi:
                            sub = mdi.activeSubWindow()
                            if sub:
                                if sub.isMaximized():
                                    sub.showNormal()
                                else:
                                    sub.showMaximized()
                                return True
                    except Exception as e:
                        fld_logger.debug(f"Viewport toggle failed: {e}")

            # Filter mouse events: only allow those in the viewport to reach the tools
            if (is_mouse_event or is_context_event) and not is_viewport_event:
                return False

            # Middle mouse MUST always reach FreeCAD's navigation system.
            # Use event.buttons() (live Qt bitmask) rather than our tracked flag so
            # this is immune to any flag-tracking race.  This early-exit runs before
            # tool dispatch AND before the no-tool else-branch, covering every state.
            #
            # `button()` alone is not enough: it names only the button this event is
            # ABOUT, so a left or right press that lands while MMB is already held
            # fell through to the tool -- which swallowed it. Those combinations are
            # navigation gestures (MMB+LMB, MMB+RMB), and the RMB one additionally
            # opened the Fields context menu mid-orbit, whose modal exec_() blocks the
            # GUI thread. `buttons()` is the whole live bitmask, so it covers them.
            if is_mouse_event:
                _mid = QtCore.Qt.MiddleButton
                if (event.type() in (QtCore.QEvent.MouseButtonPress,
                                     QtCore.QEvent.MouseButtonRelease,
                                     QtCore.QEvent.MouseButtonDblClick) and
                        (event.button() == _mid or bool(event.buttons() & _mid))):
                    return False
                if (event.type() == QtCore.QEvent.MouseMove and
                        bool(event.buttons() & _mid)):
                    return False

            tool = FldToolManager.get_instance().get_active_tool()

            # --- Dispatch to Active Tool ---
            if tool:
                # Construct clean Qt-native event dict
                event_dict = {
                    "Button": event.button() if hasattr(event, "button") else QtCore.Qt.NoButton,
                    "Modifiers": event.modifiers() if hasattr(event, "modifiers") else QtCore.Qt.NoModifier,
                    "Position": self._last_qt_pos,
                }

                # Middle mouse already returned above, for every event type.
                if event.type() in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease, QtCore.QEvent.MouseButtonDblClick):
                    # Pass through view cube clicks (top-right corner of viewport)
                    if self._cached_viewport:
                        w = self._cached_viewport.width()
                        pos = event.pos()
                        if pos.x() > w - 130 and pos.y() < 130:
                            return False

                if event.type() == QtCore.QEvent.MouseMove:
                    consumed = tool.on_mouse_move(event_dict)
                    return bool(consumed) if consumed is not None else True
                
                elif event.type() == QtCore.QEvent.MouseButtonPress:
                    consumed = tool.on_mouse_press(event_dict)
                    return bool(consumed)
                
                elif event.type() == QtCore.QEvent.MouseButtonRelease:
                    consumed = tool.on_mouse_release(event_dict)
                    return bool(consumed)
                
                elif event.type() == QtCore.QEvent.MouseButtonDblClick:
                    consumed = tool.on_mouse_press(event_dict)
                    return bool(consumed)
                    
                elif event.type() == QtCore.QEvent.KeyPress:
                    event_dict["Key"] = event.key()
                    event_dict["Text"] = event.text()
                    if tool.on_key_press(event_dict):
                        return True
                    # Let through if tool didn't consume it
                    
                elif event.type() == QtCore.QEvent.KeyRelease:
                    event_dict["Key"] = event.key()
                    if tool.on_key_release(event_dict):
                        return True
                        
                elif event.type() == QtCore.QEvent.ContextMenu:
                    # Menu already shown from the RMB release handler above;
                    # swallow so it cannot fire twice on platforms that
                    # deliver QEvent.ContextMenu for the viewport.
                    return True

                elif event.type() == QtCore.QEvent.ShortcutOverride:
                    text = event.text().lower() if hasattr(event, "text") else ""
                    # Claim hotkeys for active tool.
                    # 'v' = cycle handle type (CurveCreator)
                    # 't' = toggle gizmo space (PrimitiveCreatorBase)
                    if text in ['s', 'd', 'e', 'x', 'y', 'z', 'g', 'r', 'c', 'v', 't']:
                        event.accept()
                        return False

            # --- Global Handling (No tool active) ---
            else:
                # SDF object selection on LMB press
                if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                    from freecad.fields.core.input.fld_tool_manager import FldSelectionManager
                    if FldSelectionManager.get_instance().try_sdf_selection(self._last_qt_pos):
                        self._sdf_selected_on_press = True
                        return True # Swallow event to prevent FreeCAD from clearing selection
                    self._sdf_selected_on_press = False
                    # Otherwise let through to FreeCAD

                elif event.type() == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton:
                    if getattr(self, "_sdf_selected_on_press", False):
                        self._sdf_selected_on_press = False
                        return True # Swallow release to prevent FreeCAD from clearing selection

                # Double-click to edit objects
                elif event.type() == QtCore.QEvent.MouseButtonDblClick and event.button() == QtCore.Qt.LeftButton:
                    from freecad.fields.core.input.fld_tool_manager import FldSelectionManager
                    sel_mgr = FldSelectionManager.get_instance()
                    sel_mgr.try_sdf_selection(self._last_qt_pos)
                    sel = FreeCADGui.Selection.getSelection()
                    if sel:
                        tool_opener = _resolve_edit_tool_for(sel[0])
                        if tool_opener is not None:
                            tool_opener()
                            return True

                # Global hotkeys
                elif event.type() == QtCore.QEvent.KeyPress:
                    key = event.key()
                    text = event.text().lower() if hasattr(event, "text") else ""
                    
                    if global_action == "global.context_menu" and not self._is_menu_active():
                        from freecad.fields.core.input.fld_menu import FldMenuManager
                        if not FldMenuManager.get_instance()._ignore_hotkeys:
                            FldMenuManager.get_instance().show_context_menu()
                            return True

                    if global_action == "global.toggle_group" and not self._is_menu_active():
                        sel = FreeCADGui.Selection.getSelection()
                        toggled_any = False
                        for obj in sel:
                            if hasattr(obj, "Group"):
                                cur = getattr(obj, "Group", "Additive")
                                new_grp = "Subtractive" if cur == "Additive" else "Additive"
                                from freecad.fields.core.objects.fld_modifier_stack import get_chain
                                base, modifiers = get_chain(obj)
                                all_chain = ([base] + modifiers) if base is not None else [obj]
                                for o in all_chain:
                                    if hasattr(o, "Group"):
                                        o.Group = new_grp
                                tail = modifiers[-1] if modifiers else obj
                                if tail.Document:
                                    def _deferred_q_recompute(o=tail):
                                        try:
                                            if o.Document:
                                                o.Document.recompute([o])
                                                if hasattr(o, "Proxy") and hasattr(o.Proxy, "SdfField"):
                                                    from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                                                    label = f"{o.Document.Name}.{o.Name}"
                                                    FldSceneVoxelRenderer.get_instance().update_field(label, o.Proxy.SdfField)
                                        except Exception as ex:
                                            fld_logger.debug(f"InputManager: deferred Q-key recompute error: {ex}")
                                    QtCore.QTimer.singleShot(0, _deferred_q_recompute)
                                toggled_any = True
                        if toggled_any:
                            return True

                    if global_action == "global.edit_mode":
                        # Toggle edit tool
                        active_tool = FldToolManager.get_instance().get_active_tool()
                        if active_tool:
                            active_tool.finish()
                            return True
                        
                        sel = FreeCADGui.Selection.getSelection()
                        if sel:
                            obj = sel[0]
                            proxy_name = getattr(getattr(obj, "Proxy", None), "__class__", type(None)).__name__
                            shape_type = getattr(obj, "ShapeType", None)
                            fld_logger.debug(f"InputManager: Tab pressed. Selection={obj.Name}, proxy={proxy_name}, shape_type={shape_type}")

                            tool_opener = _resolve_edit_tool_for(obj)
                            if tool_opener is not None:
                                tool_opener()
                                return True
 
                    if global_action == "global.edit_mode_alt" and not self._is_menu_active():
                        sel = FreeCADGui.Selection.getSelection()
                        if sel:
                            obj = sel[0]
                            proxy_name = getattr(getattr(obj, "Proxy", None), "__class__", type(None)).__name__
                            st = getattr(obj, "ShapeType", "")
                            fld_logger.debug(f"InputManager: E pressed. Selection={obj.Name}, proxy_name={proxy_name}, ShapeType={st}")

                            tool_opener = _resolve_edit_tool_for(obj)
                            if tool_opener is not None:
                                tool_opener()
                                return True

                # ShortcutOverride for 'E'/'Q' when no tool
                elif event.type() == QtCore.QEvent.ShortcutOverride:
                    if global_action == "global.edit_mode_alt":
                        sel = FreeCADGui.Selection.getSelection()
                        if any(hasattr(o, "Proxy") and getattr(o.Proxy, "is_fld_workplane", False) for o in sel):
                            event.accept(); return False
                    elif global_action == "global.toggle_group":
                        sel = FreeCADGui.Selection.getSelection()
                        # Must match what the Q handler above actually toggles. This read
                        # IsSubtractive, which onDocumentRestored removes and nothing adds
                        # any more, so the override never fired and Q stayed stealable.
                        if any(hasattr(o, "Group") for o in sel):
                            event.accept(); return False

                elif event.type() == QtCore.QEvent.ContextMenu:
                    # Menu already shown from the RMB release handler above;
                    # swallow to avoid double-fire.
                    return True

        except Exception as e:
            fld_logger.error(f"FldInputManager eventFilter error: {e}")

        return False

    def get_mouse_pos(self, event_dict=None):
        """Standardized Top-Left coordinate retrieval for all tools.
        After refactor, we standardize on Qt-native coordinates.
        """
        if event_dict and "Position" in event_dict:
            self._last_qt_pos = event_dict["Position"]
        return self._last_qt_pos

    def get_gl_pos_phys(self, view, event_dict=None):
        """Returns (x_phys, y_phys) in Bottom-Left (OpenGL) physical pixels.
        Used for FreeCAD view.getPoint() and getRay() which expect flipped Y.
        """
        pos = self.get_mouse_pos(event_dict)
        vp_sz = self._get_vp_size(view)
        if not vp_sz:
            return None, None
        
        # Calculate flipped Y in logical space first to avoid rounding drift
        y_log_flipped = (vp_sz[1] - 1.0 - pos[1])
        ratio = max(1.0, self._device_pixel_ratio)
        return int(round(pos[0] * ratio)), int(round(y_log_flipped * ratio))

    def device_pixel_ratio(self):
        """Single source of truth for the viewport's device-pixel ratio.

        `_last_qt_pos` is in LOGICAL Qt pixels; anything indexing a GL buffer
        needs DEVICE pixels. `get_gl_pos` (below) already multiplies by this for
        the CPU picker -- this accessor exists so the GPU surface-id picker can
        do the same without reaching into a private attribute.
        """
        return max(1.0, self._device_pixel_ratio)

    def is_shift_down(self):
        """Single source of truth for Shift key state."""
        return self._shift_down

    def is_ctrl_down(self):
        """Single source of truth for Control key state."""
        return self._control_down

    def is_left_mouse_down(self):
        """Single source of truth for left mouse button state."""
        return self._left_mouse_down

    def _get_vp_size(self, view):
        """Get viewport (width, height) in logical pixels matching _last_qt_pos space."""
        # Method 0: the actual GL viewport size from the render manager (PHYSICAL),
        # converted to LOGICAL with our tracked ratio. This was also spelled out a
        # second time as a "Method 3" fallback below; that copy was unreachable
        # except when this one had returned early on a zero-sized viewport, in
        # which case it returned (0.0, 0.0) for callers to divide by.
        ratio = max(1.0, self._device_pixel_ratio)
        try:
            # Gui.View3D -> Gui.View3DInventorViewer -> SoRenderManager
            rm = view.getViewer().getSoRenderManager()
            sz_pixels = rm.getViewportRegion().getViewportSizePixels()
            if sz_pixels[0] > 0 and sz_pixels[1] > 0:
                return float(sz_pixels[0]) / ratio, float(sz_pixels[1]) / ratio
        except Exception as e:
            fld_logger.debug(f"InputManager._get_vp_size: SoRenderManager viewport size lookup failed: {e}")

        # Fallback Method 1: Use the cached viewport widget ourselves (LOGICAL)
        # Same dead-wrapper trap as the event filter: after a document close the
        # cache names a destroyed widget. Drop it and fall through rather than
        # logging a size lookup that could never have worked.
        if self._cached_viewport is not None and not _qt_alive(self._cached_viewport):
            self._cached_viewport = None
        if self._cached_viewport:
            try:
                return float(self._cached_viewport.width()), float(self._cached_viewport.height())
            except Exception as e:
                fld_logger.debug(f"InputManager._get_vp_size: cached viewport width/height lookup failed: {e}")

        # Fallback Method 2: viewer size methods
        try:
            viewer = view.getViewer()
            for method_name in ("getSize", "getGlxSize"):
                if hasattr(viewer, method_name):
                    try:
                        sz = getattr(viewer, method_name)()
                        # This might return logical or physical depending on platform.
                        # We assume physical if it's much larger than view.width()
                        w = float(sz[0] if isinstance(sz, (list, tuple)) else sz.width())
                        h = float(sz[1] if isinstance(sz, (list, tuple)) else sz.height())
                        
                        # Heuristic: if size is roughly physical, scale it down
                        # (This is safer than blind division if ratio is already applied)
                        # However, for now we follow the instruction to ensure logical.
                        if h > 0:
                            return w / ratio, h / ratio
                    except Exception as e:
                        fld_logger.debug(f"InputManager._get_vp_size: viewer.{method_name}() failed: {e}")
                        continue
        except Exception as e:
            fld_logger.debug(f"InputManager._get_vp_size: viewer size methods (getSize/getGlxSize) failed: {e}")

        return None

    def get_scene_point(self, view, event_dict=None):
        """Returns the 3D scene point under the cursor via view.getPoint().
        Standardizes on physical pixel mapping for the FreeCAD API.
        """
        if not view: return None
        x_phys, y_phys = self.get_gl_pos_phys(view, event_dict)
        if x_phys is None: return None

        try:
            return view.getPoint(x_phys, y_phys)
        except Exception as e:
            fld_logger.debug(f"get_scene_point failed: x={x_phys} y_phys={y_phys} err={e}")
            return None

    def get_ray(self, view, event_dict=None):
        """Centralized ray generation from screen coordinates.

        Delegates to ViewProjector, which owns viewport projection math; kept
        here as a thin wrapper since most tool call sites reach this through
        FldInputManager.get_instance() rather than holding a ViewProjector.
        """
        from freecad.fields.core.input.view_projector import ViewProjector
        return ViewProjector(view).get_ray(event_dict)

    def get_projected_point(self, view, base_point_3d, normal_3d, event_dict):
        """Delegates to ViewProjector.get_projected_point. See that method for details."""
        from freecad.fields.core.input.view_projector import ViewProjector
        return ViewProjector(view).get_projected_point(base_point_3d, normal_3d, event_dict)

    def get_axis_point(self, view, base, normal, event_dict):
        """Delegates to ViewProjector.get_axis_point. See that method for details."""
        from freecad.fields.core.input.view_projector import ViewProjector
        return ViewProjector(view).get_axis_point(base, normal, event_dict)

    def project_to_screen(self, view, world_pt):
        """Delegates to ViewProjector.project_to_screen. See that method for details."""
        from freecad.fields.core.input.view_projector import ViewProjector
        return ViewProjector(view).project_to_screen(world_pt)

    def get_view_transform(self, view, target_pt):
        """
        Returns a Placement parallel to the screen at target_pt.
        Consolidates matrix math for camera-facing visuals.
        """
        if not view: return FreeCAD.Placement()
        
        try:
            vd = view.getViewDirection()
            ud = view.getUpDirection()
            
            # Basis vectors
            z_axis = FreeCAD.Vector(-vd[0], -vd[1], -vd[2]); z_axis.normalize()
            up_axis = FreeCAD.Vector(ud[0], ud[1], ud[2]); up_axis.normalize()
            x_axis = up_axis.cross(z_axis); x_axis.normalize()
            y_axis = z_axis.cross(x_axis); y_axis.normalize()
            
            m = FreeCAD.Matrix(
                x_axis.x, y_axis.x, z_axis.x, target_pt.x,
                x_axis.y, y_axis.y, z_axis.y, target_pt.y,
                x_axis.z, y_axis.z, z_axis.z, target_pt.z,
                0.0,      0.0,      0.0,      1.0
            )
            return FreeCAD.Placement(m)
        except Exception as e:
            fld_logger.debug(f"get_view_transform failed: {e}")
        return FreeCAD.Placement(target_pt, FreeCAD.Rotation())


    def initialize(self):
        try:
            if not getattr(self, "_is_initialized", False):
                QtWidgets.QApplication.instance().installEventFilter(self)
                from freecad.fields.core.input.fld_tool_manager import FldSelectionObserver
                self._sel_observer = FldSelectionObserver()
                FreeCADGui.Selection.addObserver(self._sel_observer)
                self._is_initialized = True
        except Exception as e:
            fld_logger.error(f"FldInputManager initialization error: {e}")

    def restore(self):
        try:
            if getattr(self, "_is_initialized", False):
                QtWidgets.QApplication.instance().removeEventFilter(self)
                if self._sel_observer is not None:
                    FreeCADGui.Selection.removeObserver(self._sel_observer)
                    self._sel_observer = None
                self._is_initialized = False
        except Exception as e:
            fld_logger.error(f"FldInputManager restore error: {e}")
