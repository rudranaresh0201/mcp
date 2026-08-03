"""Phase 4's telemetry tests, two layers:

1. Real-pipe integration tests (same shape as test_proxy_integration.py --
   spawn the real `verimcp` command fronting real `devmcp`, `--otel-exporter
   console`) proving spans genuinely reach the console exporter over the
   *correct* stream. This matters concretely: verimcp's own stdout is the
   live MCP protocol channel (jsonrpc.write_message_sync writes JSON-RPC
   there directly), so ConsoleSpanExporter's default destination (stdout)
   would corrupt it -- telemetry.configure_sdk routes it to stderr instead
   (see telemetry.py). These tests parse the subprocess's real stderr, not a
   mock, so a regression back to the stdout default would fail loudly here
   (protocol messages on stdout would stop parsing as clean JSON-RPC).

2. Fast unit tests directly against telemetry.finish_span's attribute/status
   mapping, using OTel's own InMemorySpanExporter/InMemoryMetricReader (the
   standard way to unit-test instrumentation). Covers verified_failed and
   the policy-violation counter -- outcomes real devmcp (an honest backend)
   can't produce through the live pipe, since a full "verifier catches a
   lying backend" fixture doesn't exist yet (that's ROADMAP.md Phase 6,
   Adversarial test corpus, not this phase's scope). This mirrors the
   project's existing precedent: verifier "catches a fabricated X" tests
   (test_git_commit_verifier.py etc.) are unit-level against verify()
   directly, not routed through a real lying backend either.
"""
import asyncio
import json
import sys
from pathlib import Path

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from verimcp import telemetry


async def _send(proc, message: dict) -> None:
    proc.stdin.write((json.dumps(message) + "\n").encode())
    await proc.stdin.drain()


async def _recv(proc) -> dict:
    line = await proc.stdout.readline()
    return json.loads(line)


async def _spawn_verimcp(repo_path: Path, verimcp_args: list[str]):
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", *verimcp_args, "--",
        sys.executable, "-m", "devmcp.cli", "--repo-path", str(repo_path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _initialize(proc, capabilities: dict | None = None) -> None:
    await _send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": capabilities or {},
            "clientInfo": {"name": "test-client", "version": "0"},
        },
    })
    await _recv(proc)
    await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})


def _parse_console_json_objects(text: str) -> list[dict]:
    """ConsoleSpanExporter/ConsoleMetricExporter each print one pretty-printed
    JSON object per export call, back to back with no separator -- not
    NDJSON. json.JSONDecoder.raw_decode walks the concatenated text one
    object at a time."""
    decoder = json.JSONDecoder()
    objects = []
    pos = 0
    text = text.strip()
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        obj, end = decoder.raw_decode(text, pos)
        objects.append(obj)
        pos = end
    return objects


async def _run_to_completion_and_get_stderr(proc) -> str:
    proc.stdin.close()
    stderr = await proc.stderr.read()
    await proc.wait()
    return stderr.decode()


async def _safe_terminate(proc) -> None:
    """proc.terminate() raises ProcessLookupError on Windows if the process
    already exited (e.g. via _run_to_completion_and_get_stderr's own
    proc.wait()) -- unlike test_proxy_integration.py's tests, these always
    run the subprocess to a real, natural exit first."""
    if proc.returncode is None:
        proc.terminate()
    await proc.wait()


async def test_span_emitted_for_an_allowed_verified_tool_call(tmp_path: Path):
    proc = await _spawn_verimcp(tmp_path, ["--otel-exporter", "console"])
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })
        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        response = await _recv(proc)
        assert response["result"]["isError"] is False

        stderr = await _run_to_completion_and_get_stderr(proc)
        spans = [o for o in _parse_console_json_objects(stderr) if "context" in o]
        assert len(spans) == 1
        span = spans[0]
        assert span["name"] == "execute_tool write_file"
        attrs = span["attributes"]
        assert attrs["gen_ai.operation.name"] == "execute_tool"
        assert attrs["gen_ai.tool.name"] == "write_file"
        assert attrs["verimcp.outcome"] == "verified_ok"
        assert attrs["verimcp.verification.passed"] is True
        assert span["status"]["status_code"] == "OK"
    finally:
        await _safe_terminate(proc)


async def test_span_emitted_for_a_policy_denied_tool_call(tmp_path: Path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: write_file\n    action: deny\n")

    proc = await _spawn_verimcp(tmp_path, ["--otel-exporter", "console", "--policy-config", str(policy_file)])
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })
        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        response = await _recv(proc)
        assert "error" in response

        stderr = await _run_to_completion_and_get_stderr(proc)
        spans = [o for o in _parse_console_json_objects(stderr) if "context" in o]
        assert len(spans) == 1
        span = spans[0]
        assert span["attributes"]["verimcp.outcome"] == "denied"
        assert span["attributes"]["verimcp.gate.action"] == "deny"
        assert span["status"]["status_code"] == "ERROR"

        metrics_blob = next(o for o in _parse_console_json_objects(stderr) if "resource_metrics" in o)
        counters = _flatten_metric_points(metrics_blob, "verimcp.policy_violations.total")
        assert any(p["attributes"].get("action") == "deny" for p in counters)
    finally:
        await _safe_terminate(proc)


async def test_span_combines_verification_and_approval_on_accept(tmp_path: Path):
    """require_approval + verification both apply to the same call --
    ADR 0004 combines them into one audit entry rather than two partial
    ones; the span should carry both sets of attributes for the same
    reason (one span per call, not one per proxy stage)."""
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: write_file\n    action: require_approval\n")

    proc = await _spawn_verimcp(tmp_path, ["--otel-exporter", "console", "--policy-config", str(policy_file)])
    try:
        await _initialize(proc, capabilities={"elicitation": {}})
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })
        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        elicitation = await _recv(proc)
        await _send(proc, {"jsonrpc": "2.0", "id": elicitation["id"], "result": {"action": "accept"}})
        response = await _recv(proc)
        assert response["result"]["isError"] is False

        stderr = await _run_to_completion_and_get_stderr(proc)
        spans = [o for o in _parse_console_json_objects(stderr) if "context" in o]
        assert len(spans) == 1
        attrs = spans[0]["attributes"]
        assert attrs["verimcp.outcome"] == "verified_ok"
        assert attrs["verimcp.approval.status"] == "accept"
        assert attrs["verimcp.verification.passed"] is True
    finally:
        await _safe_terminate(proc)


async def test_span_emitted_for_approval_decline(tmp_path: Path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: write_file\n    action: require_approval\n")

    proc = await _spawn_verimcp(tmp_path, ["--otel-exporter", "console", "--policy-config", str(policy_file)])
    try:
        await _initialize(proc, capabilities={"elicitation": {}})
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })
        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        elicitation = await _recv(proc)
        await _send(proc, {"jsonrpc": "2.0", "id": elicitation["id"], "result": {"action": "decline"}})
        response = await _recv(proc)
        assert "error" in response

        stderr = await _run_to_completion_and_get_stderr(proc)
        spans = [o for o in _parse_console_json_objects(stderr) if "context" in o]
        assert len(spans) == 1
        attrs = spans[0]["attributes"]
        assert attrs["verimcp.outcome"] == "denied"
        assert attrs["verimcp.approval.status"] == "decline"
        assert attrs["verimcp.gate.action"] == "require_approval"
        assert spans[0]["status"]["status_code"] == "ERROR"

        metrics_blob = next(o for o in _parse_console_json_objects(stderr) if "resource_metrics" in o)
        counters = _flatten_metric_points(metrics_blob, "verimcp.policy_violations.total")
        assert any(p["attributes"].get("action") == "require_approval" for p in counters)
    finally:
        await _safe_terminate(proc)


async def test_no_otel_exporter_flag_means_nothing_on_stderr(tmp_path: Path):
    """Default (no --otel-exporter) must stay a true no-op -- no spans, no
    metrics, nothing printed anywhere, matching every other opt-in flag's
    existing convention (--audit-log, --policy-config)."""
    proc = await _spawn_verimcp(tmp_path, [])
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })
        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        await _recv(proc)

        stderr = await _run_to_completion_and_get_stderr(proc)
        assert stderr.strip() == ""
    finally:
        await _safe_terminate(proc)


def _flatten_metric_points(metrics_blob: dict, name: str) -> list[dict]:
    points = []
    for rm in metrics_blob.get("resource_metrics", []):
        for sm in rm.get("scope_metrics", []):
            for metric in sm.get("metrics", []):
                if metric.get("name") == name:
                    points.extend(metric.get("data", {}).get("data_points", []))
    return points


# -- Unit tests: telemetry.py's own attribute/status mapping, in-process,
# against OTel's in-memory test exporters. Covers outcomes a real (honest)
# devmcp backend can't produce through the live pipe -- see module docstring.
#
# OTel's global tracer/meter provider can only be *set* once per process
# (a second set_tracer_provider/set_meter_provider call is a documented no-op
# with a warning, protecting against accidental double-configuration) -- so
# unlike the subprocess tests above (each its own fresh process), these two
# unit tests share one in-memory provider, configured once at module import,
# and disambiguate their data by using distinct tool_name values per test
# rather than by resetting global state between tests.
_SPAN_EXPORTER = InMemorySpanExporter()
_METRIC_READER = InMemoryMetricReader()
_unit_test_tracer_provider = TracerProvider()
_unit_test_tracer_provider.add_span_processor(SimpleSpanProcessor(_SPAN_EXPORTER))
trace.set_tracer_provider(_unit_test_tracer_provider)
metrics.set_meter_provider(MeterProvider(metric_readers=[_METRIC_READER]))


def test_finish_span_marks_verified_failed_as_error():
    _SPAN_EXPORTER.clear()
    span = telemetry.start_span("tools/call", "git_commit", request_id=1)
    telemetry.finish_span(
        span, start_time=0.0, tool_name="git_commit", outcome="verified_failed",
        verification={"verifiers": ["GitCommitVerifier"], "passed": False},
    )

    finished = _SPAN_EXPORTER.get_finished_spans()
    assert len(finished) == 1
    assert finished[0].status.status_code.name == "ERROR"
    assert finished[0].attributes["verimcp.verification.passed"] is False


def test_policy_violation_counter_only_increments_for_deny_and_require_approval():
    allowed = telemetry.start_span("tools/call", "read_file_unit_test", request_id=101)
    telemetry.finish_span(allowed, start_time=0.0, tool_name="read_file_unit_test", outcome="forwarded")

    denied = telemetry.start_span("tools/call", "write_file_unit_test", request_id=102)
    telemetry.finish_span(
        denied, start_time=0.0, tool_name="write_file_unit_test", outcome="denied", gate={"action": "deny"}
    )

    points = []
    for rm in _METRIC_READER.get_metrics_data().resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == "verimcp.policy_violations.total":
                    points.extend(metric.data.data_points)

    assert any(dict(p.attributes).get("tool_name") == "write_file_unit_test" for p in points)
    assert not any(dict(p.attributes).get("tool_name") == "read_file_unit_test" for p in points)
