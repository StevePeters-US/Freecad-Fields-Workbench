# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Cache for compiled compute programs, keyed by GLSL source text or key string.

Extracted from FldSceneVoxelRenderer (RS-007). Every method must be
called from inside a GL context: `get` may compile, and eviction destroys a
program.
"""
import time
from freecad.fields.core import fld_logger


class ComputeProgramCache:
    def __init__(self, max_entries=8, dump_path="/tmp/cage_shader_debug.glsl"):
        self._progs = {}        # glsl source -> GLProgram
        self._lru = []          # sources, least-recently-used first
        self._max = max_entries
        self._dump_path = dump_path

    def get(self, src, key=None, protected=None):
        """The program for this source or structural key, compiling only if new.

        `key` defaults to `src`. `protected` is the program currently bound;
        eviction must never destroy it.
        """
        from freecad.fields.core.gl.gl_program import GLProgram

        cache_key = key if key is not None else src
        hit = self._progs.get(cache_key)
        if hit is not None:
            self._lru.remove(cache_key)
            self._lru.append(cache_key)
            fld_logger.render_debug(
                f"SceneRayMarch: program cache HIT (key len={len(cache_key) if hasattr(cache_key, '__len__') else 1}, "
                f"{len(self._progs)} cached)")
            return hit

        t_gpu_start = time.perf_counter()
        # Dump source to /tmp for post-hang inspection
        try:
            with open(self._dump_path, "w") as _f:
                _f.write(src)
        except Exception as e:
            fld_logger.render_debug(f"ComputeProgramCache: Failed to dump shader source: {e}")
        prog = GLProgram()
        prog.compile_compute(src)
        t_gpu_total = time.perf_counter() - t_gpu_start
        _msg = (f"SceneRayMarch: GPU compile_compute MISS done in "
                f"{t_gpu_total*1000:.2f}ms (source len={len(src)})")
        if t_gpu_total > 0.05:
            fld_logger.debug(_msg)
        else:
            fld_logger.render_debug(_msg)

        self._progs[cache_key] = prog
        self._lru.append(cache_key)
        while len(self._lru) > self._max:
            old = self._lru.pop(0)
            dead = self._progs.pop(old, None)
            if dead is not None and dead is not protected:
                try:
                    dead.destroy()
                except Exception as e:
                    fld_logger.render_debug(
                        f"SceneRayMarch: evicting cached compute program failed: {e}")
        return prog

    def discard(self, src, key=None):
        """Forget a source or key (used after its compile raised). Does not destroy."""
        cache_key = key if key is not None else src
        self._progs.pop(cache_key, None)
        if cache_key in self._lru:
            self._lru.remove(cache_key)

    def destroy_all(self):
        """Destroy every cached program. GL context required."""
        for prog in list(self._progs.values()):
            try:
                prog.destroy()
            except Exception as e:
                fld_logger.render_debug(f"ComputeProgramCache.destroy_all failed: {e}")
        self._progs.clear()
        self._lru.clear()

    def __len__(self):
        return len(self._progs)
