# Roadmap: verimcp as a trust/governance layer for AI agents

## Status

Draft — long-term vision, phased. Nothing here is cut from scope; phases
exist to sequence dependencies, not to decide what does or doesn't get
built. Everything in the original 10-item vision appears in some phase
below.

## Where we are today

- `devmcp`: fully working MCP server (tools, resources+subscriptions,
  prompts, roots, sampling) — proven over real stdio, not mocked.
- `verimcp`: Phase 0 and Phase 1 both done, plus M4's `SamplingRateLimitGate`
  (see README) -- the first `RequestGate`, checking a backend-*originated*
  request (`sampling/createMessage`) before the Host ever sees it, rather
  than a response to one the Host made. Six verifiers registered via
  `importlib.metadata.entry_points` plugin discovery (`verifiers/registry.py`),
  selectable per-backend with `--verifiers`, plus a `--root` override for
  backends that never negotiate `roots/list` with the Host at all. Proven
  genericity claim: `GitServerCommitVerifier` runs against the official
  third-party **`mcp-server-git`** reference server (a backend we did not
  write), independently confirming its claimed commit hash and catching a
  fabricated one — see `tests/test_git_server_commit_integration.py` and
  the unit test alongside it. Phase 2 (the full policy engine -- allow,
  deny, and require_approval via real MCP elicitation), Phase 3 (audit
  logging as a real `verimcp://audit` MCP resource, plus policy-replay
  backtesting), and Phase 4 (OpenTelemetry spans/metrics using GenAI
  semantic conventions) are all done (see below) -- 125 tests passing
  across both packages. Phase 5 (real client compatibility) is partially
  done: a live run against the real MCP Inspector caught a genuine protocol
  bug our own tests never triggered (see below), now fixed with a
  regression test.

Everything below Phase 5's remaining sub-item is still ahead, starting from
Phase 6.

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

## Phase 2 — Policy engine ✅ done

*(covers vision item #2)*

Goal: allow/deny/require-approval rules evaluated **before** a tool call
reaches the backend at all — not just verified after the fact.

- ✅ `allow`/`deny` on tool name (+ arguments), via `ToolCallPolicyGate`
  (`src/verimcp/gates/tool_call_policy.py`) — a `RequestGate` on the
  Host→backend direction, the mirror case of M4's `SamplingRateLimitGate`
  (which gates backend→Host requests). A YAML file
  (`rules: [{tool: <name>, arguments?: {key: glob}, action: allow|deny|require_approval}]`,
  loaded via `--policy-config`) is matched first-rule-wins, default allow
  if nothing matches — an opt-in overlay, not a default-deny gate.
  Argument matching (`fnmatch`-based glob per key, AND across keys, a
  missing key just doesn't match) followed as a clean second pass on the
  same `action_for()` lookup, not bundled into the first. Proven the
  strong way throughout: `tests/test_proxy_integration.py`'s policy tests
  assert the denied tool's real side effect (a file write) never
  happened, not just that the response looked like a denial.
- ✅ `require_approval`, via the real MCP `elicitation/create` primitive —
  confirmed present in the exact `protocolVersion` (`2025-06-18`) devmcp
  already declares, not invented. Full design in
  [ADR 0003](adr/0003-require-approval-via-elicitation.md): id-namespacing
  so verimcp's self-originated requests can't collide with a backend's own
  (verimcp had never originated a request before this), async dispatch via
  `asyncio.create_task` to avoid the same read-loop deadlock class fixed in
  `devmcp/server.py` earlier, and fail-closed on every non-approval outcome
  (missing Host capability, decline, cancel, timeout) — no path silently
  degrades to allow.

**Free/leverage:** a plain Python rule matcher (`fnmatch` + a first-match
loop) was enough for allow/deny/require_approval routing — no policy
runtime dependency needed, as originally planned. `elicitation/create`
being a real, already-adopted spec primitive meant `require_approval`
needed zero new transport/UX invention, just correct use of what MCP
already defines.

**Free/leverage:** a plain Python rule matcher is enough at this scale —
no need to pull in a full policy engine like Open Policy Agent/Rego, which
would add a whole separate runtime dependency for what a YAML list can do
here. Worth citing OPA in docs as the "real" industry reference point
we're deliberately building a lighter version of, not as something to
depend on.

## Phase 3 — Audit logging + session replay ✅ done

*(covers vision items #3 Audit logging, #4 Session replay)*

Goal: every tool call recorded (arguments, timestamp, verification result,
approval status), served as a `verimcp://audit` resource; sessions can be
replayed.

- ✅ `AuditStore` (`src/verimcp/audit.py`) — stdlib JSONL, one entry per
  completed `tools/call`/`resources/read`, opt-in via `--audit-log` (no flag,
  no behavior change at all). Session id per `Proxy` run, per-session `seq`
  counter so ordering never depends on filesystem mtime.
- ✅ Actually reachable by the Host, not just written to a file: verimcp
  patches the `initialize` response in transit to guarantee
  `capabilities.resources` is declared (merged with, never overriding,
  whatever the backend itself already declares — not every backend does, and
  Phase 1's whole premise is backend independence), injects its own entries
  into `resources/list`, and answers `resources/read` /
  `resources/subscribe` / `resources/unsubscribe` for `verimcp://audit`,
  `verimcp://audit/current`, and `verimcp://audit/<session_id>` locally —
  including firing a real `notifications/resources/updated` after each
  recorded call if the Host subscribed. Built against the live MCP spec
  (`modelcontextprotocol.io/specification/2025-06-18/server/resources`), not
  guessed. Full design in
  [ADR 0004](adr/0004-audit-log-as-mcp-resource-and-policy-replay.md).
- ✅ Session replay, scoped deliberately: `verimcp replay <audit-log>
  --policy-config <file>` (`src/verimcp/replay.py`) re-runs
  `ToolCallPolicyGate` decisions against recorded traffic and reports verdict
  changes — sound to do offline because the gate is a pure function of
  (tool name, arguments). Full verifier re-runs are **not** included: verifiers
  like `FilesystemVerifier`/`GitServerCommitVerifier` check live disk/repo
  state, so replaying them against today's filesystem for an old response
  would silently check the wrong thing once that state has moved on — see
  ADR 0004 for why this is a stated boundary, not a gap.

**Free/leverage:** stdlib `json` + flat files, exactly as planned — no new
dependency. SQLite remains a natural free upgrade later if querying JSONL
gets painful at scale.

## Phase 4 — Metrics & observability ✅ done

*(covers vision item #5)*

Goal: total tool calls, verified vs failed, policy violations, latency,
most-used tools — visible, not just logged.

- ✅ Every `tools/call`/`resources/read` gets a real OpenTelemetry span,
  named and attributed using the **OpenTelemetry GenAI semantic
  conventions** (`gen_ai.operation.name`, `gen_ai.tool.name`) where they
  fit, plus a `verimcp.*`-namespaced set of attributes
  (`verimcp.outcome`, `verimcp.gate.action`, `verimcp.verification.passed`,
  `verimcp.approval.status`) for the governance data the standard doesn't
  cover — cited as *Experimental* upstream, not presented as frozen.
  `verimcp.tool_calls.total` and `verimcp.policy_violations.total` counters,
  plus a `gen_ai.client.operation.duration` histogram
  (`src/verimcp/telemetry.py`).
- ✅ OTel API/SDK split: `proxy.py` only ever touches the always-safe,
  no-op-by-default OTel **API**; the real exporter (`--otel-exporter
  console|otlp`) is configured once at the CLI entry point, never inside
  `Proxy` itself — so `pip install verimcp` without the optional `[otel]`
  extra still works for everything except that flag, and omitting the flag
  entirely reproduces prior behavior exactly (same opt-in shape as
  `--audit-log`/`--policy-config`).
- ✅ Point a free/self-hostable viewer (Grafana, Jaeger, or any OTLP
  collector) at `--otel-exporter otlp --otel-endpoint <host:port>`; `console`
  exists for local no-infrastructure debugging, routed to **stderr**
  deliberately (verimcp's own stdout is the live MCP protocol channel).
  Full design, including two real bugs this caught (telemetry silently
  never firing without also passing `--audit-log`, due to guards that
  predated this phase) in
  [ADR 0005](adr/0005-metrics-via-otel-genai-conventions.md).

**Free/leverage:** this turned "build a dashboard" (a genuinely large
undertaking — storage, API, frontend) into "emit standard spans + use an
existing free viewer." The OTel Python SDK was already resolvable in this
environment (a transitive dependency of an unrelated project) — free,
open-source, and the credibility win ("uses real OTel GenAI conventions" vs
"built a custom dashboard") is worth more than a bespoke UI would be.

## Phase 5 — Real client compatibility 🟡 partially done

*(covers vision item #6)*

Goal: prove correctness against real MCP Hosts, not just our own test
suite.

- ✅ Run devmcp, then verimcp-fronting-devmcp, through **MCP Inspector**
  (official, free, open-source) — `scripts/inspector_smoke_test.py`,
  pinned to v1.0.1 (v2/`@latest` has a reproducible Windows CLI bug, see
  [ADR 0006](adr/0006-mcp-inspector-compatibility.md)). This is exactly what
  the goal describes it should do: the first live run caught a real bug our
  own test suite had never triggered — a `resources/list`/`initialize`
  response-tracking id collision with a backend's own self-originated
  request ids (same class ADR 0003 already solved once for elicitation,
  reintroduced by Phase 3's tracking sets) — fixed in `proxy.py`, with a
  permanent regression test
  (`test_resource_list_id_collision_with_backends_own_roots_list_id`).
  6/6 smoke-test checks pass after the fix, including proof that Phase 3's
  injected `verimcp://audit` resources are visible to this real,
  independently-implemented client.
- ⬜ Test against at least one more real client if accessible (e.g. an
  editor's MCP integration) to catch spec-compliance edge cases our own
  tests would never generate — not yet done, stated plainly rather than
  implied.

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
