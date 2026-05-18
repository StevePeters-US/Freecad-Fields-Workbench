"""
core/sdf/sdf_cache_manager.py

Singleton cache for SdfOctreeCache instances.
Tools call get() to avoid rebuilding the octree on every operation.
"""
from core import dm_logger


class SdfCacheManager:
    _instance = None
    _cache = {}   # (field_id, leaf_size) → SdfOctreeCache

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get(self, field, leaf_size: float, force_rebuild: bool = False):
        """
        Return a built SdfOctreeCache for field at leaf_size.
        Builds it if not cached. force_rebuild=True ignores the cache.
        """
        from core.sdf.sdf_octree import SdfOctreeCache
        # Use id(field) as part of the key. 
        # Note: id() is only safe if the object is alive.
        # But fields are usually held by FeaturePython objects or tools.
        key = (id(field), round(leaf_size, 6))
        
        if not force_rebuild and key in self._cache:
            return self._cache[key]
            
        dm_logger.debug(f"SdfCacheManager: building octree leaf_size={leaf_size:.3f}mm")
        cache = SdfOctreeCache(field, leaf_size=leaf_size)
        cache.build()
        self._cache[key] = cache
        return cache

    def invalidate(self, field=None):
        """
        Remove all cached octrees for field (or all if field is None).
        Call this when a field's parameters change.
        """
        if field is None:
            self._cache.clear()
            dm_logger.debug("SdfCacheManager: full invalidation")
        else:
            keys_to_remove = [k for k in self._cache if k[0] == id(field)]
            for k in keys_to_remove:
                del self._cache[k]
            if keys_to_remove:
                dm_logger.debug(f"SdfCacheManager: invalidated {len(keys_to_remove)} entries for field {id(field)}")
