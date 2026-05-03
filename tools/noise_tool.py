import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui
from tools.dm_base import DMBase, DragTimerMixin, ToolState
from core import dm_logger
from core.dm_point import DMPoint
from pivy import coin
from core.input_manager import DMInputManager

class QuantityLineEdit(QtGui.QLineEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.step = 0.1
        
    def wheelEvent(self, event):
        try:
            dy = event.angleDelta().y()
        except AttributeError:
            dy = event.delta()
        if dy == 0:
            event.ignore()
            return
        try:
            val = float(self.text())
            val += self.step if dy > 0 else -self.step
            self.setText(f"{val:.3f}")
            self.editingFinished.emit()
        except Exception:
            pass
        event.accept()

class NoiseTaskPanel:
    def __init__(self, tool):
        self.tool = tool
        self.form = QtGui.QWidget()
        self.layout = QtGui.QVBoxLayout(self.form)
        self.layout.setContentsMargins(10, 10, 10, 10)
        
        self.params_group = QtGui.QGroupBox("Noise Parameters")
        self.p_layout = QtGui.QFormLayout(self.params_group)
        self.layout.addWidget(self.params_group)
        
        self.amp_input = QuantityLineEdit()
        self.amp_input.editingFinished.connect(self._on_amp_changed)
        self.p_layout.addRow("Amplitude:", self.amp_input)
        
        self.freq_input = QuantityLineEdit()
        self.freq_input.step = 0.01
        self.freq_input.editingFinished.connect(self._on_freq_changed)
        self.p_layout.addRow("Frequency:", self.freq_input)
        
        self.layout.addStretch()
        self.update_ui()
        
    def update_ui(self):
        obj = self.tool._target_obj
        if not obj: return
        self.amp_input.blockSignals(True)
        self.freq_input.blockSignals(True)
        self.amp_input.setText(f"{getattr(obj, 'Amplitude', 1.0):.3f}")
        self.freq_input.setText(f"{getattr(obj, 'Frequency', 1.0):.3f}")
        self.amp_input.blockSignals(False)
        self.freq_input.blockSignals(False)
        
    def _on_amp_changed(self):
        try:
            val = float(self.amp_input.text())
            if self.tool._target_obj:
                self.tool._target_obj.Amplitude = val
                self.tool._commit_changes()
        except Exception:
            pass

    def _on_freq_changed(self):
        try:
            val = float(self.freq_input.text())
            if self.tool._target_obj:
                self.tool._target_obj.Frequency = val
                self.tool._commit_changes()
        except Exception:
            pass
            
    def accept(self):
        self.tool.finish()
        return True
        
    def reject(self):
        self.tool.terminate()
        return True

class NoiseTool(DMBase, DragTimerMixin):
    def get_command_id(self):
        return "DM_EditObject"

    def get_handled_types(self):
        return ["DMNoiseProxy"]

    def __init__(self):
        super().__init__()
        self._target_obj = None
        self._is_editing = False
        
        self.dm_points = []
        self.points_root = coin.SoSeparator()
        if self.view and self.view.getSceneGraph():
            self.view.getSceneGraph().addChild(self.points_root)

    def _post_init(self):
        super()._post_init()
        if not getattr(self, "_terminated", False) and self._is_editing:
            self.panel = NoiseTaskPanel(self)
            FreeCADGui.Control.showDialog(self.panel)
            self._dialog_open = True

    def edit_object(self, obj):
        super().edit_object(obj)
        self._target_obj = obj
        self._is_editing = True
        dm_logger.info(f"NoiseTool activated for {obj.Label}")

    def handle_click(self, event_dict):
        # We don't have interactive handles for this tool yet, just the task panel.
        # But we consume clicks so FreeCAD doesn't deselect the object.
        return True

    def _commit_changes(self):
        if self._target_obj and hasattr(self._target_obj, "Proxy") and hasattr(self._target_obj.Proxy, "execute"):
            self._target_obj.Proxy.execute(self._target_obj)
            self._target_obj.touch()
            if self._target_obj.Document:
                self._target_obj.Document.recompute([self._target_obj])
            self.view.redraw()

    def finish(self):
        self._is_editing = False
        self.terminate()

    def _do_terminate(self):
        if self.points_root and self.view and self.view.getSceneGraph():
            self.view.getSceneGraph().removeChild(self.points_root)
        super()._do_terminate()

def activate():
    tool = NoiseTool()
