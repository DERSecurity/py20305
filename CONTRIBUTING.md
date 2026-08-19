# Contributing

Contributions are welcome — bug reports, interop findings from real utility
servers, connectors, and documentation.

## Before you write code

**For a bug**, open an issue with enough to reproduce it: the server
implementation you were talking to, the resource or event involved, and the XML
if you have it. Interop bugs are the most valuable reports this project gets
and the hardest to reconstruct after the fact.

**For a feature or a behavior change**, open an issue first. This is a protocol
implementation, so the useful question is usually not "should we do this" but
"what does the standard say" — and that discussion is cheaper before the code
than after.

**For a connector**, note that only SunSpec Modbus ships in-tree. A connector
for your own device usually belongs in your project, using `CustomDeviceConfig`
or a `factory_resolver`; see the
[connector guide](https://dersecurity.github.io/py20305/connectors/).
Open an issue if you think one belongs here.

## Development setup

```bash
git clone https://github.com/DERSecurity/py20305
cd py20305
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Then:

```bash
pytest tests -q
ruff check src tests examples scripts
mypy src/py20305
mkdocs serve
```

CI runs all of these on Python 3.11, 3.12 and 3.13, plus a Windows and a macOS
job. It also builds the package and checks the XSDs are inside the wheel.

## What a good change looks like

**Tests that could fail.** A test that passes against the broken code tests
nothing. The check that catches this: revert your fix, confirm the test fails,
restore it. Assertions shaped like the fixture rather than the behavior are the
usual way this goes wrong.

**Comments that say why.** What the code does is visible; why it does that
rather than the obvious thing is not. Where the standard forced your hand, cite
the clause — `IEEE 2030.5-2018 §8.9.3.4 rule (r)` tells the next reader far
more than a restatement of the line below it.

**Behavior documented where a user will find it.** A change an operator can see
belongs in `docs/`, in the same pull request.

**Type annotations on public API.** `mypy` runs with `disallow_untyped_defs` on
`src/`. Tests are exempt.

## Conventions

- Line length 100, enforced by `ruff`.
- American English in code, comments, and documentation.
- Commit messages: a short imperative subject, then prose explaining why the
  change is right. No trailers.

## Reporting a security issue

Please do not open a public issue for a vulnerability. See
[SECURITY.md](SECURITY.md).
