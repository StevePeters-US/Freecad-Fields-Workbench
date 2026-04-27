with open("tools/primitive_tool.py", "r") as f:
    content = f.read()

bad_str = """        if self._dragging_idx in ('center', 'rot'):
            super()._drag_update()
            return

        if self._dragging_idx in ('center', 'rot'):
            super()._drag_update()
            return"""

good_str = """        if self._dragging_idx in ('center', 'rot'):
            super()._drag_update()
            return"""

content = content.replace(bad_str, good_str)

with open("tools/primitive_tool.py", "w") as f:
    f.write(content)
