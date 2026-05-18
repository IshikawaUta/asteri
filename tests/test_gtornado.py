import unittest
from unittest.mock import patch, MagicMock
from asteri.workers.tornado import TornadoWorker


class TestTornadoWorker(unittest.TestCase):
    @patch("asteri.workers.tornado.tornado.httpserver.HTTPServer")
    @patch("asteri.workers.tornado.tornado.ioloop.IOLoop")
    @patch("asteri.workers.tornado.tornado.wsgi.WSGIContainer")
    @patch("asteri.workers.tornado.tornado.ioloop.PeriodicCallback")
    @patch("asteri.utils.import_app")
    def test_tornado_worker_lifecycle(
        self,
        mock_import_app,
        mock_periodic_callback,
        mock_container_class,
        mock_ioloop_class,
        mock_server_class,
    ):
        # Mock Tornado classes
        mock_ioloop = MagicMock()
        mock_ioloop_class.current.return_value = mock_ioloop

        mock_server = MagicMock()
        mock_server_class.return_value = mock_server

        mock_monitor = MagicMock()
        mock_periodic_callback.return_value = mock_monitor

        # Instantiate Tornado worker
        mock_sock = MagicMock()
        worker = TornadoWorker(
            age=0,
            ppid=999,
            sockets=[mock_sock],
            app_path="example_wsgi:app",
            timeout=30,
        )

        # Run worker with mocked signal and set_proctitle
        with patch("signal.signal"), patch("asteri.workers.base.set_proctitle"):
            worker.alive = False
            worker.run()

            # Verify app container creation
            mock_container_class.assert_called_once()
            called_app = mock_container_class.call_args[0][0]
            if hasattr(called_app, "wsgi_app"):
                self.assertEqual(called_app.wsgi_app, worker.app)
            else:
                self.assertEqual(called_app, worker.app)

            # Verify server creation & socket binding
            mock_server_class.assert_called_once()
            mock_sock.setblocking.assert_called_with(False)
            mock_server.add_socket.assert_called_once_with(mock_sock)

            # Verify loop is fetched and started
            mock_ioloop_class.current.assert_called_once()
            mock_ioloop.start.assert_called_once()

            # Verify PeriodicCallback was registered and started
            mock_periodic_callback.assert_called_once()
            mock_monitor.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
