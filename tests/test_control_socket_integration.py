import os
import socket
import json
import time
import unittest
from unittest.mock import patch
from asteri.arbiter import Arbiter


class TestControlSocketIntegration(unittest.TestCase):
    def setUp(self):
        self.sock_path = "test_control.sock"
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)

    def tearDown(self):
        try:
            if os.path.exists(self.sock_path):
                os.unlink(self.sock_path)
        except FileNotFoundError:
            pass

    @patch("asteri.arbiter.Arbiter.setup_signals")
    @patch("asteri.arbiter.Arbiter.manage_workers")
    def test_control_socket_commands(self, mock_manage_workers, mock_setup_signals):
        arbiter = Arbiter(
            app_path="example_wsgi:app",
            worker_class="sync",
            num_workers=2,
            control_socket=self.sock_path,
        )

        arbiter.start()

        # Give a small brief moment for the thread server to bind and listen
        time.sleep(0.1)

        self.assertTrue(os.path.exists(self.sock_path))

        # 1. Test status command
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(self.sock_path)
        client.sendall(json.dumps({"command": "status"}).encode("utf-8"))
        resp = json.loads(client.recv(1024).decode("utf-8"))
        client.close()

        self.assertEqual(resp["status"], "running")
        self.assertEqual(resp["num_workers"], 2)
        self.assertEqual(resp["workers_count"], 0)

        # 2. Test add-worker command
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(self.sock_path)
        client.sendall(json.dumps({"command": "add-worker"}).encode("utf-8"))
        resp = json.loads(client.recv(1024).decode("utf-8"))
        client.close()

        self.assertEqual(resp["status"], "added")
        self.assertEqual(resp["num_workers"], 3)
        self.assertEqual(arbiter.num_workers, 3)

        # 3. Test remove-worker command
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(self.sock_path)
        client.sendall(json.dumps(
            {"command": "remove-worker"}).encode("utf-8"))
        resp = json.loads(client.recv(1024).decode("utf-8"))
        client.close()

        self.assertEqual(resp["status"], "removed")
        self.assertEqual(resp["num_workers"], 2)
        self.assertEqual(arbiter.num_workers, 2)

        # 4. Test stop command
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(self.sock_path)
        client.sendall(json.dumps({"command": "stop"}).encode("utf-8"))
        resp = json.loads(client.recv(1024).decode("utf-8"))
        client.close()

        self.assertEqual(resp["status"], "stopping")
        self.assertFalse(arbiter.alive)


if __name__ == "__main__":
    unittest.main()
