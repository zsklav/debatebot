"""Cost regressions: reuse, invalidation, ownership and concurrent requests."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from unittest.mock import Mock

from app.core.cache import AnalysisCache


class CacheTests(unittest.TestCase):
    def test_reuse_is_isolated_by_owner_content_and_model(self):
        cache = AnalysisCache()
        compute = Mock(return_value={"score": [7]})
        key = ("alice", "scorer", "model-a", "text-a")
        first = cache.run(key, compute)
        first["score"].append(0)
        self.assertEqual(cache.run(key, compute), {"score": [7]})
        compute.assert_called_once()
        for changed in [("bob", *key[1:]), (*key[:2], "model-b", key[3]), (*key[:3], "text-b")]:
            cache.run(changed, compute)
        self.assertEqual(compute.call_count, 4)

    def test_expiry_eviction_and_failed_requests_recompute(self):
        clock = Mock(return_value=0)
        cache = AnalysisCache(ttl=10, max_entries=1, clock=clock)
        compute = Mock(return_value={"ok": True})
        cache.run("a", compute)
        clock.return_value = 10
        cache.run("a", compute)
        self.assertEqual(compute.call_count, 2)
        cache.run("b", compute)
        cache.run("a", compute)
        self.assertEqual(compute.call_count, 4)
        fail = Mock(side_effect=ValueError("invalid output"))
        for _ in range(2):
            with self.assertRaises(ValueError):
                cache.run("bad", fail)
        self.assertEqual(fail.call_count, 2)
        self.assertFalse(cache.pending)

    def test_simultaneous_same_key_shares_generation(self):
        cache = AnalysisCache()
        entered, release = Event(), Event()
        def compute():
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test release not received")
            return {"score": 7}
        work = Mock(side_effect=compute)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(cache.run, "same", work)
            self.assertTrue(entered.wait(3))
            second = pool.submit(cache.run, "same", work)
            release.set()
            self.assertEqual(first.result(timeout=3), second.result(timeout=3))
        work.assert_called_once()
