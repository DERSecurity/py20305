# Rules for working in this repository

py20305 is an open IEEE 2030.5 / CSIP client for distributed energy resources, with
CSIP-AUS support. It registers with a utility's server, discovers what the server exposes,
runs the DERControl schedule it is given, applies the setpoints to a device and posts
telemetry back. A wrong setpoint or a missed event is a defect in someone's equipment and
in a utility's program.

These rules apply to every contributor, human or coding agent. They say what a change is
held to. [CONTRIBUTING.md](CONTRIBUTING.md) covers setup and how changes land, and is not
repeated here.

**Before changing anything, read:**

1. [README.md](README.md), for what the client does and which standards it follows.
2. The guide under [docs/](docs/) for the area you are changing, and any plan for it in
   [docs/planning/](docs/planning/).
3. [docs/testing.md](docs/testing.md), for what each test suite can and cannot prove.
4. The module's own docstring. Most design choices are recorded there, with the clause
   that forced them.

## Quick reference

### The stack

- Python 3.11, 3.12 and 3.13, on Linux, Windows and macOS.
- The base install is the protocol client: `aiohttp`, `cryptography`, `pydantic` 2,
  `xsdata-pydantic` and `lxml`. Everything else is an extra: `api` (FastAPI and uvicorn),
  `cli` (PyYAML), `sunspec` (pysunspec2 and pyserial) and `mqtt` (aiomqtt).
- Build: `setuptools`. Tests: `pytest` with `pytest-asyncio` in auto mode, `pytest-aiohttp`
  and `pytest-timeout`. Lint: `ruff`, line length 100. Types: `mypy` with
  `disallow_untyped_defs`. Documentation: MkDocs 1.x with Material and mkdocstrings.
- One command is installed: `py20305`.

### Commands

```bash
pip install -e ".[dev]"                      # once; everything below needs it

pytest tests -q                              # the unit suite: offline, no Docker
pytest tests/test_identity.py -q             # one file
pytest tests -q -k "lfdi"                    # the tests whose names match

ruff check src tests examples scripts        # lint
mypy src/py20305                             # types
python scripts/build_changelog.py --check    # the changelog fragments; --preview renders them
mkdocs build --strict                        # the documentation; `mkdocs serve` to read it

py20305 --config client.yaml --check         # validate a configuration and print the LFDI

python scripts/e2e_server.py up              # the end-to-end suite; needs Docker
eval "$(python scripts/e2e_server.py env)"
pytest tests/e2e -v
python scripts/e2e_server.py down
```

`pytest` leaves `tests/e2e` out unless it is named. The end-to-end job also runs in CI on
every pull request.

### Boundaries

**Always:**

- Tie protocol behavior to a clause of the standard or of a profile, with its edition.
- Add a test that fails without the change.
- Update the guides, the reference pages and the example configuration in the same change.
- Add a changelog fragment for anything a user can observe.
- Keep every public module importable on its own, with only the base install present.
- Run the commands above before pushing.

**Ask first:**

- Adding a dependency to the base install, or raising the minimum Python version.
- Changing what the client sends to a server, when it sends it, or what it applies to a
  device.
- Changing a configuration key, an exit code, a management API route or public API that
  others build on.
- Adding or replacing a schema file, or regenerating the models.
- Weakening, skipping or deleting a test.
- Changing a CI workflow, the Dockerfile or the release process.
- Anything that would connect to a utility's server or a real device.
- A protocol fact you cannot find in the source documents.

**Never:**

- Commit a real certificate, private key, LFDI, server address or device identity.
- Commit a capture from a utility's server or a real device that has not been scrubbed and
  cleared by its owner.
- Invent a clause number, a resource, an enumeration value or a default.
- Weaken TLS: no new way to skip verification, and no secure default changed.
- Edit the generated models or `CHANGELOG.md` by hand.
- Say or imply that py20305 or a product built on it is certified.
- Push to `main`, force-push a shared branch, go around a check, or tag a release.
- Add tool or AI attribution to a commit, a pull request or a comment.

### What good looks like

A test states the value it expects, taken from outside the code under test:

```python
def test_compute_lfdi(test_cert_pem: str):
    lfdi = compute_lfdi(test_cert_pem)
    assert lfdi == "fe9d4315af233c2e9bfa89e3f5f9b645e5b157f2"
```

A docstring starts with what the function does and names the clause behind it:

```python
"""Query the local network for IEEE 2030.5 servers (IEEE 2030.5 §6.9.2)."""
```

A comment says why, and where the requirement comes from:

```python
# The poll path passes the hrefs it is subscribed to, so a subscribed resource is not
# fetched again (IEEE 2030.5 §8.9.3.4 rule (r)).
```

The rest of this page gives the rules in full.

## 1. Correctness comes first

Correctness outranks features, speed, convenience and a tidy API. A change that is not
known to be correct is not finished, however complete it looks.

### What decides what is correct

In this order:

1. **IEEE 2030.5-2023 and IEEE 2030.5-2018.** The generated bindings track 2023. Servers
   still on 2018 are supported through `server_2018_compat`. Where the two editions
   differ, the code handles both and says which edition each branch is for.
2. **The published schemas.** The IEEE 2030.5 and CSIP-AUS XSDs ship in
   `src/py20305/schemas/`, and they decide what valid XML is.
3. **CSIP, the Common Smart Inverter Profile**, the IEEE 2030.5 implementation guide for
   smart inverters. Where CSIP narrows or fixes something the standard leaves open, a
   CSIP client follows CSIP.
4. **CSIP-AUS**, the Australian profile and its extensions, including dynamic operating
   envelope limits. It applies where the client is used under CSIP-AUS and does not change
   behavior for anyone else.
5. **The SunSpec CSIP conformance test procedures**, for what a certification test checks
   and how.
6. **The RFCs the standard builds on**: TLS, HTTP, X.509 certificates, DNS-SD and mDNS.
7. **The SunSpec Modbus information models**, for the SunSpec connector only.

Other implementations are witnesses, not authorities. The end-to-end suite runs against an
independently written CSIP-AUS server, the recorded run in `docs/conformance/` shows how
the client answered a CSIP client test suite, and reports from real utility servers are
the most valuable bug reports the project gets. When a server disagrees with this client,
go back to the documents above to find out which is right.

These do not decide anything:

- What this client does today.
- What one server happens to accept.
- Memory, a forum post, or a model's recollection of a clause.

### Rules that follow

- **Cite the source, with its edition.** Where a document forced a choice, name it in the
  comment or docstring: `IEEE 2030.5-2018 §8.9.3.4 rule (r)`, `RFC 6763`. A reader must be
  able to check the claim.
- **Never invent a protocol fact.** No guessed clause numbers, resource names, attribute
  defaults, enumeration values, status codes or timing rules. If you cannot find the
  source, say so in the pull request and ask. "Not verified against the standard" is an
  acceptable sentence. A confident guess is not.
- **Say which edition and which profile a behavior is for.** A change for 2018 servers
  must not change what a 2023 server sees, and a CSIP-AUS behavior must not leak into a
  client that is not using it. Test both sides.
- **Where the documents are silent or ambiguous, decide in the open.** Record the choice
  and what it costs where the next reader will find it (section 3), and prefer what
  interoperates with independent servers.
- **Working around a server is a named exception.** A server that departs from the
  standard is accommodated behind a setting or a clearly marked branch, with a comment
  that says what the standard requires and what the server does. The standard's behavior
  stays the default.
- **A failing test is a finding.** Do not weaken an assertion, widen a tolerance, or mark a
  test skipped to get a green run. Find out which side is wrong first.
- **Report what you did not check.** If a step was skipped or a test could not run on your
  machine, say so.

### Certification

py20305 is not SunSpec CSIP certified, and using it does not make a product certified.
Certification belongs to an assembled product tested as a whole. Do not write "certified",
"compliant" or "conformant" about the library in code, documentation, a changelog entry
or a pull request. Say what was tested and what the result was.

## 2. What must not be committed

This repository is public, and the client handles identities and credentials.

- **No real credentials or identities.** No certificate, private key, LFDI, SFDI, PIN or
  registration detail of a real device or a real utility account. The only certificates in
  the tree are test fixtures made for the purpose.
- **No real addresses.** No utility server URL, host name or IP address, and no address of
  a device on someone's network. Examples use `example.com` and documentation ranges.
- **No unscrubbed captures.** An exchange recorded against a utility's server or a real
  device is committed only when its owner agreed and it names no host, manufacturer,
  model, serial number or software version. `docs/conformance/index.md` describes what the
  recorded run there does and does not contain; hold new material to the same standard.
- **Do not reproduce the standards.** No sections, tables or figures of IEEE 2030.5, the
  CSIP guide, CSIP-AUS or the SunSpec test procedures. Refer to a requirement by its clause
  and describe it in your own words. A single sentence quoted to show what a clause
  requires, with its clause number, is the limit.
- **A schema file is added only after its license is checked.** The XSDs in
  `src/py20305/schemas/` are redistributed deliberately. A new one needs the same check,
  an entry in the package data, and the CI step that counts the schemas in the wheel
  updated.
- **No private names.** No names of private repositories, internal products, customers or
  people, and no local paths.
- **No code copied from another implementation.** The end-to-end server is run against,
  not copied from.
- **No check or script that lists private names so that they can be caught.** The list
  would publish them. Remove the reference itself.

Related rules:

- **Everything posted here is public and permanent**: issues, pull requests, comments,
  commit messages and branch names. Write each as a self-contained record a stranger can
  follow, and scrub any XML or log you paste.
- **A bug found in another project is reported there** as a neutral, self-contained
  report: the protocol, the version, a reproduction and a suggested fix.
- **A vulnerability is reported privately**, as [SECURITY.md](SECURITY.md) describes, not
  in a public issue or a pull request.
- **Logs and forwarded traffic must not carry a private key.** Do not log key material,
  and do not add a field to a forwarded record without checking what it exposes.

## 3. Design rules

- **The base install stays small.** It is the protocol client and what it needs. A web
  framework, an MQTT library, a Modbus library or a YAML parser belongs in an extra, so a
  consumer who embeds the client does not install them. A new dependency goes in the
  narrowest extra that needs it, with a comment in `pyproject.toml` that says why.
- **Every public module imports on its own.** `tests/test_import_isolation.py` imports
  each module in a fresh interpreter. An optional dependency is imported where it is used
  or behind a guard, never at the top of a module the base install loads.
- **The models are generated.** `py20305.models` is produced from the schemas with xsdata
  and is not edited by hand. A change to it comes from a change to a schema and a
  regeneration, and the pull request says how it was regenerated.
- **Both editions are supported.** Validation runs against both IEEE 2030.5 XSDs. Do not
  remove 2018 behavior because the bindings track 2023.
- **Security defaults stay secure.** IEEE 2030.5 is mutual TLS throughout. The server's
  certificate is always verified. Host name checking is on unless the operator turns it
  off for a server whose certificate does not name the address it is reached by. Do not
  add another way to weaken TLS, do not change a secure default, and do not use the
  existing switches to make a test pass against a real server.
- **One certificate is one end device.** The client registers and drives the device its
  certificate identifies. Do not fan a server-side EndDevice out across several local
  devices.
- **Everything from the network is untrusted**, including what an authenticated server
  sends. A response is parsed into the generated models and checked against the schemas
  where validation is configured. A malformed or unexpected response is handled and
  reported, never an unhandled exception in the run loop.
- **A control reaches a device only through the event engine.** The engine decides what
  is active, superseded, randomized and acknowledged. A connector applies what it is
  given and does not decide.
- **The configuration file is strict.** An unknown key is an error, so a misspelling is
  reported and not ignored. A new setting is a typed field with a default and a message
  that names where a mistake is.
- **Exit codes are an interface.** `0`, `2` and `3` mean what `docs/running.md` says they
  mean, because a supervisor acts on them. A bad configuration exits 2 so it is not
  restarted in a loop.
- **The container image carries the client and nothing else.** Configuration and
  certificates are mounted. It runs unprivileged. CI checks both.
- **It runs on Linux, Windows and macOS.** Use `pathlib`, name the encoding of every text
  file, and do not assume a path separator, a line ending or a signal.
- **Only the SunSpec Modbus connector ships in the tree.** A connector for one device
  belongs in the project that owns the device. See the connector guide.
- **Follow the pattern that is already there.** Read a neighboring module before adding a
  new shape.
- **Prefer one general mechanism to many similar edits.**
- **Do not expose what does not work.** A setting, a route or a command is added when its
  behavior works, not before. No placeholders and no "coming soon".
- **Use the standard's terms**: EndDevice, FunctionSetAssignments, DERProgram, DERControl,
  DefaultDERControl, MirrorUsagePoint, LFDI, SFDI, poll rate, subscription, response.

### Recording a decision

A choice a reader could reasonably argue with is written down where the next reader will
find it:

- In the docstring of the module or function it shapes, with the clause that bears on it,
  for a choice local to that code.
- In a page under `docs/planning/`, for a choice that spans modules or was weighed against
  alternatives.
- In the changelog fragment, for what changed for a user and why.

State the decision in one sentence, then the reason, then what it costs.

### Where things go

| What | Where |
|---|---|
| The protocol client: transport, discovery, polling, retry, time | `src/py20305/client/` |
| The event engine | `src/py20305/events/` |
| Telemetry posted to the server | `src/py20305/telemetry/` |
| Subscriptions and the notification listener | `src/py20305/subscription/` |
| Device connectors | `src/py20305/connectors/` |
| The management API | `src/py20305/api/` |
| Traffic forwarding | `src/py20305/forwarders/` |
| Certificates and identity | `src/py20305/security/` |
| Generated models and the schemas they come from | `src/py20305/models/`, `src/py20305/schemas/` |
| The runner and its configuration | `src/py20305/cli.py`, `src/py20305/config.py` |
| Unit tests, one file per subject | `tests/` |
| Recorded XML and other test data | `tests/fixtures/` |
| Tests against simulated devices and brokers | `tests/scenario/` |
| Tests against a live server | `tests/e2e/` |
| Example configuration and deployment files | `examples/` |
| Scripts a maintainer or CI runs | `scripts/` |
| Release tooling | `tools/` |
| Guides, plans and recorded test results | `docs/`, `docs/planning/`, `docs/conformance/` |
| Published reference pages | `docs/reference/` |
| Pending changelog entries | `changelog.d/` |

A new top-level directory or a new kind of file needs a reason in the pull request.

## 4. Tests

- **Every change in behavior has a test that fails without it.** Check this: commit, break
  the line the test is for, confirm the test fails, restore it. Say in the pull request
  that you did.
- **Expected values come from outside the code under test.** Compare against XML written
  out by hand or recorded from an independent server, a value worked out from the
  standard, or a schema. A test that serializes and parses with this package agrees with
  itself through any mistake both halves share.
- **Test behavior, not source text.** Do not assert on the wording of the implementation
  or search source files for a string.
- **Do not replace the thing under test.** A test that mocks the code path it claims to
  check proves nothing. Substitute what is outside the unit: the transport, the clock,
  the device, the broker.
- **Prove a check can fail.** A test, a guard or a validator is shown to catch the fault it
  is for, with a case that has the fault.
- **Know what each suite proves.** The unit suite shows the client does what this project
  believes the standard requires. Only the end-to-end suite, against a server written by
  someone else, can show that belief is shared. A change to what goes on the wire is
  expected to be exercised there.
- **Cover both editions and both profiles** where a behavior differs between them.
- **Time is injected.** Event timers take the clock they fire against. Do not sleep in
  real time where a clock can be passed in.
- **The unit suite is offline.** No network beyond the loopback interface, no Docker, and
  it passes on Linux, Windows and macOS.
- **A hang is a failure.** The suite has a global timeout. A test whose own handshake is
  the thing under test carries a tighter bound, so the failure names the defect.
- **Async tests need no marker.** `asyncio_mode` is `auto`.
- **Name a test for the behavior it checks**, so the name reads on its own in a failure
  report.
- **A skip needs a reason and must be visible.** The end-to-end tests skip when no server
  is configured and say so.
- **The examples are tested.** `tests/test_deployment_examples.py` loads the example
  configuration and checks the systemd unit and the image against the code. Change them
  together.
- **Run the full unit suite once before pushing**, on its own.

## 5. Documentation and generated files move with the code

A change is not complete until every surface that describes it is updated in the same
pull request.

| When you | Also update |
|---|---|
| Add or change a feature | The guide that covers it under `docs/` (`running.md`, `connectors.md`, `discovery.md`, `forwarding.md`, `api.md`, `docker.md`), the README where it describes what the client does, and the docstrings |
| Add a guide page | `nav` in `mkdocs.yml`, and links to it from the pages a reader would come from |
| Add a public module | `docs/reference/`, so its docstrings are published |
| Add or change a setting | The configuration model in `src/py20305/config.py`, `docs/running.md`, and `examples/client.example.yaml` |
| Add or change a management API route, a parameter or a response | The route's signature (typed request and response models, a summary and a description), `docs/api.md`, and the tests in `tests/test_client_routes.py` that read `/openapi.json` |
| Change an exit code, a log line an operator relies on, or the systemd unit | `docs/running.md` and `examples/` |
| Change the image | `docs/docker.md`, `examples/docker-compose.yml` and the `docker` job's checks |
| Change a schema | The package data, the wheel check in CI, and the models, regenerated |
| Change how something is tested | `docs/testing.md` |
| Finish or change a planned item | Its page in `docs/planning/` |
| Change anything a user can observe | A changelog fragment (section 7) |

Rules for these:

- **The API describes itself from the code.** The OpenAPI document at `/openapi.json` is
  built from the route signatures, so an untyped body or a missing response model leaves a
  hole in it. Give every route typed models and declare its status codes. A new route gets
  a test that finds it in `/openapi.json`.
- **A setting has three parts**: a typed, validated field in the configuration model, its
  description in `docs/running.md`, and its line in the example configuration.
- **The documentation build is strict.** `mkdocs build --strict` runs on every pull
  request, and a warning is an error.
- **Guides describe what the software does now.** Plans and what is left belong in
  `docs/planning/`.
- **The bar for a guide is that it is enough.** An operator who reads only the guide can
  use the feature: the setting, what the client then does, and what they will see.
- **A fact lives in many places.** When a name, a default, a key or a behavior changes,
  search for every place that states it: the README, the guides, the docstrings, the
  examples, the plans and the tests.
- **Describe a capability by what it does for the reader**, not by the name of the module
  that implements it.
- **Do not write counts that will drift** in comments, guides or the README. The badges
  carry the numbers, and CI generates them.
- **Bold marks a label** at the start of a paragraph or a list item. It is not used for
  emphasis in the middle of a sentence.

## 6. Comments, docstrings, names and messages

Write in plain, direct technical English. Say what the code does in the fewest words that
are still clear.

| Do not write | Write |
|---|---|
| `"""When the change this reading shows is said to have happened."""` | `"""Return the timestamp for an event raised by this reading."""` |
| `# Whoever asked has stopped waiting, so it is ended here and kept as a result.` | `# The caller cancelled while the request was in flight. End it as abandoned and record it.` |
| `def test_and_so_is_the_other_one(self):` | `def test_superseded_event_is_reported_once(self):` |

- **Lead with the action or the fact.** A function's docstring starts with a verb:
  "Return...", "Build...", "Wait until...".
- **Use the standard terms** of IEEE 2030.5 and of this codebase: request, response,
  pending, active, superseded, timeout, retry. Do not invent figurative substitutes.
- **Name the subject.** Avoid chains of "it", "one" and "that" the reader has to resolve.
- **Prefer active voice and short sentences.** No storytelling.
- **Keep the why** when it is not obvious, and cite the clause where the standard is the
  reason. Do not restate what the line below already says.
- **Do not refer to pull requests, issues or project phases in code comments.** They go
  stale. A clause stays true. The changelog is where a pull request is cited.
- **A test name makes sense alone.** No names that depend on the test before them.
- **An error message says what is wrong and where**, and how to fix it when that is known.
  An operator reads it in a log at a bad moment.
- **Text a user sees** (command help, log lines, API messages, guides) describes what the
  software does. It carries no internal names, no build status and no plans.
- **American English** in code, comments and documentation.
- **Line length 100**, enforced by `ruff`.
- **Type annotations on everything in `src/`**, apart from the generated models. `mypy`
  runs with `disallow_untyped_defs`.
- **A new module starts with a docstring** that says what the module is for and which
  part of the standard it implements.
- **Run `pylint` on the files you add or change** and clear its warnings and errors. It is
  not in CI.

## 7. Commits, pull requests and review

- **Every change reaches `main` through a pull request**, the maintainers' own included.
  Direct pushes, force pushes and branch deletion are blocked for everyone.
- **Look before you start.** Check the open issues and pull requests for work that
  overlaps yours. For a feature or a change in behavior, open an issue first: what the
  standard says is cheaper to settle before the code than after.
- **Name a branch for its author or its kind, then its topic**: `<user>/<topic>`,
  `fix/<topic>`, `feature/<topic>`.
- **One subject to a pull request.** Split unrelated work.
- **Do not push to a branch that is someone else's** without asking them.
- **A pull request merges when** the required checks are green (`lint`, `build`, `docker`
  and the Linux test job) and it has an approving review. The other test jobs, the
  end-to-end job and the documentation build run on every pull request as well: read them
  before merging.
- **Merge the head you checked.** Auto-merge is not a substitute for reading the checks.
- **Commit messages**: a short imperative subject, then prose that says why the change is
  right. No trailers.
- **No tool or AI attribution.** No `Co-Authored-By` line for a tool, and no "generated
  with" footer, in a commit, a pull request description or a comment.
- **The pull request description says** what changed, why, how it was tested, what was
  deliberately left out, and anything that was not verified.
- **A changelog entry is a file**, `changelog.d/<pull-request>.<category>.md`, not an edit
  to `CHANGELOG.md`. The body opens with a bold sentence that says what changed and cites
  its own number: `(#42)`. Use the real number of the pull request.
  `changelog.d/README.md` has the rest.
- **Answer every review comment.** Fix it with a test, or say why not. Reply on the thread
  with what was done. This applies to automated reviewers as well.
- **In a stack of pull requests**, say in each description where it sits in the stack,
  keep the stack short, and merge from the bottom. Point the child at `main` before the
  parent's branch is deleted.
- **Do not go around a check.** No skipped hooks, no disabled jobs, no merging on red.
- **Review effort goes where CI cannot**: whether the behavior is what the standard
  requires, whether the tests could fail, and whether the documentation is enough.
- **Releases** are cut by maintainers as CONTRIBUTING.md describes. A version on PyPI
  cannot be replaced, so nothing is tagged casually.

## 8. Before opening a pull request

Run the commands in [Quick reference](#commands).

CI runs them on every pull request. It runs the unit suite on three versions of Python on
Linux and on one each of Windows and macOS, builds the package and checks the schemas are
inside the wheel, builds the image and runs the client in it, runs the end-to-end suite
against an independent server, and builds the documentation. The workflow files in
`.github/workflows/` are the authority on what runs. Where this page and a workflow
disagree, the workflow is right and this page needs fixing.

Then confirm:

- [ ] Each new behavior has a test, and the test fails when the behavior is broken.
- [ ] Protocol behavior is tied to a clause, with its edition and profile.
- [ ] The guides, the README, the reference pages and the example configuration say what
      the code now does.
- [ ] A changelog fragment exists and carries the pull request's number.
- [ ] `git status` shows only files that belong to the change, and none of the material
      section 2 forbids.

## 9. For coding agents

Everything above applies. In addition:

- **Do not rely on recall for protocol facts.** If the standard, the profile or the test
  procedure is not in front of you, say so and ask the maintainer. Do not fill the gap
  with what seems likely.
- **Read before you write.** Match the module you are in: its comment density, its naming
  and the way its tests are laid out.
- **Stage files by name.** Do not `git add -A` without reading `git status` first. Test
  runs and the end-to-end server leave files behind.
- **Send traffic only to the loopback interface, the local end-to-end server and
  simulated devices.** Never connect to a utility's server, and never read from or write
  to a real inverter, meter or battery, unless the person directing the work names it and
  asks for it. A Modbus write moves real equipment.
- **Never create, request or handle a real credential.** Test certificates are generated
  for the test.
- **Do not publish without being asked.** Opening a pull request, commenting on an issue
  and pushing a branch are visible to everyone. Tags and releases are the maintainers'.
- **Report faithfully.** State failures with their output, name the steps you skipped, and
  do not describe work as verified when it was only written.
- **When two rules here conflict, or a rule does not fit the case, stop and ask.**
