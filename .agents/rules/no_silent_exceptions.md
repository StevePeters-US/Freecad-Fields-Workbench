---
description: Ensure exceptions and logical failures are logged, not silently ignored.
---

# No Silent Exceptions

When writing `try...except` blocks or handling any logic operations that might fail, you **MUST NOT** fail silently. 
All exceptions or significant logical failures must be logged using the project's logging mechanism (e.g., `from . import dm_logger` and then `dm_logger.debug()` or `dm_logger.error()`).

Do not use empty `except:` or `except Exception:` blocks with only a `pass` statement, unless explicitly requested by the user. If an exception is caught and ignored for control flow reasons, it must still be logged so that debugging is possible.
