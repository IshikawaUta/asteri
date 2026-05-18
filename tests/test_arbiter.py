import unittest
import socket
from asteri.arbiter import Arbiter
from asteri.workers.sync import SyncWorker


class TestArbiter(unittest.TestCase):
    def test_arbiter_initialization_defaults(self):
        arb = Arbiter("example_wsgi:app", SyncWorker)
        self.assertEqual(arb.app_path, "example_wsgi:app")
        self.assertEqual(arb.worker_class, SyncWorker)
        self.assertEqual(arb.num_workers, 1)
        self.assertEqual(arb.binds, ["127.0.0.1:8000"])
        self.assertFalse(arb.reload)
        self.assertIsNone(arb.certfile)
        self.assertIsNone(arb.keyfile)
        self.assertFalse(arb.daemon)
        self.assertEqual(arb.proc_name, "master")
        self.assertEqual(arb.timeout, 30)
        self.assertEqual(arb.backlog, 2048)

    def test_arbiter_initialization_custom(self):
        arb = Arbiter(
            "example_wsgi:app",
            SyncWorker,
            num_workers=4,
            binds=["127.0.0.1:9000", "0.0.0.0:9001"],
            reload=True,
            proc_name="my_master",
            timeout=60,
            backlog=1024,
            custom_arg="hello",
        )
        self.assertEqual(arb.num_workers, 4)
        self.assertEqual(arb.binds, ["127.0.0.1:9000", "0.0.0.0:9001"])
        self.assertTrue(arb.reload)
        self.assertEqual(arb.proc_name, "my_master")
        self.assertEqual(arb.timeout, 60)
        self.assertEqual(arb.backlog, 1024)
        self.assertEqual(arb.worker_kwargs.get("custom_arg"), "hello")

    def test_arbiter_socket_binding(self):
        # We find a free port to bind to dynamically
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("", 0))
        port = s.getsockname()[1]
        s.close()

        bind_addr = f"127.0.0.1:{port}"
        arb = Arbiter("example_wsgi:app", SyncWorker, binds=[bind_addr])

        # We can mock parts of manage_workers or start so that it sets up socks but doesn't enter the infinite loop
        # We override start or just do a partial call
        # Let's mock manage_workers to do nothing so we can call start without blocking
        def dummy_manage_workers():
            pass

        arb.manage_workers = dummy_manage_workers

        # Ensure it doesn't daemonize or write PID
        arb.daemon = False
        arb.pidfile = None
        arb.umask = 0

        try:
            arb.start()
            self.assertEqual(len(arb.socks), 1)
            # Verify socket is active
            sock = arb.socks[0]
            self.assertEqual(sock.getsockname()[1], port)
        finally:
            # Clean up sockets
            for sock in arb.socks:
                sock.close()


if __name__ == "__main__":
    unittest.main()
