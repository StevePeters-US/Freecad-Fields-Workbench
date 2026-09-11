# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""The formula/noise-expression editor widget.

Split out of fld_gui_utils.py (CR-062) -- one large, domain-specific UI builder
(node-editor button, cheatsheet text, `@param` comment-insertion logic wired to the
noise/SDF formula editor) that was mixed in among that file's generic reusable Qt
widgets (DynamicLimitSlider, NumericLineEdit, etc.). No import dependency on
core/gui/node_editor/ -- the "Visual Node Editor..." button it builds is wired up by
callers (noise_base_tool.py), not here -- so this stays alongside fld_gui_utils.py in
core/input/ rather than moving under core/gui/.
"""
from PySide import QtCore, QtGui, QtWidgets


def make_formula_editor(hint_html):
    """Returns (group_box, plain_text_edit, apply_button, node_editor_button) for the formula display/edit section."""
    group = QtWidgets.QGroupBox("Formula")
    group.setCheckable(True)
    group.setChecked(True)

    group_layout = QtWidgets.QVBoxLayout(group)
    group_layout.setContentsMargins(6, 8, 6, 6)
    group_layout.setSpacing(6)

    content_widget = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(content_widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    group_layout.addWidget(content_widget)

    group.toggled.connect(content_widget.setVisible)

    # Top action / header bar: Node editor button & Cheatsheet info button
    top_bar = QtWidgets.QHBoxLayout()
    top_bar.setContentsMargins(0, 0, 0, 0)

    node_editor_btn = QtWidgets.QPushButton("Visual Node Editor...")
    node_editor_btn.setToolTip("Open visual node graph editor to design noise formulas interactively.")
    node_editor_btn.setStyleSheet("font-weight: bold; padding: 4px;")
    top_bar.addWidget(node_editor_btn, 1)

    cheatsheet_btn = QtWidgets.QToolButton()
    cheatsheet_btn.setText("?")
    cheatsheet_btn.setToolTip("Click to view formula syntax and available functions")
    cheatsheet_btn.setStyleSheet("font-weight: bold; border-radius: 10px; min-width: 20px; min-height: 20px;")
    top_bar.addWidget(cheatsheet_btn)
    layout.addLayout(top_bar)

    # Collapsible hint details (default collapsed to save vertical space)
    hint_box = QtWidgets.QWidget()
    hint_box_layout = QtWidgets.QVBoxLayout(hint_box)
    hint_box_layout.setContentsMargins(4, 4, 4, 4)
    hint = QtWidgets.QLabel(hint_html)
    hint.setWordWrap(True)
    hint.setTextFormat(QtCore.Qt.RichText)
    hint_box_layout.addWidget(hint)
    hint_box.setVisible(False)
    layout.addWidget(hint_box)

    def _toggle_hint():
        hint_box.setVisible(not hint_box.isVisible())
    cheatsheet_btn.clicked.connect(_toggle_hint)

    edit = QtWidgets.QPlainTextEdit()
    font = QtGui.QFont("Monospace")
    font.setStyleHint(QtGui.QFont.TypeWriter)
    font.setPointSize(9)
    edit.setFont(font)
    edit.setMinimumHeight(56)
    edit.setMaximumHeight(96)
    layout.addWidget(edit)

    tooltip_text = (
        "Custom Parameters syntax in comments:\n"
        "  // @param slider <name> <default> <min> <max> [<step>]\n"
        "  // @param float <name> <default> [<min> <max>]\n"
        "  // @param int <name> <default> [<min> <max>]\n\n"
        "Example:\n"
        "  // @param slider speed 1.5 0.0 5.0 0.1\n"
        "  // @param float bias -0.2\n"
        "  sin(p.x * speed) + bias"
    )
    edit.setToolTip(tooltip_text)
    group.setToolTip(tooltip_text)

    btn_layout = QtWidgets.QHBoxLayout()

    float_slider_btn = QtWidgets.QPushButton("+ Float")
    float_slider_btn.setToolTip("Insert a float slider parameter comment and insert variable name at cursor.")
    btn_layout.addWidget(float_slider_btn)

    int_slider_btn = QtWidgets.QPushButton("+ Int")
    int_slider_btn.setToolTip("Insert an int slider parameter comment and insert variable name at cursor.")
    btn_layout.addWidget(int_slider_btn)

    btn_layout.addStretch()

    apply_btn = QtWidgets.QPushButton("Apply")
    apply_btn.setToolTip("Compile and apply formula.")
    apply_btn.setStyleSheet("font-weight: bold;")
    btn_layout.addWidget(apply_btn)

    layout.addLayout(btn_layout)

    def insert_param(ptype, default_name):
        text = edit.toPlainText()
        name = default_name
        counter = 1
        # Find unique name not used in the formula text
        import re
        words = set(re.findall(r'\b\w+\b', text))
        while name in words:
            name = f"{default_name}_{counter}"
            counter += 1

        # Build comment declaration (must end with newline)
        if ptype == "int":
            comment = f"// @param int {name} 5 1 10\n"
        else:
            comment = f"// @param float {name} 1.0 0.0 5.0 0.1\n"

        # Get the current cursor
        cursor = edit.textCursor()
        position = cursor.position()

        # Insert comment at the absolute beginning of the document
        begin_cursor = edit.textCursor()
        begin_cursor.movePosition(QtGui.QTextCursor.Start)
        begin_cursor.insertText(comment)

        # Restore the cursor to its correct original position plus the length of the comment we just inserted
        new_position = position + len(comment)
        cursor.setPosition(new_position)
        cursor.insertText(name)
        edit.setTextCursor(cursor)
        edit.setFocus()

    float_slider_btn.clicked.connect(lambda: insert_param("float", "speed"))
    int_slider_btn.clicked.connect(lambda: insert_param("int", "steps"))

    group.node_editor_btn = node_editor_btn
    group.apply_btn = apply_btn

    return group, edit, apply_btn, node_editor_btn


