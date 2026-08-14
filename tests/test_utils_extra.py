import errno
import logging
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock
from unittest.mock import MagicMock

from asteri import utils
from asteri.utils import (
    NonBlockingStream,
    NoColorFormatter,
    PrettyFormatter,
    StatsdClient,
    import_app,
    make_websocket_frame,
    parse_proxy_protocol,
    parse_websocket_frame,
    print_banner,
    set_proctitle,
    setup_access_logging,
    setup_logging,
)


class TestNonBlockingStream(unittest.TestCase):
    def _make(self, **attrs):
        s = MagicMock()
        s.fileno.side_effect = AttributeError  # skip fcntl setup
        for k, v in attrs.items():
            getattr(s, k).side_effect = v
        return NonBlockingStream(s)

    def test_write_blocking_io_error_returns_len(self):
        stream = self._make(write=BlockingIOError(11, "again"))
        self.assertEqual(stream.write(b"xyz"), 3)

    def test_write_eagain_and_enospc(self):
        for err in (errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOSPC):
            stream = self._make(write=OSError(err, "full"))
            self.assertEqual(stream.write(b"abc"), 3)

    def test_write_other_oserror_raises(self):
        stream = self._make(write=OSError(errno.EIO, "io"))
        with self.assertRaises(OSError):
            stream.write(b"x")

    def test_flush_swallows(self):
        stream = self._make(flush=BlockingIOError(11, "again"))
        stream.flush()

    def test_fileno_and_getattr(self):
        class FdStream:
            value = "attr-value"

            def fileno(self):
                return 9

        s = NonBlockingStream(FdStream())
        self.assertEqual(s.fileno(), 9)
        self.assertEqual(s.value, "attr-value")

    def test_isatty_failure(self):
        stream = self._make(isatty=OSError(9, "bad fd"))
        self.assertFalse(stream.isatty())


class TestFormatters(unittest.TestCase):
    def test_pretty_formatter_includes_level(self):
        rec = logging.LogRecord("x", logging.INFO, "f", 1,
                                "hello world", None, None)
        fmt = PrettyFormatter(datefmt="%H:%M:%S")
        out = fmt.format(rec)
        self.assertIn("hello world", out)
        self.assertIn("INFO", out)
        self.assertIn("\033[", out)  # colored

    def test_no_color_formatter_strips_ansi(self):
        rec = logging.LogRecord("x", logging.ERROR, "f", 1,
                                "\033[31mred\033[0m", None, None)
        fmt = NoColorFormatter("%(message)s")
        self.assertEqual(fmt.format(rec), "red")


class TestLoggingSetup(unittest.TestCase):
    def tearDown(self):
        # Restore the default stdout-based handlers after every test.
        if hasattr(self, "_saved_out"):
            sys.stdout, sys.stderr = self._saved_out
        setup_logging()

    def test_file_handler_and_capture(self):
        with tempfile.TemporaryDirectory() as d:
            log_file = os.path.join(d, "err.log")
            self._saved_out = (sys.stdout, sys.stderr)
            logger = setup_logging(level=logging.INFO, log_file=log_file,
                                   capture_output=True)
            self.assertIsInstance(sys.stdout, object)
            sys.stdout.write("captured-line-one\n")
            sys.stdout.flush()
            logger.error("direct-error")
            for h in logger.handlers:
                try:
                    h.flush()
                except Exception:
                    pass
            content = open(log_file).read()
            self.assertIn("captured-line-one", content)
            self.assertIn("direct-error", content)
            sys.stdout, sys.stderr = self._saved_out
            del self._saved_out

    def test_access_log_disabled_env(self):
        with mock.patch.dict(os.environ,
                             {"ASTERI_NO_ACCESS_LOG": "1"}):
            logger = setup_access_logging()
            self.assertTrue(logger.disabled)

    def test_access_log_to_file(self):
        acc = logging.getLogger("asteri.access")
        acc.disabled = False
        for h in list(acc.handlers):
            acc.removeHandler(h)
        with tempfile.TemporaryDirectory() as d:
            log_file = os.path.join(d, "access.log")
            logger = setup_access_logging(log_file=log_file)
            logger.info("test-request-line")
            for h in logger.handlers:
                try:
                    h.flush()
                except Exception:
                    pass
            self.assertIn("test-request-line", open(log_file).read())


class TestMiscHelpers(unittest.TestCase):
    def test_print_banner(self):
        import io as io_mod

        buf = io_mod.StringIO()
        with mock.patch("sys.stdout", buf):
            print_banner()
        self.assertIn("ASTERI", buf.getvalue())

    def test_set_proctitle_success(self):
        fake = mock.Mock()
        with mock.patch.dict(sys.modules, {"setproctitle": fake}):
            set_proctitle("master")
        fake.setproctitle.assert_called_once()

    def test_set_proctitle_import_error(self):
        def fake_import(name, *a, **k):
            if name == "setproctitle":
                raise ImportError("no")
            return __import__(name, *a, **k)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            set_proctitle("worker")

    def test_import_app(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "fake_app_module_for_import.py"),
                      "w") as f:
                f.write('app = "the-app"\n')
            with mock.patch("os.getcwd", return_value=d):
                self.assertEqual(import_app("fake_app_module_for_import:app"),
                                 "the-app")

    def test_import_app_reexecutes_when_cached(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "cached_mod_for_import.py"),
                      "w") as f:
                f.write('app = "newed"\n')
            old_path = list(sys.path)
            sys.path.insert(0, d)
            try:
                import cached_mod_for_import  # type: ignore[import-not-found]
            finally:
                sys.path[:] = old_path

            cached_mod_for_import.app = "old"  # stale in-memory value
            with mock.patch("os.getcwd", return_value=d):
                result = import_app("cached_mod_for_import:app")
            self.assertEqual(result, "newed")  # proved fresh re-import
            sys.modules.pop("cached_mod_for_import", None)

    def test_import_app_failure_raises(self):
        with mock.patch("asteri.utils.logger"):
            with self.assertRaises(Exception):
                import_app("does.not.exist:app")

    def test_get_num_workers(self):
        self.assertEqual(utils.get_num_workers(),
                         (os.cpu_count() * 2 + 1))


class TestStatsdClient(unittest.TestCase):
    def test_send_calls(self):
        client = StatsdClient("127.0.0.1", 8125, "ast")
        client.sock = mock.Mock()
        client.increment("req", 2)
        client.gauge("workers", 3.0)
        client.timing("lat", 12.5)
        calls = [c.args[0].decode("utf-8")
                 for c in client.sock.sendto.call_args_list]
        self.assertIn("ast.req:2|c", calls)
        self.assertIn("ast.workers:3.0|g", calls)
        self.assertIn("ast.lat:12.5|ms", calls)

    def test_send_oserror_swallowed(self):
        client = StatsdClient("127.0.0.1", 8125, "ast")
        client.sock = mock.Mock()
        client.sock.sendto.side_effect = OSError("down")
        client.increment("k")


class TestProxyProtocol(unittest.TestCase):
    def test_v1_no_crlf(self):
        self.assertEqual(parse_proxy_protocol(b"PROXY TCP4"),
                         (None, None, b"PROXY TCP4"))

    def test_v1_too_few_parts(self):
        data = b"PROXY TCP4 1.2.3.4\r\nrest"
        out = parse_proxy_protocol(data)
        self.assertEqual(out[:2], (None, None))
        self.assertIn(b"rest", out[2])

    def test_v2_short(self):
        data = b"\r\n\r\n\x00\r\nQUIT\n\x00"
        self.assertEqual(parse_proxy_protocol(data), (None, None, data))

    def test_v2_incomplete(self):
        data = (b"\r\n\r\n\x00\r\nQUIT\n" + b"\x11\x00" +
                b"\x00\x40" + b"x" * 10)  # len_val=64 but only 16 present
        self.assertEqual(parse_proxy_protocol(data)[:2], (None, None))

    def test_v2_ipv6(self):
        body = (bytes(16) + bytes(16) + struct.pack(">HH", 1234, 80))
        data = (b"\r\n\r\n\x00\r\nQUIT\n" + b"\x21\x21" +
                struct.pack(">H", len(body)) + body + b"trail")
        client, server, remaining = parse_proxy_protocol(data)
        self.assertEqual(client[1], 1234)
        self.assertEqual(server[1], 80)
        self.assertEqual(remaining, b"trail")


class TestWebSocketFrames(unittest.TestCase):
    def test_make_str(self):
        frame = make_websocket_frame("hi")
        self.assertEqual(frame, b"\x81\x02hi")

    def test_make_medium_length(self):
        payload = b"a" * 200
        frame = make_websocket_frame(payload)
        self.assertEqual(frame[1], 126)
        self.assertEqual(int.from_bytes(frame[2:4], "big"), 200)

    def test_make_long_length(self):
        payload = b"a" * 70000
        frame = make_websocket_frame(payload)
        self.assertEqual(frame[1], 127)
        self.assertEqual(int.from_bytes(frame[2:10], "big"), 70000)

    def test_parse_medium_masked(self):
        payload = b"payload-200"
        frame = make_websocket_frame(payload, opcode=2)
        # Make it masked (client-style)
        mask = b"\x01\x02\x03\x04"
        header = bytearray(frame[:2])
        header[0] |= 0x80
        header[1] |= 0x80
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        full = bytes(header) + mask + masked
        opcode, out, remaining = parse_websocket_frame(full)
        self.assertEqual(opcode, 2)
        self.assertEqual(out, payload)
        self.assertEqual(remaining, b"")

    def test_parse_long_masked(self):
        payload = b"z" * 70000
        mask = b"\x05\x06\x07\x08"

        def frame(plen):
            b = bytearray()
            b.append(0x82)
            if plen <= 65535:
                b.append(0x80 | 126)
                b.extend(plen.to_bytes(2, "big"))
            else:
                b.append(0x80 | 127)
                b.extend(plen.to_bytes(8, "big"))
            return bytes(b)

        full = frame(len(payload)) + mask + \
            bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
        opcode, out, remaining = parse_websocket_frame(full)
        self.assertEqual(opcode, 2)
        self.assertEqual(out, payload)

    def test_parse_short_and_partial(self):
        self.assertEqual(parse_websocket_frame(b"\x81")[0], None)
        # 16-bit length declared but truncated
        data = b"\x81\xfe\x04\x00" + b"ab"
        self.assertEqual(parse_websocket_frame(data)[0], None)
        # 64-bit length declared but truncated
        data = b"\x81\xff" + b"\x00" * 6 + b"xx"
        self.assertEqual(parse_websocket_frame(data)[0], None)
        # mask key missing
        data = b"\x81\x80ab"
        self.assertEqual(parse_websocket_frame(data)[0], None)
        # payload truncated
        data = b"\x81\x83" + b"\x00\x00\x00\x00ab"
        self.assertEqual(parse_websocket_frame(data)[0], None)

    def test_parse_extended_length_header_too_short(self):
        # 16-bit extended length: only 3 bytes (< 4) available
        data = b"\x81\xfe\x04"
        self.assertEqual(parse_websocket_frame(data), (None, None, data))
        # 64-bit extended length: only 7 bytes (< 10) available
        data = b"\x81\xff" + b"\x00" * 5
        self.assertEqual(parse_websocket_frame(data), (None, None, data))

    def test_parse_unmasked(self):
        opcode, out, remaining = parse_websocket_frame(b"\x81\x02hi")
        self.assertEqual((opcode, out), (1, b"hi"))


if __name__ == "__main__":
    unittest.main()