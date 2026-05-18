import subprocess
import time
import os
import signal
import re

# Configuration
CONCURRENCY = 50
REQUESTS = 1000
TARGET_URL = "http://127.0.0.1:8080/"
WSGI_APP = "example_flask:app"
ASGI_APP = "example_fastapi:app"


def run_server(command):
    """Starts a server in a subprocess."""
    print(f"Starting server: {' '.join(command)}")
    # Use a new process group to make it easier to kill the server and its children
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid
    )
    time.sleep(3)  # Wait for boot
    return proc


def run_benchmark():
    """Runs Apache Benchmark (ab)."""
    print(f"Running benchmark: ab -n {REQUESTS} -c {CONCURRENCY} {TARGET_URL}")
    cmd = ["ab", "-n", str(REQUESTS), "-c", str(CONCURRENCY), TARGET_URL]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout


def parse_ab_output(output):
    """Extracts RPS and Latency from ab output."""
    rps = re.search(r"Requests per second:\s+([\d.]+)", output)
    latency = re.search(
        r"Time per request:\s+([\d.]+)\s+\[ms\]\s+\(mean\)", output)

    return {
        "rps": float(rps.group(1)) if rps else 0,
        "latency": float(latency.group(1)) if latency else 0,
    }


def main():
    servers = [
        # WSGI Servers
        {
            "name": "Asteri (Sync)",
            "type": "WSGI",
            "cmd": [
                "python3",
                "-m",
                "asteri",
                "-w",
                "4",
                "-b",
                "127.0.0.1:8080",
                WSGI_APP,
            ],
        },
        {
            "name": "Asteri (GThread)",
            "type": "WSGI",
            "cmd": [
                "python3",
                "-m",
                "asteri",
                "-w",
                "4",
                "-k",
                "gthread",
                "--threads",
                "4",
                "-b",
                "127.0.0.1:8080",
                WSGI_APP,
            ],
        },
        {
            "name": "Asteri (Gevent)",
            "type": "WSGI",
            "cmd": [
                "python3",
                "-m",
                "asteri",
                "-w",
                "4",
                "-k",
                "gevent",
                "-b",
                "127.0.0.1:8080",
                WSGI_APP,
            ],
        },
        {
            "name": "Asteri (Tornado)",
            "type": "WSGI",
            "cmd": [
                "python3",
                "-m",
                "asteri",
                "-w",
                "4",
                "-k",
                "tornado",
                "-b",
                "127.0.0.1:8080",
                WSGI_APP,
            ],
        },
        {
            "name": "Asteri (GTornado)",
            "type": "WSGI",
            "cmd": [
                "python3",
                "-m",
                "asteri",
                "-w",
                "4",
                "-k",
                "gtornado",
                "-b",
                "127.0.0.1:8080",
                WSGI_APP,
            ],
        },
        {
            "name": "Gunicorn (Sync)",
            "type": "WSGI",
            "cmd": ["gunicorn", "-w", "4", "-b", "127.0.0.1:8080", WSGI_APP],
        },
        # ASGI Servers
        {
            "name": "Asteri (ASGI)",
            "type": "ASGI",
            "cmd": [
                "python3",
                "-m",
                "asteri",
                "-w",
                "4",
                "-k",
                "asgi",
                "-b",
                "127.0.0.1:8080",
                ASGI_APP,
            ],
        },
        {
            "name": "Uvicorn",
            "type": "ASGI",
            "cmd": [
                "uvicorn",
                "--workers",
                "4",
                "--host",
                "127.0.0.1",
                "--port",
                "8080",
                ASGI_APP,
            ],
        },
    ]

    results = []

    for server in servers:
        # Check if command exists
        try:
            # For python -m commands, check if the module exists is complex, so we just try to run it
            if server["cmd"][1] == "-m":
                pass
            else:
                subprocess.run([server["cmd"][0], "--version"],
                               capture_output=True)
        except FileNotFoundError:
            print(
                f"Skipping {server['name']}: {server['cmd'][0]} not installed.")
            continue

        proc = run_server(server["cmd"])

        try:
            raw_output = run_benchmark()
            stats = parse_ab_output(raw_output)
            results.append(
                {"name": server["name"], "type": server["type"], **stats})
        finally:
            print(f"Stopping {server['name']}...")
            try:
                # Kill the whole process group
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                pass
            proc.wait()

    # Print Report
    print("\n" + "=" * 70)
    print(f"{'Server Name':<20} | {'Type':<6} | {'RPS':<10} | {'Latency (ms)':<12}")
    print("-" * 70)
    for res in results:
        print(
            f"{res['name']:<20} | {res['type']:<6} | {res['rps']:<10.2f} | {res['latency']:<12.2f}"
        )
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
