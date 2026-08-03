# ADR 0005: Metrics/tracing via the OpenTelemetry API/SDK split, GenAI semantic conventions where they fit

## Status

Accepted

## Context

Phase 4 (`docs/ROADMAP.md`) asks for aggregated visibility into what verimcp
is doing -- total tool calls, verified vs failed, policy violations, latency,
most-used tools -- "visible, not just logged," and specifically calls for
using **OpenTelemetry GenAI semantic conventions** instead of a bespoke
dashboard, the same way ADR 0004 chose a real MCP resource over a bespoke
audit viewer.

Two design questions had to be settled against how verimcp is actually
structured, not assumed:

1. verimcp's own stdout is not free real estate -- `jsonrpc.write_message_sync`
   (`src/verimcp/jsonrpc.py`) writes live MCP protocol JSON-RPC there
   directly, the same channel the Host reads. Any telemetry exporter that
   also defaults to stdout would interleave trace/metric JSON into that
   stream and corrupt it.
2. A `tools/call`'s request and its response are read by two different
   coroutines (`Proxy._forward_host_to_backend` / `_forward_backend_to_host`),
   the same split ADR 0003 and `self._pending` already exist to handle --
   whatever represents "this call is in flight" for tracing purposes has to
   survive being started in one coroutine and finished in the other.

## Decision: OTel API in library code, SDK configured once at the CLI entry point

`src/verimcp/telemetry.py` and `proxy.py` only ever call the **OpenTelemetry
API** (`opentelemetry.trace`, `opentelemetry.metrics`) -- confirmed, not
assumed, that `trace.get_tracer()`/`metrics.get_meter()` return live proxy
objects that forward to whatever provider is *currently* set at call time,
not at `get_tracer()` time: a tracer obtained before any SDK provider exists
produces true no-op spans, and the exact same tracer object starts exporting
correctly the moment a real provider is installed later, with no re-import
needed (verified directly: a span created before `set_tracer_provider` never
appears in the exporter; the same call after does). This is what lets
`--otel-exporter` default to `None` at zero cost -- every span/counter call
in `proxy.py` runs unconditionally, and is a genuine no-op until asked
otherwise, the same "opt-in, zero behavior change by default" shape
`--audit-log`/`--policy-config` already established, except here the
no-op-by-default behavior is provided by OTel's own design rather than
something we had to build ourselves.

The **SDK** (real `TracerProvider`/`MeterProvider`, real exporters) is
configured exactly once, in `cli.py`'s `main()`, via
`telemetry.configure_sdk(exporter, endpoint)`, before `Proxy.run()` starts --
never inside `Proxy` itself. This is the standard shape for any instrumented
library (never force a library's consumer to pull in exporter dependencies
just to import it), reflected in the dependency split:
`opentelemetry-api` is a base dependency (lightweight, no exporters);
`opentelemetry-sdk` and the OTLP gRPC exporter are behind the optional
`verimcp[otel]` extra, imported lazily inside `configure_sdk` so a plain
`pip install verimcp` never needs them.

**Console exporter routed to stderr, not its default stdout.** Confirmed via
`ConsoleSpanExporter`/`ConsoleMetricExporter`'s actual constructor
signatures that both accept an `out:` parameter. `configure_sdk`'s
`"console"` branch passes `out=sys.stderr` explicitly -- this is not
cosmetic, it is the fix for the exact stdout-corruption risk in Context
item 1 above, and it matches the general MCP-over-stdio convention that
stdout is protocol-only and diagnostics belong on stderr.

## Decision: span correlation via a dict, same shape as `self._pending`

`Proxy._active_spans: dict[id, tuple[Span, float]]`, keyed by JSON-RPC id.
A span is started for every `tools/call`/`resources/read` at the earliest
point that id is first seen in `_forward_host_to_backend` -- before gating,
before the `require_approval` dispatch, before `self._pending` itself is
populated -- so one span covers the full lifecycle regardless of which path
the call takes afterward (denied, held for approval, or forwarded).
`_record_audit` -- already the single choke point every one of those paths
funnels through (gate denial, all three `_handle_approval` denial branches,
and the accept-then-completed path via `_record_completed_audit`) -- pops
and finishes the span there. `Proxy.run()`'s existing cleanup (which already
cancels leftover `_background_tasks` on exit) now also ends any span still
open if the Host disconnects mid-call, via `telemetry.abandon_span`, so a
span can't stay open forever un-exported.

**Two pre-existing `self._audit is None` guards had to be found and
loosened**, not just the one in `_record_audit` itself:
`_record_completed_audit` had its own separate early return before ever
reaching `_record_audit`, and `_handle_approval`'s accept branch only
stashed gate/approval metadata into `_pending_approval_meta`
`if self._audit is not None`. Both meant telemetry would have silently
never fired for anyone running `--otel-exporter` without also passing
`--audit-log` -- caught by writing an integration test first (spawn real
`verimcp` fronting real `devmcp`, `--otel-exporter console`, no
`--audit-log`) and tracing the actual miss with temporary debug prints
rather than assuming the refactor was correct because it read correctly.
Both guards are now removed; audit logging and telemetry are independent
opt-ins that happen to share the same lifecycle hooks.

## Decision: GenAI semantic conventions where they fit, `verimcp.*` custom namespace elsewhere

Span name `f"execute_tool {target}"` / `f"read_resource {target}"`,
attributes `gen_ai.operation.name` and `gen_ai.tool.name`, and the histogram
name `gen_ai.client.operation.duration` follow the OpenTelemetry **GenAI
semantic conventions** -- a real, adopted-in-spirit industry convention for
LLM/agent/tool telemetry, cited here rather than invented. That convention
set is explicitly marked **Experimental** upstream as of this writing, so
these exact attribute names may shift in a future OTel release; this is
stated plainly rather than presented as a frozen standard.

Governance concepts verimcp itself introduces -- gate verdicts
(`verimcp.gate.action`), verification results (`verimcp.verification.passed`),
approval status (`verimcp.approval.status`), and the two counters
(`verimcp.tool_calls.total`, `verimcp.policy_violations.total`) -- have no
GenAI semconv equivalent, so they get an explicit `verimcp.*` namespace
rather than names that look standard but aren't. Only `None`-valued fields
(a stage of the proxy that didn't run for this call) are omitted from a
span's attributes, the same convention `AuditStore.record` already uses for
`gate`/`verification`/`approval`.

## Decision: not a custom dashboard

Per `docs/ROADMAP.md` Phase 4's own framing: point a free, self-hostable
viewer (Jaeger, Grafana, or any OTLP-speaking collector) at
`--otel-exporter otlp --otel-endpoint <host:port>`, rather than building
storage + an API + a frontend ourselves. `--otel-exporter console` exists
purely for local, no-infrastructure debugging (see stderr routing above).

## Consequences

- `--otel-exporter`/`--otel-endpoint` are both fully opt-in: omitting
  `--otel-exporter` reproduces prior behavior exactly (proven by the full
  existing 117-test suite passing unchanged after this phase's changes).
- Testing instrumentation without a real collector uses two layers: real
  subprocess integration tests (spawn `verimcp --otel-exporter console`
  fronting real `devmcp`, parse the real stderr as OTel's own
  pretty-printed-JSON console format) for outcomes an honest backend can
  actually produce, and fast in-process unit tests against OTel's
  `InMemorySpanExporter`/`InMemoryMetricReader` for outcomes it can't
  (`verified_failed`, since no lying-backend fixture exists yet --
  `docs/ROADMAP.md` Phase 6, Adversarial test corpus, is where that belongs).
  The in-process tests share one global provider setup across the module
  rather than one per test, because OTel's `set_tracer_provider`/
  `set_meter_provider` are documented one-shot calls per process (a second
  call is a no-op with a warning) -- confirmed directly rather than assumed,
  after the first attempt at per-test provider setup silently failed for
  every test after the first.
- Full verifier-triggered telemetry (a real lying backend producing
  `verified_failed` through the live pipe) remains future work under Phase 6,
  not silently faked as covered here.
