import unittest
import logging
import sys
import os
from asteri.utils import setup_logging, setup_access_logging, PrettyFormatter, NoColorFormatter

class TestLogger(unittest.TestCase):
    def setUp(self):
        self.log_file = "test_asteri_logger.log"

    def tearDown(self):
        if os.path.exists(self.log_file):
            os.remove(self.log_file)
        # Restore sys.stdout and sys.stderr
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__

    def test_setup_logging_console(self):
        logger = setup_logging(level=logging.DEBUG)
        self.assertEqual(logger.level, logging.DEBUG)
        
        # Check that we have a StreamHandler with PrettyFormatter
        has_console_handler = False
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                self.assertIsInstance(handler.formatter, PrettyFormatter)
                has_console_handler = True
        self.assertTrue(has_console_handler)

    def test_setup_logging_file(self):
        logger = setup_logging(log_file=self.log_file)
        
        # Check that we have a FileHandler
        has_file_handler = False
        for handler in logger.handlers:
            if isinstance(handler, logging.FileHandler):
                has_file_handler = True
        self.assertTrue(has_file_handler)

    def test_capture_output_redirection(self):
        # When capture_output is True, sys.stdout and sys.stderr should be mocked/redirected
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        
        try:
            setup_logging(log_file=self.log_file, capture_output=True)
            
            # sys.stdout/sys.stderr should now be instances of the internal StreamToLogger wrapper
            self.assertNotEqual(sys.stdout, original_stdout)
            self.assertNotEqual(sys.stderr, original_stderr)
            self.assertTrue(hasattr(sys.stdout, "logger"))
            self.assertTrue(hasattr(sys.stderr, "logger"))
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

    def test_setup_access_logging(self):
        access_logger = setup_access_logging(log_file=self.log_file, log_format="%(message)s")
        self.assertEqual(access_logger.level, logging.INFO)
        
        has_file_handler = False
        for handler in access_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                self.assertIsInstance(handler.formatter, NoColorFormatter)
                has_file_handler = True
        self.assertTrue(has_file_handler)

if __name__ == "__main__":
    unittest.main()
