"""Tests for py20305.version_info."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from py20305.version_info import (
    PACKAGE_NAME,
    _find_build_info,
    get_version_string,
)


class TestGetVersionString:
    def test_returns_version_without_build_info(self) -> None:
        with patch("py20305.version_info._find_build_info", return_value=None):
            result = get_version_string()
        assert result.startswith(f"{PACKAGE_NAME} v")
        assert "build" not in result

    def test_returns_version_with_build_info(self) -> None:
        with patch(
            "py20305.version_info._find_build_info",
            return_value="20260305-143022",
        ):
            result = get_version_string()
        assert "(build 20260305-143022)" in result
        assert result.startswith(f"{PACKAGE_NAME} v")

    def test_unknown_version_on_missing_package(self) -> None:
        with (
            patch(
                "py20305.version_info.version",
                side_effect=__import__(
                    "importlib.metadata", fromlist=["PackageNotFoundError"]
                ).PackageNotFoundError,
            ),
            patch("py20305.version_info._find_build_info", return_value=None),
        ):
            result = get_version_string()
        assert "unknown" in result

    def test_installed_package_version(self) -> None:
        """Smoke test: installed package returns a real version string."""
        result = get_version_string()
        assert result.startswith(f"{PACKAGE_NAME} v")
        assert "unknown" not in result


class TestFindBuildInfo:
    def test_returns_none_when_no_file_exists(self) -> None:
        assert _find_build_info() is None

    def test_reads_from_adjacent_file(self, tmp_path: Path) -> None:
        build_file = tmp_path / "build_info.txt"
        build_file.write_text("20260305-100000\n")
        with patch("py20305.version_info.__file__", str(tmp_path / "version_info.py")):
            result = _find_build_info()
        assert result == "20260305-100000"

    def test_reads_from_meipass(self, tmp_path: Path) -> None:
        build_file = tmp_path / "build_info.txt"
        build_file.write_text("20260305-120000\n")
        with patch.object(__import__("sys"), "_MEIPASS", str(tmp_path), create=True):
            result = _find_build_info()
        assert result == "20260305-120000"

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        build_file = tmp_path / "build_info.txt"
        build_file.write_text("  20260305-120000  \n")
        with patch.object(__import__("sys"), "_MEIPASS", str(tmp_path), create=True):
            result = _find_build_info()
        assert result == "20260305-120000"
