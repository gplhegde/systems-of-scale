"""
Redis caching helpers for the API.

Pattern used: Cache-aside (lazy loading)
- Check cache first
- On miss: fetch from DB, store in cache, return
- Invalidate on writes
"""

from django.core.cache import cache

PRODUCT_LIST_KEY = "shop:products:list"
PRODUCT_DETAIL_KEY = "shop:products:detail:{id}"
CACHE_TTL = 60 * 5  # 5 minutes


def get_cached_product_list():
    """Return cached product list or None if not cached."""
    return cache.get(PRODUCT_LIST_KEY)


def set_cached_product_list(data):
    """Cache serialized product list."""
    cache.set(PRODUCT_LIST_KEY, data, timeout=CACHE_TTL)


def get_cached_product(product_id):
    """Return cached product detail or None."""
    key = PRODUCT_DETAIL_KEY.format(id=product_id)
    return cache.get(key)


def set_cached_product(product_id, data):
    """Cache serialized product detail."""
    key = PRODUCT_DETAIL_KEY.format(id=product_id)
    cache.set(key, data, timeout=CACHE_TTL)


def invalidate_product_cache(product_id=None):
    """
    Invalidate product cache on writes.
    Call this whenever a product is created, updated, or deleted.
    """
    cache.delete(PRODUCT_LIST_KEY)
    if product_id:
        cache.delete(PRODUCT_DETAIL_KEY.format(id=product_id))
