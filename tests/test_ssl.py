import unittest
import socket
from unittest.mock import MagicMock, patch
from asteri.arbiter import Arbiter
from asteri.workers.sync import SyncWorker


class TestSSL(unittest.TestCase):
    @patch("ssl.SSLContext")
    def test_ssl_socket_wrap(self, mock_ssl_context):
        # We mock standard socket behavior
        mock_context_instance = MagicMock()
        mock_ssl_context.return_value = mock_context_instance

        # ephemaral port
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("", 0))
        port = s.getsockname()[1]
        s.close()

        arb = Arbiter(
            "example_wsgi:app",
            SyncWorker,
            binds=[f"127.0.0.1:{port}"],
            certfile="dummy.crt",
            keyfile="dummy.key",
        )

        # Override manage_workers to avoid infinite loop
        arb.manage_workers = MagicMock()

        # Call start to see if it wraps the socket
        arb.start()

        try:
            # Verify SSLContext was initialized with server protocol
            import ssl

            mock_ssl_context.assert_called_once_with(ssl.PROTOCOL_TLS_SERVER)

            # Verify load_cert_chain was called with dummy paths
            mock_context_instance.load_cert_chain.assert_called_once_with(
                certfile="dummy.crt", keyfile="dummy.key"
            )

            # Verify wrap_socket was called to wrap our listening socket
            mock_context_instance.wrap_socket.assert_called_once()
        finally:
            for sock in arb.socks:
                sock.close()


if __name__ == "__main__":
    unittest.main()
