import os
import FreeCAD

LOG_PATH = os.path.expanduser("~/sdf_debug.log")

# By default, only log to console. 
# Enable file logging via environment variable if investigating a crash.
ENABLE_LOG_FILE = os.getenv("DEBUG_SDF_CRASH", "0") == "1"

def _log(level, msg):
    """Internal helper to write to file and console."""
    formatted = f"[{level}] {msg}"
    
    if ENABLE_LOG_FILE:
        try:
            with open(LOG_PATH, "a") as f:
                f.write(formatted + "\n")
                f.flush()
        except:
            pass
            
    # Always print to FreeCAD console (use Message to ensure visibility)
    FreeCAD.Console.PrintMessage(formatted + "\n")

def info(msg):
    _log("INFO", msg)

def debug(msg):
    _log("DEBUG", msg)

def warn(msg):
    _log("WARN", msg)

def error(msg):
    _log("ERROR", msg)
