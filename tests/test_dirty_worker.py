import unittest
from unittest.mock import patch
from asteri.workers.base import BaseWorker
from asteri.dirty import DirtyAppLoader, StashClient


class TestDirtyWorker(unittest.TestCase):
    @patch("asteri.utils.import_app")
    def test_worker_initializes_dirty_app_and_stash(self, mock_import):
        worker = BaseWorker(
            age=0,
            ppid=100,
            sockets=[],
            app_path="example_wsgi:app",
            timeout=30,
            dirty_apps="example.com=example_wsgi:app",
            stash_address=("127.0.0.1", 9999),
        )

        self.assertEqual(worker.dirty_apps, "example.com=example_wsgi:app")
        self.assertEqual(worker.stash_address, ("127.0.0.1", 9999))
        self.assertIsInstance(worker.stash, StashClient)

        # Test loading process
        with patch("signal.signal"):
            worker.init_process()
            self.assertIsInstance(worker.app, DirtyAppLoader)


if __name__ == "__main__":
    unittest.main()
