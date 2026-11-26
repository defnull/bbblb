# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

from bbblb.lib.ratelimit import InMemoryCircuitBreaker

LOGIN_MAX_FAILURES = 10
LOGIN_FAILURE_TIMEOUT = 5 * 60
MAX_TRACKED_NETWORKS = 10_000


class LoginRateLimiter(InMemoryCircuitBreaker):
    def __init__(self):
        super().__init__(
            limit=LOGIN_MAX_FAILURES,
            timeout=LOGIN_FAILURE_TIMEOUT,
            max_keys=MAX_TRACKED_NETWORKS,
        )
