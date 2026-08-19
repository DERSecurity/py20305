"""Version and build information, for startup logging and message provenance."""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

#: Distribution name, as it appears on the package index.
PACKAGE_NAME = "py20305"


def _find_build_info() -> str | None:
    """Return the build stamp if one was baked in, else ``None``.

    Checks, in order: a PyInstaller bundle, a container image's ``/app``, and
    the source tree next to this file. Only a packaged build has one; running
    from a checkout returns ``None``.
    """
    candidates = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "build_info.txt")

    candidates.append(Path("/app/build_info.txt"))
    candidates.append(Path(__file__).parent / "build_info.txt")

    for path in candidates:
        if path.is_file():
            return path.read_text().strip()

    return None


def get_package_version() -> str:
    """Return the bare package version, e.g. ``'0.1.0'``.

    Returns ``'unknown'`` when the package is not installed, which happens
    when running straight from a source tree.
    """
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "unknown"


def get_version_string() -> str:
    """Return a human-readable version line for startup logs."""
    ver = get_package_version()
    build = _find_build_info()
    if build:
        return f"{PACKAGE_NAME} v{ver} (build {build})"
    return f"{PACKAGE_NAME} v{ver}"
