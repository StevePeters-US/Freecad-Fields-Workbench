# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import os
import time

# When this process imported the workbench. run_live compares it to the source files'
# mtimes: a FreeCAD older than its code is stale and must be restarted (INP-200).
LOADED_AT = time.time()

ADDON_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESOURCES_DIR = os.path.join(ADDON_DIR, "Resources")
ICONS_DIR = os.path.join(RESOURCES_DIR, "icons")
