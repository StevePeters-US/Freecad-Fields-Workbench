import os
import FreeCAD
import traceback

LOG_PATH = os.path.expanduser("~/dm_debug.log")

# Path to log file
ENABLE_LOG_FILE = os.getenv("DEBUG_DM_CRASH", "0") == "1"

def _log(level, msg):
    """Internal helper to write to file and console."""
    # Handle multi-line messages (e.g. tracebacks)
    lines = str(msg).splitlines()
    for line in lines:
        formatted = f"[{level}] {line}"
        
        # Log to file if enabled OR if it's an ERROR (crash investigation)
        if ENABLE_LOG_FILE or level == "ERROR":
            try:
                # Ensure directory exists if we use a different path
                # For ~/ we assume it exists.
                with open(LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(formatted + "\n")
                    f.flush()
            except:
                pass
            
        # Always print to FreeCAD console
        # Use Message for INFO/DEBUG, Warning for WARN, Error for ERROR
        if level == "ERROR":
            FreeCAD.Console.PrintError(formatted + "\n")
        elif level == "WARN":
            FreeCAD.Console.PrintWarning(formatted + "\n")
        else:
            FreeCAD.Console.PrintMessage(formatted + "\n")

def info(msg):
    _log("INFO", msg)

def debug(msg):
    _log("DEBUG", msg)

def warn(msg):
    _log("WARN", msg)

def error(msg):
    _log("ERROR", msg)

def exception(msg_header=""):
    """Logs the current exception traceback as an ERROR."""
    full_msg = f"{msg_header}\n{traceback.format_exc()}"
    _log("ERROR", full_msg)
