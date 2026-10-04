# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import FreeCADGui
from freecad.fields.core import fld_logger
from freecad.fields.tools.primitive_creator_base import PrimitiveCreatorBase
from freecad.fields.tools.fld_base import ToolState
from freecad.fields.core.sdf.sdf_curve_sweep import SdfCurveSweepField
from freecad.fields.core.sdf.curve_sampler import extract_bezier_segments_3d
from freecad.fields.core.sdf.sdf2d.box import Sdf2dBox
from freecad.fields.core.sdf.sdf2d.circle import Sdf2dCircle

MIN_PROFILE_DIM_MM = 0.5

# Property name -> (FreeCAD property type, default, enumeration or None).
# One table drives three things: the tool's own state, what the panel edits,
# and the properties stamped on the committed object. The stamping matters --
# the object is what a reload rebuilds the field from, so a parameter missing
# there comes back as a default and the solid silently changes shape.
_PARAM_SPECS = {
    "ProfileType": ("App::PropertyEnumeration", "Rectangle", ["Circle", "Rectangle", "Source"]),
    "Width":       ("App::PropertyFloat", 10.0, None),
    "Height":      ("App::PropertyFloat", 6.0, None),
    "Radius":      ("App::PropertyFloat", 5.0, None),
    "StartScale":  ("App::PropertyFloat", 1.0, None),
    "EndScale":    ("App::PropertyFloat", 1.0, None),
    "Twist":       ("App::PropertyFloat", 0.0, None),
    "CapType":     ("App::PropertyEnumeration", "Flat", list(SdfCurveSweepField.CAP_TYPES)),
}
_PARAM_DEFAULTS = {k: v[1] for k, v in _PARAM_SPECS.items()}


class PathExtrudeCreator(PrimitiveCreatorBase):
    """Interactive tool for creating and editing Extrude Along Path solids.

    Drag sets the profile size; everything else comes from the task panel.
    """

    CREATION_STEPS = [ToolState.DRAG_Z]

    # Geometry is anchored to the linked path curve — the panel must not move it.
    SUPPORTS_ORIGIN_EDIT = False

    def get_command_id(self):
        return "Fields_ExtrudeAlongPath"

    def get_sdf_type(self):
        return "path_extrude"

    def _get_source_curve_link(self):
        return self._curve_obj

    def __init__(self):
        super().__init__()
        self._curve_obj = None
        self._source_obj = None
        self._params = dict(_PARAM_DEFAULTS)
        self._segs_3d = None
        self._cached_sweep = None

        for obj in FreeCADGui.Selection.getSelection():
            shape_type = getattr(obj, "ShapeType", None)
            if shape_type == "curve" and self._curve_obj is None:
                self._curve_obj = obj
            elif shape_type == "sdf" and self._source_obj is None:
                self._source_obj = obj

        if self._curve_obj is not None:
            self._segs_3d = extract_bezier_segments_3d(self._curve_obj)
            self.working_plane = self._curve_obj.Placement
            self._working_plane_is_fallback = False
            # Drag distance is measured from the start of the path, not the
            # curve's Placement.Base — a curve whose points were edited after
            # creation no longer has its origin on the path.
            if self._segs_3d:
                self._anchor_pt = FreeCAD.Vector(*self._segs_3d[0][0])
            else:
                self._anchor_pt = self._curve_obj.Placement.Base

    def _detect_selected_workplane(self):
        pass

    def _post_init(self):
        """Show this tool's own panel instead of the generic PrimitiveTaskPanel.

        Skips PrimitiveCreatorBase._post_init deliberately: it shows a
        PrimitiveTaskPanel, and two dialogs would be stacked in the task view.
        """
        super(PrimitiveCreatorBase, self)._post_init()
        if getattr(self, "_terminated", False):
            return
        from freecad.fields.tools.path_extrude_panel import PathExtrudeTaskPanel
        self.panel = PathExtrudeTaskPanel(self)
        FreeCADGui.Control.showDialog(self.panel)
        self._dialog_open = True

    # ── parameters ──────────────────────────────────────────────────────────

    def get_parameters(self):
        return dict(self._params)

    def _apply_parameters(self, params):
        for key in _PARAM_DEFAULTS:
            if key in params:
                self._params[key] = params[key]
        for key in ("Width", "Height", "Radius"):
            self._params[key] = max(MIN_PROFILE_DIM_MM, float(self._params[key]))
        self._cached_sweep = None
        # While editing, the object is the live thing being rendered, so its
        # properties have to follow the panel or a later recompute would
        # rebuild the field from stale values.
        if getattr(self, "_is_editing", False) and self._preview_obj is not None:
            self._write_params(self._preview_obj)
        return True

    def _drag_dimension(self, pos):
        """Profile size implied by a drag from the path start to `pos`."""
        if not pos or not self._anchor_pt:
            return None
        return max(MIN_PROFILE_DIM_MM, (pos - self._anchor_pt).Length)

    def _on_stage_preview(self, state, pos):
        self._set_size_from_drag(state, pos)

    def _on_stage_accept(self, state, pos):
        self._set_size_from_drag(state, pos)

    def _set_size_from_drag(self, state, pos):
        """DRAG_Z sizes the profile: the radius directly, or the width with the
        height following the aspect ratio the panel currently shows."""
        if state != ToolState.DRAG_Z:
            return
        d = self._drag_dimension(pos)
        if d is None:
            return
        if self._params["ProfileType"] == "Circle":
            self._params["Radius"] = d
        else:
            aspect = self._params["Height"] / max(self._params["Width"], 1e-6)
            self._params["Width"] = 2.0 * d
            self._params["Height"] = max(MIN_PROFILE_DIM_MM, 2.0 * d * aspect)
        self._cached_sweep = None

    # ── field ───────────────────────────────────────────────────────────────

    def _build_profile(self):
        ptype = self._params["ProfileType"]
        if ptype == "Circle":
            return Sdf2dCircle(self._params["Radius"])
        if ptype == "Rectangle":
            return Sdf2dBox(size=(self._params["Width"], self._params["Height"]))
        from freecad.fields.core.objects.fld_curve_extrude_objects import section_profile_of
        profile = section_profile_of(self._source_obj)
        if profile is None:
            # "Source Solid" with nothing selectable to section. Say so once and
            # fall back to the circle the Radius slider already describes, so the
            # preview keeps up instead of silently vanishing.
            fld_logger.warn("Extrude Along Path: no source solid selected to take a "
                            "profile from; using the Radius circle instead.")
            return Sdf2dCircle(self._params["Radius"])
        return profile

    def _sweep_field(self):
        if not self._segs_3d:
            return None
        if self._cached_sweep is None:
            try:
                self._cached_sweep = SdfCurveSweepField(
                    profile=self._build_profile(),
                    segments_3d=self._segs_3d,
                    start_scale=self._params["StartScale"],
                    end_scale=self._params["EndScale"],
                    twist_degrees=self._params["Twist"],
                    cap_type=self._params["CapType"],
                    is_closed=getattr(self._curve_obj, "Closed", False),
                )
            except TypeError as e:
                # e.g. ProfileType "Source" pointed at something that is not a field
                fld_logger.error(f"PathExtrudeCreator: cannot sweep this profile: {e}")
                return None
        return self._cached_sweep

    def _get_preview_field(self):      return self._sweep_field()
    def _get_edit_preview_field(self): return self._sweep_field()
    def _get_final_field(self):        return self._sweep_field()

    def _primitive_name(self):
        base = self._curve_obj.Label if self._curve_obj else "Path"
        return f"{base}_Extrude"

    # ── edit / commit ───────────────────────────────────────────────────────

    def edit_object(self, obj):
        """Resume a path extrude: its properties are the tool state.

        Reached two ways. On a genuine re-edit the object already carries the
        parameters and they win. On the creation path the base class commits
        the preview object and comes straight here, before anything has been
        stamped -- so the properties get created from the tool's current values
        rather than the tool being reset to defaults.
        """
        super().edit_object(obj)
        self._sync_params(obj)
        path = (getattr(obj, "Path", None) or getattr(obj, "SourceCurveLink", None)
                or self._curve_obj)
        if path is not None:
            self._curve_obj = path
            self._segs_3d = extract_bezier_segments_3d(path)
        self._source_obj = getattr(obj, "Source", None) or self._source_obj
        self._link_source(obj)
        self._cached_sweep = None
        if getattr(self, "panel", None) is not None and hasattr(self.panel, "load_from_tool"):
            self.panel.load_from_tool()

    def _sync_params(self, obj):
        """Give `obj` the parameter properties, then read them back into the tool."""
        for key, (prop_type, default, enum) in _PARAM_SPECS.items():
            if not hasattr(obj, key):
                try:
                    obj.addProperty(prop_type, key, "Path Extrude",
                                    "Swept profile parameter")
                    if enum is not None:
                        setattr(obj, key, list(enum))
                    setattr(obj, key, self._params.get(key, default))
                except Exception as e:
                    fld_logger.debug(f"PathExtrudeCreator: could not add {key}: {e}")
                    continue
            try:
                self._params[key] = getattr(obj, key)
            except Exception as e:
                fld_logger.debug(f"PathExtrudeCreator: could not read {key}: {e}")

    def _link_source(self, obj):
        """Keep the profile solid reachable from the object, like the path link."""
        if self._source_obj is None:
            return
        try:
            if not hasattr(obj, "Source"):
                obj.addProperty("App::PropertyLink", "Source", "Path Extrude",
                                "Solid whose cross-section is swept")
            obj.Source = self._source_obj
        except Exception as e:
            fld_logger.debug(f"PathExtrudeCreator: could not link Source: {e}")

    def _write_params(self, obj):
        if obj is None:
            return
        self._sync_params(obj)          # the properties have to exist first
        for key, value in self._params.items():
            try:
                setattr(obj, key, value)
            except Exception as e:
                fld_logger.debug(f"PathExtrudeCreator: could not set {key} on {obj.Label}: {e}")
        self._link_source(obj)

    def _on_committed(self, obj):
        """The other commit path (_finalize_object) lands here, not in edit_object."""
        try:
            super()._on_committed(obj)
        except Exception as e:
            fld_logger.debug(f"PathExtrudeCreator._on_committed: super failed: {e}")
        self._write_params(obj)

    def finish(self):
        if getattr(self, "_terminated", False):
            return
        if getattr(self, "_is_editing", False):
            self._write_params(self._preview_obj)
            super().finish()
            self._finished = True
            self.terminate()
        elif self.is_in_progress():
            self._finalize_object(self._primitive_name(), terminate=True)
        else:
            self.terminate()
