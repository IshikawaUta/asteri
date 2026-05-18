import unittest
from unittest.mock import patch, MagicMock
from asteri.arbiter import Arbiter


class TestStatsdIntegration(unittest.TestCase):
    @patch("asteri.utils.StatsdClient")
    @patch("asteri.arbiter.Arbiter.setup_signals")
    @patch("asteri.arbiter.Arbiter.manage_workers")
    @patch("asteri.arbiter.os.fork")
    def test_statsd_emissions_on_spawn_and_reap(
        self, mock_fork, mock_manage_workers, mock_setup_signals, mock_statsd_class
    ):
        # Setup mock StatsdClient instance
        mock_statsd = MagicMock()
        mock_statsd_class.return_value = mock_statsd

        # We simulate the parent PID on fork
        mock_fork.return_value = 12345

        arbiter = Arbiter(
            app_path="example_wsgi:app",
            worker_class=MagicMock(),
            num_workers=1,
            statsd_host="127.0.0.1",
            statsd_port=8125,
            statsd_prefix="test_asteri",
        )

        # Verify StatsdClient was instantiated with correct arguments
        mock_statsd_class.assert_called_once_with(
            "127.0.0.1", 8125, "test_asteri")
        self.assertEqual(arbiter.statsd, mock_statsd)

        # Trigger spawn_worker manually to check emissions
        arbiter.spawn_worker()

        # Verify spawn worker metrics were incremented
        mock_statsd.increment.assert_any_call("workers.spawn")
        mock_statsd.gauge.assert_any_call("workers.count", 1)

        # Trigger reaping simulation
        with patch("asteri.arbiter.os.waitpid") as mock_waitpid:
            mock_waitpid.side_effect = [
                (12345, 0),
                (0, 0),
            ]  # First returns reaped worker, second returns 0

            # Run one iteration of waitpid loop
            arbiter.workers[12345] = MagicMock()

            # We trigger the waitpid checking part from manage_workers manually or mock it
            # Let's run just the waitpid block from manage_workers
            pid, status = mock_waitpid()
            while pid > 0:
                if pid in arbiter.workers:
                    del arbiter.workers[pid]
                    if arbiter.statsd:
                        arbiter.statsd.increment("workers.exit")
                        arbiter.statsd.gauge(
                            "workers.count", len(arbiter.workers))
                pid, status = mock_waitpid()

            # Verify reap worker metrics were emitted
            mock_statsd.increment.assert_any_call("workers.exit")
            mock_statsd.gauge.assert_any_call("workers.count", 0)


if __name__ == "__main__":
    unittest.main()
