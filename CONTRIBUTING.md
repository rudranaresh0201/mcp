# Contributing

## Dev setup

```bash
pip install -e ".[dev]"
pip install -e "./devmcp[dev]"
pip install -e examples/third_party_verifier   # optional -- exercises the plugin-discovery test

pytest tests devmcp/tests
ruff check src tests devmcp/src devmcp/tests
```

`devmcp` and `verimcp` are independently installable and have no import
dependency on each other — you can work on either alone.

Some tests need extras not covered by the base `dev` install:

- `tests/test_git_server_commit_integration.py` needs the real third-party
  `mcp-server-git` package (already in `dev` extras) and a `git` binary on
  `PATH`.
- `scripts/inspector_smoke_test.py` needs `node`/`npx` on `PATH` (fetches
  MCP Inspector on first run).

## Adding a verifier

See [`docs/writing-a-verifier.md`](docs/writing-a-verifier.md) for the full
guide. Short version: implement `Verifier` (`applies_to` + `verify`),
register it via `[project.entry-points."verimcp.verifiers"]` — no verimcp
source changes needed, whether you're adding it to this repo or shipping it
as your own package (`examples/third_party_verifier/` is a real, working
example of the latter).

## Architectural decisions get an ADR first

This project's discipline (see `docs/adr/*.md`): a change that introduces a
new protocol-level capability, a new failure mode, or a non-obvious tradeoff
gets a short ADR — context, decision, consequences — written *before* the
code, not after. A bug fix or a new verifier following an existing pattern
doesn't need one. If you're unsure which your change is, look at the
existing ADRs for the bar: each one exists because a real design question
had more than one reasonable answer.

## Testing philosophy

Prefer real subprocess/stdio integration tests over mocks wherever the
thing under test is protocol behavior — every "catches a lie" claim in this
repo is backed by either a real spawned process (`tests/
test_adversarial_corpus.py`, `tests/test_proxy_integration.py`) or, where a
live-pipe test genuinely can't reach the scenario (e.g. an outcome only a
lying backend that doesn't exist yet could produce), a fast unit test
against the exact function responsible, with a comment explaining why it
isn't a full-pipe test. Don't add mocks where a real fixture is feasible —
several real bugs in this project (a Windows stdin deadlock, a `resources/list`
id collision) were only ever caught by real-pipe tests, never by a unit test
calling the function directly.

## Commit / PR conventions

- Keep commits scoped to one logical change; the git log here reads as a
  sequence of phases for a reason.
- Run the full test suite and `ruff check` before opening a PR — CI runs
  both packages' tests + lint on Python 3.11 and 3.12.
- Update `docs/ROADMAP.md`'s status for the phase you're touching, and
  `CHANGELOG.md`'s `[Unreleased]` section (add one if it doesn't exist) if
  the change is user-visible.

## License

MIT — see [LICENSE](LICENSE). By contributing, you agree your contribution
is licensed under the same terms.
