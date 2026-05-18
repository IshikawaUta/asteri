import unittest
from asteri.dirty import StashServer, StashClient


class TestDirtyStash(unittest.TestCase):
    def setUp(self):
        # Bind to a dynamic high-port on localhost
        self.address = ("127.0.0.1", 0)
        # We start the stash server
        self.server = StashServer(self.address)
        self.server.start()

        # Determine bound port
        self.bound_address = self.server.server_sock.getsockname()
        self.client = StashClient(self.bound_address)

    def tearDown(self):
        self.server.stop()

    def test_stash_set_get_delete(self):
        # 1. Set key
        success = self.client.set("app_state:user_1", b"session_token_123")
        self.assertTrue(success)

        # 2. Get key
        value = self.client.get("app_state:user_1")
        self.assertEqual(value, b"session_token_123")

        # 3. Delete key
        del_success = self.client.delete("app_state:user_1")
        self.assertTrue(del_success)

        # 4. Get deleted key (should return None)
        missing_value = self.client.get("app_state:user_1")
        self.assertIsNone(missing_value)

    def test_get_nonexistent_key(self):
        val = self.client.get("nonexistent")
        self.assertIsNone(val)


if __name__ == "__main__":
    unittest.main()
