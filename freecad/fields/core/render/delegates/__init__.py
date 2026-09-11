# SPDX-License-Identifier: CC-BY-NC-SA-4.0


def add_to_scene(renderer, node):
    """Add `node` to whichever scene-graph parent the renderer currently exposes.

    Every delegate's `setup_coin_overlay` repeated this same
    `if renderer.vis_switch: ... else: renderer.vobj.RootNode.addChild(...)`
    branch (CR-031) -- `vis_switch` is preferred when present (it gates visibility
    without detaching the node), and `vobj.RootNode` is the fallback for delegates
    set up before a switch exists.
    """
    if renderer.vis_switch:
        renderer.vis_switch.addChild(node)
    else:
        renderer.vobj.RootNode.addChild(node)
