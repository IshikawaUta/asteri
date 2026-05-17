import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from asteri.workers.asgi import ASGIWorker
from asteri.http import HTTPRequest

class TestASGIWorker(unittest.TestCase):
    def setUp(self):
        self.worker = ASGIWorker(
            age=0,
            ppid=100,
            sockets=[],
            app_path="example_asgi:app",
            timeout=30
        )

    def test_build_asgi_scope(self):
        req = HTTPRequest(
            method="GET",
            path="/hello?name=world",
            version="HTTP/1.1",
            headers={"host": "localhost", "x-test": "value"},
            body=b""
        )
        
        mock_sock = MagicMock()
        mock_sock.getsockname.return_value = ("127.0.0.1", 8080)
        mock_sock.getpeername.return_value = ("127.0.0.1", 54321)
        
        scope = self.worker.build_asgi_scope(req, mock_sock)
        
        self.assertEqual(scope["type"], "http")
        self.assertEqual(scope["asgi"]["version"], "3.0")
        self.assertEqual(scope["method"], "GET")
        self.assertEqual(scope["path"], "/hello")
        self.assertEqual(scope["query_string"], b"name=world")
        self.assertEqual(scope["server"], ("127.0.0.1", 8080))
        self.assertEqual(scope["client"], ("127.0.0.1", 54321))
        
        # Verify headers format
        headers_dict = dict(scope["headers"])
        self.assertEqual(headers_dict[b"host"], b"localhost")
        self.assertEqual(headers_dict[b"x-test"], b"value")

    def test_asgi_app_execution_flow(self):
        # We write an async test wrapper using asyncio.run
        async def run_test():
            # Mock socket to return a valid HTTP GET request
            mock_sock = MagicMock()
            
            # Simulated HTTP GET Request bytes
            request_bytes = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
            
            # Use AsyncMock for socket's recv/sendall inside the event loop
            # Note: handle_asgi_request uses asyncio loop.sock_recv / loop.sock_sendall
            # Let's mock loop.sock_recv and loop.sock_sendall
            loop = asyncio.get_running_loop()
            loop.sock_recv = AsyncMock(side_effect=[request_bytes, b""]) # first call returns request, second EOF
            loop.sock_sendall = AsyncMock()
            
            # Standard ASGI application that responds "Hello ASGI"
            async def dummy_asgi_app(scope, receive, send):
                self.assertEqual(scope["type"], "http")
                self.assertEqual(scope["path"], "/")
                
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")]
                })
                await send({
                    "type": "http.response.body",
                    "body": b"Hello ASGI",
                    "more_body": False
                })

            self.worker.app = dummy_asgi_app
            
            # Run the handler
            await self.worker.handle_asgi_request(mock_sock)
            
            # Verify sock_sendall was called with the built HTTP response
            loop.sock_sendall.assert_called_once()
            args, kwargs = loop.sock_sendall.call_args
            response_sent = args[1]
            
            self.assertTrue(response_sent.startswith(b"HTTP/1.1 200 OK\r\n"))
            self.assertIn(b"content-type: text/plain\r\n", response_sent)
            self.assertTrue(response_sent.endswith(b"\r\n\r\nHello ASGI"))

        asyncio.run(run_test())

    def test_asgi_dashboard_enabled_by_default(self):
        async def run_test():
            mock_sock = MagicMock()
            request_bytes = b"GET /asteri-status HTTP/1.1\r\nHost: localhost\r\n\r\n"
            
            loop = asyncio.get_running_loop()
            loop.sock_recv = AsyncMock(side_effect=[request_bytes, b""])
            loop.sock_sendall = AsyncMock()

            app_called = False
            async def dummy_asgi_app(scope, receive, send):
                nonlocal app_called
                app_called = True

            self.worker.app = dummy_asgi_app
            await self.worker.handle_asgi_request(mock_sock)
            
            self.assertFalse(app_called)
            loop.sock_sendall.assert_called_once()
            args, kwargs = loop.sock_sendall.call_args
            response_sent = args[1]
            self.assertIn(b"Asteri Dashboard", response_sent)

        asyncio.run(run_test())

    def test_asgi_dashboard_disabled(self):
        self.worker.disable_dashboard = True
        
        async def run_test():
            mock_sock = MagicMock()
            request_bytes = b"GET /asteri-status HTTP/1.1\r\nHost: localhost\r\n\r\n"
            
            loop = asyncio.get_running_loop()
            loop.sock_recv = AsyncMock(side_effect=[request_bytes, b""])
            loop.sock_sendall = AsyncMock()

            app_called = False
            async def dummy_asgi_app(scope, receive, send):
                nonlocal app_called
                app_called = True
                await send({
                    "type": "http.response.start",
                    "status": 404,
                    "headers": []
                })
                await send({
                    "type": "http.response.body",
                    "body": b"Not Found",
                    "more_body": False
                })

            self.worker.app = dummy_asgi_app
            await self.worker.handle_asgi_request(mock_sock)
            
            self.assertTrue(app_called)
            loop.sock_sendall.assert_called_once()
            args, kwargs = loop.sock_sendall.call_args
            response_sent = args[1]
            self.assertTrue(response_sent.startswith(b"HTTP/1.1 404 Not Found"))
            self.assertTrue(response_sent.endswith(b"Not Found"))

        asyncio.run(run_test())

if __name__ == "__main__":
    unittest.main()
