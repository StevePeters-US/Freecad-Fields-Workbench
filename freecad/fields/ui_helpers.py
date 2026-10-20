# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Shared Qt helpers for Fields task panels and commands (qt_ui_conventions, tooltips)."""

import os
import re

import FreeCADGui
from PySide import QtGui, QtWidgets

from freecad.fields.core import fld_logger

TOOLTIP_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "Resources", "tooltips")
_SVG_WIDTH = re.compile(r'<svg\b[^>]*?\swidth="(\d+)"')


def QT_TRANSLATE_NOOP(context, text):
    """Mark text for the .ts extractor and return it unchanged.

    A marker, not a translator: rich_tooltip (and Qt's display path) translates later.
    """
    return text


def rich_tooltip(context, text, image=None):
    """Translate a tooltip source string and return it as rich text.

    Args:
        context: translation context, the same one given to QT_TRANSLATE_NOOP.
        text: the QT_TRANSLATE_NOOP-marked source. Each "\n" starts a new line.
        image: optional diagram file name in Resources/tooltips/.

    Returns:
        HTML with one <p> per line and the diagram last. Qt word-wraps it. With a
        diagram, the tooltip is exactly the diagram's width and the text wraps to it.
    """
    translated = QtWidgets.QApplication.translate(context, text)
    parts = ["<p style='margin:0 0 4px 0'>%s</p>" % line for line in translated.split("\n") if line]
    if not image:
        return "".join(parts)
    path = os.path.normpath(os.path.join(TOOLTIP_DIR, image))
    if not os.path.isfile(path):
        # Qt draws a broken-image box for a missing file and says nothing.
        fld_logger.warn(f"[ui_helpers] Tooltip image missing: {path}")
        return "".join(parts)
    parts.append("<p style='margin:4px 0 0 0'><img src='%s'></p>" % path.replace("\\", "/"))
    with open(path, encoding="utf-8") as f:
        match = _SVG_WIDTH.search(f.read())
    if match is None:
        fld_logger.warn(f"[ui_helpers] Tooltip image has no width attribute: {path}")
        return "".join(parts)
    return "<table width='%s' cellspacing='0' cellpadding='0'><tr><td>%s</td></tr></table>" % (
        match.group(1), "".join(parts))


def make_double_spinbox(value, minimum, maximum, step=None, decimals=None, suffix="", tooltip=None):
    """A configured QDoubleSpinBox (qt_ui_conventions).

    Decimals are set before the range, and the range before the value, so neither the
    range nor the value is rounded or clamped by Qt's defaults (2 decimals, 0-99.99).
    """
    spin = QtWidgets.QDoubleSpinBox()
    if decimals is not None:
        spin.setDecimals(decimals)
    spin.setRange(minimum, maximum)
    if step is not None:
        spin.setSingleStep(step)
    if suffix:
        spin.setSuffix(suffix)
    spin.setValue(value)
    if tooltip:
        spin.setToolTip(tooltip)
    return spin
def make_int_spinbox(value, minimum, maximum, step=None, suffix="", tooltip=None):
    """A configured QSpinBox (qt_ui_conventions).

    The range is set before the value, so the value is not clamped by Qt's defaults (0-99).
    """
    spin = QtWidgets.QSpinBox()
    spin.setRange(minimum, maximum)
    if step is not None:
        spin.setSingleStep(step)
    if suffix:
        spin.setSuffix(suffix)
    spin.setValue(value)
    if tooltip:
        spin.setToolTip(tooltip)
    return spin

def set_status_tips(command_ids):
    """Give each command with a rich ToolTip a plain status-bar tip: its first line.

    FreeCAD copies ToolTip into the status bar and shows HTML there as raw tags, and it
    ignores a Python command's StatusTip key. Call from Workbench.Activated(), after the
    toolbars exist.
    """
    for cmd_id in command_ids:
        command = FreeCADGui.Command.get(cmd_id)
        tip = command.getInfo()["toolTip"]
        if not tip.startswith(("<p", "<table")):
            continue
        document = QtGui.QTextDocument()
        document.setHtml(tip)
        # A table starts the plain text with an empty line.
        first_line = next(line for line in document.toPlainText().split("\n") if line.strip())
        for action in command.getAction():
            action.setStatusTip(first_line)
