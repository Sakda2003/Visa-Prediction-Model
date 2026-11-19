from collections import OrderedDict, deque
from time import monotonic


class ProcessLocalRateLimiter:
    def __init__(self, max_clients: int = 10000) -> None:
        self._max_clients = max_clients
        self._buckets: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()

    def allow(
        self, scope: str, client: str, limit: int, window_seconds: float
    ) -> bool:
        key = (scope, client)
        now = monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= self._max_clients:
                self._buckets.popitem(last=False)
            bucket = deque()
            self._buckets[key] = bucket
        else:
            self._buckets.move_to_end(key)

        while bucket and now - bucket[0] >= window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    def clear(self) -> None:
        self._buckets.clear()
