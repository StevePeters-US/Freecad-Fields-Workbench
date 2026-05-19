import FreeCAD
from core.dm_object import DMViewProvider

class DMNoiseProxy:
    """
    Proxy object for a parametric Noise modifier.
    Wraps a Source SDF object and applies SdfNoiseField.
    """
    def __init__(self, obj):
        obj.Proxy = self
        if not hasattr(obj, "ShapeType"):
            obj.addProperty("App::PropertyString", "ShapeType", "DM", "Type of primitive")
        obj.ShapeType = "sdf"
        
        if not hasattr(obj, "Source"):
            obj.addProperty("App::PropertyLink", "Source", "Noise", "Base SDF object")
        if not hasattr(obj, "Amplitude"):
            obj.addProperty("App::PropertyFloat", "Amplitude", "Noise", "Noise amplitude").Amplitude = 5.0
        if not hasattr(obj, "Frequency"):
            obj.addProperty("App::PropertyFloat", "Frequency", "Noise", "Noise frequency").Frequency = 0.1
        if not hasattr(obj, "Group"):
            obj.addProperty("App::PropertyEnumeration", "Group", "Sdf", "Rendering group")
            obj.Group = ["Group 1", "Group 2"]
            obj.Group = "Group 1"
            
        self.SdfField = None

    def get_sdf_field(self, fp):
        """Return the noise SdfField, reconstructing from source if needed."""
        field = getattr(self, "SdfField", None)
        if field is None:
            self._build_field(fp)
            field = getattr(self, "SdfField", None)
        return field

    def _build_field(self, fp):
        from core.sdf.sdf.noise import SdfNoiseField
        source = getattr(fp, "Source", None)
        if not source:
            return
        proxy = getattr(source, "Proxy", None)
        if proxy is None:
            return
        base_field = (proxy.get_sdf_field(source) if hasattr(proxy, "get_sdf_field")
                      else getattr(proxy, "SdfField", None))
        if base_field:
            self.SdfField = SdfNoiseField(base_field, fp.Amplitude, fp.Frequency)

    def execute(self, fp):
        self.SdfField = None  # force rebuild from current properties
        self._build_field(fp)

        field = getattr(self, "SdfField", None)
        if field is not None:
            vp = getattr(fp, "ViewObject", None)
            vp_proxy = getattr(vp, "Proxy", None) if vp else None
            strategy = getattr(vp_proxy, "_strategy", None) if vp_proxy else None
            if strategy and hasattr(strategy, "label") and strategy.label:
                from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
                DMSceneRayMarchRenderer.get_instance().update_field(strategy.label, field)

        import Part
        fp.Shape = Part.Shape()

    def __getstate__(self):
        return {}

    def __setstate__(self, state):
        pass

def create_noise_modifier(name, source_obj):
    doc = FreeCAD.activeDocument()
    obj = doc.addObject("Part::FeaturePython", name)
    DMNoiseProxy(obj)
    obj.Source = source_obj
    
    if FreeCAD.GuiUp:
        DMViewProvider(obj.ViewObject)
        if hasattr(obj, "ViewObject") and obj.ViewObject:
            try:
                obj.ViewObject.Visibility = True
                obj.ViewObject.ShapeColor = (1.0, 0.5, 0.0) # default orange
                obj.ViewObject.DisplayMode = "Shaded"
            except Exception:
                pass
                
    obj.touch()
    doc.recompute()
    return obj
