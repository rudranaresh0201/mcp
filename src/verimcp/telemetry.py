"""Phase 4's metrics/observability layer: OpenTelemetry spans + metrics for
every tools/call and resources/read verimcp handles.

Deliberate API/SDK split, the same one every real instrumented library uses:
this module (and proxy.py, which calls it) only ever touches the
**OpenTelemetry API** (`opentelemetry.trace`, `opentelemetry.metrics`) --
which is always importable and produces true no-op spans/metrics if no SDK
provider has been configured. The **SDK** (exporters, providers -- the part
that actually sends data anywhere) is wired up exactly once, at the process
entry point (`cli.py`'s `main()`, via `configure_sdk`), never here. This is
why `--otel-exporter` can default to doing nothing at zero cost, the same
"opt-in, zero behavior change by default" norm `--audit-log` and
`--policy-config` already follow -- except here the no-op is free, provided
by OTel's own design, not something we had to build.

Span/attribute naming: `gen_ai.operation.name`, `gen_ai.tool.name`, and the
`gen_ai.client.operation.duration` histogram name follow the OpenTelemetry
**GenAI semantic conventions** -- an actual, if still explicitly
*Experimental*, industry standard for LLM/agent telemetry (see
docs/ROADMAP.md Phase 4 and docs/adr/0005-metrics-via-otel-genai-conventions.md).
Governance concepts with no semconv equivalent (gate verdicts, verification
results, approval status) get a `verimcp.*`-namespaced attribute instead --
the documented pattern for custom attributes that aren't part of a standard
convention, rather than inventing names that look standard but aren't.
"""
import sys
import time
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.trace import Span, Status, StatusCode

_tracer = trace.get_tracer("verimcp")
_meter = metrics.get_meter("verimcp")

_tool_call_counter = _meter.create_counter(
    "verimcp.tool_calls.total",
    unit="1",
    description="Every tools/call or resources/read verimcp handled, by tool_name and outcome.",
)
_policy_violation_counter = _meter.create_counter(
    "verimcp.policy_violations.total",
    unit="1",
    description="tools/call requests denied or held for approval by a policy gate, by tool_name and action.",
)
_duration_histogram = _meter.create_histogram(
    "gen_ai.client.operation.duration",
    unit="ms",
    description="End-to-end duration of a tools/call or resources/read as seen by verimcp, by tool_name and outcome.",
)

# Outcomes AuditStore.record already uses for a denial/failure -- kept in
# sync with proxy.py's outcome strings rather than re-deriving them, since
# they're the same "what happened" vocabulary the audit log already commits to.
# The call did not succeed. `backend_error` belongs here even though no
# verifier caught anything -- for a span's status, what matters is that the
# tool failed; *why* is the audit log's job, and the two answer different
# questions on purpose.
_ERROR_OUTCOMES = {"denied", "verified_failed", "backend_error"}

_provider_holders: dict[str, Any] = {"tracer_provider": None, "meter_provider": None}


def start_span(method: str, target: str, request_id: Any) -> Span:
    """Starts (but does not end) a span for one tools/call or resources/read.
    Returned span must be passed to finish_span once the outcome is known --
    started and finished from different points in Proxy's two independent
    read loops, the same request/response correlation problem
    Proxy._pending already solves, solved the same way (see proxy.py's
    self._active_spans)."""
    verb = "execute_tool" if method == "tools/call" else "read_resource"
    span = _tracer.start_span(f"{verb} {target}")
    span.set_attribute("gen_ai.operation.name", verb)
    span.set_attribute("gen_ai.tool.name", target)
    span.set_attribute("verimcp.request_id", str(request_id))
    return span


def finish_span(
    span: Span,
    start_time: float,
    *,
    tool_name: str,
    outcome: str,
    gate: dict | None = None,
    verification: dict | None = None,
    approval: dict | None = None,
) -> None:
    """Records the outcome of a span started by start_span: attributes, span
    status, the tool-call counter, and (if this was a policy denial or
    require_approval outcome) the policy-violation counter, then ends the
    span. `gate`/`verification`/`approval` follow AuditStore.record's own
    convention: None means that stage of the proxy didn't run for this call,
    not that it ran and found nothing."""
    duration_ms = (time.monotonic() - start_time) * 1000

    span.set_attribute("verimcp.outcome", outcome)
    if gate is not None:
        span.set_attribute("verimcp.gate.action", gate.get("action", ""))
    if verification is not None:
        span.set_attribute("verimcp.verification.passed", bool(verification.get("passed")))
    if approval is not None:
        span.set_attribute("verimcp.approval.status", approval.get("status", ""))

    if outcome in _ERROR_OUTCOMES:
        span.set_status(Status(StatusCode.ERROR, outcome))
    else:
        span.set_status(Status(StatusCode.OK))
    span.end()

    attributes = {"tool_name": tool_name, "outcome": outcome}
    _tool_call_counter.add(1, attributes)
    _duration_histogram.record(duration_ms, attributes)
    if gate is not None and gate.get("action") in ("deny", "require_approval"):
        _policy_violation_counter.add(1, {"tool_name": tool_name, "action": gate["action"]})


def abandon_span(span: Span) -> None:
    """Ends a span whose outcome will never be known -- the Host disconnected
    mid-call, so finish_span's normal path (reached via Proxy._record_audit)
    never runs for this request id. Called from Proxy.run()'s cleanup,
    alongside the existing cancellation of any still-pending background
    tasks, so a span doesn't stay open (and unexported) forever."""
    span.set_status(Status(StatusCode.ERROR, "connection closed before completion"))
    span.end()


def configure_sdk(exporter: str | None, endpoint: str) -> None:
    """Wires a real TracerProvider/MeterProvider so start_span/finish_span
    and the counters above actually export somewhere. Called once from
    cli.py's main(), before Proxy.run() -- never from inside Proxy itself
    (see this module's docstring for why). No-op if exporter is None
    (the default): every call in this module already produces true no-op
    spans/metrics against OTel's own default (unconfigured) global
    providers, so there is nothing to set up.

    Imports the SDK/exporter packages lazily (only when a real exporter is
    requested) so `pip install verimcp` without the `[otel]` extra still
    works for everything except this flag -- the same "fail loudly at
    startup, not on first call" shape ToolCallPolicyGate.from_yaml already
    uses for a bad --policy-config file."""
    if exporter is None:
        return

    try:
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import (
            ConsoleMetricExporter,
            PeriodicExportingMetricReader,
        )
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
    except ImportError as e:
        raise RuntimeError(
            f"--otel-exporter {exporter!r} needs the optional OTel SDK -- install it with "
            "`pip install verimcp[otel]`."
        ) from e

    resource = Resource.create({SERVICE_NAME: "verimcp"})

    if exporter == "console":
        # out=sys.stderr, not the default stdout: verimcp's own stdout *is*
        # the MCP protocol channel (jsonrpc.write_message_sync writes JSON-RPC
        # there directly) -- ConsoleSpanExporter's default would interleave
        # trace/metric JSON into that stream and corrupt it. stderr is the
        # correct place for a stdio MCP server's own diagnostic output.
        span_exporter = ConsoleSpanExporter(out=sys.stderr)
        metric_exporter = ConsoleMetricExporter(out=sys.stderr)
    elif exporter == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
        except ImportError as e:
            raise RuntimeError(
                "--otel-exporter otlp needs the OTLP gRPC exporter -- install it with "
                "`pip install verimcp[otel]`."
            ) from e
        span_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        metric_exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True)
    else:
        raise RuntimeError(f"unknown --otel-exporter {exporter!r} -- expected 'console' or 'otlp'")

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource, metric_readers=[PeriodicExportingMetricReader(metric_exporter)]
    )
    metrics.set_meter_provider(meter_provider)

    # trace.get_tracer/metrics.get_meter were already called at module import
    # time above, against the *default* (no-op) global providers -- OTel's
    # get_tracer/get_meter return live proxies that forward to whatever
    # provider is current, so re-pointing the globals here retroactively
    # makes the already-created _tracer/_meter (and every counter/histogram
    # built from _meter) start exporting for real, with no re-import needed.
    _provider_holders["tracer_provider"] = tracer_provider
    _provider_holders["meter_provider"] = meter_provider


def shutdown_sdk() -> None:
    """Flushes and shuts down whatever configure_sdk set up, so spans/metrics
    from the final calls of a run aren't lost when the process exits.
    No-op if configure_sdk was never called with a real exporter.

    shutdown() alone (not force_flush() first) -- both providers' shutdown()
    already performs a final flush internally; calling force_flush() first
    was redundant and produced two separate exports of the same final data
    (confirmed by inspecting real console-exporter output)."""
    tracer_provider = _provider_holders.get("tracer_provider")
    if tracer_provider is not None:
        tracer_provider.shutdown()
    meter_provider = _provider_holders.get("meter_provider")
    if meter_provider is not None:
        meter_provider.shutdown()
