import io
import inspect
import os
import sys
import tempfile
import unittest
from unittest import mock

from asteri import __main__ as m


class FakeASGI:
    def __call__(self, scope, receive, send):
        pass


class FakeASGIByClass:
    def __call__(self, scope, receive, send):
        pass


FakeASGIByClass.__name__ = "FastAPI"


class FakeWSGI:
    def __call__(self, environ, start_response):
        return []


def _patch_main(import_app=None, arbiter=None):
    """Common mocks so main() never does real work outside the paths tested."""
    stack = mock.patch("asteri.__main__.print_banner")
    import_app_p = mock.patch(
        "asteri.__main__.import_app",
        import_app if import_app is not None else mock.Mock(
            return_value=FakeWSGI()))
    arbiter_p = mock.patch(
        "asteri.__main__.Arbiter",
        arbiter if arbiter is not None else mock.Mock())
    setup_logging_p = mock.patch("asteri.__main__.setup_logging")
    setup_access_p = mock.patch("asteri.__main__.setup_access_logging")
    for p in (stack, import_app_p, arbiter_p, setup_logging_p,
              setup_access_p):
        p.start()
    return arbiter_p.new if arbiter is not None else arbiter_p, \
        import_app_p.new, setup_logging_p.new


def _run_main(argv, exit_ok=True):
    exit_ = mock.Mock(side_effect=SystemExit(0))
    with mock.patch.object(sys, "argv", argv):
        with mock.patch("sys.exit", exit_):
            try:
                m.main()
            except SystemExit:
                pass
    return exit_


class TestMainCLI(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()

    def test_starts_arbiter_with_args(self):
        arb_mock = mock.Mock()
        with mock.patch("asteri.__main__.print_banner"):
            with mock.patch("asteri.__main__.import_app",
                            return_value=FakeWSGI()):
                with mock.patch("asteri.__main__.Arbiter", arb_mock):
                    with mock.patch("asteri.__main__.setup_logging"):
                        with mock.patch("asteri.__main__.setup_access_logging"):
                            with mock.patch.object(
                                sys, "argv",
                                ["asteri", "example_wsgi:app",
                                 "-b", "127.0.0.1:9100", "-w", "3",
                                 "-k", "gthread", "--threads", "4"]):
                                m.main()
        arb = arb_mock.call_args
        self.assertEqual(arb.args[0], "example_wsgi:app")
        kwargs = arb.kwargs
        self.assertEqual(kwargs["num_workers"], 3)
        self.assertEqual(kwargs["binds"], ["127.0.0.1:9100"])
        self.assertEqual(kwargs["threads"], 4)
        arb_mock.return_value.start.assert_called_once()

    def test_bind_str_converted_to_list(self):
        arb_mock = mock.Mock()
        with mock.patch("asteri.__main__.print_banner"):
            with mock.patch("asteri.__main__.import_app",
                            return_value=FakeWSGI()):
                with mock.patch("asteri.__main__.Arbiter", arb_mock):
                    with mock.patch("asteri.__main__.setup_logging"):
                        with mock.patch("asteri.__main__.setup_access_logging"):
                            with mock.patch.object(
                                sys, "argv",
                                ["asteri", "app:app", "--bind",
                                 "127.0.0.1:9123"]):
                                m.main()
        self.assertEqual(arb_mock.call_args.kwargs["binds"],
                         ["127.0.0.1:9123"])

    def test_default_bind(self):
        arb_mock = mock.Mock()
        with mock.patch("asteri.__main__.print_banner"):
            with mock.patch("asteri.__main__.import_app",
                            return_value=FakeWSGI()):
                with mock.patch("asteri.__main__.Arbiter", arb_mock):
                    with mock.patch("asteri.__main__.setup_logging"):
                        with mock.patch("asteri.__main__.setup_access_logging"):
                            with mock.patch.object(
                                sys, "argv", ["asteri", "app:app"]):
                                m.main()
        self.assertEqual(arb_mock.call_args.kwargs["binds"],
                         ["127.0.0.1:8000"])

    def test_config_file_applies_values(self):
        arb_mock = mock.Mock()
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "conf.py")
            with open(cfg, "w") as f:
                f.write('workers = 4\n')
                f.write('backlog = 1234\n')
                f.write('keep_alive = 7\n')
            with mock.patch("asteri.__main__.print_banner"):
                with mock.patch("asteri.__main__.import_app",
                                return_value=FakeWSGI()):
                    with mock.patch("asteri.__main__.Arbiter", arb_mock):
                        with mock.patch("asteri.__main__.setup_logging"):
                            with mock.patch("asteri.__main__.setup_access_logging"):
                                with mock.patch.object(
                                    sys, "argv",
                                    ["asteri", "app:app", "-c", cfg]):
                                    m.main()
        kwargs = arb_mock.call_args.kwargs
        self.assertEqual(kwargs["num_workers"], 4)
        self.assertEqual(kwargs["backlog"], 1234)
        self.assertEqual(kwargs["keep_alive"], 7)

    def test_config_bind_str_converted_to_list(self):
        arb_mock = mock.Mock()
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "conf.py")
            with open(cfg, "w") as f:
                f.write('bind = "0.0.0.0:9999"\n')
            with mock.patch("asteri.__main__.print_banner"):
                with mock.patch("asteri.__main__.import_app",
                                return_value=FakeWSGI()):
                    with mock.patch("asteri.__main__.Arbiter", arb_mock):
                        with mock.patch("asteri.__main__.setup_logging"):
                            with mock.patch("asteri.__main__.setup_access_logging"):
                                with mock.patch.object(
                                    sys, "argv",
                                    ["asteri", "app:app", "-c", cfg]):
                                    m.main()
        self.assertEqual(arb_mock.call_args.kwargs["binds"],
                         ["0.0.0.0:9999"])

    def test_config_file_cli_wins(self):
        arb_mock = mock.Mock()
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "conf.py")
            with open(cfg, "w") as f:
                f.write("workers = 9\n")
            with mock.patch("asteri.__main__.print_banner"):
                with mock.patch("asteri.__main__.import_app",
                                return_value=FakeWSGI()):
                    with mock.patch("asteri.__main__.Arbiter", arb_mock):
                        with mock.patch("asteri.__main__.setup_logging"):
                            with mock.patch("asteri.__main__.setup_access_logging"):
                                with mock.patch.object(
                                    sys, "argv",
                                    ["asteri", "app:app", "-c", cfg,
                                     "-w", "2"]):
                                    m.main()
        self.assertEqual(arb_mock.call_args.kwargs["num_workers"], 2)

    def test_print_config(self):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            with mock.patch("asteri.__main__.print_banner"):
                with mock.patch.object(
                    sys, "argv",
                    ["asteri", "app:app", "--print-config"]):
                    exit_ = _run_main(["asteri", "app:app", "--print-config"])
        self.assertIn("workers", buf.getvalue())
        exit_.assert_called_once()

    def test_check_config_exits(self):
        exit_ = _run_main(["asteri", "app:app", "--check-config"])
        exit_.assert_called_once_with(0)

    def test_chdir_env(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("os.chdir") as chdir:
                with mock.patch("asteri.__main__.print_banner"):
                    with mock.patch("asteri.__main__.import_app",
                                    return_value=FakeWSGI()):
                        with mock.patch("asteri.__main__.Arbiter"):
                            with mock.patch("asteri.__main__.setup_logging"):
                                with mock.patch("asteri.__main__.setup_access_logging"):
                                    with mock.patch.object(
                                        sys, "argv",
                                        ["asteri", "app:app",
                                         "--chdir", d, "-e", "FOO=bar"]):
                                        m.main()
            chdir.assert_called_once_with(d)
        self.assertEqual(os.environ.get("FOO"), "bar")
        os.environ.pop("FOO", None)

    def test_no_app_prints_help(self):
        with mock.patch("asteri.__main__.print_banner"):
            with mock.patch("asteri.__main__.Arbiter"):
                with mock.patch.object(
                    sys, "argv", ["asteri", "-w", "2"]):
                    exit_ = _run_main(["asteri", "-w", "2"])
        exit_.assert_called_once_with(1)

    def test_worker_class_not_available(self):
        with mock.patch("asteri.__main__.print_banner"):
            with mock.patch("asteri.__main__.TornadoWorker", None):
                with mock.patch("asteri.__main__.logger") as lg:
                    exit_ = _run_main(["asteri", "app:app", "-k", "tornado"])
        lg.error.assert_called_once()
        exit_.assert_called_once_with(1)

    def test_worker_class_none_after_successful_import(self):
        with mock.patch("asteri.__main__.print_banner"):
            with mock.patch("asteri.__main__.import_app",
                            return_value=FakeWSGI()):
                with mock.patch("asteri.__main__.TornadoWorker", None):
                    with mock.patch("asteri.__main__.logger") as lg:
                        exit_ = _run_main(
                            ["asteri", "app:app", "-k", "tornado"])
        lg.error.assert_called_once()
        exit_.assert_called_once_with(1)

    def test_asgi_signature_value_error_swallowed(self):
        arb_mock = mock.Mock()
        real_signature = inspect.signature

        def _signature_raising_for_app(target, *args, **kwargs):
            if isinstance(target, FakeWSGI):
                raise ValueError("boom")
            return real_signature(target, *args, **kwargs)

        with mock.patch("asteri.__main__.print_banner"):
            with mock.patch("asteri.__main__.import_app",
                            return_value=FakeWSGI()):
                with mock.patch("asteri.__main__.Arbiter", arb_mock):
                    with mock.patch("asteri.__main__.setup_logging"):
                        with mock.patch("asteri.__main__.setup_access_logging"):
                            with mock.patch("inspect.signature",
                                            side_effect=_signature_raising_for_app):
                                with mock.patch.object(
                                    sys, "argv",
                                    ["asteri", "app:app", "-k", "sync"]):
                                    m.main()
        self.assertEqual(arb_mock.call_args.args[1].__name__, "SyncWorker")

    def test_asgi_promote_fastapi_class(self):
        arb_mock = mock.Mock()
        with mock.patch("asteri.__main__.print_banner"):
            with mock.patch("asteri.__main__.import_app",
                            return_value=FakeASGIByClass()):
                with mock.patch("asteri.__main__.Arbiter", arb_mock):
                    with mock.patch("asteri.__main__.setup_logging"):
                        with mock.patch("asteri.__main__.setup_access_logging"):
                            with mock.patch("asteri.__main__.logger"):
                                with mock.patch.object(
                                    sys, "argv",
                                    ["asteri", "app:app", "-k", "sync"]):
                                    m.main()
        self.assertEqual(arb_mock.call_args.args[1].__name__, "ASGIWorker")

    def test_asgi_promote_three_params(self):
        arb_mock = mock.Mock()
        with mock.patch("asteri.__main__.print_banner"):
            with mock.patch("asteri.__main__.import_app",
                            return_value=FakeASGI()):
                with mock.patch("asteri.__main__.Arbiter", arb_mock):
                    with mock.patch("asteri.__main__.setup_logging"):
                        with mock.patch("asteri.__main__.setup_access_logging"):
                            with mock.patch.object(
                                sys, "argv",
                                ["asteri", "app:app", "-k", "sync"]):
                                m.main()
        self.assertEqual(arb_mock.call_args.args[1].__name__, "ASGIWorker")

    def test_asgi_import_exception_swallowed(self):
        arb_mock = mock.Mock()
        with mock.patch("asteri.__main__.print_banner"):
            with mock.patch("asteri.__main__.import_app",
                            side_effect=ImportError("no app")):
                with mock.patch("asteri.__main__.Arbiter", arb_mock):
                    with mock.patch("asteri.__main__.setup_logging"):
                        with mock.patch("asteri.__main__.setup_access_logging"):
                            with mock.patch.object(
                                sys, "argv",
                                ["asteri", "app:app", "-k", "sync"]):
                                m.main()
        self.assertEqual(arb_mock.call_args.args[1].__name__, "SyncWorker")

    def test_preload_with_reload_warns(self):
        arb_mock = mock.Mock()
        with mock.patch("asteri.__main__.print_banner"):
            with mock.patch("asteri.__main__.import_app",
                            return_value=FakeWSGI()):
                with mock.patch("asteri.__main__.Arbiter", arb_mock):
                    with mock.patch("asteri.__main__.setup_logging"):
                        with mock.patch("asteri.__main__.setup_access_logging"):
                            with mock.patch("asteri.__main__.logger") as lg:
                                with mock.patch.object(
                                    sys, "argv",
                                    ["asteri", "app:app", "--preload",
                                     "--reload"]):
                                    m.main()
        lg.warning.assert_called_once()

    def test_preload_imports_app(self):
        imp = mock.Mock(return_value=FakeWSGI())
        with mock.patch("asteri.__main__.print_banner"):
            with mock.patch("asteri.__main__.import_app", imp):
                with mock.patch("asteri.__main__.Arbiter"):
                    with mock.patch("asteri.__main__.setup_logging"):
                        with mock.patch("asteri.__main__.setup_access_logging"):
                            with mock.patch.object(
                                sys, "argv",
                                ["asteri", "app:app", "--preload"]):
                                m.main()
        self.assertEqual(imp.call_count, 2)

    def test_keyboard_interrupt(self):
        arb_mock = mock.Mock()
        arb_mock.return_value.start.side_effect = KeyboardInterrupt
        with mock.patch("asteri.__main__.print_banner"):
            with mock.patch("asteri.__main__.import_app",
                            return_value=FakeWSGI()):
                with mock.patch("asteri.__main__.Arbiter", arb_mock):
                    with mock.patch("asteri.__main__.setup_logging"):
                        with mock.patch("asteri.__main__.setup_access_logging"):
                            with mock.patch.object(
                                sys, "argv", ["asteri", "app:app"]):
                                m.main()
        arb_mock.return_value.start.assert_called_once()

    def test_fatal_error_exits(self):
        arb_mock = mock.Mock()
        arb_mock.return_value.start.side_effect = RuntimeError("boom")
        with mock.patch("asteri.__main__.print_banner"):
            with mock.patch("asteri.__main__.import_app",
                            return_value=FakeWSGI()):
                with mock.patch("asteri.__main__.Arbiter", arb_mock):
                    with mock.patch("asteri.__main__.setup_logging"):
                        with mock.patch("asteri.__main__.setup_access_logging"):
                            with mock.patch("asteri.__main__.logger") as lg:
                                exit_ = _run_main(["asteri", "app:app"])
        lg.error.assert_called_once()
        exit_.assert_called_once_with(1)

    def test_import_app_error_path(self):
        exit_ = mock.Mock(side_effect=SystemExit(1))
        with mock.patch("asteri.__main__.logger"):
            with mock.patch("sys.exit", exit_):
                try:
                    m.import_app("no_such_module_xyz:app")
                except SystemExit:
                    pass
        exit_.assert_called_once_with(1)

    def test_import_app_success(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "cli_app_module.py"), "w") as f:
                f.write('app = lambda e, s: []\n')
            old = list(sys.path)
            sys.path.insert(0, d)
            try:
                with mock.patch("os.getcwd", return_value=d):
                    app = m.import_app("cli_app_module:app")
            finally:
                sys.path[:] = old
            self.assertTrue(callable(app))

    def test_module_entrypoint_runs(self):
        import runpy
        import warnings

        def fake_exit(code=0):
            raise SystemExit(code)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with mock.patch("sys.exit", side_effect=fake_exit):
                with self.assertRaises(SystemExit):
                    with mock.patch.object(
                            sys, "argv",
                            ["asteri", "app:app", "--check-config"]):
                        runpy.run_module("asteri", run_name="__main__")


if __name__ == "__main__":
    unittest.main()