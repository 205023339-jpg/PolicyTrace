"""Redis 客户端封装。"""
from functools import lru_cache

import redis

from config.settings import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    s = get_settings()
    # decode_responses=True，存取都是 str，省得到处 decode
    return redis.Redis.from_url(s.redis_url, decode_responses=True)
