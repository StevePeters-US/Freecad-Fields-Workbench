import os
import FreeCAD

LOG_PATH = os.path.expanduser("~/sdf_debug.log")

def _log(level, msg):
    """Internal helper to write to file and console."""
    formatted = f"[{level}] {msg}"
    try:
        with open(LOG_PATH, "a") as f:
            f.write(formatted + "\n")
            f.flush()
    except:
        pass
    # Print to FreeCAD console as well
    FreeCAD.Console.PrintLog(formatted + "\n")

def info(msg):
    _log("INFO", msg)

def debug(msg):
    _log("DEBUG", msg)

def warn(msg):
    _log("WARN", msg)

def error(msg):
    _log("ERROR", msg)
