# SPDX-License-Identifier: CC-BY-NC-SA-4.0
from freecad.fields.core.sdf.sdf_constants import SURFACE_ID_UNSET


class FldModifierProxyBase:
    """
    Common base for SDF modifier proxy objects (Twist, Bend, Lattice, Noise,
    Noise2D, Heightmap). Handles the shared ShapeType/Source/Group property
    setup, lazy SdfField caching, and upstream source-field resolution.
    """

    SHAPE_TYPE_TOOLTIP = "Type"
    SOURCE_GROUP = "Sdf"
    SOURCE_TOOLTIP = "Base SDF object"

    def __init__(self, obj):
        obj.Proxy = self
        if not hasattr(obj, "ShapeType"):
            obj.addProperty("App::PropertyString", "ShapeType", "Fields", self.SHAPE_TYPE_TOOLTIP)
        obj.ShapeType = "sdf"

        if not hasattr(obj, "SurfaceIdBase"):
            obj.addProperty("App::PropertyInteger", "SurfaceIdBase", "Fields", "Monotonic surface ID base")
            from freecad.fields.core.objects.fld_surface_id import allocate_surface_ids
            doc = getattr(obj, "Document", None)
            obj.SurfaceIdBase = allocate_surface_ids(doc, 1) if doc else SURFACE_ID_UNSET

        if not hasattr(obj, "Source"):
            obj.addProperty("App::PropertyLink", "Source", self.SOURCE_GROUP, self.SOURCE_TOOLTIP)
        if not hasattr(obj, "Group"):
            obj.addProperty("App::PropertyEnumeration", "Group", "Sdf", "Rendering group")
            obj.Group = ["Additive", "Subtractive"]
            obj.Group = "Additive"

        self._ensure_enabled(obj)

        self.SdfField = None

    @staticmethod
    def _ensure_enabled(obj):
        """Add the bypass checkbox if it is missing.

        Called from `__init__` for new objects and from `onDocumentRestored` for
        documents saved before the property existed. Without the restore half, an
        older modifier reloads with no `Enabled` at all: reads are safe because every
        one of them goes through `getattr(fp, "Enabled", True)`, but the panel's write
        lands on a plain Python attribute instead of a document property, so nothing
        is touched, no recompute runs, and the checkbox is a silent no-op.
        """
        if not hasattr(obj, "Enabled"):
            obj.addProperty("App::PropertyBool", "Enabled", "Fields",
                            "Uncheck to bypass this modifier")
            obj.Enabled = True

    def __getstate__(self):
        """Nothing on the proxy is persisted; the document properties are the state.

        FreeCAD serialises a Proxy by JSON-dumping whatever this returns, and Python
        3.11+ supplies a default that returns `__dict__` -- which here holds live
        SdfField objects and the `_extrude_cache`. That raises "Object of type
        SdfCageDeformField is not JSON serializable" during save, and the proxy is
        then written out broken. Every subclass rebuilds its field from the document
        properties in `_build_field`, so an empty state restores correctly: after
        `__setstate__` the instance has no `SdfField` attribute at all and
        `get_sdf_field` lazily rebuilds it.

        Any subclass that adds a cache or a field to `self` is covered by this and
        must NOT override it to persist that value -- if something genuinely has to
        survive a save, it belongs in a document property.
        """
        return {}

    def __setstate__(self, state):
        pass

    def onDocumentRestored(self, obj):
        if not hasattr(obj, "SurfaceIdBase"):
            obj.addProperty("App::PropertyInteger", "SurfaceIdBase", "Fields", "Monotonic surface ID base")
            from freecad.fields.core.objects.fld_surface_id import allocate_surface_ids
            doc = getattr(obj, "Document", None)
            obj.SurfaceIdBase = allocate_surface_ids(doc, 1) if doc else SURFACE_ID_UNSET
        if getattr(obj, "Group", None) in ("Group 1", "Group 2"):
            old_val = obj.Group
            obj.Group = ["Additive", "Subtractive"]
            obj.Group = "Subtractive" if old_val == "Group 2" else "Additive"
        self._ensure_enabled(obj)

    def get_sdf_field(self, fp):
        if not getattr(fp, "Enabled", True):
            field = self._resolve_source_field(fp)   # bypass: expose source unchanged
        else:
            field = getattr(self, "SdfField", None)
            if field is None:
                self._build_field(fp)
                field = getattr(self, "SdfField", None)
        from freecad.fields.core.objects.fld_surface_id import stamp_surface_id
        return stamp_surface_id(fp, field)

    _NEVER_BUILT = object()

    # False: the subclass clears SdfField from onChanged, so a rebuild is only
    # needed when the upstream input changed. True: the subclass has no onChanged
    # (the noise and heightmap proxies), so the recompute is its only invalidation
    # signal and every execute must rebuild.
    ALWAYS_REBUILD = False

    def execute(self, fp):
        """Rebuild only if the field is missing or its upstream input changed.

        The deform subclasses clear `SdfField` from `onChanged` for each property
        they read, so a parameter edit still rebuilds; what this skips is the
        recompute that FreeCAD runs for unrelated reasons, which used to rebuild
        the whole field for nothing. Subclasses with no `onChanged` set
        ALWAYS_REBUILD and opt out of the skip entirely.

        The comparison is against `_built_from` -- what the last build actually
        consumed -- and NOT against `self.SdfField.source`. `.source` is a
        per-class implementation detail, not a contract: `UnionField` calls it
        `a`/`b`, `SdfNoiseField` has none, and `SdfCageDeformField.source` is the
        *extruded* source (`SdfExtrusionStackField(base, ...)`), never the base
        field this method resolves. Keying on it therefore skipped nothing for an
        extruded cage -- the one case the CW- work is about -- while silently
        varying by boolean mode on any modifier that booleans its own lobe
        into the source.
        """
        base_field = self._resolve_source_field(fp)
        if (self.ALWAYS_REBUILD
                or getattr(self, "SdfField", None) is None
                or getattr(self, "_built_from", self._NEVER_BUILT) is not base_field):
            if self.ALWAYS_REBUILD:
                self.SdfField = None
            if getattr(fp, "Enabled", True):
                self._build_field(fp)
            else:
                # Bypassed: _publish hands the renderer the *source* field, so building
                # this modifier's own would only be thrown away -- and for a lattice or
                # a deform cage that is the expensive part. Re-enabling clears SdfField
                # through onChanged (or ALWAYS_REBUILD forces it), so the build happens
                # then, on the recompute that makes it visible again.
                self.SdfField = None
            self._built_from = base_field
            self._publish(fp)

    def _publish(self, fp):
        """Hand the field just built to the scene renderer.

        Nothing else does it. FldViewProvider.updateData only forwards to the render
        strategy, and SdfRendererStrategy.update registers a field for `prop ==
        "Shape"` or `prop is None` only -- so a modifier whose execute never assigns
        Shape is never drawn, and the one `prop is None` call (from the strategy's
        setup) happens inside the FldViewProvider constructor, before the factory has
        assigned Source, when there is still no field to register.

        The noise, 2D-noise and heightmap proxies each carried a private copy of
        this. Twist, Bend, Lattice and Array carried none, which is why creating one
        hid the source object and put nothing in its place.
        """
        field = self.get_sdf_field(fp)
        vp = getattr(fp, "ViewObject", None)
        vp_proxy = getattr(vp, "Proxy", None) if vp else None
        strategy = getattr(vp_proxy, "_strategy", None) if vp_proxy else None
        label = getattr(strategy, "label", None) if strategy else None
        if field is not None and label:
            from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
            renderer = FldSceneVoxelRenderer.get_instance()
            self._register_gpu_resources(fp, renderer, label)
            renderer.update_field(label, field)

        import Part
        fp.Shape = Part.Shape()

    def _register_gpu_resources(self, fp, renderer, label):
        """Hook for GPU state a field needs before it can be baked -- the heightmap's
        image texture. Runs immediately before update_field(). Default: nothing."""

    def _build_field(self, fp):
        raise NotImplementedError

    @staticmethod
    def _resolve_source_field(fp):
        """Returns the upstream SdfField from fp.Source, or None if unavailable."""
        source = getattr(fp, "Source", None)
        if not source:
            return None
        proxy = getattr(source, "Proxy", None)
        if proxy is None:
            return None
        return (proxy.get_sdf_field(source) if hasattr(proxy, "get_sdf_field")
                else getattr(proxy, "SdfField", None))
