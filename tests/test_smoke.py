"""Smoke tests to verify the package is importable and toolchain works."""


def test_import() -> None:
    import py20305

    assert py20305.__doc__ is not None
