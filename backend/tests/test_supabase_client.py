"""ThreadLocalSupabase: one underlying client per thread, reused within it."""

import threading
import unittest
from types import SimpleNamespace

from app.supabase_client import ThreadLocalSupabase


class ThreadLocalSupabaseTests(unittest.TestCase):
    def test_each_thread_gets_and_reuses_its_own_client(self):
        built = []
        lock = threading.Lock()

        def factory(url, key):
            client = SimpleNamespace(url=url, key=key, table=lambda name: name)
            with lock:
                built.append(client)
            return client

        shared = ThreadLocalSupabase("https://x.supabase.co", "k", factory=factory)
        seen = {}

        def use(name):
            first = shared._client()
            shared.table("chats")
            seen[name] = (first, shared._client())

        threads = [threading.Thread(target=use, args=(n,)) for n in ("a", "b", "c")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(3, len(built))
        self.assertTrue(all(first is again for first, again in seen.values()))
        self.assertEqual(3, len({id(first) for first, _ in seen.values()}))

    def test_delegates_attributes_and_keeps_url_and_key(self):
        shared = ThreadLocalSupabase("https://x.supabase.co", "k",
                                     factory=lambda url, key: SimpleNamespace(rpc=lambda n, p: (n, p)))
        self.assertEqual(("f", {}), shared.rpc("f", {}))
        self.assertEqual(("https://x.supabase.co", "k"), (shared.supabase_url, shared.supabase_key))


if __name__ == "__main__":
    unittest.main()
