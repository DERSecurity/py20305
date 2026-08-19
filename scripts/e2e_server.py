"""Bring up a real IEEE 2030.5 server for the end-to-end tests.

The server is `envoy <https://github.com/bsgip/envoy>`_, the open-source
CSIP-AUS utility server from the Australian National University's Battery
Storage and Grid Integration Program. It is the implementation ANU's CSIP-AUS
certification program runs on, which makes it the closest thing to an
independent referee available without a commercial test service.

It is not vendored. This script clones it at a pinned tag, builds its image and
brings up its own demo compose stack, so the harness follows upstream rather
than drifting from a copy. What we own is the pin and the readiness check.

Usage::

    python scripts/e2e_server.py up      # clone, build, start, wait for /dcap
    python scripts/e2e_server.py down    # stop and remove
    python scripts/e2e_server.py env     # print the variables the tests read

``up`` prints the two environment variables the tests need, so a shell can do::

    eval "$(python scripts/e2e_server.py env)"
"""

from __future__ import annotations

import argparse
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

#: Pinned upstream version. Bumping this is a deliberate act: a new version can
#: change protocol behavior, which is the entire point of testing against it.
ENVOY_REF = "v1.6.0"
ENVOY_REPO = "https://github.com/bsgip/envoy.git"

#: Where the clone lands. Outside the repository so it is never committed and
#: never confuses a source tree walk.
CHECKOUT = Path(
    os.environ.get(
        "PY20305_E2E_CHECKOUT",
        Path(__file__).resolve().parent.parent / ".e2e" / "envoy",
    )
)

SERVER_URL = "https://localhost:8443"
READY_TIMEOUT_SECONDS = 180


def _run(cmd: list[str], cwd: Path | None = None, **kw: object) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=cwd, check=True, text=True, **kw)  # type: ignore[call-overload,no-any-return]


def _normalize_line_endings(root: Path) -> int:
    """Rewrite CRLF to LF in the scripts the containers execute.

    Git for Windows checks shell scripts out with CRLF by default, and the
    container's ``sh`` fails on the trailing carriage return with a message
    ("set: Illegal option -") that says nothing about line endings. Cheap to
    prevent, expensive to diagnose.
    """
    changed = 0
    for path in root.rglob("*"):
        if ".git" in path.parts or not path.is_file():
            continue
        if path.suffix not in {".sh", ".conf"} and path.suffix != "":
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\r\n" in raw:
            path.write_bytes(raw.replace(b"\r\n", b"\n"))
            changed += 1
    return changed


def _clone() -> None:
    if (CHECKOUT / ".git").is_dir():
        # Reuse only if it is the pinned revision. Reusing whatever is there
        # makes ENVOY_REF ineffective after the first run: bumping the pin
        # would still test the old server, silently.
        current = subprocess.run(
            ["git", "describe", "--tags", "--exact-match"],
            cwd=CHECKOUT, capture_output=True, text=True, check=False,
        ).stdout.strip()
        if current == ENVOY_REF:
            print(f"reusing {ENVOY_REF} checkout at {CHECKOUT}")
            return
        print(f"checkout is at {current or 'an unknown revision'}, replacing with {ENVOY_REF}")
        shutil.rmtree(CHECKOUT, ignore_errors=True)
    CHECKOUT.parent.mkdir(parents=True, exist_ok=True)
    # core.autocrlf=false at clone time rather than a fix afterwards: it keeps
    # the working tree byte-identical to upstream on every platform.
    _run(
        [
            "git", "clone", "--depth", "1", "--branch", ENVOY_REF,
            "--config", "core.autocrlf=false",
            ENVOY_REPO, str(CHECKOUT),
        ]
    )
    n = _normalize_line_endings(CHECKOUT)
    if n:
        print(f"normalized line endings in {n} file(s)")


def _compose(*args: str) -> list[str]:
    return ["docker", "compose", *args]


def _compose_env() -> dict[str, str]:
    """Environment for the compose invocation.

    Two values that have to be set rather than inherited:

    ``HOST_UID`` / ``HOST_GID`` are build arguments upstream uses so the
    certificates the container generates end up owned by the user who will
    read them. They must be *this* user's ids -- hardcoding 1000 works on a
    typical workstation and fails on a CI runner that numbers its user
    differently, with a permission error on the private key rather than
    anything that mentions ownership. Windows has no such ids and no such
    problem, since the bind mount is not POSIX-owned there.

    ``PWD`` is what the upstream compose file interpolates to locate the
    certificate directory it binds. Compose reads the variable, not the
    process working directory, so inheriting it writes the certificates
    wherever the caller happened to be standing.
    """
    uid = getattr(os, "getuid", None)
    gid = getattr(os, "getgid", None)
    return {
        **os.environ,
        "HOST_UID": str(uid() if uid else 1000),
        "HOST_GID": str(gid() if gid else 1000),
        "PWD": str(_demo_dir()),
    }


def _demo_dir() -> Path:
    return CHECKOUT / "demo"


def _cert_dir() -> Path:
    return _demo_dir() / "tls-termination" / "test_certs"


def _wait_for_seed() -> None:
    """Wait for the one-shot database seeder to exit successfully.

    ``/dcap`` can answer while ``envoy-db_init`` is still creating the records
    registration and control depend on, so an HTTP probe alone leaves a race
    that shows up as a test failing rather than as a server not being ready.
    """
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        result = subprocess.run(
            _compose("ps", "-a", "--status", "exited", "--format", "{{.Service}} {{.ExitCode}}"),
            cwd=_demo_dir(), env=_compose_env(), capture_output=True, text=True, check=False,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].endswith("db_init"):
                if parts[1] == "0":
                    print("database seeded")
                    return
                raise SystemExit(f"database seeding failed with exit code {parts[1]}")
        time.sleep(3)
    raise SystemExit(f"database seeding did not finish within {READY_TIMEOUT_SECONDS}s")


def _wait_until_ready() -> None:
    """Poll /dcap over mTLS until the server answers.

    Readiness is "serves a DeviceCapability to a client certificate", not
    "containers are running": certificate generation finishes after the
    containers report up, and a test starting in that window fails for
    reasons that have nothing to do with it.
    """
    ca = _cert_dir() / "testca.crt"
    crt = _cert_dir() / "testdevice1.crt"
    key = _cert_dir() / "testdevice1.key"

    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    last: str = "never attempted"
    while time.monotonic() < deadline:
        if ca.is_file() and crt.is_file() and key.is_file():
            try:
                ctx = ssl.create_default_context(cafile=str(ca))
                ctx.load_cert_chain(str(crt), str(key))
                with urllib.request.urlopen(  # noqa: S310 - fixed localhost URL
                    f"{SERVER_URL}/dcap", context=ctx, timeout=10
                ) as response:
                    if response.status == 200 and b"DeviceCapability" in response.read():
                        print(f"server ready at {SERVER_URL}")
                        return
                    last = f"HTTP {response.status}"
            except Exception as exc:  # noqa: BLE001 - any failure means not ready yet
                last = f"{type(exc).__name__}: {exc}"
        else:
            last = "certificates not generated yet"
        time.sleep(3)

    raise SystemExit(
        f"server did not become ready within {READY_TIMEOUT_SECONDS}s. Last attempt: {last}"
    )


def up() -> None:
    if shutil.which("docker") is None:
        raise SystemExit("docker is required for the end-to-end tests and was not found on PATH")

    _clone()
    _run(
        ["docker", "build", "--quiet", "-t", "envoy:latest", "-f", "Dockerfile.server", "."],
        cwd=CHECKOUT,
    )
    env = _compose_env()
    _run(_compose("up", "-d"), cwd=_demo_dir(), env=env)
    _wait_until_ready()
    _wait_for_seed()
    print()
    env_lines()


def down() -> None:
    if not _demo_dir().is_dir():
        print("nothing to bring down")
        return
    env = _compose_env()
    subprocess.run(_compose("down", "-v"), cwd=_demo_dir(), env=env, check=False)


def env_lines() -> None:
    print(f"export PY20305_E2E_SERVER_URL={SERVER_URL}")
    print(f"export PY20305_E2E_CERT_DIR={_cert_dir()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["up", "down", "env"])
    action = parser.parse_args().action
    {"up": up, "down": down, "env": env_lines}[action]()


if __name__ == "__main__":
    sys.exit(main())
