"""Every public module must import on its own, as the first thing a process does.

Importing the package a module at a time in one process proves less than it
appears to: whichever module comes first pulls in its dependencies, and by the
time a later one is reached the cycle it would have triggered is already
resolved. A consumer who reaches straight for a single module gets no such
head start.

So each module is imported in a fresh interpreter here. That is slow enough to
be worth justifying and it is the only way this class of defect shows up --
`py20305.subscription` and `py20305.events.processor` both
raised ImportError on a cold import while every in-process check passed.
"""

from __future__ import annotations

import pkgutil
import subprocess
import sys

import pytest

import py20305


def _public_modules() -> list[str]:
    """Every importable module in the package, excluding private ones."""
    return sorted(
        m.name
        for m in pkgutil.walk_packages(py20305.__path__, "py20305.")
        if not any(part.startswith("_") for part in m.name.split("."))
    )


@pytest.mark.parametrize("module", _public_modules())
def test_module_imports_cold(module: str) -> None:
    """The module imports in an interpreter that has loaded nothing else."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"cold `import {module}` failed:\n{result.stderr.strip()}"
    )
