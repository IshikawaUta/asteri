import unittest
import sys
import os
import io
from unittest.mock import patch
from asteri.__main__ import main

class TestConfig(unittest.TestCase):
    def setUp(self):
        self.conf_file = "test_config_temp.py"

    def tearDown(self):
        if os.path.exists(self.conf_file):
            os.remove(self.conf_file)

    def test_cli_parsing_and_print_config(self):
        # We mock sys.argv to pass arguments and print configuration
        test_args = ["asteri", "example_wsgi:app", "--workers", "3", "--worker-class", "gthread", "--disable-dashboard", "--print-config"]
        
        with patch.object(sys, "argv", test_args):
            # Capture standard output to verify config printing
            captured_stdout = io.StringIO()
            with patch("sys.stdout", captured_stdout):
                with self.assertRaises(SystemExit) as cm:
                    main()
                
                # Main should exit with 0 when --print-config is set
                self.assertEqual(cm.exception.code, 0)
                
                output = captured_stdout.getvalue()
                self.assertIn("workers: 3", output)
                self.assertIn("worker_class: gthread", output)
                self.assertIn("app: example_wsgi:app", output)
                self.assertIn("disable_dashboard: True", output)

    def test_config_file_loading_overrides(self):
        # Write temporary config file
        with open(self.conf_file, "w") as f:
            f.write("workers = 5\nthreads = 8\n")

        test_args = ["asteri", "example_wsgi:app", "-c", self.conf_file, "--print-config"]
        
        with patch.object(sys, "argv", test_args):
            captured_stdout = io.StringIO()
            with patch("sys.stdout", captured_stdout):
                with self.assertRaises(SystemExit) as cm:
                    main()
                
                self.assertEqual(cm.exception.code, 0)
                output = captured_stdout.getvalue()
                # Verify settings from config file are loaded
                self.assertIn("workers: 5", output)
                self.assertIn("threads: 8", output)

    def test_cli_overrides_config_file_priority(self):
        # Write config file specifying workers = 5
        with open(self.conf_file, "w") as f:
            f.write("workers = 5\n")

        # Pass CLI argument specifying --workers 2. CLI should take precedence!
        test_args = ["asteri", "example_wsgi:app", "-c", self.conf_file, "--workers", "2", "--print-config"]
        
        with patch.object(sys, "argv", test_args):
            captured_stdout = io.StringIO()
            with patch("sys.stdout", captured_stdout):
                with self.assertRaises(SystemExit) as cm:
                    main()
                
                self.assertEqual(cm.exception.code, 0)
                output = captured_stdout.getvalue()
                # CLI takes priority: --workers 2 must override workers = 5 from file
                self.assertIn("workers: 2", output)

    def test_new_cli_options(self):
        test_args = [
            "asteri", "example_wsgi:app",
            "--control-socket", "test_ctrl.sock",
            "--dirty-apps", "dirty_config",
            "--stash-address", "localhost:9999",
            "--statsd-host", "127.0.0.1",
            "--statsd-port", "8125",
            "--statsd-prefix", "my_asteri",
            "--print-config"
        ]
        
        with patch.object(sys, "argv", test_args):
            captured_stdout = io.StringIO()
            with patch("sys.stdout", captured_stdout):
                with self.assertRaises(SystemExit) as cm:
                    main()
                
                self.assertEqual(cm.exception.code, 0)
                output = captured_stdout.getvalue()
                self.assertIn("control_socket: test_ctrl.sock", output)
                self.assertIn("dirty_apps: dirty_config", output)
                self.assertIn("stash_address: localhost:9999", output)
                self.assertIn("statsd_host: 127.0.0.1", output)
                self.assertIn("statsd_port: 8125", output)
                self.assertIn("statsd_prefix: my_asteri", output)

if __name__ == "__main__":
    unittest.main()
