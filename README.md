# A trusted agent stack for MCP

[![PyPI - verimcp](https://img.shields.io/pypi/v/verimcp?label=verimcp)](https://pypi.org/project/verimcp/)
[![PyPI - devmcp-server](https://img.shields.io/pypi/v/devmcp-server?label=devmcp-server)](https://pypi.org/project/devmcp-server/)
[![CI](https://github.com/rudranaresh0201/mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/rudranaresh0201/mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Problem

MCP tool calls are trusted by default. When a server says a commit succeeded, a file was written, or a CI check passed, the Host has no way to know if that's true — `isError: false` only means the tool didn't crash, not that it did what it claimed. An agent wired up to git, CI, or infrastructure through MCP is one confidently-wrong tool response away from acting on a lie.

## Solution

**`verimcp`** is a transparent proxy that sits between an MCP Host and a backend server. It forwards every message unchanged, except: for calls it knows how to check, it independently re-derives the real outcome (re-reads the file, re-checks the git log, re-runs the safe parts of a CI step) and compares that against what the backend claimed. A lie gets rewritten into a real error before the Host ever sees it as a success.

**`devmcp`** is the backend it's proven against — a real git/CI MCP server, deliberately built with the full protocol surface (tools, resources, prompts, roots, sampling), because a proxy is only as convincing as what it's shown catching.

```
Host  <--stdio-->  verimcp (proxy + verification)  <--stdio-->  devmcp (git/CI server)
```

Both are independently installable and have no import dependency on each other — `verimcp` works in front of *any* MCP backend (proven against the official `mcp-server-git` too, not just devmcp), and `devmcp` works with any Host directly, unproxied.

## See it work

```bash
pip install verimcp devmcp-server
python scripts/demo.py
```

That spins up two real subprocess pipes and prints the raw JSON-RPC exchange: one where a backend fabricates a commit hash and verimcp catches it (`isError: true`, with the real reason), one where a backend writes a real file and verimcp's independent check confirms it and lets the response through unchanged. No mocks — real git repos, real subprocesses, real proxy.

The same claim, proven at larger scale: `tests/test_adversarial_corpus.py` runs 6 different fabrications (fake commit, fake branch, false CI pass, fake resource read, wrong-shape fake commit, a write that never touched disk) through the real proxy pipe. **verimcp catches 6/6.**

## Technical summary

- **Correctness verifiers** — one per tool/resource with an independently-checkable postcondition: `write_file` (disk hash-compare), `git_commit`/`git_branch` (re-derived from real git state), `run_ci_pipeline` (self-consistency + re-execution of steps marked safe to re-run), plus resource reads (`repo://status`, `repo://log`, `repo://file/{path}`). New verifiers are a plugin system (`importlib.metadata.entry_points`, the same mechanism pytest/Black use) — see [`docs/writing-a-verifier.md`](docs/writing-a-verifier.md).
- **Policy gates** — allow/deny/require-approval rules evaluated *before* a call reaches the backend, for the calls that have no objective truth to check (sampling rate limits, arbitrary tool-name/argument policy via YAML, human-in-the-loop approval over real MCP `elicitation/create`).
- **Audit + replay** — every call verimcp handles is logged and served back as a real `verimcp://audit` MCP resource; `verimcp replay` re-runs recorded traffic against a new policy to backtest "would this have changed anything."
- **Observability** — every call gets an OpenTelemetry span/metric using the GenAI semantic conventions, exportable to any OTLP collector.
- **Proven against real clients, not just our own tests** — the official MCP Inspector and VS Code's native MCP support (Copilot Chat) each caught a real bug our own test suite never triggered, now fixed with regression tests.

Deeper design reasoning (why prompts/roots get no verifier on principle, why policy is a separate concept from verification, etc.) lives in [`docs/adr/`](docs/adr/) for anyone who wants to go that deep — the summary above is everything needed to use or evaluate the project.

## Getting started

**As a user** — install straight from PyPI:

```bash
pip install verimcp devmcp-server
verimcp -- devmcp --repo-path ./some-repo
```

> Note on names: the PyPI distribution is `devmcp-server` (`devmcp` was blocked by
> PyPI's typosquat-similarity check against an unrelated existing package), but the
> Python import and CLI command are both still plain `devmcp` — nothing above changes
> if you're reading devmcp's own source.

**As a contributor** — editable installs from this repo:

```bash
pip install -e ".[dev]"
pip install -e "./devmcp[dev]"

pytest tests devmcp/tests   # 136 tests, real subprocess + real git repo, nothing mocked
ruff check src tests devmcp/src devmcp/tests

python scripts/inspector_smoke_test.py   # verify against the real MCP Inspector client (needs node/npx)
```

## Try the observability yourself

```bash
# spans/metrics print to stderr (verimcp's stdout is the live MCP protocol channel)
verimcp --otel-exporter console -- devmcp --repo-path ./some-repo

# or point a real collector, Jaeger, or Grafana Agent at it
pip install "verimcp[otel]"
verimcp --otel-exporter otlp --otel-endpoint localhost:4317 -- devmcp --repo-path ./some-repo
```

Omitting `--otel-exporter` entirely means zero telemetry overhead — the default, same as every other opt-in flag here.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, how to add a verifier,
this project's ADR discipline, and commit/PR conventions. See
[CHANGELOG.md](CHANGELOG.md) for release history.

## License

MIT — see [LICENSE](LICENSE).
