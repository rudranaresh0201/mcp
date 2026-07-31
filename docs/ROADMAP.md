# Roadmap: verimcp as a trust/governance layer for AI agents

## Status

Draft — long-term vision, phased. Nothing here is cut from scope; phases
exist to sequence dependencies, not to decide what does or doesn't get
built. Everything in the original 10-item vision appears in some phase
below.

## Where we are today

- `devmcp`: fully working MCP server (tools, resources+subscriptions,
  prompts, roots, sampling) — proven over real stdio, not mocked.
- `verimcp`: Phase 0 and Phase 1 both done. Six verifiers registered via
  `importlib.metadata.entry_points` plugin discovery (`verifiers/registry.py`),
  selectable per-backend with `--verifiers`, plus a `--root` override for
  backends that never negotiate `roots/list` with the Host at all. Proven
  genericity claim: `GitServerCommitVerifier` runs against the official
  third-party **`mcp-server-git`** reference server (a backend we did not
  write), independently confirming its claimed commit hash and catching a
  fabricated one — see `tests/test_git_server_commit_integration.py` and
  the unit test alongside it. 55 tests passing across both packages.

Everything below builds on top of that, starting from Phase 2.

## Phase 0 — Foundation (make what already exists actually run)

Nothing new conceptually — just finishing what's half-built so every later
phase has solid ground under it.

- Fix `FilesystemVerifier.verify()`'s missing `root` param (currently
  crashes on the first real tool call).
- Write `verimcp/cli.py` — currently `verimcp` isn't a runnable command.
- Add real tests for `proxy.py` (currently only `jsonrpc.py` is tested).

**Free/leverage:** nothing new needed — this is just closing gaps in code
that already exists.

## Phase 1 — Backend independence + plugin verifier system ✅ done

*(covers vision items #7 Backend independence, #1 Plugin verifier system)*

Goal: verimcp works in front of **any** MCP backend, and a verifier for a
new tool can be added without forking verimcp's source.

- ✅ Replaced the hardcoded `ALL_VERIFIERS` list in `verifiers/registry.py`
  with `importlib.metadata.entry_points` plugin discovery
  (group `verimcp.verifiers`, declared in root `pyproject.toml`).
- ✅ `proxy.py`/`cli.py` turned out to already have no devmcp-specific
  logic — the real backend-specific gap found by testing against a real
  third-party server wasn't in the proxy loop, it was an unstated
  assumption about *how a backend's root is learned*: devmcp asks the Host
  via `roots/list`, but plenty of real backends (e.g. `mcp-server-git`)
  take their working directory from their own CLI flag and never negotiate
  roots at all. Fixed with a `--root` override on `verimcp`'s CLI, threaded
  into `Proxy` as `root_override`, rather than inventing a devmcp-specific
  config format.
- ✅ Proved genericity against a backend we didn't write: the official
  reference **`mcp-server-git`** (PyPI, pinned to `mcp<2.0.0` — its
  2026.7.10 release isn't yet updated for the `mcp` 2.0.0 SDK rewrite that
  shipped alongside the 2026-07-28 stateless spec). Its `git_commit` tool
  happens to share a name with devmcp's own, but returns a different shape
  (plain-text content, no `structuredContent`) — exactly the case
  `--verifiers` exists for, since loading every installed verifier
  unconditionally would be wrong here. `GitServerCommitVerifier` re-derives
  the same ground truth (`git cat-file -e`) against this backend's real
  response shape, proven end-to-end: a true-positive integration test
  against the real server, plus a hand-verified run against a fake lying
  backend that fabricates a commit hash, which verimcp correctly rejects
  with `isError: true`.

**Free/leverage:** Python's `importlib.metadata.entry_points` — the exact
mechanism pytest and Black use for third-party plugins — gave us plugin
discovery without writing a plugin loader ourselves.

**Superseded by [ADR 0002](adr/0002-devmcp-broad-verifiable-server.md):**
devmcp is no longer staying small — it grows into further real domains,
gated by the three-part inclusion boundary (free+local to test,
independently checkable ground truth, part of the dev inner loop). Docker,
local-container Postgres, and local (`kind`/`minikube`) Kubernetes are
**in scope and get built**, sequenced by setup cost: Docker first (next
domain after this phase's git/CI verifier set), Postgres next, Kubernetes
after that. Slack and AWS/cloud-provider domains fail the boundary
permanently (real account/billing required, ground truth lives in a third
party's API) — they stay as documented "here's how you'd add one" examples
under Phase 7, not something devmcp itself builds or maintains.

## Phase 2 — Policy engine

*(covers vision item #2)*

Goal: allow/deny/require-approval rules evaluated **before** a tool call
reaches the backend at all — not just verified after the fact.

- A YAML rule matcher: tool name (+ arguments) → `allow` / `deny` /
  `require_approval`.
- Open design question worth its own ADR: how does "require approval"
  actually work mid-flight, since MCP's message flow is fire-and-respond,
  not built-in human-in-the-loop? (Options: block locally on stdin for CLI
  use; investigate whether the MCP spec version in use has any
  elicitation/user-input primitive to lean on instead of inventing one.)

**Free/leverage:** a plain Python rule matcher is enough at this scale —
no need to pull in a full policy engine like Open Policy Agent/Rego, which
would add a whole separate runtime dependency for what a YAML list can do
here. Worth citing OPA in docs as the "real" industry reference point
we're deliberately building a lighter version of, not as something to
depend on.

## Phase 3 — Audit logging + session replay

*(covers vision items #3 Audit logging, #4 Session replay)*

Goal: every tool call recorded (arguments, timestamp, verification result,
approval status), served as a `verimcp://audit` resource; sessions can be
replayed.

- JSONL audit store behind an `AuditStore` protocol (already the resolved
  design decision from the original milestone plan).
- Session replay is **not a separate system** — once every request/response
  is recorded in order, "replay a session" is just feeding that JSONL back
  through the same proxy message flow. This is a thin feature on top of
  the audit log, not a new milestone's worth of work.

**Free/leverage:** stdlib `json` + flat files. No new dependency needed
for v1; SQLite is a natural free upgrade later if querying JSONL gets
painful.

## Phase 4 — Metrics & observability

*(covers vision item #5)*

Goal: total tool calls, verified vs failed, policy violations, latency,
most-used tools — visible, not just logged.

- Emit spans using the **OpenTelemetry GenAI semantic conventions** (an
  actual emerging industry standard for LLM/agent telemetry) instead of
  building a custom dashboard.
- Point a free/self-hostable viewer (Grafana, Jaeger) at that data.

**Free/leverage:** this turns "build a dashboard" (a genuinely large
undertaking — storage, API, frontend) into "emit standard spans + use an
existing free viewer." The OTel Python SDK is a new dependency, but it's
free, open-source, and the credibility win ("uses real OTel GenAI
conventions" vs "built a custom dashboard") is worth more than a bespoke
UI would be.

## Phase 5 — Real client compatibility

*(covers vision item #6)*

Goal: prove correctness against real MCP Hosts, not just our own test
suite.

- Run devmcp, then verimcp-fronting-devmcp, through **MCP Inspector**
  (official, free, open-source).
- Test against at least one more real client if accessible (e.g. an
  editor's MCP integration) to catch spec-compliance edge cases our own
  tests would never generate.

**Free/leverage:** MCP Inspector is free. This phase costs time, not
money or new infrastructure.

## Phase 6 — Adversarial test corpus

*(covers vision item #8)*

Goal: a suite of deliberately-lying MCP servers as fixtures, proving
verifiers actually catch bad behavior — this becomes both the regression
suite and the evidence behind any "verimcp catches N/N adversarial
backends" claim in a README or blog post.

**Free/leverage:** pure test code, no new infrastructure.

## Phase 7 — SDK for verifier authors

*(covers vision item #9)*

Goal: someone who isn't us can write a new verifier without reading all of
verimcp's internals first.

- A documented `Verifier` base class + one clean worked example (already
  exists in spirit as `filesystem.py` — this phase is about writing it up,
  not re-architecting it).
- A short "how to add a verifier" doc.

**Free/leverage:** the plugin interface already exists by this point
(Phase 1) — this phase is mostly documentation and one polished example,
not new code.

## Phase 8 — Packaging & OSS polish

*(covers vision item #10)*

- Finalize YAML config, CLI polish, versioning/CHANGELOG, CONTRIBUTING.md.
- Publish to PyPI.

**Free/leverage:** PyPI hosting is free, GitHub Pages/GitHub-native docs
are free, `setuptools` build backend is already in place.

## Sequencing note

Phases are ordered by dependency, not by importance — Phase 1 (plugin
system + backend independence) comes early because Phases 2–7 are all
easier to build against a generic verifier interface than against the
current devmcp-specific one. No phase is being deferred because it's "too
ambitious" — only because something earlier needs to exist first.
