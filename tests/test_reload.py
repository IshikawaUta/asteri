import unittest
import signal
import os
from unittest.mock import MagicMock, patch
from asteri.arbiter import Arbiter
from asteri.workers.sync import SyncWorker

class TestReload(unittest.TestCase):
    @patch("asteri.arbiter.Observer")
    @patch("asteri.arbiter.WATCHDOG_AVAILABLE", True)
    def test_setup_reloader_with_watchdog(self, mock_observer):
        arb = Arbiter("example_wsgi:app", SyncWorker, reload=True)
        arb.setup_reloader()
        
        # Verify watchdog observer is created and started
        self.assertIsNotNone(arb.reloader)
        mock_observer.return_value.schedule.assert_called_once()
        mock_observer.return_value.start.assert_called_once()
        
        # Clean up
        arb.reloader.stop()

    @patch("asteri.arbiter.WATCHDOG_AVAILABLE", True)
    def test_reload_handler_triggers_stop_workers(self):
        arb = Arbiter("example_wsgi:app", SyncWorker, reload=True)
        arb.stop_workers = MagicMock()
        
        # Manually invoke the inner ReloadHandler class
        # Look up setup_reloader code to instantiate
        from watchdog.events import FileSystemEvent
        
        # We trigger setup_reloader with Observer mocked to avoid actual FS watching
        with patch("asteri.arbiter.Observer") as mock_observer:
            arb.setup_reloader()
            
            # Retrieve the ReloadHandler class inside setup_reloader
            # It was scheduled with Observer.schedule(Handler, path, recursive)
            # The handler is the first positional arg in schedule
            args, kwargs = mock_observer.return_value.schedule.call_args
            reload_handler = args[0]
            
            # Simulate a non-python file modification (should NOT trigger reload)
            mock_event_txt = MagicMock(spec=FileSystemEvent)
            mock_event_txt.src_path = "test.txt"
            reload_handler.on_modified(mock_event_txt)
            arb.stop_workers.assert_not_called()
            
            # Simulate a python file modification (SHOULD trigger reload)
            mock_event_py = MagicMock(spec=FileSystemEvent)
            mock_event_py.src_path = "test.py"
            reload_handler.on_modified(mock_event_py)
            arb.stop_workers.assert_called_once_with(signal.SIGTERM)

if __name__ == "__main__":
    unittest.main()
