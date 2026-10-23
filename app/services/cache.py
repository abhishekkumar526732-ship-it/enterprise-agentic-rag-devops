import json
import time
import hashlib
import logging
from typing import Any, Dict, List, Optional
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class RedisCacheService:
    """Enterprise Redis caching service with fallback in-memory TTL cache."""

    def __init__(self):
        self.enabled = settings.redis_enabled
        self.client = None
        self._in_memory_cache: Dict[str, Dict[str, Any]] = {}
        self._rate_limits: Dict[str, List[float]] = {}
        self._sessions: Dict[str, List[Dict[str, str]]] = {}

        if self.enabled:
            try:
                import redis
                client = redis.Redis.from_url(
                    settings.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=2.0,
                    socket_timeout=2.0,
                )
                client.ping()
                self.client = client
                logger.info(f"Connected to Redis at {settings.redis_url}")
            except Exception as exc:
                logger.warning(
                    f"Redis unavailable ({exc}). Using in-memory fallback cache (no crash)."
                )
                self.client = None

    def _hash_key(self, question: str, user_role: str) -> str:
        norm = question.strip().lower()
        q_hash = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
        return f"hr:cache:{user_role}:{q_hash}"

    # --- Response Caching ---

    def get_response(self, question: str, user_role: str) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None

        key = self._hash_key(question, user_role)
        try:
            if self.client:
                data = self.client.get(key)
                if data:
                    logger.info(f"Redis Cache HIT for key: {key}")
                    return json.loads(data)
            else:
                # In-memory fallback check
                entry = self._in_memory_cache.get(key)
                if entry and entry["expires_at"] > time.time():
                    logger.info(f"Memory Cache HIT for key: {key}")
                    return entry["data"]
                elif entry:
                    del self._in_memory_cache[key]
        except Exception as e:
            logger.warning(f"Cache get error: {e}")

        return None

    def set_response(
        self,
        question: str,
        user_role: str,
        data: Dict[str, Any],
        ttl: Optional[int] = None,
    ) -> None:
        if not self.enabled:
            return

        ttl = ttl or settings.redis_cache_ttl_seconds
        key = self._hash_key(question, user_role)
        payload = json.dumps(data)

        try:
            if self.client:
                self.client.setex(key, ttl, payload)
            else:
                self._in_memory_cache[key] = {
                    "data": data,
                    "expires_at": time.time() + ttl,
                }
        except Exception as e:
            logger.warning(f"Cache set error: {e}")

    # --- Rate Limiting ---

    def check_rate_limit(self, user_id: str, limit: Optional[int] = None) -> bool:
        """Returns True if within rate limit, False if exceeded."""
        if not self.enabled:
            return True

        max_req = limit or settings.redis_rate_limit_per_minute
        now = time.time()
        key = f"hr:rate:{user_id}"

        try:
            if self.client:
                pipeline = self.client.pipeline()
                pipeline.zremrangebyscore(key, 0, now - 60)
                pipeline.zcard(key)
                pipeline.zadd(key, {str(now): now})
                pipeline.expire(key, 60)
                _, count, _, _ = pipeline.execute()
                return count <= max_req
            else:
                # In-memory sliding window rate limiter
                timestamps = self._rate_limits.get(user_id, [])
                timestamps = [t for t in timestamps if t > now - 60]
                timestamps.append(now)
                self._rate_limits[user_id] = timestamps
                return len(timestamps) <= max_req
        except Exception as e:
            logger.warning(f"Rate limiter error: {e}")
            return True

    # --- Multi-Turn Session Memory ---

    def add_session_message(self, session_id: str, role: str, content: str) -> None:
        """Stores dialogue turns for conversational context."""
        key = f"hr:session:{session_id}"
        msg = json.dumps({"role": role, "content": content, "timestamp": time.time()})

        try:
            if self.client:
                self.client.rpush(key, msg)
                self.client.expire(key, 86400)  # 24 hour session TTL
            else:
                if session_id not in self._sessions:
                    self._sessions[session_id] = []
                self._sessions[session_id].append({"role": role, "content": content})
        except Exception as e:
            logger.warning(f"Session memory error: {e}")

    def get_session_history(self, session_id: str, max_turns: int = 6) -> List[Dict[str, str]]:
        key = f"hr:session:{session_id}"
        try:
            if self.client:
                items = self.client.lrange(key, -max_turns, -1)
                return [json.loads(x) for x in items]
            else:
                return self._sessions.get(session_id, [])[-max_turns:]
        except Exception as e:
            logger.warning(f"Session history error: {e}")
            return []

    def clear(self) -> None:
        try:
            if self.client:
                keys = self.client.keys("hr:cache:*")
                if keys:
                    self.client.delete(*keys)
            self._in_memory_cache.clear()
        except Exception as e:
            logger.warning(f"Cache clear error: {e}")


_cache_service: Optional[RedisCacheService] = None

def get_cache_service() -> RedisCacheService:
    global _cache_service
    if _cache_service is None:
        _cache_service = RedisCacheService()
    return _cache_service
