"""_fetch_rpc_responses: concurrent RPC fan-out without a live Supabase."""

import threading
import time
import unittest
from types import SimpleNamespace

from app.retrieval import _fetch_rpc_responses


class FakeClient:
    def __init__(self, delay=0.0, fail=()):
        self.delay = delay
        self.fail = set(fail)
        self.calls = {}
        self._lock = threading.Lock()

    def rpc(self, name, params):
        client = self

        class Builder:
            def execute(self_inner):
                time.sleep(client.delay)
                with client._lock:
                    client.calls[name] = dict(params)
                if name in client.fail:
                    raise RuntimeError(f"{name} down")
                return SimpleNamespace(data=[{"id": name}])

        return Builder()


class FetchRpcResponsesTests(unittest.TestCase):
    def test_vector_and_lexical_calls_get_their_own_payloads(self):
        client = FakeClient()
        result = _fetch_rpc_responses(
            client, ["a", "b"], {"query_embedding": "[1]", "min_similarity": 0.3},
            {"a": 30, "b": 80}, "학점",
        )
        self.assertEqual({"a", "a_lexical", "b", "b_lexical"}, set(result))
        self.assertEqual(30, client.calls["a"]["match_count"])
        self.assertEqual(80, client.calls["b"]["match_count"])
        self.assertEqual({"query_text": "학점", "match_count": 80}, client.calls["b_lexical"])
        response, elapsed_ms, error = result["a"]
        self.assertEqual([{"id": "a"}], response.data)
        self.assertIsNone(error)
        self.assertGreaterEqual(elapsed_ms, 0)

    def test_no_lexical_calls_without_lexical_query(self):
        result = _fetch_rpc_responses(FakeClient(), ["a"], {}, {"a": 30}, None)
        self.assertEqual({"a"}, set(result))

    def test_failure_is_returned_not_raised(self):
        result = _fetch_rpc_responses(FakeClient(fail={"b_lexical"}), ["a", "b"], {}, {"a": 30, "b": 30}, "q")
        self.assertIsInstance(result["b_lexical"][2], RuntimeError)
        self.assertIsNone(result["b_lexical"][0])
        self.assertIsNone(result["a"][2])

    def test_calls_run_concurrently_when_allowed(self):
        client = FakeClient(delay=0.3)
        started = time.perf_counter()
        _fetch_rpc_responses(client, ["a", "b", "c", "d"], {}, {k: 30 for k in "abcd"}, "q", max_workers=8)
        # 8 calls x 0.3s would take 2.4s sequentially.
        self.assertLess(time.perf_counter() - started, 1.2)

    def test_default_is_sequential_in_original_order(self):
        client = FakeClient()
        order = []
        original = client.rpc
        client.rpc = lambda name, params: (order.append(name), original(name, params))[1]
        _fetch_rpc_responses(client, ["a", "b"], {}, {"a": 30, "b": 30}, "q")
        self.assertEqual(["a", "a_lexical", "b", "b_lexical"], order)


if __name__ == "__main__":
    unittest.main()
