# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import importlib

import FreeCAD
import FreeCADGui
from freecad.fields.core import fld_logger


def require_sdf_selection(op_name: str):
    from freecad.fields.core.input.fld_tool_manager import resolve_command_target

    from freecad.fields.core.objects.fld_modifier_stack import is_sdf_object, stack_target

    obj = resolve_command_target()
    if obj is None:
        fld_logger.error(f"{op_name}: please select an SDF object first.")
        return None
    if not is_sdf_object(obj):
        fld_logger.error(f"{op_name}: selected object must be an SDF object.")
        return None

    # Stack onto the top of whatever chain the selection belongs to, not onto the
    # row that happened to be clicked -- see fld_modifier_stack.stack_target. The tree
    # nests each source under its consumer, so selecting the base of an existing
    # chain is easy to do by accident, and wrapping it branches the chain instead of
    # extending it: two modifiers on the same Source, both visible, unioned by the
    # renderer, and the second one appears to have done nothing.
    tail = stack_target(obj)
    if tail is not None and tail is not obj:
        fld_logger.info(f"{op_name}: stacking onto {tail.Label}, the top of "
                       f"{obj.Label}'s modifier chain")
        return tail
    return obj


class CommandFldModifierBase:
    """
    Base for SDF modifier commands (Twist, Bend, Lattice, Noise3D, Noise2D,
    Heightmap): validates an SDF selection, creates the modifier document
    object, hides the source, and opens the edit tool.
    """

    def __init__(self, op_name, name_suffix, pixmap, menu_text, tooltip,
                 creator_module, creator_func, tool_module, tool_class):
        self.op_name = op_name
        self.name_suffix = name_suffix
        self.pixmap = pixmap
        self.menu_text = menu_text
        self.tooltip = tooltip
        self.creator_module = creator_module
        self.creator_func = creator_func
        self.tool_module = tool_module
        self.tool_class = tool_class

    def GetResources(self):
        return {'Pixmap': self.pixmap, 'MenuText': self.menu_text, 'ToolTip': self.tooltip}

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

    def Activated(self):
        obj = require_sdf_selection(self.op_name)
        if obj is None:
            return

        if hasattr(obj, "ViewObject") and obj.ViewObject:
            obj.ViewObject.Visibility = False

        create_modifier = getattr(importlib.import_module(self.creator_module), self.creator_func)
        mod_obj = create_modifier(f"{obj.Name}{self.name_suffix}", obj)

        # The creators only touch (IF-016): a factory has no way to know whether its
        # caller is a command or an event callback, so the recompute lives here, on
        # the one path all seven of them are reached from -- Activated(), which is
        # not inside a callback and must not defer.
        doc = FreeCAD.activeDocument()
        if doc:
            doc.recompute()

        tool_cls = getattr(importlib.import_module(self.tool_module), self.tool_class)
        tool_cls().edit_object_from_creation(mod_obj, obj)


class CommandFldTransformBase:
    """Base for transform-tool commands (Translate, Rotate, Scale).

    These commands do not create a document object — they just launch the
    corresponding interactive tool.  Subclasses supply only menu_text,
    tooltip, tool_class, and optionally accel.
    """

    def __init__(self, menu_text, tooltip, tool_module, tool_class, accel=None):
        self.menu_text = menu_text
        self.tooltip = tooltip
        self.tool_module = tool_module
        self.tool_class = tool_class
        self.accel = accel

    def GetResources(self):
        res = {
            'Pixmap': 'view-unselectable',
            'MenuText': self.menu_text,
            'ToolTip': self.tooltip,
        }
        if self.accel:
            res['Accel'] = self.accel
        return res

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

    def Activated(self):
        tool_cls = getattr(importlib.import_module(self.tool_module), self.tool_class)
        tool_cls()

    def getIsChecked(self):
        from freecad.fields.core.input.fld_tool_manager import FldToolManager
        tool_cls = getattr(importlib.import_module(self.tool_module), self.tool_class)
        active_tool = FldToolManager.get_instance().get_active_tool()
        return isinstance(active_tool, tool_cls)
