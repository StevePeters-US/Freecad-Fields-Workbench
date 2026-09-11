# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Task panel for the boolean Fuse/Cut/Common commands: internal edge radius and
edge-bevel table, with live viewport preview.

Split out of commands/cmd_boolean.py (CR-064) -- matches this codebase's own
convention (SculptBrushTaskPanel lives in tools/sculpt_brush_tool.py, not
commands/cmd_sculpt.py); cmd_boolean.py was the one commands/*.py file with a
TaskPanel class defined in commands/ at all.

`Bevel` MUST be imported at true module level here, not inside a function -- this
file's __init__/_on_add_bevel/reject all construct Bevel() from class-body scope,
where a function-local import elsewhere in a DIFFERENT function's scope is not
visible. A prior version had it only inside cmd_boolean._recompose_boolean, which
raised a NameError swallowed by Qt's slot dispatch on every "Add Bevel" click --
see that history in commands/cmd_boolean.py's own top-of-file comment. Don't undo
this by moving the import back inside a method.
"""
import FreeCADGui
from PySide import QtWidgets, QtCore

from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf_rewriter import Bevel
from freecad.fields.core.sdf.sdf_boolean_compose import build_boolean_field


class BooleanTaskPanel:
    """
    Task panel for setting the internal edge radius and edge bevels with live viewport preview.
    Works for both creation (initial k=0) and editing (initial k=existing value).
    """

    _LABEL = {
        "Add":          "Global Blend Radius (mm):",
        "Subtract":     "Global Cutter Radius (mm):",
        "Intersection": "Global Blend Radius (mm):",
    }
    _TIP = {
        "Add": "0 = sharp boolean.\n"
               "Values > 0 fillet the crease where the shapes meet (mm).\n"
               "Editable later via the object's SmoothK property.",
        "Subtract": "0 = sharp boolean.\n"
                    "Values > 0 round the cutter's own edges, so the cavity gets\n"
                    "internal corners of this radius -- what a corner-radius end\n"
                    "mill leaves. The rim where the cut breaks the surface stays\n"
                    "sharp. Clamped to what the cutter can hold: 3 mm inside a\n"
                    "4 mm slot gives the 2 mm stadium.\n"
                    "Editable later via the object's SmoothK property.",
        "Intersection": "0 = sharp boolean.\n"
                        "Values > 0 blend the two shapes over this radius (mm).\n"
                        "Editable later via the object's SmoothK property.",
    }

    def __init__(self, operation, orange_fields, blue_fields, result_obj,
                 is_creation=False, sel_objects=None):
        self._operation     = operation
        self._orange_fields = orange_fields
        self._blue_fields   = blue_fields
        self._result_obj    = result_obj
        self._is_creation   = is_creation
        self._sel_objects   = list(sel_objects) if sel_objects else []
        self._original_k    = float(getattr(result_obj, "SmoothK", 0.0) or 0.0)
        self._original_ida  = list(getattr(result_obj, "BevelIdA", []) or [])
        self._original_idb  = list(getattr(result_obj, "BevelIdB", []) or [])
        self._original_rad  = list(getattr(result_obj, "BevelRadius", []) or [])
        self._original_chm  = list(getattr(result_obj, "BevelChamfer", []) or [])

        self._bevels = []
        for i in range(min(len(self._original_ida), len(self._original_idb), len(self._original_rad))):
            chm = self._original_chm[i] if i < len(self._original_chm) else 0.0
            self._bevels.append(Bevel(id_a=int(self._original_ida[i]), id_b=int(self._original_idb[i]),
                                      radius=float(self._original_rad[i]), chamfer=float(chm)))

        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(f"Boolean {operation} & Bevels")

        main_layout = QtWidgets.QVBoxLayout(self.form)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Global radius section
        form_layout = QtWidgets.QFormLayout()
        self._smooth_spin = QtWidgets.QDoubleSpinBox()
        self._smooth_spin.setRange(0.0, 500.0)
        self._smooth_spin.setSingleStep(1.0)
        self._smooth_spin.setDecimals(1)
        self._smooth_spin.setValue(self._original_k)
        self._smooth_spin.setToolTip(self._TIP.get(operation, ""))
        form_layout.addRow(self._LABEL.get(operation, "Radius (mm):"), self._smooth_spin)
        main_layout.addLayout(form_layout)

        # Edge Bevels section
        bevel_group = QtWidgets.QGroupBox("Edge Bevels (Surface Pairs)")
        bevel_layout = QtWidgets.QVBoxLayout(bevel_group)

        self._bevel_table = QtWidgets.QTableWidget(0, 5)
        self._bevel_table.setHorizontalHeaderLabels(["Surface A", "Surface B", "Radius (mm)", "Chamfer", ""])
        self._bevel_table.horizontalHeader().setStretchLastSection(False)
        self._bevel_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        bevel_layout.addWidget(self._bevel_table)

        # Controls to add new bevel
        add_layout = QtWidgets.QHBoxLayout()
        self._id_a_spin = QtWidgets.QSpinBox()
        self._id_a_spin.setRange(0, 65534)
        self._id_a_spin.setPrefix("A: ")
        self._id_b_spin = QtWidgets.QSpinBox()
        self._id_b_spin.setRange(0, 65534)
        self._id_b_spin.setPrefix("B: ")
        self._bev_rad_spin = QtWidgets.QDoubleSpinBox()
        self._bev_rad_spin.setRange(0.1, 500.0)
        self._bev_rad_spin.setValue(2.0)
        self._bev_rad_spin.setSingleStep(0.5)
        self._bev_rad_spin.setPrefix("R: ")

        self._add_bev_btn = QtWidgets.QPushButton("Add Bevel")
        self._add_bev_btn.clicked.connect(self._on_add_bevel)

        add_layout.addWidget(self._id_a_spin)
        add_layout.addWidget(self._id_b_spin)
        add_layout.addWidget(self._bev_rad_spin)
        add_layout.addWidget(self._add_bev_btn)
        bevel_layout.addLayout(add_layout)

        main_layout.addWidget(bevel_group)
        main_layout.addStretch()

        self._rebuild_bevel_table()

        # 80 ms debounce so rapid spin-box changes don't each trigger a rebuild
        self._preview_timer = QtCore.QTimer(self.form)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(80)
        self._preview_timer.timeout.connect(self._apply_preview)

        self._smooth_spin.valueChanged.connect(lambda _: self._preview_timer.start())

    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def _rebuild_bevel_table(self):
        self._bevel_table.setRowCount(len(self._bevels))
        for row, b in enumerate(self._bevels):
            item_a = QtWidgets.QTableWidgetItem(str(b.id_a))
            item_b = QtWidgets.QTableWidgetItem(str(b.id_b))
            item_r = QtWidgets.QTableWidgetItem(f"{b.radius:.1f}")
            item_c = QtWidgets.QTableWidgetItem("Yes" if b.chamfer > 0.0 else "No")
            item_a.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            item_b.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            item_r.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            item_c.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)

            del_btn = QtWidgets.QPushButton("X")
            del_btn.setMaximumWidth(30)
            del_btn.clicked.connect(lambda _, r=row: self._on_delete_bevel(r))

            self._bevel_table.setItem(row, 0, item_a)
            self._bevel_table.setItem(row, 1, item_b)
            self._bevel_table.setItem(row, 2, item_r)
            self._bevel_table.setItem(row, 3, item_c)
            self._bevel_table.setCellWidget(row, 4, del_btn)

    def _on_add_bevel(self):
        ida = self._id_a_spin.value()
        idb = self._id_b_spin.value()
        rad = self._bev_rad_spin.value()
        if ida == idb:
            return
        self._bevels.append(Bevel(id_a=ida, id_b=idb, radius=rad, chamfer=0.0))
        self._rebuild_bevel_table()
        self._preview_timer.start()

    def _on_delete_bevel(self, row):
        if 0 <= row < len(self._bevels):
            self._bevels.pop(row)
            self._rebuild_bevel_table()
            self._preview_timer.start()

    # ------------------------------------------------------------------ preview

    def _apply_preview(self):
        self._push_preview(self._smooth_spin.value(), self._bevels)

    def _push_preview(self, k, bevels=None):
        """Build a new composed field for k and bevels, and push it directly to renderer."""
        if bevels is None:
            bevels = self._bevels
        try:
            new_field = build_boolean_field(self._operation, self._orange_fields,
                                            self._blue_fields, k, bevels=bevels)
            if new_field is None:
                return

            # Write k and bevels back so recompute / serialisation is consistent
            proxy = getattr(self._result_obj, "Proxy", None)
            if proxy:
                proxy.SdfField = new_field
            self._result_obj.SmoothK = k
            self._result_obj.BevelIdA = [b.id_a for b in bevels]
            self._result_obj.BevelIdB = [b.id_b for b in bevels]
            self._result_obj.BevelRadius = [b.radius for b in bevels]
            self._result_obj.BevelChamfer = [b.chamfer for b in bevels]

            # Push directly to renderer (bypasses full doc recompute)
            vp       = getattr(self._result_obj, "ViewObject", None)
            vp_proxy = getattr(vp, "Proxy", None) if vp else None
            strategy = getattr(vp_proxy, "_strategy", None) if vp_proxy else None
            if strategy and getattr(strategy, "label", None):
                from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                FldSceneVoxelRenderer.get_instance().update_field(strategy.label, new_field)

        except Exception as e:
            fld_logger.error(f"BooleanTaskPanel preview failed: {e}")

    # ------------------------------------------------------------------ accept / reject

    def accept(self):
        """OK: flush any pending debounced edit, select result, and close."""
        self._preview_timer.stop()
        self._apply_preview()
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(self._result_obj)
        fld_logger.info(f"SDF {self._operation}: {len(self._sel_objects)} inputs, "
                       f"k={self._result_obj.SmoothK:.1f} mm")
        return True

    def reject(self):
        """Cancel: restore original properties or undo creation."""
        self._preview_timer.stop()
        if self._is_creation:
            for obj in self._sel_objects:
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    try:
                        obj.ViewObject.Visibility = True
                    except Exception as e:
                        fld_logger.debug(f"BooleanTaskPanel.reject: restore visibility failed: {e}")
            obj = self._result_obj
            if obj and obj.Document:
                try:
                    from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                    FldSceneVoxelRenderer.get_instance().unregister_field(
                        f"{obj.Document.Name}.{obj.Name}")
                except Exception as e:
                    fld_logger.debug(f"BooleanTaskPanel.reject: unregister failed: {e}")
                doc = obj.Document
                result_name = obj.Name
                def _deferred_remove(d=doc, n=result_name):
                    if d and d.getObject(n):
                        d.removeObject(n)
                        d.recompute()
                QtCore.QTimer.singleShot(0, _deferred_remove)
        else:
            orig_bevels = []
            for i in range(min(len(self._original_ida), len(self._original_idb), len(self._original_rad))):
                chm = self._original_chm[i] if i < len(self._original_chm) else 0.0
                orig_bevels.append(Bevel(id_a=int(self._original_ida[i]), id_b=int(self._original_idb[i]),
                                         radius=float(self._original_rad[i]), chamfer=float(chm)))
            self._push_preview(self._original_k, orig_bevels)
        return True


_BooleanDialog = BooleanTaskPanel
