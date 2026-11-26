# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

import functools
import ipaddress
import math
import time
import typing
from collections import OrderedDict
from dataclasses import dataclass

NETWORK_KEY_CACHE_SIZE = 4096

t_BasicKey = str | int
t_Key = t_BasicKey | tuple[t_BasicKey, ...]


@dataclass
class _Entry:
    expires_at: float
    count: int


class InMemoryCircuitBreaker:
    """In-memory circuit breaker that allows ``limit`` errors per key before tripping
    and resets after ``timeout`` seconds with no errors for a given key.

    Example use is to limit login attempts per IP or network. A failed login is
    an error, and a tripped circuit breaker prevents future attempts for
    ``timeout`` seconds.
    """

    def __init__(
        self,
        limit: int,
        timeout: float,
        *,
        max_keys: int = 10_000,
        clock: typing.Callable[[], float] = time.monotonic,
    ):
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_keys < 1:
            raise ValueError("max_keys must be at least 1")
        self.limit = limit
        self.timeout = timeout
        self.max_keys = max_keys
        self.clock = clock
        self._entries: OrderedDict[t_Key, _Entry] = OrderedDict()

    def retry_after(self, key: t_Key) -> int:
        """Return 0 if not tripped, or seconds until the key is reset."""
        now = self.clock()
        entry = self._get(key, now)
        if entry and entry.count >= self.limit:
            return max(1, math.ceil(entry.expires_at - now))
        return 0

    def record_failure(self, key: t_Key) -> bool:
        """Record a failure for this key, return true if it is tripped."""
        now = self.clock()
        entry = self._get(key, now)
        if entry:
            entry.count += 1
            entry.expires_at = now + self.timeout
        else:
            self._entries[key] = entry = _Entry(now + self.timeout, 1)
            while len(self._entries) > self.max_keys:
                self._entries.popitem(last=False)
        return entry.count >= self.limit

    def clear(self, key: t_Key) -> None:
        """Reset a key."""
        self._entries.pop(key, None)

    def _get(self, key: t_Key, now: float) -> _Entry | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= now:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return entry


@functools.lru_cache(maxsize=NETWORK_KEY_CACHE_SIZE)
def network_key(host: str | None) -> str:
    """Return an IPv4 /24 or IPv6 /64 network key for a client address."""
    if not host:
        return "unknown"
    host = host.strip()
    if not host:
        return "unknown"
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    host = host.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "unknown"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    prefix = 24 if isinstance(address, ipaddress.IPv4Address) else 64
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))
