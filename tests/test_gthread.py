import unittest
from unittest.mock import MagicMock, patch
from asteri.workers.gthread import GThreadWorker


class TestGThread(unittest.TestCase):
    def test_gthread_init(self):
        # Verify initialization and thread count storage
        worker = GThreadWorker(0, 100, [], "example_wsgi:app", 30, threads=8)
        self.assertEqual(worker.threads, 8)
        self.assertEqual(worker.app_path, "example_wsgi:app")

    @patch("asteri.workers.gthread.ThreadPoolExecutor")
    @patch("select.select")
    def test_gthread_run_loop_submits_to_executor(self, mock_select, mock_executor):
        # We mock select to say a socket is readable exactly once, then we make the loop exit
        mock_sock = MagicMock()
        mock_client = MagicMock()
        mock_sock.accept.return_value = (mock_client, ("127.0.0.1", 12345))

        # select.select returns (readable, writable, exceptional)
        # We simulate mock_sock being readable
        mock_select.return_value = ([mock_sock], [], [])

        worker = GThreadWorker(
            0, 100, [mock_sock], "example_wsgi:app", 30, threads=4)

        # We patch getppid to return the correct parent pid so it doesn't exit immediately on that check
        with patch("os.getppid", return_value=100):
            # To break the infinite loop after one iteration, we mock executor.submit to stop the worker
            mock_executor_instance = MagicMock()
            mock_executor.return_value.__enter__.return_value = mock_executor_instance

            def stop_worker(*args, **kwargs):
                worker.alive = False
                return MagicMock()

            mock_executor_instance.submit.side_effect = stop_worker

            # Execute run loop
            worker.run()

            # Verify socket accept was called
            mock_sock.accept.assert_called_once()

            # Verify request was submitted to the thread pool executor
            mock_executor_instance.submit.assert_called_once_with(
                worker._run_guarded, mock_client, mock_sock
            )


if __name__ == "__main__":
    unittest.main()
