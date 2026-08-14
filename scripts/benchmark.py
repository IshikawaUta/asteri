import subprocess
import time
import os
import signal
import re
import statistics

# Configuration
CONCURRENCY = 50
REQUESTS = 8000
WARMUP = 500
REPS = 3
KEEP_ALIVE = False
TARGET_URL = "http://127.0.0.1:8080/"
WSGI_APP = "example_flask:app"
ASGI_APP = "example_fastapi:app"

# NOTE: "tornado" and "gtornado" are aliases for the same worker class; their
# rows will look identical once the sample size is large enough. They are kept
# as separate rows only for documentation parity with README.
# NOTE: -n REQUESTS must be large enough that the run outlives warm-up effects
# and load-average drift. A single -n 1000 run produced >2.5x noise on
# identical code, so each server is now measured REP times and the median is
# reported.


def run_server(command):
    """Starts a server in a subprocess."""
    print(f"Starting server: {' '.join(command)}")
    # Use a new process group to make it easier to kill the server and its children
    # Server output is discarded (DEVNULL) so an undrained PIPE cannot fill up and
    # stall SIGTERM shutdown under heavy access-log traffic.
    proc = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid,
    )
    time.sleep(3)  # Wait for boot
    return proc


def stop_server(proc):
    """Stops a server process group, escalating to SIGKILL if needed."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        proc.wait()


def run_ab(requests):
    """Runs a single ab pass, returning (rps, mean_latency_ms)."""
    cmd = ["ab", "-n", str(requests), "-c", str(CONCURRENCY)]
    if KEEP_ALIVE:
        cmd.append("-k")
    cmd.append(TARGET_URL)
    result = subprocess.run(cmd, capture_output=True, text=True)
    stats = parse_ab_output(result.stdout)
    return stats["rps"], stats["latency"]


def run_benchmark():
    """Warms up the server, then runs REP measured passes (median reported)."""
    print(f"Warming up: ab -n {WARMUP} -c {CONCURRENCY} {TARGET_URL}")
    run_ab(WARMUP)
    print(f"Benchmarking: ab -n {REQUESTS} -c {CONCURRENCY}"
          f"{' -k' if KEEP_ALIVE else ''} {TARGET_URL} x{REPS}")
    rps = []
    lat = []
    for _ in range(REPS):
        rps_val, lat_val = run_ab(REQUESTS)
        rps.append(rps_val)
        lat.append(lat_val)
    return {
        "rps": statistics.median(rps),
        "latency": statistics.median(lat),
    }


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
            stats = run_benchmark()
            results.append(
                {"name": server["name"], "type": server["type"], **stats})
        finally:
            print(f"Stopping {server['name']}...")
            stop_server(proc)

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
