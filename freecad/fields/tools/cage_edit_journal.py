# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Session-scoped undo for a cage edit session.

CageEditTool makes topology edits that are not individually transacted, so a
cancelled session needs one snapshot it can replay wholesale. dumpContent()
serializes every property the object currently has, so a property added after
this file was written is still captured.
"""

import FreeCAD
from freecad.fields.core import fld_logger

_VIEW_PROPERTIES = ("Visibility", "DisplayMode", "ShapeColor",
                    "LineColor", "PointColor", "Transparency")


class CageEditJournal:
    """Snapshot a cage object once per session and replay it on cancel."""

    def __init__(self, document):
        self.document = document
        self.snapshots = {}

    def capture(self, obj):
        """Snapshot *obj* the first time it is seen. Later calls are no-ops."""
        if obj is None or obj.Name in self.snapshots:
            return
        view = getattr(obj, "ViewObject", None)
        appearance = {}
        if view is not None:
            appearance = {name: getattr(view, name)
                          for name in _VIEW_PROPERTIES
                          if name in getattr(view, "PropertiesList", [])
                          and getattr(view, name) is not None}
        self.snapshots[obj.Name] = (obj.dumpContent(0), appearance)

    def restore(self):
        """Replay every snapshot. Safe to call twice; clears itself."""
        doc = self.document
        if doc is None:
            return
        for name, (content, appearance) in self.snapshots.items():
            obj = doc.getObject(name)
            if obj is None:
                fld_logger.warn(f"CageEditJournal: {name} vanished; cannot restore")
                continue
            try:
                obj.restoreContent(content)
                view = getattr(obj, "ViewObject", None)
                if view is not None:
                    for prop, value in appearance.items():
                        setattr(view, prop, value)
                obj.purgeTouched()
            except Exception as e:
                fld_logger.warn(f"CageEditJournal: restoring {name} failed: {e}")
        self.clear()
        doc.recompute()

    def clear(self):
        self.snapshots.clear()
        self.document = None
