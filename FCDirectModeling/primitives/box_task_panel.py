import FreeCAD
from PySide import QtCore, QtGui
from FCDirectModeling import sdf_logger

class PanelWidget(QtGui.QWidget):
    def __init__(self, panel):
        super(PanelWidget, self).__init__()
        self.panel = panel
        # Install filter on self to catch keys when the widget itself has focus (e.g. after clear_focus)
        self.installEventFilter(self)

    def eventFilter(self, source, event):
        if event.type() == QtCore.QEvent.KeyPress:
            key = event.key()
            text = event.text().upper()
            
            if key == QtCore.Qt.Key_Escape:
                import FreeCADGui
                FreeCADGui.Control.closeDialog()
                return True
            
            # Check for C (Cutter Mode)
            if text == 'C':
                self.panel.creator.toggle_cutter_mode()
                return True
                
            # Check for X, Y, Z
            if text in ['X', 'Y', 'Z']:
                # Pass to creator to toggle/switch
                self.panel.creator.toggle_axis(text.lower())
                return True
                
        return super(PanelWidget, self).eventFilter(source, event)

class BoxTaskPanel:
    def __init__(self, creator):
        self.creator = creator
        self.form = PanelWidget(self)
        self.layout = QtGui.QFormLayout(self.form)
        
        # Dimensions
        self.ui_length = QtGui.QDoubleSpinBox()
        self.ui_length.setRange(-100000.0, 100000.0)
        self.ui_length.setSingleStep(1.0)
        self.ui_length.setSuffix(" mm")
        
        self.ui_width = QtGui.QDoubleSpinBox()
        self.ui_width.setRange(-100000.0, 100000.0)
        self.ui_width.setSingleStep(1.0)
        self.ui_width.setSuffix(" mm")
        
        self.ui_height = QtGui.QDoubleSpinBox()
        self.ui_height.setRange(-100000.0, 100000.0)
        self.ui_height.setSingleStep(1.0)
        self.ui_height.setSuffix(" mm")
        
        self.layout.addRow("Length (X):", self.ui_length)
        self.layout.addRow("Width (Y):", self.ui_width)
        self.layout.addRow("Height (Z):", self.ui_height)
        
        # Connections
        self.ui_length.valueChanged.connect(self.on_length_changed)
        self.ui_width.valueChanged.connect(self.on_width_changed)
        self.ui_height.valueChanged.connect(self.on_height_changed)
        
        # Install Event Filter to catch keys in spinboxes
        self.ui_length.installEventFilter(self.form)
        self.ui_width.installEventFilter(self.form)
        self.ui_height.installEventFilter(self.form)
        
        self._block_updates = False

    def getStandardButtons(self):
        return QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel
        
    def accept(self):
        self.creator.finish()
        return True
        
    def reject(self):
        # If the creator already finished (3rd click committed the object),
        # closeDialog() triggers reject() — we must NOT terminate the creator
        # again or it will delete the freshly-created final object.
        if getattr(self.creator, "_finished", False):
            return True
        self.creator.terminate()
        return True

    def on_length_changed(self, val):
        if self._block_updates: return
        self.creator.set_length_lock(val)

    def on_width_changed(self, val):
        if self._block_updates: return
        self.creator.set_width_lock(val)

    def on_height_changed(self, val):
        if self._block_updates: return
        self.creator.set_height_lock(val)

    def update_values(self, length, width, height):
        sdf_logger.debug(f"DEBUG: BoxTaskPanel.update_values({length}, {width}, {height})")
        self._block_updates = True
        try:
            self.ui_length.setValue(length)
            self.ui_width.setValue(width)
            self.ui_height.setValue(height)
            sdf_logger.debug("DEBUG: Spinbox values set")
        finally:
            self._block_updates = False
            
    def focus_field(self, axis):
        sdf_logger.debug(f"DEBUG: focus_field {axis}")
        if axis == 'x':
            self.ui_length.setFocus()
            self.ui_length.selectAll()
        elif axis == 'y':
            self.ui_width.setFocus()
            self.ui_width.selectAll()
        elif axis == 'z':
            self.ui_height.setFocus()
            self.ui_height.selectAll()

    def clear_focus(self):
        self.ui_length.clearFocus()
        self.ui_width.clearFocus()
        self.ui_height.clearFocus()
        # Try to focus the form itself (container) to fully remove focus from input
        self.form.setFocus()
