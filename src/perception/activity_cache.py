"""活动去重缓存：相同屏幕活动在 TTL 内不重复触发分析/广播。"""
from __future__ import annotations

import time


class ActivityCache:
    def __init__(self, ttl_s: float = 60.0) -> None:
        self._ttl = ttl_s
        self._last_summary: str = ""
        self._last_time: float = 0.0

    def should_skip(self, summary: str) -> bool:
        """相同 summary 且未过期 → 跳过。"""
        if not summary:
            return True
        if summary == self._last_summary and (time.time() - self._last_time) < self._ttl:
            return True
        return False

    def set(self, summary: str) -> None:
        self._last_summary = summary
        self._last_time = time.time()

    @property
    def current(self) -> str:
        if self._last_summary and (time.time() - self._last_time) < self._ttl:
            return self._last_summary
        return ""
