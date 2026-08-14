import struct
import unittest
from unittest import mock
import socket

from asteri.workers.gthread import GThreadWorker
from asteri.workers.sync import SyncWorker
from asteri.uwsgi import UWSGIHandler


class TestGeventWorker(unittest.TestCase):
    def tearDown(self):
        try:
            import asteri.workers.gevent as g
            g.GEVENT_AVAILABLE = True  # guard against reload side effects
        except Exception:
            pass

    def test_init_process_no_gevent_raises(self):
        from asteri.workers.gevent import GeventWorker

        with mock.patch("asteri.workers.gevent.GEVENT_AVAILABLE", False):
            w = GeventWorker(0, 999, [], "dummy:app", 30)
            with self.assertRaises(RuntimeError):
                w.init_process()

    def test_run_no_gevent_logs_error(self):
        from asteri.workers.gevent import GeventWorker

        with mock.patch("asteri.workers.gevent.GEVENT_AVAILABLE", False):
            with mock.patch("asteri.workers.gevent.logger") as lg:
                GeventWorker(0, 999, [], "dummy:app", 30).run()
        lg.error.assert_called_once()

    def test_init_process_patch_all_and_super(self):
        from asteri.workers.gevent import GeventWorker

        fake_gevent = mock.Mock()
        with mock.patch("asteri.workers.gevent.GEVENT_AVAILABLE", True):
            with mock.patch("asteri.workers.gevent.gevent", fake_gevent):
                with mock.patch.object(SyncWorker, "init_process") as super_init:
                    GeventWorker(0, 999, [], "dummy:app", 30).init_process()
                    fake_gevent.monkey.patch_all.assert_called_once()
                    super_init.assert_called_once()

    def test_run_spawns_servers_and_handles_client(self):
        from asteri.workers.gevent import GeventWorker

        class FakeGevent:
            monkey = mock.Mock()

            def spawn(self, fn, *a):
                return mock.Mock()

            def sleep(self, *a):
                pass

        w = GeventWorker(0, 999, [], "dummy:app", 30)
        w.ppid = -1  # ensures the master-check exits the wait loop
        sock = mock.Mock()
        w.sockets = [sock]
        w.handle_request = mock.Mock()
        w.release_connection = mock.Mock()
        w.acquire_connection = mock.Mock(return_value=True)

        fake_stream_server = mock.Mock()
        with mock.patch("asteri.workers.gevent.gevent", FakeGevent()):
            with mock.patch("asteri.workers.gevent.StreamServer",
                            fake_stream_server) as SS:
                with mock.patch("asteri.workers.gevent.os.getppid",
                                return_value=-2):
                    w.run()

        self.assertEqual(SS.call_count, 1)
        handler = SS.call_args_list[0].args[1]
        client = mock.Mock()
        handler(client, ("1.2.3.4", 11))
        w.acquire_connection.assert_called_once()
        w.handle_request.assert_called_once()
        w.release_connection.assert_called_once()

    def test_run_wait_loop_calls_sleep(self):
        from asteri.workers.gevent import GeventWorker

        fake_gevent = mock.Mock()
        fake_gevent.spawn.return_value = mock.Mock()
        w = GeventWorker(0, 999, [], "dummy:app", 30)
        w.ppid = 999
        w.sockets = []
        with mock.patch("asteri.workers.gevent.gevent", fake_gevent):
            with mock.patch("asteri.workers.gevent.os.getppid",
                            side_effect=[999, -2]):
                w.run()
        fake_gevent.sleep.assert_called_once_with(1.0)

    def test_run_handler_swallows_exception(self):
        from asteri.workers.gevent import GeventWorker

        w = GeventWorker(0, 999, [], "dummy:app", 30)
        w.ppid = -1
        sock = mock.Mock()
        w.sockets = [sock]
        w.handle_request = mock.Mock(side_effect=RuntimeError("boom"))
        w.acquire_connection = mock.Mock(return_value=True)
        w.release_connection = mock.Mock()

        with mock.patch("asteri.workers.gevent.gevent") as g_fake:
            with mock.patch("asteri.workers.gevent.StreamServer") as g_fake:
                g_fake.monkey = mock.Mock()
                g_fake.spawn = lambda fn: mock.Mock()
                with mock.patch("asteri.workers.gevent.os.getppid", return_value=-2):
                    with mock.patch("asteri.workers.gevent.StreamServer") as SS:
                        with mock.patch.object(w, "alive", True):
                            w.run()
        handler = SS.call_args_list[0].args[1]
        handler(mock.Mock(), ("1.2.3.4", 11))
        w.handle_request.assert_called_once()

    def test_run_handler_rejects_when_capacity_full(self):
        from asteri.workers.gevent import GeventWorker

        w = GeventWorker(0, 999, [], "dummy:app", 30)
        w.ppid = -1
        sock = mock.Mock()
        w.sockets = [sock]
        w.handle_request = mock.Mock()
        w.acquire_connection = mock.Mock(return_value=False)

        with mock.patch("asteri.workers.gevent.os.getppid", return_value=-2):
            with mock.patch("asteri.workers.gevent.StreamServer") as SS:
                w.run()
        handler = SS.call_args_list[0].args[1]
        handler(mock.Mock(), ("1.2.3.4", 11))
        w.acquire_connection.assert_called_once()
        w.handle_request.assert_not_called()


class TestGThreadWorker(unittest.TestCase):
    def test_run_accept_and_submit(self):
        sock = mock.Mock()
        sock.accept.return_value = ("client", "addr")

        exec_mock = mock.Mock()
        exec_mock.__enter__ = mock.Mock(return_value=exec_mock)
        exec_mock.__exit__ = mock.Mock(return_value=False)

        def submit(fn, client, listener):
            fn(client, listener)

        exec_mock.submit.side_effect = submit
        w = GThreadWorker(0, 999, [sock], "dummy:app", 30)
        w.ppid = -1
        w.handle_request = mock.Mock()

        with mock.patch("asteri.workers.gthread.select.select",
                        return_value=([sock], [], [])):
            with mock.patch("asteri.workers.gthread.ThreadPoolExecutor",
                            return_value=exec_mock):
                with mock.patch("asteri.workers.gthread.os.getppid",
                                return_value=-2):
                    w.run()
        w.handle_request.assert_called_once()
        exec_mock.submit.assert_called()
        self.assertEqual(w.metrics_active_connections, 0)

    def test_run_select_exception_continues(self):
        sock = mock.Mock()
        calls = {"n": 0}

        def flaky_select(*a):
            calls["n"] += 1
            if calls["n"] == 1:
                raise socket.timeout("timeout")
            return ([], [], [])

        w = GThreadWorker(0, 999, [sock], "dummy:app", 30)
        w.ppid = -1
        with mock.patch("asteri.workers.gthread.select.select", flaky_select):
            with mock.patch("asteri.workers.gthread.ThreadPoolExecutor"):
                with mock.patch("asteri.workers.gthread.os.getppid",
                                return_value=-2):
                    w.run()
        self.assertGreaterEqual(calls["n"], 2)

    def test_run_select_generic_exception_sleeps(self):
        sock = mock.Mock()
        calls = {"n": 0}

        def flaky_select(*a):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return ([], [], [])

        w = GThreadWorker(0, 999, [sock], "dummy:app", 30)
        w.ppid = -1
        with mock.patch("asteri.workers.gthread.select.select", flaky_select):
            with mock.patch("asteri.workers.gthread.ThreadPoolExecutor"):
                with mock.patch("asteri.workers.gthread.os.getppid",
                                return_value=-2):
                    with mock.patch("asteri.workers.gthread.time.sleep") as ts:
                        w.run()
        self.assertGreaterEqual(calls["n"], 2)
        ts.assert_called_once_with(0.1)

    def test_run_guarded_exception(self):
        w = GThreadWorker(0, 999, [], "dummy:app", 30)
        client = mock.Mock()
        w.handle_request = mock.Mock(side_effect=RuntimeError("x"))
        w._run_guarded(client, mock.Mock())
        w.handle_request.assert_called_once()
        self.assertEqual(w.metrics_active_connections, 0)

    def test_run_guarded_capacity_full(self):
        w = GThreadWorker(0, 999, [], "dummy:app", 30, worker_connections=1)
        w.metrics_active_connections = 1
        w.handle_request = mock.Mock()
        w._run_guarded(mock.Mock(), mock.Mock())
        w.handle_request.assert_not_called()


def make_uwsgi_packet(modifier1, vars_dict):
    var_data = b""
    for k, v in vars_dict.items():
        kb = k.encode("latin-1")
        vb = v.encode("latin-1")
        var_data += struct.pack("<H", len(kb)) + kb
        var_data += struct.pack("<H", len(vb)) + vb
    return struct.pack("<BHB", modifier1, len(var_data), 0) + var_data


class TestUWSGIHandler(unittest.TestCase):
    def test_parse_happy_python_fallback(self):
        packet = make_uwsgi_packet(0, {"REQUEST_METHOD": "GET",
                                       "PATH_INFO": "/x"})
        with mock.patch("asteri.uwsgi.FAST_PARSER_AVAILABLE", False):
            vars_dict, mod = UWSGIHandler.parse(packet)
        self.assertEqual(mod, 0)
        self.assertEqual(vars_dict["REQUEST_METHOD"], "GET")
        self.assertEqual(vars_dict["PATH_INFO"], "/x")

    def test_parse_data_too_short(self):
        with mock.patch("asteri.uwsgi.FAST_PARSER_AVAILABLE", False):
            self.assertEqual(UWSGIHandler.parse(b"\x00\x01"),
                             (None, None))

    def test_parse_incomplete_size(self):
        packet = struct.pack("<BHB", 0, 100, 0) + b"short"
        with mock.patch("asteri.uwsgi.FAST_PARSER_AVAILABLE", False):
            self.assertEqual(UWSGIHandler.parse(packet), (None, None))

    def test_parse_raises_falls_back(self):
        packet = make_uwsgi_packet(0, {"A": "B"})
        with mock.patch("asteri.uwsgi.fastparser.parse_uwsgi",
                        side_effect=ValueError("bad")):
            with mock.patch("asteri.uwsgi.logger") as lg:
                vars_dict, mod = UWSGIHandler.parse(packet)
        lg.debug.assert_called_once()
        self.assertEqual(vars_dict["A"], "B")

    def test_parse_python_fastparser_returns_none(self):
        packet = make_uwsgi_packet(1, {"K": "V"})
        with mock.patch("asteri.uwsgi.fastparser.parse_uwsgi",
                        return_value=None):
            vars_dict, mod = UWSGIHandler.parse(packet)
        self.assertEqual(mod, 1)
        self.assertEqual(vars_dict, {"K": "V"})

    def test_parse_truncated_key_len(self):
        var_data = b"\x00"
        packet = struct.pack("<BHB", 0, 1, 0) + var_data
        with mock.patch("asteri.uwsgi.FAST_PARSER_AVAILABLE", False):
            vars_dict, mod = UWSGIHandler.parse(packet)
        self.assertEqual(vars_dict, {})

    def test_parse_truncated_key(self):
        var_data = b"\x0a\x00ab"
        packet = struct.pack("<BHB", 0, len(var_data), 0) + var_data
        with mock.patch("asteri.uwsgi.FAST_PARSER_AVAILABLE", False):
            vars_dict, mod = UWSGIHandler.parse(packet)
        self.assertEqual(vars_dict, {})

    def test_parse_truncated_val_len(self):
        var_data = b"\x02\x00ab\x00"
        packet = struct.pack("<BHB", 0, len(var_data), 0) + var_data
        with mock.patch("asteri.uwsgi.FAST_PARSER_AVAILABLE", False):
            vars_dict, mod = UWSGIHandler.parse(packet)
        self.assertEqual(vars_dict, {})

    def test_parse_truncated_val(self):
        var_data = b"\x02\x00ab\x03\x00a"
        packet = struct.pack("<BHB", 0, len(var_data), 0) + var_data
        with mock.patch("asteri.uwsgi.FAST_PARSER_AVAILABLE", False):
            vars_dict, mod = UWSGIHandler.parse(packet)
        self.assertEqual(vars_dict, {})

    def test_import_failure_disables_fastparser(self):
        import asteri.uwsgi as mod
        import importlib
        import builtins

        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(),
                        level=0):
            if name == "asteri" and "fastparser" in (fromlist or ()):
                raise ImportError("disabled")
            return real_import(name, globals, locals, fromlist, level)

        try:
            with mock.patch("builtins.__import__", side_effect=fake_import):
                importlib.reload(mod)
                self.assertFalse(mod.FAST_PARSER_AVAILABLE)
        finally:
            importlib.reload(mod)

    def test_is_uwsgi(self):
        self.assertFalse(UWSGIHandler.is_uwsgi(b"\x01"))
        self.assertFalse(UWSGIHandler.is_uwsgi(b"\x01\x00\x00\x00"))
        self.assertTrue(UWSGIHandler.is_uwsgi(b"\x00\x02\x00\x00"))


if __name__ == "__main__":
    unittest.main()