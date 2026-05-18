import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from asteri.workers.sync import SyncWorker
from asteri.workers.asgi import ASGIWorker
from asteri.http import HTTPParser


class TestHTTP103EarlyHints(unittest.TestCase):
    def test_wsgi_early_hints(self):
        # Setup mock socket to collect sent bytes
        mock_sock = MagicMock()
        sent_data = []

        def mock_sendall(data):
            sent_data.append(data)

        mock_sock.sendall.side_effect = mock_sendall

        # Define WSGI app calling early hints
        def wsgi_app(environ, start_response):
            # Call early hints standard WSGI extension
            environ["wsgi.early_hints"](
                [("Link", "</style.css>; rel=preload")])
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"Hello World"]

        worker = SyncWorker(
            age=0, ppid=999, sockets=[], app_path="dummy_app:app", timeout=30
        )
        worker.app = wsgi_app

        # Build mock HTTPRequest using parser
        req = HTTPParser.parse(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        env = worker.build_wsgi_environ(req, MagicMock(), mock_sock)

        # Execute
        worker.execute_wsgi(mock_sock, env)

        # Verify 103 Early Hints was sent
        sent_bytes = b"".join(sent_data)
        self.assertIn(b"HTTP/1.1 103 Early Hints", sent_bytes)
        self.assertIn(b"Link: </style.css>; rel=preload", sent_bytes)

        # Verify final 200 OK response was also sent
        self.assertIn(b"HTTP/1.1 200 OK", sent_bytes)
        self.assertIn(b"Hello World", sent_bytes)

    def test_asgi_early_hints(self):
        async def run_test():
            mock_sock = MagicMock()

            # Simulated HTTP GET Request bytes
            request_bytes = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"

            # Use AsyncMock for socket's recv/sendall inside the event loop
            loop = asyncio.get_running_loop()
            loop.sock_recv = AsyncMock(side_effect=[request_bytes, b""])

            sent_chunks = []

            async def track_sendall(sock, data):
                sent_chunks.append(data)

            loop.sock_sendall = AsyncMock(side_effect=track_sendall)

            # Define ASGI app emitting early hints
            async def asgi_app(scope, receive, send):
                await send(
                    {
                        "type": "http.response.early_hints",
                        "headers": [(b"link", b"</style.css>; rel=preload")],
                    }
                )
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"content-type", b"text/plain")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"Hello ASGI",
                        "more_body": False,
                    }
                )

            worker = ASGIWorker(
                age=0, ppid=999, sockets=[], app_path="dummy_app:app", timeout=30
            )
            worker.app = asgi_app

            # Run the handler
            await worker.handle_asgi_request(mock_sock)

            # Verify sent chunks contain early hints and final response
            sent_bytes = b"".join(sent_chunks)
            self.assertIn(b"HTTP/1.1 103 Early Hints", sent_bytes)
            self.assertIn(b"link: </style.css>; rel=preload", sent_bytes)
            self.assertIn(b"HTTP/1.1 200 OK", sent_bytes)
            self.assertIn(b"Hello ASGI", sent_bytes)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
