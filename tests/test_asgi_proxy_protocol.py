import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from asteri.workers.sync import SyncWorker
from asteri.workers.asgi import ASGIWorker
from asteri.http import HTTPParser


class TestASGIProxyProtocol(unittest.TestCase):
    def test_proxy_protocol_v1_sync_worker(self):
        # 1. WSGI test with Proxy Protocol v1
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [
            b"PROXY TCP4 203.0.113.50 198.51.100.10 56324 80\r\nGET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        ]
        mock_sock.getsockname.return_value = ("127.0.0.1", 8080)
        mock_sock.getpeername.return_value = ("127.0.0.1", 12345)

        worker = SyncWorker(
            age=0, ppid=999, sockets=[], app_path="dummy_app:app", timeout=30
        )
        worker.proxy_protocol = True
        worker.keep_alive = 0
        worker.app = MagicMock(return_value=[b"hello"])
        # Mock handle_request's socket reading flow
        # In base.py: chunk = client_sock.recv(4096)
        worker.handle_request(mock_sock, listener_sock=mock_sock)

        # Verify proxy client/server are parsed and set
        self.assertEqual(worker._current_proxy_client, ("203.0.113.50", 56324))
        self.assertEqual(worker._current_proxy_server, ("198.51.100.10", 80))

        # Verify WSGI environ has the corrected addresses
        req = HTTPParser.parse(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        env = worker.build_wsgi_environ(req, mock_sock, mock_sock)
        self.assertEqual(env["REMOTE_ADDR"], "203.0.113.50")
        self.assertEqual(env["REMOTE_PORT"], "56324")
        self.assertEqual(env["SERVER_NAME"], "198.51.100.10")
        self.assertEqual(env["SERVER_PORT"], "80")

    def test_proxy_protocol_v2_asgi_worker(self):
        # 2. ASGI test with Proxy Protocol v2
        async def run_test():
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("127.0.0.1", 8080)
            mock_sock.getpeername.return_value = ("127.0.0.1", 12345)

            # Proxy Protocol v2 header prefix (12 bytes)
            v2_header = bytearray(b"\r\n\r\n\x00\r\nQUIT\n")
            # Version & command: v2 proxy (0x21)
            v2_header.append(0x21)
            # Address family & protocol: AF_INET stream (0x11)
            v2_header.append(0x11)
            # Length: 12 bytes (4 bytes src ip + 4 bytes dst ip + 2 bytes src port + 2 bytes dst port)
            v2_header.extend((12).to_bytes(2, byteorder="big"))

            # Src IP: 198.51.100.22 (0xC6, 0x33, 0x64, 0x16)
            v2_header.extend([198, 51, 100, 22])
            # Dst IP: 203.0.113.88 (0xCB, 0x00, 0x71, 0x58)
            v2_header.extend([203, 0, 113, 88])
            # Src Port: 54321 (0xD4, 0x31)
            v2_header.extend((54321).to_bytes(2, byteorder="big"))
            # Dst Port: 443 (0x01, 0xBB)
            v2_header.extend((443).to_bytes(2, byteorder="big"))

            # HTTP request immediately following the proxy protocol bytes
            http_request = b"GET /chat HTTP/1.1\r\nHost: localhost\r\n\r\n"

            loop = asyncio.get_running_loop()
            # Loop returns v2 proxy header and initial request chunk
            loop.sock_recv = AsyncMock(
                side_effect=[bytes(v2_header) + http_request, b""]
            )
            loop.sock_sendall = AsyncMock()

            called_scope = {}

            async def dummy_asgi_app(scope, receive, send):
                called_scope.update(scope)
                await send(
                    {"type": "http.response.start", "status": 200, "headers": []}
                )
                await send(
                    {"type": "http.response.body", "body": b"OK", "more_body": False}
                )

            worker = ASGIWorker(
                age=0, ppid=999, sockets=[], app_path="dummy_app:app", timeout=30
            )
            worker.proxy_protocol = True
            worker.app = dummy_asgi_app

            await worker.handle_asgi_request(mock_sock)

            # Verify proxy information inside ASGI scope
            self.assertEqual(called_scope["client"], ("198.51.100.22", 54321))
            self.assertEqual(called_scope["server"], ("203.0.113.88", 443))

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
