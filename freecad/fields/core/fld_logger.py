# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import os
import time
import traceback

_throttle_times = {}  # key -> last_emit_time

_PARAM_PATH = "User parameter:FCFields"
def get_enable_crash_log():
    try:
        return FreeCAD.ParamGet(_PARAM_PATH).GetBool("EnableCrashLog", True) # Default True
    except Exception:
        return True

def set_enable_crash_log(val):
    try:
        FreeCAD.ParamGet(_PARAM_PATH).SetBool("EnableCrashLog", bool(val))
    except Exception:
        pass  # Ignore parameter store exceptions during logger setup

# ── Debug logging categories ────────────────────────────────────────────────
# debug() output is gated by (a) a master switch and (b) a per-category switch.
# Render debug is very verbose, so it defaults OFF; everything else defaults ON.
# Values are cached (see _refresh_debug_cache) so the hot paths that call
# debug() every frame do not hit FreeCAD.ParamGet on each call.
DEBUG_CATEGORIES = ("general", "render", "cage", "sdf", "freecad.fields.tools", "input")
_DEBUG_CATEGORY_DEFAULTS = {
    "general": True,
    "render":  False,   # very verbose; off by default (see settings to re-enable)
    "cage":    True,
    "sdf":     True,
    "freecad.fields.tools":   True,
    "input":   True,
}

_debug_master = None          # cached master switch (None = not yet loaded)
_debug_cats = None            # cached {category: bool}

def get_enable_debug_log():
    """Master debug switch. When False, no debug()/debug_throttled() output."""
    try:
        return FreeCAD.ParamGet(_PARAM_PATH).GetBool("EnableDebugLog", True)
    except Exception:
        return True

def set_enable_debug_log(val):
    try:
        FreeCAD.ParamGet(_PARAM_PATH).SetBool("EnableDebugLog", bool(val))
    except Exception:
        pass  # Ignore parameter store exceptions during debug switch update
    _refresh_debug_cache()

def get_debug_category(cat):
    default = _DEBUG_CATEGORY_DEFAULTS.get(cat, True)
    try:
        return FreeCAD.ParamGet(_PARAM_PATH).GetBool(f"DebugCat_{cat}", default)
    except Exception:
        return default

def set_debug_category(cat, val):
    try:
        FreeCAD.ParamGet(_PARAM_PATH).SetBool(f"DebugCat_{cat}", bool(val))
    except Exception:
        pass  # Ignore parameter store exceptions during category switch update
    _refresh_debug_cache()

def _refresh_debug_cache():
    """Reload cached debug switches from params (call after settings change)."""
    global _debug_master, _debug_cats
    _debug_master = get_enable_debug_log()
    _debug_cats = {c: get_debug_category(c) for c in DEBUG_CATEGORIES}

def refresh_debug_settings():
    """Public alias — the settings dialog calls this on accept."""
    _refresh_debug_cache()

def _debug_active(category):
    if _debug_master is None:
        _refresh_debug_cache()
    if not _debug_master:
        return False
    return _debug_cats.get(category, True)

def get_log_path():
    try:
        app_data = FreeCAD.ConfigGet("UserAppData")
        return os.path.join(app_data, "Fields.log")
    except Exception:
        return os.path.expanduser("~/Fields.log")

def _log(level, msg):
    """Internal helper to write to file and console."""
    # Handle multi-line messages (e.g. tracebacks)
    text = str(msg)
    lines = text.splitlines()
    
    # Always print to FreeCAD console
    # Use Message for INFO/DEBUG, Warning for WARN, Error for ERROR
    for line in lines:
        formatted = f"[{level}] {line}" if level else line
        
        if level == "ERROR":
            FreeCAD.Console.PrintError(formatted + "\n")
        elif level == "WARN":
            FreeCAD.Console.PrintWarning(formatted + "\n")
        elif level == "LOG":
            FreeCAD.Console.PrintLog(formatted + "\n")
        else:
            FreeCAD.Console.PrintMessage(formatted + "\n")

    # Log to file if enabled OR if it's an ERROR (crash investigation)
    if get_enable_crash_log() or level == "ERROR":
        try:
            log_p = get_log_path()
            d = os.path.dirname(log_p)
            if d and not os.path.exists(d):
                os.makedirs(d, exist_ok=True)
            
            with open(log_p, "a", encoding="utf-8") as f:
                for line in lines:
                    formatted = f"[{level}] {line}" if level else line
                    f.write(formatted + "\n")
                f.flush()
        except Exception:
            pass  # Ignore file write failures inside logger to prevent log recursion

def log(msg):
    """Generic log message (FreeCAD.Console.PrintLog equivalent)."""
    _log("LOG", msg)

def info(msg):
    _log("INFO", msg)

def debug(msg, category="general"):
    if not _debug_active(category):
        return
    _log("DEBUG", msg)

def warn(msg):
    _log("WARN", msg)

def error(msg):
    _log("ERROR", msg)

def exception(msg_header=""):
    """Logs the current exception traceback as an ERROR."""
    full_msg = f"{msg_header}\n{traceback.format_exc()}"
    _log("ERROR", full_msg)

def debug_throttled(key, msg, interval=0.5, category="general"):
    """Like debug(), but only emits once per `interval` seconds for a given key."""
    if not _debug_active(category):
        return
    now = time.monotonic()
    if now - _throttle_times.get(key, 0) >= interval:
        _throttle_times[key] = now
        _log("DEBUG", msg)


def render_debug(msg):
    """debug() tagged with the (verbose, default-off) 'render' category."""
    debug(msg, category="render")


def render_debug_throttled(key, msg, interval=0.5):
    """debug_throttled() tagged with the 'render' category."""
    debug_throttled(key, msg, interval=interval, category="render")
