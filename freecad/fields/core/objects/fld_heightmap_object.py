# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.objects.fld_view_provider import FldViewProvider
from freecad.fields.core.objects.fld_modifier_proxy_base import FldModifierProxyBase
from freecad.fields.core.render.field_appearance import apply_default_view_settings


class FldHeightmapProxy(FldModifierProxyBase):
    """
    Proxy object for a parametric Heightmap displacement modifier.

    Applies SdfHeightmapField: samples a grayscale image projected onto
    the plane perpendicular to Direction, adds amplitude * pixel_value to
    the base SDF distance so white pixels displace the surface outward.
    """
    SHAPE_TYPE_TOOLTIP = "Type of primitive"
    SOURCE_GROUP = "Heightmap"
    # No onChanged: the recompute is the only invalidation signal this proxy gets.
    ALWAYS_REBUILD = True

    def __init__(self, obj):
        super().__init__(obj)
        if not hasattr(obj, "ImagePath"):
            obj.addProperty("App::PropertyString", "ImagePath", "Heightmap",
                            "Path to grayscale heightmap image").ImagePath = ""
        if not hasattr(obj, "Amplitude"):
            obj.addProperty("App::PropertyFloat", "Amplitude", "Heightmap",
                            "Maximum displacement (white pixel = Amplitude)").Amplitude = 5.0
        if not hasattr(obj, "AmplitudeMin"):
            obj.addProperty("App::PropertyFloat", "AmplitudeMin", "Heightmap",
                            "Slider lower limit").AmplitudeMin = 0.0
        if not hasattr(obj, "AmplitudeMax"):
            obj.addProperty("App::PropertyFloat", "AmplitudeMax", "Heightmap",
                            "Slider upper limit").AmplitudeMax = 50.0
        if not hasattr(obj, "SizeX"):
            obj.addProperty("App::PropertyFloat", "SizeX", "Heightmap",
                            "World-space width of image projection").SizeX = 100.0
        if not hasattr(obj, "SizeY"):
            obj.addProperty("App::PropertyFloat", "SizeY", "Heightmap",
                            "World-space height of image projection").SizeY = 100.0
        if not hasattr(obj, "SizeMin"):
            obj.addProperty("App::PropertyFloat", "SizeMin", "Heightmap",
                            "Slider lower limit").SizeMin = 1.0
        if not hasattr(obj, "SizeMax"):
            obj.addProperty("App::PropertyFloat", "SizeMax", "Heightmap",
                            "Slider upper limit").SizeMax = 500.0
        if not hasattr(obj, "DirectionX"):
            obj.addProperty("App::PropertyFloat", "DirectionX", "Heightmap",
                            "Displacement direction X").DirectionX = 0.0
        if not hasattr(obj, "DirectionY"):
            obj.addProperty("App::PropertyFloat", "DirectionY", "Heightmap",
                            "Displacement direction Y").DirectionY = 0.0
        if not hasattr(obj, "DirectionZ"):
            obj.addProperty("App::PropertyFloat", "DirectionZ", "Heightmap",
                            "Displacement direction Z").DirectionZ = 1.0
        if not hasattr(obj, "OriginX"):
            obj.addProperty("App::PropertyFloat", "OriginX", "Heightmap",
                            "Projection origin X").OriginX = 0.0
        if not hasattr(obj, "OriginY"):
            obj.addProperty("App::PropertyFloat", "OriginY", "Heightmap",
                            "Projection origin Y").OriginY = 0.0
        if not hasattr(obj, "OriginZ"):
            obj.addProperty("App::PropertyFloat", "OriginZ", "Heightmap",
                            "Projection origin Z").OriginZ = 0.0
        if not hasattr(obj, "FacingCutoff"):
            obj.addProperty("App::PropertyFloat", "FacingCutoff", "Heightmap",
                            "dot(normal, direction) threshold: -1 = all, 0 = front hemisphere, 0.5 = tight front"
                            ).FacingCutoff = 0.0
        if not hasattr(obj, "Tile"):
            obj.addProperty("App::PropertyBool", "Tile", "Heightmap",
                            "Tile (repeat) the heightmap image across the surface"
                            ).Tile = True

    def _build_field(self, fp):
        from freecad.fields.core.sdf.sdf.heightmap import SdfHeightmapField
        base_field = self._resolve_source_field(fp)
        if not base_field:
            return

        source = fp.Source
        direction = FreeCAD.Vector(fp.DirectionX, fp.DirectionY, fp.DirectionZ)
        if hasattr(fp, "OriginX") and hasattr(fp, "OriginY") and hasattr(fp, "OriginZ"):
            origin = FreeCAD.Vector(fp.OriginX, fp.OriginY, fp.OriginZ)
        else:
            origin = source.Placement.Base if hasattr(source, "Placement") else FreeCAD.Vector(0, 0, 0)

        self.SdfField = SdfHeightmapField(
            base_field,
            image_path=getattr(fp, "ImagePath", "") or "",
            amplitude=fp.Amplitude,
            size_x=fp.SizeX,
            size_y=fp.SizeY,
            direction=direction,
            origin=origin,
            facing_cutoff=getattr(fp, "FacingCutoff", -1.0),
            tile=getattr(fp, "Tile", True),
        )

    def _register_gpu_resources(self, fp, renderer, label):
        """The bake shader samples the image, so the texture must be uploaded
        before the field it belongs to is registered."""
        renderer.register_heightmap_texture(label, getattr(fp, "ImagePath", "") or "")


def create_heightmap_modifier(name, source_obj):
    doc = FreeCAD.activeDocument()
    obj = doc.addObject("Part::FeaturePython", name)
    FldHeightmapProxy(obj)
    obj.Source = source_obj
    if hasattr(source_obj, "Placement") and source_obj.Placement:
        base = source_obj.Placement.Base
        obj.OriginX = base.x
        obj.OriginY = base.y
        obj.OriginZ = base.z

    if FreeCAD.GuiUp:
        FldViewProvider(obj.ViewObject)
        if hasattr(obj, "ViewObject") and obj.ViewObject:
            try:
                apply_default_view_settings(obj.ViewObject, (0.4, 0.8, 1.0))
            except Exception as e:
                from freecad.fields.core import fld_logger
                fld_logger.debug(f"create_heightmap_modifier: ViewObject appearance setup failed: {e}")

    obj.touch()   # recompute belongs to the caller -- see CommandFldModifierBase (IF-016)
    return obj
