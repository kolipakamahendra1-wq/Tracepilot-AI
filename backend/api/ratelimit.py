"""Fixed-window rate limiter. Uses Redis when REDIS_URL is set, else process memory."""
import time


class RateLimiter:
    def __init__(self, limit_per_min: int, redis_url: str | None = None):
        self.limit = limit_per_min
        self.mem: dict[str, tuple[int, int]] = {}
        self.redis = None
        if redis_url:
            import redis

            self.redis = redis.Redis.from_url(redis_url)

    def allow(self, who: str) -> bool:
        window = int(time.time() // 60)
        if self.redis is not None:
            key = f"rl:{who}:{window}"
            pipe = self.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, 70)
            return pipe.execute()[0] <= self.limit
        w, n = self.mem.get(who, (window, 0))
        n = n + 1 if w == window else 1
        self.mem[who] = (window, n)
        return n <= self.limit
