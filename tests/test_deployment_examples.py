"""Guards that the shipped deployment examples still match the code.

A stale example is worse than no example: it is a recipe someone follows and
then debugs. These pin the couplings that break quietly -- the example document
against the config model, and the service name, config path and exit-code
contract against the code the examples invoke.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

from py20305.cli import EXIT_CONFIG_ERROR
from py20305.config import load_config

_REPO = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO / "pyproject.toml"
_EXAMPLE_CONFIG = _REPO / "examples" / "client.example.yaml"
_UNIT = _REPO / "examples" / "systemd" / "py20305.service"
_UNIT_README = _REPO / "examples" / "systemd" / "README.md"
_DOCKERFILE = _REPO / "Dockerfile"
_COMPOSE = _REPO / "examples" / "docker-compose.yml"


def _console_script() -> str:
    """Return the single console script declared in pyproject."""
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert len(scripts) == 1, f"expected one console script, found {sorted(scripts)}"
    return next(iter(scripts))


def _unit_directive(name: str) -> str:
    """Return the value of a systemd directive from the example unit."""
    for raw in _UNIT.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f"{name}= not found in {_UNIT.name}")


def _dockerfile_exec_list(instruction: str) -> list[str]:
    """Return the JSON-form argument list of ENTRYPOINT or CMD."""
    match = re.search(
        rf"^{instruction}\s+(\[[^\]]*\])", _DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert match, f"{instruction} not found in exec form in Dockerfile"
    return [part.strip().strip('"') for part in match.group(1).strip("[]").split(",")]


def _config_path_argument(argv: list[str]) -> str:
    """Return the value following --config in an argument list."""
    for flag, value in zip(argv, argv[1:], strict=False):
        if flag == "--config":
            return value
    raise AssertionError(f"no --config argument in {argv}")


def test_example_config_loads() -> None:
    """The documented example must validate against the current config model."""
    config = load_config(_EXAMPLE_CONFIG)

    assert str(config.server.url)
    assert config.devices, "the example should show at least one device"


def test_unit_stops_instead_of_looping_on_a_bad_config() -> None:
    """RestartPreventExitStatus must track the exit code the CLI actually uses.

    If they drift, a typo in the configuration becomes a restart loop that
    reads as a network problem.
    """
    assert _unit_directive("RestartPreventExitStatus") == str(EXIT_CONFIG_ERROR)


def test_unit_and_image_invoke_the_declared_console_script() -> None:
    """Renaming the entry point must not leave the examples calling the old name."""
    script = _console_script()

    assert _unit_directive("ExecStart").split()[0].endswith(f"/{script}")
    assert _dockerfile_exec_list("ENTRYPOINT") == [script]


def test_image_default_config_path_is_inside_the_documented_mount() -> None:
    """The compose mount has to land where the image's default CMD looks."""
    cmd_config = _config_path_argument(_dockerfile_exec_list("CMD"))

    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    volumes = compose["services"]["py20305"]["volumes"]
    targets = [entry.split(":")[1] for entry in volumes]

    assert any(cmd_config.startswith(f"{target.rstrip('/')}/") for target in targets), (
        f"the image looks for {cmd_config}, which no compose volume mounts: {targets}"
    )


@pytest.mark.parametrize(
    "path",
    [_EXAMPLE_CONFIG, _UNIT, _UNIT_README, _DOCKERFILE, _COMPOSE],
    ids=lambda p: p.name,
)
def test_example_files_are_shipped(path: Path) -> None:
    """The docs link to these by path; a move has to update the docs too."""
    assert path.is_file(), f"{path} is referenced by the deployment docs"
