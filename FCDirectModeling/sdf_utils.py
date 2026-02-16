import FreeCAD
import FreeCADGui
import FCDirectModeling.sdf_renderer as sdf_renderer

class SDFObjectFactory:
    @staticmethod
    def create_sdf_object(doc, name_prefix, proxy_class=None):
        """
        Creates a FeaturePython object with the given name prefix.
        Attaches the proxy class if provided (e.g., SDFBoxFeature).
        """
        obj = doc.addObject("Part::FeaturePython", name_prefix)
        if proxy_class:
            proxy_class(obj)
        return obj

    @staticmethod
    def add_common_properties(obj):
        """
        Adds standard SDF properties to the object: Resolution, Margin, Wireframe settings.
        """
        if not hasattr(obj, "Resolution"):
            obj.addProperty("App::PropertyInteger", "Resolution", "SDF", "Grid resolution").Resolution = 32
        if not hasattr(obj, "Margin"):
            obj.addProperty("App::PropertyFloat", "Margin", "SDF", "Grid margin").Margin = 0.2
            
        # Wireframe Properties
        if not hasattr(obj, "WireframeColor"):
            obj.addProperty("App::PropertyColor", "WireframeColor", "SDF", "Wireframe Color").WireframeColor = (1.0, 1.0, 0.0)
        if not hasattr(obj, "WireframeWidth"):
            obj.addProperty("App::PropertyFloat", "WireframeWidth", "SDF", "Wireframe Width").WireframeWidth = 2.0
        if not hasattr(obj, "ShowVertices"):
            obj.addProperty("App::PropertyBool", "ShowVertices", "SDF", "Show Vertices").ShowVertices = True
        if not hasattr(obj, "VertexSize"):
            obj.addProperty("App::PropertyFloat", "VertexSize", "SDF", "Vertex Size").VertexSize = 5.0

    @staticmethod
    def setup_view_provider(obj, compute_mesh=True):
        """
        Initializes the SDFRenderer ViewProvider.
        Safe to call if GUI is not up (checks FreeCAD.GuiUp).
        :param compute_mesh: If True, forces an update() to generate mesh immediately.
        """
        if FreeCAD.GuiUp:
            vp = sdf_renderer.SDFRenderer(obj.ViewObject)
            if compute_mesh:
                # Force update to ensure mesh is generated immediately
                vp.update()
