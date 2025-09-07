import os, time
from fastapi import Header, HTTPException
import redis as redis_lib

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
_API_KEYS = {k.strip() for k in os.getenv("API_KEYS", "devkey").split(",") if k.strip()}
_RATE = int(os.getenv("RATE_LIMIT_PER_MIN", "60"))

_redis = None
def _r():
    global _redis
    if _redis is None:
        _redis = redis_lib.from_url(REDIS_URL, decode_responses=True)
    return _redis

async def require_api_key(x_api_key: str = Header(None)):
    # If no keys configured, allow all (dev mode)
    if not _API_KEYS:
        return
    if not x_api_key or x_api_key not in _API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

async def rate_limit(x_api_key: str = Header("anon")):
    try:
        r = _r()
    except Exception:
        return  # if Redis is down, don't block
    bucket = f"rl:{x_api_key}:{int(time.time()//60)}"
    hits = r.incr(bucket)
    if hits == 1:
        r.expire(bucket, 70)  # 1 minute window
    if hits > _RATE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
