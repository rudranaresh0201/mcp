# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions apply to both `verimcp` and `devmcp`, which are versioned in
lockstep since they've evolved together, despite having no import
dependency on each other -- except when a release only changes one of the
two (like 0.2.2 below), in which case only that package's version moves.

## [0.2.2] — verimcp only, `devmcp` unchanged at 0.2.1

### Added

- **Verify-before-retry** (`--idempotent-replay`), adapted from arXiv
  2608.02645 ("Verified Tool Calls Improve LLM Agent Reliability Under
  Non-Atomic Failures"). If a `tools/call` is a byte-identical retry of a
  call already independently verified true earlier in the session,
  verimcp answers from that confirmed result instead of forwarding to the
  backend again -- stops a client that retries after an ambiguous
  response (timeout, dropped connection) from duplicating a side effect
  (a second commit, a second charge). Only applies to tools with a
  verifier configured; the key is derived from `(tool name, arguments)`,
  not a caller-supplied idempotency key, and safety comes from the prior
  call's postcondition having been independently verified, not from key
  matching alone (`src/verimcp/idempotency.py`).
- `scripts/benchmark_claim_acceptance.py` and
  `scripts/benchmark_retry_duplication.py` -- real, runnable benchmarks
  (subprocess pipes, no mocks) proving both of the README's core claims
  with numbers: a raw Host accepts 100% of fabricated results (12/12);
  verimcp catches 75% (9/12), with the 3 principled misses named in the
  script's own output. A raw Host duplicates 5/5 non-idempotent side
  effects on retry; `--idempotent-replay` duplicates 0/5.
- `Dockerfile` for containerized use.
- `glama.json` for Glama server ownership/metadata verification.

## [0.2.1]

### Added

- Both packages published on PyPI: `verimcp` and `devmcp-server` (`devmcp`'s
  PyPI distribution name — blocked by a typosquat-similarity check against
  an unrelated existing package; the Python import and CLI command are
  still plain `devmcp`).
- `scripts/demo.py` — a real, runnable end-to-end demo: verimcp fronting a
  deliberately-lying backend (caught) and the real devmcp (verified and
  passed through), printing the raw JSON-RPC exchange.
- `mcp-name` ownership markers in both READMEs for the official MCP
  Registry (`registry.modelcontextprotocol.io`).

### Fixed

- `write_file`'s path-escape check (added in 0.2.0's VS Code testing) only
  actually worked on Windows — `Path("C:/foo").is_absolute()` is `False`
  on POSIX, so a Linux-hosted devmcp still accepted Windows-style absolute
  paths. Now detected by string shape, independent of host OS.

## [0.2.0]

### Added — verimcp

- **Phase 2 — Policy engine**: `ToolCallPolicyGate` (`allow`/`deny`/
  `require_approval`, tool name + glob argument matching, `--policy-config`).
  `require_approval` implemented via the real MCP `elicitation/create`
  primitive, not an invented mechanism — see
  [ADR 0003](docs/adr/0003-require-approval-via-elicitation.md).
- **Phase 3 — Audit logging + session replay**: every `tools/call`/
  `resources/read` recorded as JSONL (`--audit-log`), served back to the
  Host as a real `verimcp://audit` MCP resource (capability-patched into
  the backend's `initialize`/`resources/list` responses), plus
  `verimcp replay` for offline policy backtesting — see
  [ADR 0004](docs/adr/0004-audit-log-as-mcp-resource-and-policy-replay.md).
- **Phase 4 — Metrics & observability**: OpenTelemetry spans + metrics for
  every `tools/call`/`resources/read`, using GenAI semantic conventions
  where they fit and a `verimcp.*` namespace for governance-specific data
  (`--otel-exporter console|otlp`) — see
  [ADR 0005](docs/adr/0005-metrics-via-otel-genai-conventions.md).
- **Phase 6 — Adversarial test corpus**: a real, deliberately-lying MCP
  server fixture (`tests/fixtures/lying_server.py`) proving every verifier
  catches its corresponding lie over a real stdio pipe (`tests/
  test_adversarial_corpus.py`, 6/6).
- **Phase 7 — Verifier SDK**: `docs/writing-a-verifier.md` plus a real,
  independently pip-installable example plugin package
  (`examples/third_party_verifier/`) proving third-party verifiers are
  discoverable with zero verimcp source changes.
- `--root` CLI override for backends that never negotiate `roots/list`
  (e.g. the reference `mcp-server-git`).
- `GitServerCommitVerifier`, proving the verifier plugin system works
  against a real third-party backend, not just `devmcp`.

### Fixed — verimcp

- A `resources/list`/`initialize` response-tracking id collision with a
  backend's own self-originated request ids (e.g. `roots/list`), found via
  a real run against MCP Inspector — see
  [ADR 0006](docs/adr/0006-mcp-inspector-compatibility.md).
- Two silent-no-op bugs where telemetry never fired unless `--audit-log`
  was also passed, despite being meant as independent opt-ins.

### Verified

- **Phase 1 — Backend independence**: proven against the official
  third-party `mcp-server-git` reference server, not just `devmcp`.
- **Phase 5 — Real client compatibility**: proven against the real MCP
  Inspector CLI (`scripts/inspector_smoke_test.py`).

## [0.1.0]

Initial release.

- `devmcp`: full-protocol MCP server (tools, resources with subscriptions,
  prompts, roots negotiation, sampling) over git/CI tooling.
- `verimcp`: verification proxy with a plugin-based `Verifier` registry
  (`importlib.metadata.entry_points`), covering `write_file`, `git_commit`,
  `git_branch`, `run_ci_pipeline`, and `repo://status`/`repo://log`/
  `repo://file/{path}`.
- `SamplingRateLimitGate`: the first `RequestGate`, rate-limiting backend-
  originated `sampling/createMessage` requests before the Host sees them.
