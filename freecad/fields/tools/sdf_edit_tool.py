# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/sdf_edit_tool.py

Interactive editing tool for SDF direct-modeling modifications: dispatches
to the property-slider edit panel for the selected SDF document object.
"""
import FreeCAD
import FreeCADGui
from freecad.fields.tools.fld_base import FldBase
from freecad.fields.core import fld_logger


class SdfEditTool(FldBase):
    """
    Generic dispatcher for editing SDF primitives and modifier objects.
    Identifies the selected SDF object and launches the corresponding Creator tool in edit mode.
    """
    def get_command_id(self):
        return "Fields_EditObject"

    def activate(self):
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            fld_logger.error("No object selected to edit.")
            self.terminate()
            return
            
        obj = sel[0]
        proxy = getattr(obj, "Proxy", None)
        if not proxy:
            fld_logger.error("Selected object has no Proxy.")
            self.terminate()
            return

        # 1. Handle Noise Objects
        from freecad.fields.core.objects.fld_noise_object import FldNoiseProxy
        from freecad.fields.core.objects.fld_noise2d_object import FldNoise2DProxy
        from freecad.fields.core.objects.fld_deform_objects import FldTwistProxy, FldBendProxy, FldLatticeProxy, FldDeformCageProxy, FldArrayProxy
        from freecad.fields.core.objects.fld_heightmap_object import FldHeightmapProxy
        from freecad.fields.core.objects.fld_voxel_field import FldVoxelFieldProxy
        if isinstance(proxy, FldNoiseProxy):
            from freecad.fields.tools.noise_3d_tool import NoiseTool
            tool = NoiseTool()
            tool.edit_object(obj)
            return
        if isinstance(proxy, FldNoise2DProxy):
            from freecad.fields.tools.noise_2d_tool import Noise2DTool
            tool = Noise2DTool()
            tool.edit_object(obj)
            return
        if isinstance(proxy, FldTwistProxy):
            from freecad.fields.tools.modifiers.twist_tool import TwistTool
            tool = TwistTool()
            tool.edit_object(obj)
            return
        if isinstance(proxy, FldBendProxy):
            from freecad.fields.tools.modifiers.bend_tool import BendTool
            tool = BendTool()
            tool.edit_object(obj)
            return
        if isinstance(proxy, FldLatticeProxy):
            from freecad.fields.tools.modifiers.lattice_tool import LatticeTool
            tool = LatticeTool()
            tool.edit_object(obj)
            return
        if isinstance(proxy, FldArrayProxy):
            from freecad.fields.tools.modifiers.array_tool import ArrayTool
            tool = ArrayTool()
            tool.edit_object(obj)
            return
        if isinstance(proxy, FldDeformCageProxy):
            from freecad.fields.tools.cage_tool import CageEditTool
            tool = CageEditTool()
            tool.edit_object(obj)
            return
        if isinstance(proxy, FldHeightmapProxy):
            from freecad.fields.tools.heightmap_tool import HeightmapTool
            tool = HeightmapTool()
            tool.edit_object(obj)
            return
        if isinstance(proxy, FldVoxelFieldProxy):
            from freecad.fields.tools.tool_voxel_field import VoxelFieldTool
            tool = VoxelFieldTool()
            tool.edit_object(obj)
            return

        # 2. Handle SDF Primitives
        # All SDF primitives use FldObjectProxy, but they have different SdfField subclasses
        from freecad.fields.tools.creators.box_creator import BoxCreator
        from freecad.fields.tools.creators.sphere_creator import SphereCreator
        from freecad.fields.tools.creators.cylinder_creator import CylinderCreator
        from freecad.fields.tools.creators.torus_creator import TorusCreator
        from freecad.fields.tools.creators.extrusion_creator import CurveExtrudeCreator, SdfCurveFillExtrudeCreator
        from freecad.fields.tools.creators.revolve_creator import RevolveCreator
        from freecad.fields.tools.cage_tool import CageEditTool
        from freecad.fields.tools.modifiers.twist_tool import TwistTool
        from freecad.fields.tools.modifiers.bend_tool import BendTool
        from freecad.fields.tools.modifiers.lattice_tool import LatticeTool
        from freecad.fields.tools.modifiers.array_tool import ArrayTool
        from freecad.fields.tools.tool_voxel_field import VoxelFieldTool
        
        from freecad.fields.core.sdf.sdf.box import SdfBoxField
        from freecad.fields.core.sdf.sdf.sphere import SdfSphereField
        from freecad.fields.core.sdf.sdf.cylinder import SdfCylinderField
        from freecad.fields.core.sdf.sdf.torus import SdfTorusField
        from freecad.fields.core.sdf.sdf_extrusion import SdfExtrusionField
        from freecad.fields.core.sdf.sdf_curve_fill_extrusion import SdfCurveFillExtrusionField
        from freecad.fields.core.sdf.sdf_revolution import SdfRevolutionField
        from freecad.fields.core.sdf.sdf.cage import SdfCageField
        from freecad.fields.core.sdf.sdf.cage_deform import SdfCageDeformField
        from freecad.fields.core.sdf.sdf.twist import SdfTwistField
        from freecad.fields.core.sdf.sdf.bend import SdfBendField
        from freecad.fields.core.sdf.sdf.lattice import SdfLatticeField
        from freecad.fields.core.sdf.sdf.array import SdfArrayField
        from freecad.fields.core.sdf.sdf.voxel_field import SdfVoxelField

        # Mapping from SdfField type to Creator class
        field_to_creator = {
            SdfBoxField: BoxCreator,
            SdfSphereField: SphereCreator,
            SdfCylinderField: CylinderCreator,
            SdfTorusField: TorusCreator,
            SdfExtrusionField: CurveExtrudeCreator,
            SdfCurveFillExtrusionField: SdfCurveFillExtrudeCreator,
            SdfRevolutionField: RevolveCreator,
            SdfCageField: CageEditTool,
            SdfCageDeformField: CageEditTool,
            SdfTwistField: TwistTool,
            SdfBendField: BendTool,
            SdfLatticeField: LatticeTool,
            SdfArrayField: ArrayTool,
            SdfVoxelField: VoxelFieldTool,
        }

        sdf_field = getattr(proxy, "SdfField", None)
        if sdf_field:
            creator_cls = field_to_creator.get(type(sdf_field))
            if creator_cls:
                fld_logger.info(f"SdfEditTool: Dispatching to {creator_cls.__name__} for {obj.Label}")
                tool = creator_cls()
                tool.edit_object(obj)
                return
            else:
                fld_logger.warn(f"SdfEditTool: No specialized editor for SdfField '{type(sdf_field).__name__}'")
        else:
            fld_logger.warn(f"SdfEditTool: Object '{obj.Label}' has no SdfField.")

        self.terminate()

