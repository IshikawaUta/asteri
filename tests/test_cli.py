import subprocess
import os
import time
import unittest
import psutil
import sys


class TestAsteriCLI(unittest.TestCase):
    def setUp(self):
        # Use sys.executable to ensure we use the same python interpreter
        self.bin_name = [sys.executable, "-m", "asteri"]
        self.default_app = "example_wsgi:app"
        self.root_dir = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))
        os.chdir(self.root_dir)
        # Ensure the current directory is in PYTHONPATH
        env = os.environ.copy()
        env["PYTHONPATH"] = self.root_dir
        self.test_env = env

    def get_free_port(self):
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def run_cmd(self, args, wait=5):
        if "-b" not in args and "--bind" not in args:
            args = ["-b", f"127.0.0.1:{self.get_free_port()}"] + args

        # Determine command: add default app if no app string present
        # An app string has ':' and is NOT just numbers/dots/colons (like IP:PORT)
        has_app = any(
            ":" in arg and not arg.replace(".", "").replace(":", "").isdigit()
            for arg in args
            if not arg.startswith("-")
        )

        if not has_app:
            cmd = self.bin_name + [self.default_app] + args
        else:
            cmd = self.bin_name + args

        # Merge stderr and stdout to catch everything
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=self.test_env,
        )
        time.sleep(wait)

        if proc.poll() is not None:
            output = proc.stdout.read()
            print(f"\n[DEBUG] Command failed: {' '.join(cmd)}")
            print(f"[DEBUG] OUTPUT: {output}")
        return proc

    def test_help(self):
        result = subprocess.run(
            self.bin_name + ["-h"], capture_output=True, text=True, env=self.test_env
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage: asteri", result.stdout)

    def test_version(self):
        result = subprocess.run(
            self.bin_name + ["-v"], capture_output=True, text=True, env=self.test_env
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Asteri v2.2.2", result.stdout)

    def test_print_config(self):
        result = subprocess.run(
            self.bin_name + [self.default_app, "--print-config"],
            capture_output=True,
            text=True,
            env=self.test_env,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Resolved Configuration:", result.stdout)

    def test_pid_file(self):
        pid_file = "test_asteri.pid"
        if os.path.exists(pid_file):
            os.remove(pid_file)
        proc = self.run_cmd(["--pid", pid_file])
        try:
            self.assertTrue(os.path.exists(pid_file))
        finally:
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait()
            if os.path.exists(pid_file):
                os.remove(pid_file)

    def test_env_vars(self):
        proc = self.run_cmd(["-e", "TEST_VAR=ASTRONAUT"])
        try:
            self.assertIsNone(proc.poll())
        finally:
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait()

    def test_log_file(self):
        log_file = "test_error.log"
        if os.path.exists(log_file):
            os.remove(log_file)
        proc = self.run_cmd(["--log-file", log_file])
        try:
            self.assertTrue(os.path.exists(log_file))
        finally:
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait()
            if os.path.exists(log_file):
                os.remove(log_file)

    def test_daemon(self):
        pid_file = "daemon_test.pid"
        if os.path.exists(pid_file):
            os.remove(pid_file)
        port = self.get_free_port()
        result = subprocess.run(
            self.bin_name
            + [
                self.default_app,
                "-b",
                f"127.0.0.1:{port}",
                "--daemon",
                "--pid",
                pid_file,
            ],
            env=self.test_env,
        )
        self.assertEqual(result.returncode, 0)
        time.sleep(3)
        try:
            self.assertTrue(os.path.exists(pid_file))
            with open(pid_file, "r") as f:
                pid = int(f.read().strip())
                try:
                    p = psutil.Process(pid)
                    p.terminate()
                except Exception:
                    pass
        finally:
            if os.path.exists(pid_file):
                os.remove(pid_file)

    def test_worker_count(self):
        proc = self.run_cmd(["-w", "3"], wait=6)
        try:
            parent = psutil.Process(proc.pid)
            for _ in range(15):
                children = parent.children()
                if len(children) >= 3:
                    break
                time.sleep(1)
            self.assertGreaterEqual(len(parent.children()), 3)
        finally:
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait()

    def test_worker_class_gevent(self):
        proc = self.run_cmd(["-k", "gevent"])
        try:
            self.assertIsNone(proc.poll())
        finally:
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait()

    def test_config_file_loading(self):
        conf_file = "test_config.py"
        with open(conf_file, "w") as f:
            f.write("workers = 2\nbind = '127.0.0.1:8123'\n")
        result = subprocess.run(
            self.bin_name + [self.default_app,
                             "-c", conf_file, "--print-config"],
            capture_output=True,
            text=True,
            env=self.test_env,
        )
        try:
            self.assertIn("workers: 2", result.stdout)
        finally:
            if os.path.exists(conf_file):
                os.remove(conf_file)

    def test_umask(self):
        proc = self.run_cmd(["--umask", "007"])
        try:
            self.assertIsNone(proc.poll())
        finally:
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait()

    def test_preload(self):
        proc = self.run_cmd(["--preload"])
        try:
            self.assertIsNone(proc.poll())
        finally:
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait()

    def test_chdir(self):
        tmp_dir = "tmp_test_chdir"
        os.makedirs(tmp_dir, exist_ok=True)
        with open(os.path.join(tmp_dir, "myapp.py"), "w") as f:
            f.write("def app(e, s): s('200 OK', []); return [b'ok']")
        proc = self.run_cmd(["--chdir", tmp_dir, "myapp:app"], wait=5)
        try:
            self.assertIsNone(proc.poll())
        finally:
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait()
            import shutil

            shutil.rmtree(tmp_dir)

    def test_multiple_binds(self):
        port1 = self.get_free_port()
        port2 = self.get_free_port()
        proc = self.run_cmd(
            ["-b", f"127.0.0.1:{port1}", "-b", f"127.0.0.1:{port2}"])
        try:
            self.assertIsNone(proc.poll())
            import urllib.request

            resp1 = urllib.request.urlopen(f"http://127.0.0.1:{port1}")
            self.assertEqual(resp1.status, 200)
            resp2 = urllib.request.urlopen(f"http://127.0.0.1:{port2}")
            self.assertEqual(resp2.status, 200)
        finally:
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait()

    def test_worker_class_asgi(self):
        proc = self.run_cmd(["-k", "asgi", "example_asgi:app"])
        try:
            self.assertIsNone(proc.poll())
        finally:
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait()

    def test_worker_class_gthread(self):
        proc = self.run_cmd(["-k", "gthread", "--threads", "2"])
        try:
            self.assertIsNone(proc.poll())
        finally:
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait()

    def test_reload_flag(self):
        # We just test if it starts with the flag without crashing
        proc = self.run_cmd(["--reload"])
        try:
            self.assertIsNone(proc.poll())
        finally:
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait()

    def test_log_level_debug(self):
        proc = self.run_cmd(["--log-level", "debug"])
        try:
            self.assertIsNone(proc.poll())
        finally:
            if proc.stdout:
                proc.stdout.close()
            proc.terminate()
            proc.wait()


if __name__ == "__main__":
    unittest.main()
