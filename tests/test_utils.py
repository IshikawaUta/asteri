import unittest
import os
import sys
import logging
from unittest.mock import MagicMock
from asteri.utils import import_app, get_num_workers, NoColorFormatter, Colors

class TestUtils(unittest.TestCase):
    def test_import_app_success(self):
        # We know example_wsgi:app exists at the project root
        app = import_app("example_wsgi:app")
        self.assertTrue(callable(app))

    def test_import_app_failure(self):
        with self.assertRaises(Exception):
            import_app("non_existent_module:app")
        with self.assertRaises(Exception):
            import_app("example_wsgi:non_existent_callable")

    def test_get_num_workers(self):
        workers = get_num_workers()
        expected = os.cpu_count() * 2 + 1
        self.assertEqual(workers, expected)

    def test_no_color_formatter(self):
        formatter = NoColorFormatter("%(message)s")
        # Create a log record with ANSI color codes
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg=f"{Colors.GREEN}Hello World{Colors.ENDC}",
            args=(),
            exc_info=None
        )
        formatted = formatter.format(record)
        # Verify color codes are stripped
        self.assertEqual(formatted, "Hello World")
        self.assertNotIn("\x1B", formatted)

if __name__ == "__main__":
    unittest.main()
