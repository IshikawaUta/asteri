import unittest
import os
from asteri.arbiter import Arbiter
from asteri.workers.sync import SyncWorker

class TestPidfile(unittest.TestCase):
    def setUp(self):
        self.pid_file = "test_arbiter_temp.pid"

    def tearDown(self):
        if os.path.exists(self.pid_file):
            os.remove(self.pid_file)

    def test_write_pid_and_cleanup(self):
        arb = Arbiter("example_wsgi:app", SyncWorker, pidfile=self.pid_file)
        
        # Test creation of PID file
        arb.write_pid()
        self.assertTrue(os.path.exists(self.pid_file))
        
        # Read file contents and verify it matches current PID
        with open(self.pid_file, "r") as f:
            content = f.read().strip()
            self.assertEqual(int(content), os.getpid())

        # Test automatic cleanup
        # Simulating Arbiter shutdown cleanup (from line 242-243 in arbiter.py)
        if arb.pidfile and os.path.exists(arb.pidfile):
            os.remove(arb.pidfile)
            
        self.assertFalse(os.path.exists(self.pid_file))

if __name__ == "__main__":
    unittest.main()
