"""Run one API/job process with an IAM-authorized private Cloud SQL proxy."""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time


def run(mode: str) -> int:
    instance = os.environ.get("FLASHCART_SQL_INSTANCE", "")
    if instance != "ritru-fc-dev-01:asia-south1:flashcart-dev-inventory":
        raise ValueError("Cloud runtime requires the configured FlashCart dev SQL instance")
    proxy_command = [
        "/usr/local/bin/cloud-sql-proxy",
        "--private-ip",
        "--address=127.0.0.1",
        "--port=5432",
        "--quiet",
        instance,
    ]
    if mode != "migrate":
        proxy_command.insert(1, "--auto-iam-authn")
    if mode == "api":
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "flashcart.api:create_app",
            "--factory",
            "--host",
            "0.0.0.0",
            "--port",
            os.environ.get("PORT", "8080"),
        ]
    elif mode == "worker":
        command = ["flashcart-worker", "--deliver", "--limit", "100"]
    elif mode == "migrate":
        command = [sys.executable, "-m", "flashcart.migrate", "--bootstrap-dev"]
    else:
        raise ValueError("Expected api, worker or migrate")
    # The proxy needs workload identity, not application credentials.
    proxy_env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {"FLASHCART_API_KEY", "FLASHCART_DATABASE_URL", "FLASHCART_BOOTSTRAP_DATABASE_URL"}
    }
    proxy = subprocess.Popen(proxy_command, env=proxy_env)
    child = None
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None:
            child.terminate()

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        deadline = time.monotonic() + 45
        while not stopping:
            if proxy.poll() is not None:
                raise RuntimeError("Cloud SQL proxy exited before startup")
            try:
                with socket.create_connection(("127.0.0.1", 5432), timeout=1):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Cloud SQL proxy startup timed out") from None
                time.sleep(0.25)
        if stopping:
            return 143
        child = subprocess.Popen(command)
        while child.poll() is None:
            if proxy.poll() is not None:
                raise RuntimeError("Cloud SQL proxy exited while application was running")
            time.sleep(0.25)
        return child.returncode
    finally:
        for process in (child, proxy):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["api", "worker", "migrate"])
    args = parser.parse_args()
    try:
        raise SystemExit(run(args.mode))
    except (ValueError, RuntimeError, OSError) as error:
        # Avoid printing subprocess environment, connection strings or database secrets.
        print(f"Cloud runtime stopped ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
