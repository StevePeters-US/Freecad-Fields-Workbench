# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Phase timing for the interactive paths.

The drag profiler in ``tools/fld_base.py`` covers what happens between the mouse
going down and coming up. It says nothing about the work either side of that:
committing an extrusion, growing a cage, the picking octree a click rebuilds.
Those show up in a log only as a gap between two timestamps.

``phase()`` closes that gap. It reports wall time and, separately, how much of
it was SDF evaluation, because the two lead to different fixes: evaluation cost
is the field's shape, everything else is ours.

    with phase("commit extrusion"):
        ...

Logs at debug under the given category, so it is free when the category is off
except for the two ``perf_counter`` calls.
"""

import time
from contextlib import contextmanager

from freecad.fields.core import fld_logger


@contextmanager
def phase(name, category="freecad.fields.tools", min_ms=0.0, sink=None):
    """Time a block and log it with its SDF-evaluation share.

    Args:
        name: What the block does, as it should read in the log.
        category: fld_logger debug category to log under.
        min_ms: Skip the log below this duration. Use for blocks that run often
            and are usually trivial, so the log only shows the times they were
            not.
        sink: A list to append the finished line to *instead of* logging it.
            Use when the block is a child of a gated parent: a child cannot know
            whether its parent will clear `min_ms`, so it hands the line up and
            the parent decides. Without this, a parent at `min_ms=20` and
            children at `min_ms=0` produce orphan child lines with no parent to
            attribute them to -- which is exactly what CW-001's first pass did,
            775 lines of `0.0ms` and not one parent. Flush with `flush(sink)`.

    Yields:
        A dict that gains "seconds" on exit, for a caller that wants the number
        as well as the log line.
    """
    from freecad.fields.core.sdf import field_eval

    result = {}
    before = field_eval.eval_stats_snapshot()
    t0 = time.perf_counter()
    try:
        yield result
    finally:
        elapsed = time.perf_counter() - t0
        result["seconds"] = elapsed
        if elapsed * 1000.0 >= min_ms:
            ev = field_eval.eval_stats_delta(before)
            detail = ""
            if ev["calls"]:
                detail = (f" — {ev['seconds']*1000:.1f}ms of SDF eval "
                          f"({ev['calls']} call(s), {ev['points']} pts)")
            line = f"[PHASE] {name}: {elapsed*1000:.1f}ms{detail}"
            if sink is None:
                fld_logger.debug(line, category=category)
            else:
                sink.append(line)


def flush(sink, category="freecad.fields.tools"):
    """Log every line a `sink` collected, then empty it."""
    for line in sink:
        fld_logger.debug(line, category=category)
    del sink[:]
