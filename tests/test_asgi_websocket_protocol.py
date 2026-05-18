import unittest
import asyncio
import hashlib
import base64
from unittest.mock import MagicMock, AsyncMock
from asteri.workers.asgi import ASGIWorker


class TestASGIWebSocketProtocol(unittest.TestCase):
    def test_websocket_lifecycle(self):
        async def run_test():
            mock_sock = MagicMock()

            # Setup Sec-WebSocket-Key
            client_key = "dGhlIHNhbXBsZSBub25jZQ=="
            request_bytes = (
                "GET /chat HTTP/1.1\r\n"
                "Host: server.example.com\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {client_key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode("utf-8")

            # Prepare incoming websocket frame: a masked text frame with payload "Hello"
            # Unmasked: "Hello" = b"Hello"
            # Mask key: b"\x11\x22\x33\x44"
            # Masked: b"Hello" ^ mask = b"\x59\x47\x5f\x28\x7f"
            payload = b"Hello"
            mask_key = b"\x11\x22\x33\x44"
            masked_payload = bytes(b ^ mask_key[i % 4]
                                   for i, b in enumerate(payload))

            frame = bytearray()
            frame.append(0x81)  # fin, opcode=1 (text)
            frame.append(0x80 | len(payload))  # mask bit, length
            frame.extend(mask_key)
            frame.extend(masked_payload)

            # Add close frame (masked, close code 1000)
            # Payload: 1000 in Big Endian = b"\x03\xe8"
            close_payload = b"\x03\xe8"
            close_masked = bytes(
                b ^ mask_key[i % 4] for i, b in enumerate(close_payload)
            )
            close_frame = bytearray()
            close_frame.append(0x88)  # fin, opcode=8 (close)
            close_frame.append(0x80 | len(close_payload))
            close_frame.extend(mask_key)
            close_frame.extend(close_masked)

            loop = asyncio.get_running_loop()
            # First recv returns HTTP request, second returns text frame, third returns close frame
            loop.sock_recv = AsyncMock(
                side_effect=[request_bytes, frame, close_frame, b""]
            )

            sent_chunks = []

            async def track_sendall(sock, data):
                sent_chunks.append(data)

            loop.sock_sendall = AsyncMock(side_effect=track_sendall)

            app_messages = []

            async def dummy_websocket_app(scope, receive, send):
                self.assertEqual(scope["type"], "websocket")
                self.assertEqual(scope["path"], "/chat")

                # 1. Receive connect event
                msg1 = await receive()
                app_messages.append(msg1)

                # 2. Accept connection
                await send({"type": "websocket.accept"})

                # 3. Receive client's text frame
                msg2 = await receive()
                app_messages.append(msg2)

                # 4. Send back text reply
                await send({"type": "websocket.send", "text": "World"})

                # 5. Receive close/disconnect frame
                msg3 = await receive()
                app_messages.append(msg3)

            worker = ASGIWorker(
                age=0, ppid=999, sockets=[], app_path="dummy_app:app", timeout=30
            )
            worker.app = dummy_websocket_app

            await worker.handle_asgi_request(mock_sock)

            # Verify app received correct events
            self.assertEqual(app_messages[0]["type"], "websocket.connect")
            self.assertEqual(app_messages[1]["type"], "websocket.receive")
            self.assertEqual(app_messages[1]["text"], "Hello")
            self.assertEqual(app_messages[2]["type"], "websocket.disconnect")

            # Verify sent chunks
            # Chunk 1: Handshake response
            # Chunk 2: Frame containing "World"
            # Chunk 3: Close frame response
            handshake_resp = sent_chunks[0]
            self.assertTrue(
                handshake_resp.startswith(
                    b"HTTP/1.1 101 Switching Protocols\r\n")
            )
            self.assertIn(b"Upgrade: websocket\r\n", handshake_resp)
            self.assertIn(b"Connection: Upgrade\r\n", handshake_resp)

            # Verify Switching Protocols key accept calculation
            guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            expected_accept = base64.b64encode(
                hashlib.sha1((client_key + guid).encode("utf-8")).digest()
            )
            self.assertIn(expected_accept, handshake_resp)

            reply_frame = sent_chunks[1]
            self.assertEqual(reply_frame[0], 0x81)  # fin, opcode=1
            self.assertEqual(reply_frame[1], len("World"))  # unmasked
            self.assertEqual(reply_frame[2:], b"World")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
