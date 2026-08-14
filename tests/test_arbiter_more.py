import json
import os
import signal
import socket
import tempfile
import time
import types
import unittest
from unittest import mock

import psutil
import ssl

from asteri.arbiter import Arbiter
from asteri.workers.sync import SyncWorker


def _free_port():
    s = socket.socket()
    s.bind(("", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TestArbiterConstruct(unittest.TestCase):
    def test_statsd_host_stash_address(self):
        with mock.patch("asteri.utils.StatsdClient") as SC:
            with mock.patch("asteri.dirty.StashClient") as ST:
                arb = Arbiter("a:app", SyncWorker, statsd_host="h",
                              statsd_port=9, statsd_prefix="p",
                              stash_address="/tmp/x")
                ST.assert_called_once_with("/tmp/x")
            SC.assert_called_once_with("h", 9, "p")
            self.assertIsNotNone(arb.stash)

    def test_no_statsd_no_stash(self):
        arb = Arbiter("a:app", SyncWorker)
        self.assertIsNone(arb.statsd)
        self.assertIsNone(arb.stash)

    def test_defaults(self):
        arb = Arbiter("a:app", SyncWorker)
        self.assertEqual(arb.binds, ["127.0.0.1:8000"])
        self.assertEqual(arb.num_workers, 1)
        self.assertEqual(arb.proc_name, "master")
        self.assertEqual(arb.timeout, 30)
        self.assertTrue(arb.alive)


class TestStart(unittest.TestCase):
    def _start(self, arb, manage=True):
        arb.manage_workers = mock.Mock(return_value=manage)
        arb.setup_signals = mock.Mock()
        with mock.patch("asteri.arbiter.set_proctitle"):
            with mock.patch("asteri.arbiter.logger"):
                arb.start()
                return arb

    def test_start_daemon_calls_daemonize(self):
        arb = Arbiter("a:app", SyncWorker, daemon=True)
        with mock.patch("asteri.arbiter.set_proctitle"):
            with mock.patch("asteri.arbiter.logger"):
                with mock.patch.object(arb, "daemonize") as dm:
                    arb.manage_workers = mock.Mock()
                    arb.setup_signals = mock.Mock()
                    arb.start()
                dm.assert_called_once()

    def test_start_writes_pid_and_umask(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "asteri.pid")
            arb = Arbiter("a:app", SyncWorker, pidfile=pidfile, umask=0o22)
            arb = self._start(arb)
            self.assertEqual(open(pidfile).read().strip(), str(os.getpid()))
            arb.socks[0].close()

    def test_start_reload_watchdog_fallback_warning(self):
        arb = Arbiter("a:app", SyncWorker, reload=True)
        arb.manage_workers = mock.Mock()
        arb.setup_signals = mock.Mock()
        with mock.patch("asteri.arbiter.WATCHDOG_AVAILABLE", False):
            with mock.patch("asteri.arbiter.logger") as lg:
                with mock.patch("asteri.arbiter.set_proctitle"):
                    arb.start()
        lg.warning.assert_called_once()
        arb.socks[0].close()

    def test_start_reload_uses_watchdog(self):
        arb = Arbiter("a:app", SyncWorker, reload=True)
        with mock.patch("asteri.arbiter.Observer") as Obs:
            with mock.patch("asteri.arbiter.WATCHDOG_AVAILABLE", True):
                arb = self._start(arb)
            Obs.return_value.schedule.assert_called_once()
            Obs.return_value.start.assert_called_once()
            self.assertIsNotNone(arb.reloader)
        arb.socks[0].close()

    def test_start_http3_binds_udp(self):
        free = _free_port()
        arb = Arbiter("a:app", SyncWorker, binds=[f"127.0.0.1:{free}"],
                      http_protocols="h1,h3", reuse_port=True)
        arb = self._start(arb)
        self.assertGreaterEqual(len(arb.socks), 2)
        self.assertEqual(sum(1 for s in arb.socks
                             if s.type == socket.SOCK_DGRAM), 1)
        for s in arb.socks:
            s.close()

    def test_start_https_wraps_ssl(self):
        free = _free_port()
        arb = Arbiter("a:app", SyncWorker, binds=[f"127.0.0.1:{free}"],
                      certfile="c.pem", keyfile="k.pem")
        wrapped = mock.Mock()
        ctx = mock.Mock()
        ctx.wrap_socket.return_value = wrapped
        with mock.patch.object(arb, "build_ssl_context", return_value=ctx):
            arb = self._start(arb)
        ctx.wrap_socket.assert_called_once()
        self.assertIn(wrapped, arb.socks)
        arb.socks[0].close()

    def test_start_bind_failure_exits(self):
        arb = Arbiter("a:app", SyncWorker, binds=["bad-bind-no-port"])
        arb.manage_workers = mock.Mock()
        arb.setup_signals = mock.Mock()
        with mock.patch("asteri.arbiter.logger"):
            with mock.patch("sys.exit") as ex:
                arb.start()
        ex.assert_called_once()

    def test_start_calls_control_socket(self):
        arb = Arbiter("a:app", SyncWorker,
                      control_socket="/tmp/ctl-start.sock")
        with mock.patch.object(arb, "_start_control_socket") as cs:
            arb = self._start(arb)
        cs.assert_called_once()
        arb.socks[0].close()

    def test_start_systemd_activation(self):
        fromfd = mock.Mock()
        fake_sock = mock.Mock()
        fromfd.return_value = fake_sock
        with mock.patch.dict(os.environ,
                             {"LISTEN_FDS": "2",
                              "LISTEN_PID": str(os.getpid())}):
            with mock.patch("socket.fromfd", fromfd):
                arb = Arbiter("a:app", SyncWorker)
                arb.manage_workers = mock.Mock()
                arb.setup_signals = mock.Mock()
                with mock.patch("asteri.arbiter.logger"):
                    with mock.patch("asteri.arbiter.set_proctitle"):
                        arb.start()
        self.assertEqual(len(arb.socks), 2)
        fake_sock.setblocking.assert_called_with(False)

    def test_start_systemd_inherit_failure_exits(self):
        with mock.patch.dict(os.environ,
                             {"LISTEN_FDS": "1",
                              "LISTEN_PID": str(os.getpid())}):
            with mock.patch("socket.fromfd", side_effect=OSError("nope")):
                arb = Arbiter("a:app", SyncWorker)
                arb.manage_workers = mock.Mock()
                arb.setup_signals = mock.Mock()
                with mock.patch("asteri.arbiter.logger"):
                    with mock.patch("sys.exit") as ex:
                        arb.start()
        ex.assert_called_once()


class TestSignalsAndLifecycle(unittest.TestCase):
    def test_setup_signals_installs_handlers(self):
        arb = Arbiter("a:app", SyncWorker)
        with mock.patch("asteri.arbiter.signal.signal") as sig:
            arb.setup_signals()
        self.assertEqual(sig.call_count, 5)

    def test_handle_chld_wakes(self):
        arb = Arbiter("a:app", SyncWorker)
        with mock.patch.object(arb, "wakeup") as w:
            arb.handle_chld(None, None)
        w.assert_called_once()

    def test_handle_exit(self):
        arb = Arbiter("a:app", SyncWorker)
        with mock.patch.object(arb, "stop_workers") as sw:
            arb.handle_exit(None, None)
        self.assertFalse(arb.alive)
        sw.assert_called_once_with(signal.SIGTERM)

    def test_handle_quit(self):
        arb = Arbiter("a:app", SyncWorker)
        with mock.patch.object(arb, "stop_workers") as sw:
            arb.handle_quit(None, None)
        sw.assert_called_once_with(signal.SIGQUIT)

    def test_handle_hup(self):
        arb = Arbiter("a:app", SyncWorker)
        with mock.patch.object(arb, "stop_workers") as sw:
            with mock.patch("asteri.arbiter.logger"):
                arb.handle_hup(None, None)
        sw.assert_called_once_with(signal.SIGTERM)

    def test_wakeup_noop(self):
        arb = Arbiter("a:app", SyncWorker)
        arb.wakeup()

    def test_stop_workers_sends_signal(self):
        arb = Arbiter("a:app", SyncWorker)
        arb.workers = {100: object(), 200: object()}
        proc = mock.Mock()
        with mock.patch("psutil.Process", return_value=proc):
            arb.stop_workers(signal.SIGTERM)
        self.assertEqual(proc.send_signal.call_count, 2)

    def test_stop_workers_no_such_process_removes(self):
        arb = Arbiter("a:app", SyncWorker)
        arb.workers = {100: object()}
        with mock.patch("psutil.Process",
                        side_effect=psutil.NoSuchProcess(100)):
            arb.stop_workers(signal.SIGTERM)
        self.assertNotIn(100, arb.workers)

    def test_stop_workers_no_such_process_keeps_registered_if_not_deleted(self):
        # NoSuchProcess raised but pid already removed from map -> must not KeyError
        arb = Arbiter("a:app", SyncWorker)
        arb.workers = {100: object()}
        with mock.patch("psutil.Process",
                        side_effect=psutil.NoSuchProcess(100)):
            # Remove pid before the second guard's check via side effect
            arb.workers.pop(100, None)
            arb.stop_workers(signal.SIGTERM)
        self.assertEqual(arb.workers, {})


class TestDaemonizeAndUser(unittest.TestCase):
    def test_daemonize_first_fork_exits(self):
        arb = Arbiter("a:app", SyncWorker)
        with mock.patch("os.fork", return_value=1):
            with mock.patch("sys.exit", side_effect=SystemExit) as ex:
                with self.assertRaises(SystemExit):
                    arb.daemonize()
        ex.assert_called_once_with(0)

    def test_daemonize_second_fork_exits(self):
        arb = Arbiter("a:app", SyncWorker)
        with mock.patch("os.fork", side_effect=[0, 1]):
            with mock.patch("os.setsid"):
                with mock.patch("sys.exit", side_effect=SystemExit) as ex:
                    with self.assertRaises(SystemExit):
                        arb.daemonize()
        ex.assert_called_once_with(0)

    def test_daemonize_full(self):
        arb = Arbiter("a:app", SyncWorker)
        fake_in = mock.Mock()
        fake_in.fileno.return_value = 0
        fake_out = mock.Mock()
        fake_out.fileno.return_value = 1
        with mock.patch("os.fork", side_effect=[0, 0]):
            with mock.patch("os.setsid") as setsid:
                with mock.patch("os.dup2") as dup2:
                    with mock.patch("sys.stdin", fake_in):
                        with mock.patch("sys.stderr", fake_out):
                            with mock.patch("sys.stdout", fake_out):
                                with mock.patch("sys.exit") as ex:
                                    arb.daemonize()
            setsid.assert_called_once()
            ex.assert_not_called()
        self.assertEqual(dup2.call_count, 3)

    def test_write_pid(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "p.pid")
            arb = Arbiter("a:app", SyncWorker, pidfile=pidfile)
            arb.write_pid()
            self.assertEqual(open(pidfile).read().strip(), str(os.getpid()))

    def test_switch_user_noop(self):
        arb = Arbiter("a:app", SyncWorker)
        arb.switch_user()

    def test_switch_user_sets_gid_uid(self):
        arb = Arbiter("a:app", SyncWorker, user="u", group="g")
        with mock.patch("grp.getgrnam") as grp:
            grp.return_value.gr_gid = 7
            with mock.patch("pwd.getpwnam") as pwd:
                pwd.return_value.pw_uid = 9
                with mock.patch("os.setgid") as sg:
                    with mock.patch("os.setuid") as su:
                        arb.switch_user()
        sg.assert_called_once_with(7)
        su.assert_called_once_with(9)


class TestSSL(unittest.TestCase):
    def test_build_ssl_context_basic(self):
        arb = Arbiter("a:app", SyncWorker, certfile="c.pem", keyfile="k.pem")
        with mock.patch("ssl.SSLContext") as SC:
            ctx = SC.return_value
            arb.build_ssl_context()
        SC.assert_called_once_with(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain.assert_called_once_with(certfile="c.pem",
                                                    keyfile="k.pem")
        self.assertEqual(ctx.minimum_version,
                         ssl.TLSVersion.MINIMUM_SUPPORTED)

    def test_build_ssl_context_extras(self):
        arb = Arbiter("a:app", SyncWorker, certfile="c", keyfile="k",
                      ca_certs="ca.pem", ciphers="HIGH", ssl_version=3)
        with mock.patch("ssl.SSLContext") as SC:
            ctx = SC.return_value
            arb.build_ssl_context()
        ctx.load_verify_locations.assert_called_once_with(cafile="ca.pem")
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        ctx.set_ciphers.assert_called_once_with("HIGH")
        self.assertEqual(ctx.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_build_ssl_context_version4(self):
        arb = Arbiter("a:app", SyncWorker, certfile="c", keyfile="k",
                      ssl_version=4)
        with mock.patch("ssl.SSLContext") as SC:
            arb.build_ssl_context()
        self.assertEqual(SC.return_value.minimum_version,
                         ssl.TLSVersion.TLSv1_3)


class TestSpawnAndManage(unittest.TestCase):
    def test_spawn_worker_child_path(self):
        worker = mock.Mock()
        arb = Arbiter("a:app", SyncWorker)
        arb.worker_class = mock.Mock(return_value=worker)
        arb.socks = [mock.Mock()]
        with mock.patch("os.fork", return_value=0):
            with mock.patch("sys.exit") as ex:
                arb.spawn_worker()
        worker.init_process.assert_called_once()
        worker.run.assert_called_once()
        ex.assert_called_once_with(0)

    def test_spawn_worker_child_error(self):
        worker = mock.Mock()
        worker.init_process.side_effect = RuntimeError("fail")
        arb = Arbiter("a:app", SyncWorker)
        arb.worker_class = mock.Mock(return_value=worker)
        with mock.patch("os.fork", return_value=0):
            with mock.patch("sys.exit") as ex:
                with mock.patch("asteri.arbiter.logger"):
                    arb.spawn_worker()
        ex.assert_called_once_with(1)

    def test_spawn_worker_parent_statsd_stash(self):
        arb = Arbiter("a:app", SyncWorker)
        arb.worker_class = mock.Mock(return_value=mock.Mock())
        arb.socks = [mock.Mock()]
        arb.statsd = mock.Mock()
        arb.stash = mock.Mock()
        with mock.patch("os.fork", return_value=1234):
            pid = arb.spawn_worker()
        self.assertEqual(pid, 1234)
        self.assertIn(1234, arb.workers)
        arb.statsd.increment.assert_called_once_with("workers.spawn")
        arb.statsd.gauge.assert_called_once()
        arb.stash.set.assert_called_once()

    def test_spawn_worker_parent_stash_error_swallowed(self):
        arb = Arbiter("a:app", SyncWorker)
        arb.worker_class = mock.Mock(return_value=mock.Mock())
        arb.socks = [mock.Mock()]
        arb.stash = mock.Mock()
        arb.stash.set.side_effect = OSError("nope")
        with mock.patch("os.fork", return_value=1234):
            pid = arb.spawn_worker()
        self.assertEqual(pid, 1234)

    def test_manage_workers_loop_cleanup(self):
        arb = Arbiter("a:app", SyncWorker)
        arb.workers = {111: "w1"}
        arb.statsd = mock.Mock()
        arb.stash = mock.Mock()
        arb.stash.set.side_effect = OSError("stash down")
        close_raising = mock.Mock()
        close_raising.close.side_effect = OSError("sock gone")
        arb.socks = [close_raising]
        arb.reloader = mock.Mock()
        arb.spawn_worker = mock.Mock()
        wait_queue = iter([(111, 0), (0, 0)])

        def fake_waitpid(*a, **k):
            try:
                return next(wait_queue)
            except StopIteration:
                raise ChildProcessError("none") from None

        def fake_sleep(secs):
            arb.alive = False

        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "p.pid")
            arb.pidfile = pidfile
            with open(pidfile, "w") as f:
                f.write(str(os.getpid()))
            with mock.patch("asteri.arbiter.os.waitpid", side_effect=fake_waitpid):
                with mock.patch("asteri.arbiter.time.sleep", side_effect=fake_sleep):
                    with mock.patch("asteri.arbiter.logger"):
                        arb.manage_workers()
            self.assertFalse(os.path.exists(pidfile))

        arb.statsd.increment.assert_called_once_with("workers.exit")
        self.assertEqual(arb.statsd.gauge.call_count, 1)
        self.assertEqual(arb.stash.set.call_count, 1)
        arb.reloader.stop.assert_called_once()
        arb.reloader.join.assert_called_once()
        arb.socks[0].close.assert_called_once()
        arb.spawn_worker.assert_not_called()

    def test_manage_workers_spawns_and_force_kills(self):
        arb = Arbiter("a:app", SyncWorker)
        arb.num_workers = 1
        arb.workers = {}
        arb.statsd = mock.Mock()
        arb.reloader = mock.Mock()
        arb.socks = [mock.Mock()]

        def do_spawn():
            arb.workers[222] = "w2"

        arb.spawn_worker = do_spawn

        def fake_waitpid(*a, **k):
            raise ChildProcessError("none") from None

        def fake_sleep(secs):
            arb.alive = False

        with mock.patch("asteri.arbiter.os.waitpid", side_effect=fake_waitpid):
            with mock.patch("asteri.arbiter.time.sleep", side_effect=fake_sleep):
                with mock.patch("asteri.arbiter.time.time", return_value=1.0):
                    with mock.patch.object(arb, "stop_workers") as sw:
                        with mock.patch("asteri.arbiter.logger"):
                            arb.manage_workers()
        # Spawned during the fill loop even though alive kept True until sleep
        self.assertEqual(arb.workers, {222: "w2"})
        sw.assert_called_once_with(signal.SIGKILL)

    def test_manage_workers_graceful_wait_reaps(self):
        arb = Arbiter("a:app", SyncWorker)
        arb.workers = {111: "w1"}
        arb.statsd = mock.Mock()
        arb.reloader = mock.Mock()
        arb.socks = [mock.Mock()]
        arb.spawn_worker = mock.Mock()
        wait_queue = iter([(0, 0), (0, 0), (111, 0)])

        def fake_waitpid(*a, **k):
            try:
                return next(wait_queue)
            except StopIteration:
                raise ChildProcessError("none") from None

        def fake_sleep(secs):
            arb.alive = False

        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "p.pid")
            arb.pidfile = pidfile
            with open(pidfile, "w") as f:
                f.write(str(os.getpid()))
            with mock.patch("asteri.arbiter.os.waitpid", side_effect=fake_waitpid):
                with mock.patch("asteri.arbiter.time.sleep", side_effect=fake_sleep):
                    with mock.patch("asteri.arbiter.logger"):
                        arb.manage_workers()

        # The graceful-wait loop reaped pid 111 -> workers empty at end
        self.assertEqual(arb.workers, {})


class TestWatchdogReload(unittest.TestCase):
    def test_watchdog_not_available_on_import(self):
        import subprocess
        import sys

        code = (
            "import builtins\n"
            "real = builtins.__import__\n"
            "def fake(name, *a, **k):\n"
            "    if name.startswith('watchdog'):\n"
            "        raise ImportError('no watchdog')\n"
            "    return real(name, *a, **k)\n"
            "builtins.__import__ = fake\n"
            "import asteri.arbiter as m\n"
            "print(m.WATCHDOG_AVAILABLE)\n"
        )
        out = subprocess.check_output(
            [sys.executable, "-c", code], cwd=os.getcwd(), text=True
        )
        self.assertEqual(out.strip(), "False")


class TestReloadHandler(unittest.TestCase):
    def _handler(self, arb):
        with mock.patch("asteri.arbiter.Observer") as Obs:
            with mock.patch("asteri.arbiter.WATCHDOG_AVAILABLE", True):
                with mock.patch("asteri.arbiter.logger"):
                    arb.setup_reloader()
        return Obs.return_value.schedule.call_args[0][0]

    def test_on_modified_py_triggers(self):
        arb = Arbiter("a:app", SyncWorker)
        handler = self._handler(arb)
        ev = types.SimpleNamespace(src_path="/x/y.py")
        with mock.patch.object(arb, "stop_workers") as sw:
            with mock.patch("asteri.arbiter.logger"):
                handler.on_modified(ev)
        sw.assert_called_once_with(signal.SIGTERM)

    def test_on_modified_non_py_ignored(self):
        arb = Arbiter("a:app", SyncWorker)
        handler = self._handler(arb)
        ev = types.SimpleNamespace(src_path="/x/built.min.js")
        with mock.patch.object(arb, "stop_workers") as sw:
            handler.on_modified(ev)
        sw.assert_not_called()


class TestControlSocket(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "ctl.sock")
        self.arb = Arbiter("a:app", SyncWorker, control_socket=self.path)
        self.arb.num_workers = 2
        self.arb._start_control_socket()
        for _ in range(100):
            if os.path.exists(self.path):
                break
            time.sleep(0.01)
        self._server = self.arb.control_sock_server

    def tearDown(self):
        self.arb.alive = False
        try:
            self._server.close()
        except OSError:
            pass
        time.sleep(0.05)
        try:
            os.unlink(self.path)
        except OSError:
            pass
        self._tmp.cleanup()

    def _cmd(self, payload):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(3.0)
            s.connect(self.path)
            s.sendall(json.dumps(payload).encode("utf-8"))
            data = b""
            while True:
                try:
                    chunk = s.recv(4096)
                except (socket.timeout, ConnectionResetError):
                    break
                if not chunk:
                    break
                data += chunk
                if data.endswith(b"}"):
                    break
        return json.loads(data.decode("utf-8"))

    def test_status_command(self):
        resp = self._cmd({"command": "status"})
        self.assertEqual(resp["status"], "running")
        self.assertEqual(resp["pid"], os.getpid())
        self.assertEqual(resp["num_workers"], 2)
        self.assertEqual(resp["workers_count"], 0)

    def test_add_remove_command(self):
        self.assertEqual(self._cmd({"command": "add-worker"})["num_workers"], 3)
        self.assertEqual(self._cmd({"command": "remove-worker"})["num_workers"], 2)
        self.assertEqual(self._cmd({"command": "remove-worker"})["num_workers"], 1)

    def test_reload_command(self):
        with mock.patch("os.kill") as kill:
            resp = self._cmd({"command": "reload"})
        self.assertEqual(resp["status"], "reloading")
        kill.assert_called_once_with(os.getpid(), signal.SIGHUP)

    def test_unknown_command(self):
        resp = self._cmd({"command": "bogus"})
        self.assertEqual(resp["status"], "error")

    def test_stop_command(self):
        resp = self._cmd({"command": "stop"})
        self.assertEqual(resp["status"], "stopping")
        self.assertFalse(self.arb.alive)

    def test_malformed_json(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(3.0)
            s.connect(self.path)
            s.sendall(b"not-json{{{{")
            data = s.recv(4096)
        resp = json.loads(data.decode("utf-8"))
        self.assertEqual(resp["status"], "error")

    def test_empty_conn_returns(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(self.path)
            s.close()
        time.sleep(0.05)

    def test_error_reply_send_fails(self):
        with mock.patch("json.dumps", side_effect=OSError("peer gone")):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(5.0)
                s.connect(self.path)
                s.sendall(b"not-json{{{{")
                try:
                    s.recv(4096)
                except (socket.timeout, ConnectionResetError):
                    pass
        time.sleep(0.05)

    def test_conn_close_oserror_swallowed(self):
        with mock.patch("socket.socket.close", side_effect=OSError("closed")):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(3.0)
            s.connect(self.path)
            s.sendall(json.dumps({"command": "status"}).encode("utf-8"))
            data = b""
            try:
                while True:
                    chunk = s.recv(4096)
                    if chunk:
                        data += chunk
                        if data.endswith(b"}"):
                            break
                    else:
                        break
            except (socket.timeout, ConnectionResetError):
                pass
            try:
                s.close()
            except OSError:
                pass
            time.sleep(0.2)
        self.assertEqual(json.loads(data.decode("utf-8"))["status"], "running")


class TestControlSocketBind(unittest.TestCase):
    def test_unlinks_existing_path(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ctl.sock")
            with open(path, "w") as f:
                f.write("existing")
            arb = Arbiter("a:app", SyncWorker, control_socket=path)
            arb.alive = False
            arb._start_control_socket()
            time.sleep(0.05)
            self.assertFalse(os.path.exists(path))
            self.assertTrue(arb.control_sock_server is not None)

    def test_unlink_oserror_swallowed(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ctl.sock")
            arb = Arbiter("a:app", SyncWorker, control_socket=path)
            arb._start_control_socket()
            for _ in range(100):
                if os.path.exists(path):
                    break
                time.sleep(0.01)
            arb.alive = False
            with mock.patch("asteri.arbiter.os.unlink",
                            side_effect=OSError("rm failed")):
                try:
                    arb.control_sock_server.close()
                except OSError:
                    pass
                time.sleep(1.1)

    def test_bind_unlink_oserror_survives(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ctl.sock")
            arb = Arbiter("a:app", SyncWorker, control_socket=path)
            arb.alive = False
            with mock.patch("os.path.exists", return_value=True):
                with mock.patch("asteri.arbiter.os.unlink",
                                side_effect=OSError("rm failed")):
                    arb._start_control_socket()
            time.sleep(0.05)
            self.assertTrue(arb.control_sock_server is not None)

    def test_server_thread_timeout_close_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ctl.sock")
            arb = Arbiter("a:app", SyncWorker, control_socket=path)
            arb._start_control_socket()
            for _ in range(100):
                if os.path.exists(path):
                    break
                time.sleep(0.01)
            arb.alive = False
            with mock.patch("socket.socket.close",
                            side_effect=OSError("already closed")):
                time.sleep(1.1)


if __name__ == "__main__":
    unittest.main()