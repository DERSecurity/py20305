"""Tests for the container build.

The image itself is exercised in CI, which builds it and runs the client inside
it. What is checked here is the part that goes stale silently: the promises the
Dockerfile and the docs make to each other, and the example configuration a
reader is told to copy.

These run everywhere, need no Docker daemon, and fail on the mismatches that a
build still succeeds through -- an image that builds fine while running as root
or shipping a source tree is the failure worth catching early.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
COMPOSE = ROOT / "examples" / "docker-compose.yml"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def docs() -> str:
    return (ROOT / "docs" / "docker.md").read_text(encoding="utf-8")


class TestImageIsUnprivileged:
    def test_a_user_is_declared(self, dockerfile: str):
        """Root is the default, so not choosing is choosing root."""
        assert re.search(r"^USER\s+py20305\s*$", dockerfile, re.MULTILINE)

    def test_the_user_directive_is_the_last_one(self, dockerfile: str):
        """A later USER would silently undo it."""
        users = re.findall(r"^USER\s+(\S+)", dockerfile, re.MULTILINE)
        assert users, "no USER directive"
        assert users[-1] == "py20305"

    def test_the_ids_are_pinned(self, dockerfile: str):
        """A mounted file is matched by number, so the ids cannot drift.

        Letting the system assign them means they change when the base image
        does, and a deployment's file permissions stop matching -- with a
        private key, that is the difference between unreadable and
        world-readable.
        """
        assert re.search(r"^ARG UID=(\d+)", dockerfile, re.MULTILINE)
        assert re.search(r"^ARG GID=(\d+)", dockerfile, re.MULTILINE)
        assert '--uid "${UID}"' in dockerfile
        assert '--gid "${GID}"' in dockerfile

    def test_the_pinned_ids_can_be_overridden(self, dockerfile: str):
        """Borrowing the host's ids is how a 0600 key stays readable."""
        useradd = [
            ln
            for ln in dockerfile.splitlines()
            if "useradd" in ln and not ln.lstrip().startswith("#")
        ]
        assert useradd and all("${UID}" in ln for ln in useradd), useradd

    def test_the_user_cannot_log_in(self, dockerfile: str):
        assert "--shell /usr/sbin/nologin" in dockerfile


class TestSignalsReachTheClient:
    def test_the_entrypoint_is_exec_form(self, dockerfile: str):
        """Shell form would leave the client as a child that never sees SIGTERM.

        The runner shuts down in order on that signal; through a shell, every
        stop becomes a kill after the grace period instead.
        """
        entrypoint = re.search(r"^ENTRYPOINT\s+(.+)$", dockerfile, re.MULTILINE)
        assert entrypoint is not None
        assert entrypoint.group(1).lstrip().startswith("["), "ENTRYPOINT must be exec form"

    def test_the_entrypoint_is_the_installed_command(self, dockerfile: str):
        """The console script the package installs, not a path that could drift."""
        assert 'ENTRYPOINT ["py20305"]' in dockerfile


class TestNothingSecretIsBuildable:
    @pytest.mark.parametrize("pattern", ["*.pem", "*.key", "client.yaml"])
    def test_credentials_are_excluded_from_the_build_context(self, pattern: str):
        """A stray key in the working directory must not be copyable into an image."""
        assert pattern in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()

    def test_no_certificate_material_is_copied_in(self, dockerfile: str):
        copied = re.findall(r"^COPY\s+(.+)$", dockerfile, re.MULTILINE)
        for line in copied:
            assert not re.search(r"\.(pem|key|p12|pfx)\b", line), line


class TestRuntimeCarriesOnlyTheArtifact:
    def test_the_build_is_multi_stage(self, dockerfile: str):
        """So the runtime image holds neither the build backend nor the sources."""
        stages = re.findall(r"^FROM\s+\S+\s+AS\s+(\S+)", dockerfile, re.MULTILINE)
        assert stages == ["build", "runtime"]

    def test_the_runtime_installs_a_wheel_not_the_source_tree(self, dockerfile: str):
        runtime = dockerfile.split("AS runtime", 1)[1]
        assert ".whl" in runtime
        assert "COPY src" not in runtime

    def test_the_wheel_is_removed_after_installation(self, dockerfile: str):
        """It is dead weight in the layer once installed."""
        assert "rm -rf /tmp/*.whl" in dockerfile


class TestConfigurationContract:
    """The container's config path is quoted in three places; they must agree."""

    def test_the_default_command_matches_the_declared_config_path(self, dockerfile: str):
        env = re.search(r"^ENV\s+PY20305_CONFIG=(\S+)", dockerfile, re.MULTILINE)
        assert env is not None
        assert env.group(1) in dockerfile.split("CMD", 1)[1]

    def test_the_mount_point_exists_and_is_owned_by_the_runtime_user(self, dockerfile: str):
        assert "mkdir -p /etc/py20305" in dockerfile
        assert "chown py20305:py20305 /etc/py20305" in dockerfile

    def test_the_compose_example_mounts_where_the_image_expects(self):
        compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
        volumes = compose["services"]["py20305"]["volumes"]
        assert any(v.split(":")[1] == "/etc/py20305" for v in volumes), volumes

    def test_the_compose_example_mounts_read_only(self):
        """The client never writes there, and a config it could rewrite is a trap."""
        compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
        volumes = compose["services"]["py20305"]["volumes"]
        assert all(v.endswith(":ro") for v in volumes), volumes

    def test_the_compose_example_allows_an_ordered_shutdown(self):
        """The ten-second default kills the client mid-exchange."""
        compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
        grace = compose["services"]["py20305"]["stop_grace_period"]
        assert int(str(grace).rstrip("s")) >= 30

    def test_the_compose_example_publishes_no_ports_by_default(self):
        """The client makes outbound connections; it does not serve."""
        compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
        assert "ports" not in compose["services"]["py20305"]


class TestDocsMatchTheImage:
    """The docs tell a reader exact commands; drift makes them wrong, not vague."""

    def test_the_documented_uid_is_the_built_one(self, docs: str, dockerfile: str):
        """The docs tell an operator which id to chown to; a drift misleads them."""
        uid = re.search(r"^ARG UID=(\d+)", dockerfile, re.MULTILINE)
        assert uid is not None
        assert uid.group(1) in docs

    def test_the_documented_extras_are_the_default_ones(self, docs: str, dockerfile: str):
        default = re.search(r"^ARG EXTRAS=(\S+)", dockerfile, re.MULTILINE)
        assert default is not None
        for extra in default.group(1).split(","):
            assert f"`{extra}`" in docs, f"{extra} is built in but undocumented"

    def test_the_api_extra_is_excluded_and_said_to_be(self, docs: str, dockerfile: str):
        """Leaving it out is a decision, so it has to be a stated one."""
        default = re.search(r"^ARG EXTRAS=(\S+)", dockerfile, re.MULTILINE)
        assert default is not None
        assert "api" not in default.group(1).split(",")
        assert "management API" in docs


class TestDocumentedCommandsReproduceCI:
    """A contributor running the documented commands must see what CI sees.

    Lives beside the other drift checks because it is the same failure: two
    files stating the same fact, one of them updated. A lint path added to CI
    and not to the README means a contributor's clean run is not a clean build.
    """

    @staticmethod
    def _ci_lint_paths() -> set[str]:
        workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
        for step in workflow["jobs"]["lint"]["steps"]:
            if step.get("name") == "ruff":
                return set(step["run"].split()[2:])
        raise AssertionError("no ruff step in the lint job")

    @staticmethod
    def _documented_lint_paths(doc: str) -> set[str]:
        for line in (ROOT / doc).read_text("utf-8").splitlines():
            if line.startswith("ruff check "):
                return set(line.split()[2:])
        raise AssertionError(f"{doc} documents no ruff command")

    # Every file that tells a contributor how to lint. Checking only one of
    # them is how the other goes stale: CI gained `scripts` and CONTRIBUTING
    # kept the old command, so a clean local run still failed the build.
    @pytest.mark.parametrize("doc", ["README.md", "CONTRIBUTING.md"])
    def test_documented_lint_matches_ci(self, doc: str):
        assert self._documented_lint_paths(doc) == self._ci_lint_paths()

    def test_scripts_is_linted(self):
        """It ships in the repository and CI executes it, so it is not optional."""
        assert "scripts" in self._ci_lint_paths()
