"""A localhost Orbit stack for development and integration tests.

Starts a broker and one endpoint as subprocesses, so the real Orbit client path
(TLS websocket, plugin sessions, pushed events, PSI/J log offsets) is exercised
without any HPC allocation. The endpoint runs rhapsody on its `concurrent`
backend and PSI/J with the `local` executor, so jobs really run as processes.

Note on TLS: `--no-auth` only disables the broker's *ingress token* check. The
broker always serves HTTPS/WSS and refuses to start without a cert and key, so
this generates a throwaway self-signed pair per stack and pins it on the client.

Subprocesses rather than `embedded=True`: the embedded broker expects
operator-placed credentials in ~/.radical/orbit, which a test must not touch.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _script(name: str) -> str | None:
    """Locate an Orbit CLI script, on PATH or in the reference checkout."""
    found = shutil.which(name)
    if found:
        return found
    here = Path(__file__).resolve()
    candidates = [
        # .../backend/designagent/tasks/hpc/ -> project root
        here.parents[4] / "refcodes" / "radical.orbit" / "bin",
        Path.cwd() / "refcodes" / "radical.orbit" / "bin",
    ]
    try:  # alongside the installed package, wherever that is
        import radical.orbit as orbit_pkg

        candidates.append(Path(orbit_pkg.__file__).resolve().parents[3] / "bin")
    except Exception:
        pass
    for root in candidates:
        candidate = root / name
        if candidate.exists():
            return str(candidate)
    return None


def make_self_signed(directory: Path, common_name: str = "127.0.0.1") -> tuple[Path, Path]:
    """Generate a throwaway cert/key pair. Returns (cert, key)."""
    directory.mkdir(parents=True, exist_ok=True)
    cert = directory / "broker_cert.pem"
    key = directory / "broker_key.pem"
    if cert.exists() and key.exists():
        return cert, key

    if not shutil.which("openssl"):
        raise RuntimeError("openssl is required to generate a local broker cert")
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert),
            "-days", "3", "-subj", f"/CN={common_name}",
            # The client connects to 127.0.0.1, so the SAN must cover it.
            "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost",
        ],
        check=True,
        capture_output=True,
    )
    # The broker refuses to start if the key is more permissive than 0600.
    key.chmod(0o600)
    return cert, key


class LocalOrbitStack:
    """Broker + endpoint on localhost, for dev and tests."""

    def __init__(
        self,
        *,
        port: int | None = None,
        endpoint_name: str = "local",
        plugins: str = "rhapsody,psij",
        work_dir: Path | None = None,
        rhapsody_backend: str = "concurrent",
    ):
        self.port = port or _free_port()
        self.endpoint_name = endpoint_name
        self.plugins = plugins
        self.work_dir = Path(work_dir) if work_dir else Path.cwd() / ".orbit-local"
        self.rhapsody_backend = rhapsody_backend
        self.cert: Path | None = None
        self.key: Path | None = None
        self._broker: subprocess.Popen | None = None
        self._endpoint: subprocess.Popen | None = None
        self._logs = self.work_dir / "logs"

    @property
    def broker_url(self) -> str:
        return f"https://127.0.0.1:{self.port}"

    async def start(self, timeout: float = 90.0) -> None:
        broker_script = _script("radical-orbit-broker.py")
        endpoint_script = _script("radical-orbit-endpoint.py")
        if not broker_script or not endpoint_script:
            raise RuntimeError(
                "Orbit CLI scripts not found; install radical.orbit or run from "
                "a tree containing refcodes/radical.orbit/bin"
            )

        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._logs.mkdir(parents=True, exist_ok=True)
        self.cert, self.key = make_self_signed(self.work_dir)

        env = dict(os.environ)
        env["RADICAL_ORBIT_RHAPSODY_BACKEND"] = self.rhapsody_backend
        env["RADICAL_ORBIT_BROKER_CERT"] = str(self.cert)
        # Nothing should point the children at a real broker or token.
        env.pop("RADICAL_ORBIT_BROKER_URL", None)
        env.pop("RADICAL_ORBIT_BROKER_TOKEN", None)

        self._broker = self._spawn(
            [
                sys.executable, broker_script,
                "--no-auth",
                "--host", "127.0.0.1",
                "--port", str(self.port),
                "--cert", str(self.cert),
                "--key", str(self.key),
            ],
            env,
            "broker",
        )
        await self._wait_for_port(timeout=timeout / 2)

        self._endpoint = self._spawn(
            [
                sys.executable, endpoint_script,
                "--name", self.endpoint_name,
                "-p", self.plugins,
                "--url", self.broker_url,
                "--cert", str(self.cert),
            ],
            env,
            "endpoint",
        )
        # Readiness is not checked over HTTP: the gateway has no topology route
        # (it reads /topology as a plugin name). The authoritative view is the
        # client's own rt.topology(), so OrbitInterface.connect() polls for the
        # endpoint and this only waits for the process to come up.
        await self._wait_for_endpoint(timeout=timeout)

    def _spawn(self, argv: list[str], env: dict, label: str) -> subprocess.Popen:
        handle = (self._logs / f"{label}.log").open("w")
        return subprocess.Popen(
            argv, env=env, cwd=str(self.work_dir), stdout=handle, stderr=subprocess.STDOUT
        )

    def _tail(self, label: str, lines: int = 12) -> str:
        path = self._logs / f"{label}.log"
        if not path.exists():
            return ""
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])

    async def _wait_for_port(self, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._broker and self._broker.poll() is not None:
                raise RuntimeError(
                    f"Orbit broker exited during startup:\n{self._tail('broker')}"
                )
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    return
            except OSError:
                await asyncio.sleep(0.3)
        raise TimeoutError(
            f"Orbit broker did not open port {self.port}:\n{self._tail('broker')}"
        )

    async def _wait_for_endpoint(self, timeout: float) -> None:
        """Wait until the endpoint process reports it registered.

        Read from its own log, because the broker exposes no HTTP topology
        route; the authoritative check (rt.topology()) belongs to the client.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._endpoint and self._endpoint.poll() is not None:
                raise RuntimeError(
                    f"Orbit endpoint exited during startup:\n{self._tail('endpoint')}"
                )
            log_text = self._tail("endpoint", lines=200)
            if f"registered as '{self.endpoint_name}'" in log_text:
                return
            await asyncio.sleep(0.5)
        raise TimeoutError(
            f"Orbit endpoint did not register within {timeout}s:\n{self._tail('endpoint')}"
        )

    async def stop(self) -> None:
        for proc in (self._endpoint, self._broker):
            if proc is None or proc.poll() is not None:
                continue
            proc.terminate()
            try:
                await asyncio.to_thread(proc.wait, 10)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._endpoint = self._broker = None

    async def __aenter__(self) -> "LocalOrbitStack":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()
