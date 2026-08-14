import asyncio
import time
import types
import unittest
from unittest import mock

from asteri.http import HTTPRequest
from asteri.http3 import (
    H3Frame,
    HTTP3Handler,
    QPACK,
    QUICPacket,
)


class TestQPACKEdges(unittest.TestCase):
    def test_decode_short_prefix(self):
        self.assertEqual(QPACK.decode(b"\x00"), {})

    def test_decode_truncated_literal_name_len(self):
        self.assertEqual(QPACK.decode(b"\x00\x00\x00"), {})

    def test_decode_truncated_literal_value_len(self):
        self.assertEqual(QPACK.decode(b"\x00\x00\x00\x02ab"), {})

    def test_decode_truncated_name_ref_value_len(self):
        self.assertEqual(QPACK.decode(b"\x00\x00\x40"), {})

    def test_decode_fallback_truncated_name_len(self):
        self.assertEqual(QPACK.decode(b"\x00\x00\x30"), {})

    def test_decode_fallback_truncated_value_len(self):
        self.assertEqual(QPACK.decode(b"\x00\x00\x30\x02ab"), {})

    def test_decode_fallback_full(self):
        decoded = QPACK.decode(b"\x00\x00\x30\x02ab\x03val")
        self.assertEqual(decoded, {"ab": "val"})


class TestH3FrameEdges(unittest.TestCase):
    def test_parse_truncated_len(self):
        self.assertEqual(H3Frame.parse(b"\x01"), [])


class TestQUICEdges(unittest.TestCase):
    def test_parse_empty(self):
        self.assertIsNone(QUICPacket.parse(b""))

    def test_long_header_other_type_bits(self):
        data = (
            b"\xe0"  # long header, ptype_bits = 2 -> falls to INITIAL
            + b"\x00\x00\x00\x01"  # version
            + b"\x02" + b"ab"  # dcid_len + dcid
            + b"\x02" + b"cd"  # scid_len + scid
            + b"payload"
        )
        pkt = QUICPacket.parse(data)
        self.assertEqual(pkt.ptype, QUICPacket.TYPE_INITIAL)
        self.assertEqual(pkt.version, 1)


class TestSweeper(unittest.TestCase):
    def test_start_sweeper_twice(self):
        handler = HTTP3Handler(mock.Mock())

        async def run():
            handler.start_sweeper()
            handler.start_sweeper()
            self.assertTrue(handler._sweeper_started)

        asyncio.run(run())

    def test_start_sweeper_no_running_loop(self):
        handler = HTTP3Handler(mock.Mock())
        handler.start_sweeper()
        self.assertFalse(handler._sweeper_started)

    def test_sweep_connections_reaps_stale(self):
        handler = HTTP3Handler(mock.Mock())
        handler.connections = {
            ("a", 1): {"last_seen": time.time() - 1000},
            ("b", 2): {"last_seen": time.time()},
        }
        with mock.patch("asteri.http3.logger"):
            handler.sweep_connections()
        self.assertNotIn(("a", 1), handler.connections)
        self.assertIn(("b", 2), handler.connections)

    def test_sweep_loop_calls_sweep(self):
        handler = HTTP3Handler(mock.Mock())
        state = {"n": 0}

        def fake_sleep(delay):
            state["n"] += 1
            if state["n"] > 2:
                raise asyncio.CancelledError()
            fut = asyncio.get_running_loop().create_future()
            fut.set_result(None)
            return fut

        async def run():
            with mock.patch("asteri.http3.asyncio.sleep", side_effect=fake_sleep):
                handler.start_sweeper()
                current = asyncio.current_task()
                tasks = [t for t in asyncio.all_tasks() if t is not current]
                await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run(run())
        self.assertGreaterEqual(state["n"], 2)
        self.assertTrue(handler._sweeper_started)


class TestIsH3(unittest.TestCase):
    def test_is_h3_packet_empty(self):
        self.assertFalse(HTTP3Handler.is_h3_packet(b""))


class TestHandlePacketEdges(unittest.IsolatedAsyncioTestCase):
    async def test_empty_data_returns(self):
        handler = HTTP3Handler(mock.Mock())
        await handler.handle_packet(mock.Mock(), b"", ("127.0.0.1", 1))

    async def test_short_packet_no_conn_settings_reply(self):
        handler = HTTP3Handler(mock.Mock())
        addr = ("127.0.0.1", 555)
        payload = H3Frame.serialize(H3Frame.TYPE_SETTINGS, b"\x01\x00")
        pkt = QUICPacket(QUICPacket.TYPE_SHORT, dcid=b"01234567",
                         scid=b"", payload=payload)
        sock = mock.Mock()
        await handler.handle_packet(sock, pkt.serialize(), addr)
        self.assertIn(addr, handler.connections)
        self.assertEqual(handler.connections[addr]["state"], "ESTABLISHED")
        self.assertTrue(sock.sendto.called)

    async def test_parse_exception_logged(self):
        handler = HTTP3Handler(mock.Mock())
        with mock.patch("asteri.http3.QUICPacket.parse",
                        side_effect=RuntimeError("boom")):
            with mock.patch("asteri.http3.logger"):
                await handler.handle_packet(mock.Mock(), b"x",
                                            ("127.0.0.1", 1))


class TestDispatchAsgi(unittest.IsolatedAsyncioTestCase):
    async def test_receive_called(self):
        async def app(scope, receive, send):
            msg = await receive()
            self.assertEqual(msg["body"], b"abc")
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"x", b"y")]})
            await send({"type": "http.response.body", "body": b"",
                        "more_body": False})

        worker = types.SimpleNamespace(
            app=app, metrics_active_connections=0, stash=None,
            increment_request_metric=mock.Mock())
        handler = HTTP3Handler(worker)
        req = HTTPRequest("GET", "/x", "3.0", {}, b"abc")
        await handler.dispatch_asgi(mock.Mock(), ("127.0.0.1", 1),
                                    {"dcid": b"d"}, req)

    async def test_app_raises_metric_500(self):
        async def app(scope, receive, send):
            raise RuntimeError("app boom")

        worker = types.SimpleNamespace(
            app=app, metrics_active_connections=0, stash=None,
            increment_request_metric=mock.Mock())
        handler = HTTP3Handler(worker)
        req = HTTPRequest("GET", "/x", "3.0", {}, b"")
        with self.assertRaises(RuntimeError):
            await handler.dispatch_asgi(mock.Mock(), ("127.0.0.1", 1),
                                        {"dcid": b"d"}, req)
        worker.increment_request_metric.assert_called_once()
        self.assertEqual(worker.metrics_active_connections, 0)

    async def test_app_raises_metric_error_swallowed(self):
        async def app(scope, receive, send):
            raise RuntimeError("app boom")

        worker = types.SimpleNamespace(
            app=app, metrics_active_connections=0, stash=None,
            increment_request_metric=mock.Mock(
                side_effect=Exception("metrics down")))
        handler = HTTP3Handler(worker)
        req = HTTPRequest("GET", "/x", "3.0", {}, b"")
        with self.assertRaises(RuntimeError):
            await handler.dispatch_asgi(mock.Mock(), ("127.0.0.1", 1),
                                        {"dcid": b"d"}, req)
        self.assertEqual(worker.metrics_active_connections, 0)


if __name__ == "__main__":
    unittest.main()