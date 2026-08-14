import builtins
import importlib
import unittest
from unittest import mock

from asteri import http as asteri_http


def _assert_flag_after_blocked_reload(testcase, module, attr, predicate):
    real = builtins.__import__

    def fake(name, globals=None, locals=None, fromlist=(), level=0):
        if predicate(name, fromlist or ()):
            raise ImportError("blocked")
        return real(name, globals, locals, fromlist, level)

    try:
        with mock.patch("builtins.__import__", side_effect=fake):
            importlib.reload(module)
            testcase.assertFalse(getattr(module, attr))
    finally:
        importlib.reload(module)


class TestModuleImportFallbacks(unittest.TestCase):
    def test_fastparser_import_error(self):
        _assert_flag_after_blocked_reload(
            self, asteri_http, "FAST_PARSER_AVAILABLE",
            lambda n, fl: n == "asteri" and "fastparser" in fl)

    def test_h2_import_error(self):
        _assert_flag_after_blocked_reload(
            self, asteri_http, "H2_AVAILABLE",
            lambda n, fl: n.startswith("h2"))

    def test_gevent_import_error(self):
        import asteri.workers.gevent as g
        _assert_flag_after_blocked_reload(
            self, g, "GEVENT_AVAILABLE",
            lambda n, fl: n == "gevent" or n.startswith("gevent."))

    def test_tornado_import_error(self):
        import asteri.workers.tornado as t
        _assert_flag_after_blocked_reload(
            self, t, "TORNADO_AVAILABLE",
            lambda n, fl: n == "tornado" or n.startswith("tornado."))

    def test_watchdog_import_error(self):
        import asteri.arbiter as arb_mod
        _assert_flag_after_blocked_reload(
            self, arb_mod, "WATCHDOG_AVAILABLE",
            lambda n, fl: n == "watchdog" or n.startswith("watchdog."))

    def test_main_worker_fallbacks(self):
        import asteri.__main__ as m
        real = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(),
                        level=0):
            if name in ("asteri.workers.gevent", "asteri.workers.tornado"):
                raise ImportError("hidden")
            return real(name, globals, locals, fromlist, level)

        try:
            with mock.patch("builtins.__import__", side_effect=fake_import):
                importlib.reload(m)
            self.assertIsNone(m.GeventWorker)
            self.assertIsNone(m.TornadoWorker)
        finally:
            importlib.reload(m)


if __name__ == "__main__":
    unittest.main()
