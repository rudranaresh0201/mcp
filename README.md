# A trusted agent stack for MCP

**Because `isError: false` only means a tool didn't crash, not that it told the truth — and an agent operating across git, CI, and infrastructure needs more than hope that its tools are honest.**

This repo is two packages that only make sense together:

- **`devmcp`** — a broad, verifiable-by-design MCP server. Every domain it covers (git and CI today, expanding per [ADR 0002](docs/adr/0002-devmcp-broad-verifiable-server.md)) is deliberately implemented across the *full* MCP protocol surface (tools, resources with push subscriptions, prompts, roots negotiation, sampling), and every tool ships with a stated, independently-checkable postcondition rather than an implicit "trust me."
- **`verimcp`** — the trust engine. It sits between an MCP Host and a backend server, forwards everything transparently, and independently re-derives ground truth for the calls it has a verifier for before letting the response through. Verification is the first trust primitive; policy enforcement, audit logging, and observability (see [ROADMAP](docs/ROADMAP.md)) are the same idea applied to allow/deny, provenance, and visibility.

Neither half is the point on its own. A broad tool server without verification is just another dev-tool MCP server; a verification engine with nothing real to verify is a proof of concept. Together, the pitch is: **an agent operating through this stack cannot get away with a false claim, an unapproved action, or an unlogged side effect.**

```
Host  <--stdio-->  verimcp (proxy + verification engine)  <--stdio-->  devmcp (git/CI server)
```

Both packages are independently installable and have **no import dependency on each other** — `devmcp` is a standalone MCP server usable by any Host, and `verimcp` is a general-purpose proxy usable in front of any backend server, not just `devmcp`. They're proven together only via the end-to-end tests.

## Status

| Milestone | What it is | Status |
|---|---|---|
| M0 | devmcp scaffolding, CI, verify-vs-don't design ADR | ✅ done |
| M1 | devmcp core: transport, `initialize`, git tools (`write_file`, `git_commit`, `git_branch`, `run_ci_pipeline`) | ✅ done |
| M2 | devmcp resources (`repo://status`, `repo://log`, `repo://file/{path}`, `ci://last-run`), push subscriptions, prompts | ✅ done |
| M3 | devmcp roots negotiation + sampling (`summarize_diff`) — the two directions where devmcp asks the *Client* something | ✅ done |
| M4 | verimcp's generalized verifier engine: `GitCommitVerifier`, `GitBranchVerifier`, `CIRunVerifier`, `ResourceReadVerifier`, plus a `SamplingRateLimitGate` policy gate for `sampling/createMessage` | ✅ done |
| M5 | verimcp's own served capability — an audit log of every `tools/call`/`resources/read` it handles (`verimcp://audit`, `verimcp://audit/current`, live `resources/subscribe` updates), plus `verimcp replay` policy backtesting | ✅ done |
| M6 | OpenTelemetry spans + metrics (GenAI semantic conventions) for every `tools/call`/`resources/read`, exportable to console or any OTLP collector (Jaeger, Grafana) | ✅ done |
| M7 | Real-client verification against MCP Inspector (`scripts/inspector_smoke_test.py`) — caught and fixed a real `resources/list` id-collision bug our own tests never triggered | ✅ done |
| M8 | Packaging polish: license/authors/classifiers/urls metadata, `CHANGELOG.md`, `CONTRIBUTING.md`, publish-ready (not yet published — needs maintainer's own PyPI credentials) | ✅ done |
| M9 | Adversarial test corpus — a real, deliberately-lying MCP server fixture (`tests/fixtures/lying_server.py`), driven through the real proxy pipe to prove every verifier actually catches the lie it exists for | ✅ done |
| M10 | Verifier SDK — `docs/writing-a-verifier.md` plus a real, independently pip-installable example plugin package (`examples/third_party_verifier/`) proving third-party discovery works | ✅ done |

`devmcp` is fully built and independently working today: real `initialize` handshake, five tools, resources with live subscriptions, two prompts, roots negotiation, and sampling — all proven end-to-end over real stdio, not mocked. `verimcp` now has a verifier for every tool call and resource read with an independently-checkable postcondition — `write_file` (filesystem hash-compare), `git_commit` (commit-object existence), `git_branch` (ref existence + target), `run_ci_pipeline` (per-step self-consistency plus re-execution of steps marked safe to re-run), and `repo://status`/`repo://log`/`repo://file/{path}` (re-derive the same git/filesystem state and compare) — plus a `RequestGate` for the one primitive with no ground truth to check: `sampling/createMessage` is now rate-limited (`--sampling-limit`/`--sampling-window`) before it ever reaches the Host, closing out M4.

**Proven, not just claimed:** `tests/test_adversarial_corpus.py` spins up a real, deliberately-lying MCP server and drives 6 distinct fabrications through the real `verimcp` proxy — a fake commit hash, a branch that was never created, a CI step falsely claimed to pass, a fabricated resource read, a wrong-shape (third-party-style) fake commit, and a file write that never touched disk. **verimcp catches 6/6**, over a real stdio pipe, not a hand-built response dict.

## Why this split

Every MCP primitive gets one of three treatments, decided in [`docs/adr/0001-verify-vs-dont-framework.md`](docs/adr/0001-verify-vs-dont-framework.md):

- **Correctness verifiers** (tools, resources) — re-derive ground truth independently and compare it to the claim.
- **Policy gates** (sampling rate-limiting; tools/call allow/deny/require_approval, see [ROADMAP Phase 2](docs/ROADMAP.md)) — no objective truth to check, but a real cost/abuse/authorization surface worth constraining before the backend ever runs.
- **No verifier, on principle** (prompts, roots) — nothing independent to check them against; treating them as "unverified" is a decision, not an oversight.

## What devmcp actually does

Run `devmcp --repo-path ./some-repo` and it:
- answers `tools/call` for `write_file`, `git_commit`, `git_branch`, `run_ci_pipeline` (each producing a real, independently-checkable side effect on disk / in git)
- serves `resources/read` for live repo state, and pushes `notifications/resources/updated` unprompted when a subscribed resource changes
- serves `prompts/get` for `commit-message` and `pr-description` templates
- on startup, asks the *Client* `roots/list` and relocates its own working directory to whatever the Client declares, rather than trusting its own CLI flag blindly
- for `summarize_diff`, asks the *Client's own model* to do the summarization via `sampling/createMessage`, since devmcp has no model of its own

## Getting started

```bash
pip install -e ".[dev]"
pip install -e "./devmcp[dev]"

pytest tests devmcp/tests   # 131 tests, real subprocess + real git repo, nothing mocked
ruff check src tests devmcp/src devmcp/tests

python scripts/inspector_smoke_test.py   # verify against the real MCP Inspector client (needs node/npx)
```

## Writing a verifier

Verifiers are a plugin system (`importlib.metadata.entry_points`, the same
mechanism pytest/Black use) — a new one can be added without touching
verimcp's own source. See [`docs/writing-a-verifier.md`](docs/writing-a-verifier.md)
for the full guide, and [`examples/third_party_verifier/`](examples/third_party_verifier)
for a real, independently pip-installable package proving it — install it
and `tests/test_third_party_verifier_plugin.py` confirms `load_verifiers()`
picks it up automatically.

## Observability

Every `tools/call`/`resources/read` verimcp handles gets an OpenTelemetry
span (named after the [GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) --
`gen_ai.operation.name`, `gen_ai.tool.name` -- plus verimcp-specific
`verimcp.outcome`/`verimcp.gate.action`/`verimcp.verification.passed`/
`verimcp.approval.status` attributes for governance data the standard
doesn't cover) and contributes to two metrics: `verimcp.tool_calls.total`
and `verimcp.policy_violations.total`, plus a `gen_ai.client.operation.duration`
histogram. See [ADR 0005](docs/adr/0005-metrics-via-otel-genai-conventions.md).

Omit `--otel-exporter` entirely for zero telemetry overhead (the default --
no behavior change, same as every other opt-in flag here). To see it:

```bash
# local debugging -- spans/metrics print to stderr (never stdout, which is
# the live MCP protocol channel)
verimcp --otel-exporter console -- devmcp --repo-path ./some-repo

# point a real collector, Jaeger, or Grafana Agent at it
pip install "verimcp[otel]"
verimcp --otel-exporter otlp --otel-endpoint localhost:4317 -- devmcp --repo-path ./some-repo
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, how to add a verifier,
this project's ADR discipline, and commit/PR conventions. See
[CHANGELOG.md](CHANGELOG.md) for release history.

## License

MIT — see [LICENSE](LICENSE).
